"""Summarize pytest durations without making timing a correctness gate.

The report is deliberately advisory.  It records call/setup durations, compares
them with a prior report when requested, and surfaces metadata supplied by the
caller for fixture/source-load size.  It never fails because a test was slow.

Example::

    pytest -m "research and not historical_research and not research_deep" \
      --durations=0 --durations-min=0 > /tmp/research-fast.txt 2>&1; status=$?
    python scripts/research/report_test_performance.py /tmp/research-fast.txt \
      --exit-code "$status" --repo-root . --output-json /tmp/research-fast.json

The exit code is passed separately so an interrupted or failed pytest run is
reported as incomplete/failed and can never be mistaken for a successful
benchmark.  The reporter itself returns zero after producing an advisory
report; callers must preserve the pytest exit code in their wrapper.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "research-test-performance-v1"
_DURATION_RE = re.compile(r"^(?P<seconds>\d+(?:\.\d+)?)s\s+(?P<phase>\w+)\s+(?P<nodeid>.+?)\s*$")
_SUMMARY_STATUS_RE = re.compile(r"(?P<count>\d+)\s+(?P<status>passed|failed|skipped|deselected)")
_SUMMARY_WALL_RE = re.compile(r"\bin\s+(?P<seconds>\d+(?:\.\d+)?)s\b")
_SOURCE_CLUE_RE = re.compile(r"(?:copytree\(|read_(?:csv|bytes|text)\()")


@dataclass(frozen=True)
class Duration:
    nodeid: str
    seconds: float
    phase: str

    def as_dict(self) -> dict[str, Any]:
        return {"nodeid": self.nodeid, "seconds": self.seconds, "phase": self.phase}


def parse_durations(text: str) -> tuple[list[Duration], dict[str, Any]]:
    """Parse pytest ``--durations=0`` output.

    Only lines with a numeric phase duration are consumed.  This keeps the
    parser independent of pytest's progress format and ignores warnings or
    traceback text.  The summary is optional because interrupted runs may not
    emit one.
    """

    durations: list[Duration] = []
    for raw_line in text.splitlines():
        match = _DURATION_RE.match(raw_line.strip())
        if match:
            seconds = float(match.group("seconds"))
            if seconds >= 0 and match.group("nodeid"):
                durations.append(Duration(match.group("nodeid"), seconds, match.group("phase")))

    summary: dict[str, Any] = {}
    for raw_line in reversed(text.splitlines()):
        wall_match = _SUMMARY_WALL_RE.search(raw_line)
        statuses = list(_SUMMARY_STATUS_RE.finditer(raw_line))
        if not wall_match or not statuses:
            continue
        for match in statuses:
            summary[match.group("status")] = int(match.group("count"))
        summary["wall_seconds"] = float(wall_match.group("seconds"))
        break
    if "durations <" in text and "Use -vv" in text:
        summary["duration_coverage"] = "partial"
    return durations, summary


def _load_json(path: Path | None, default: Any) -> Any:
    if path is None:
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    return value


def _category(nodeid: str, rules: Iterable[Mapping[str, Any]]) -> str:
    for rule in rules:
        pattern = rule.get("pattern")
        category = rule.get("category")
        if isinstance(pattern, str) and isinstance(category, str) and fnmatch.fnmatch(nodeid, pattern):
            return category
    return "unclassified"


def _advisory_category(seconds: float, declared: str, *, small_seconds: float, slow_seconds: float) -> str:
    """Assign a non-gating timing bucket while preserving declared categories."""

    if declared == "deep":
        return "deep"
    if seconds >= slow_seconds:
        return "slow"
    if seconds <= small_seconds:
        return "small"
    return "medium"


def scan_source_clues(
    nodeids: Iterable[str],
    *,
    repo_root: Path,
    rules: Iterable[Mapping[str, Any]] = (),
    deep_categories: frozenset[str] = frozenset({"deep", "research-deep"}),
) -> list[dict[str, Any]]:
    """Find heuristic full-data/fixture clues with stable file and line refs.

    This is intentionally static and advisory.  It does not inspect file sizes,
    import modules, or execute test code; a clue means only that a reviewer
    should inspect that source line before keeping a test in the fast tier.
    """

    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for nodeid in sorted(set(nodeids)):
        relative = nodeid.split("::", 1)[0]
        if not relative.startswith("tests/"):
            continue
        path = repo_root / relative
        if not path.is_file():
            continue
        declared = _category(nodeid, rules)
        if declared in deep_categories:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, 1):
            match = _SOURCE_CLUE_RE.search(line)
            if not match:
                continue
            key = (relative, line_number)
            if key in seen:
                continue
            seen.add(key)
            warnings.append(
                {
                    "kind": "static_full_data_or_fixture_clue",
                    "nodeid": nodeid,
                    "category": declared,
                    "clue": f"{relative}:{line_number}",
                    "reason": "heuristic source load or fixture construction; inspect before fast-tier retention",
                    "heuristic": True,
                }
            )
    return warnings


def _normalise_metadata(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    rows = value.get("nodes", value)
    if not isinstance(rows, Mapping):
        return {}
    return {str(key): item for key, item in rows.items() if isinstance(item, Mapping)}


def build_report(
    durations: Iterable[Duration],
    summary: Mapping[str, Any],
    *,
    threshold_seconds: float = 1.0,
    small_seconds: float = 0.1,
    category_rules: Iterable[Mapping[str, Any]] = (),
    baseline: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Mapping[str, Any]] | None = None,
    fixture_bytes_threshold: int = 1_000_000,
    source_load_bytes_threshold: int = 10_000_000,
    run_exit_code: int = 0,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Build a stable advisory report from parsed durations and metadata."""

    rows = list(durations)
    phases_by_node: dict[str, list[Duration]] = {}
    for row in rows:
        phases_by_node.setdefault(row.nodeid, []).append(row)
    effective_summary = dict(summary)
    expected_calls = int(effective_summary.get("passed", 0)) + int(effective_summary.get("failed", 0))
    reported_calls = sum(1 for row in rows if row.phase == "call")
    if expected_calls and reported_calls < expected_calls:
        effective_summary["duration_coverage"] = "partial"
    categories = list(category_rules)
    node_metadata = metadata or {}
    baseline_nodes: dict[str, Mapping[str, Any]] = {}
    if baseline:
        raw_nodes = baseline.get("nodes", [])
        if isinstance(raw_nodes, list):
            baseline_nodes = {
                str(row["nodeid"]): row
                for row in raw_nodes
                if isinstance(row, Mapping) and isinstance(row.get("nodeid"), str)
            }

    node_rows: list[dict[str, Any]] = []
    for nodeid, phase_rows in phases_by_node.items():
        declared = _category(nodeid, categories)
        total_seconds = sum(row.seconds for row in phase_rows)
        node_rows.append(
            {
                "nodeid": nodeid,
                "total_seconds": total_seconds,
                "category": declared,
                "advisory_category": _advisory_category(
                    total_seconds,
                    declared,
                    small_seconds=small_seconds,
                    slow_seconds=threshold_seconds,
                ),
                "phases": [row.as_dict() for row in phase_rows],
                **({"metadata": dict(node_metadata[nodeid])} if nodeid in node_metadata else {}),
            }
        )
    node_rows.sort(key=lambda item: (-item["total_seconds"], item["nodeid"]))

    slow = [row for row in node_rows if row["total_seconds"] >= threshold_seconds]
    new_slow = []
    for row in slow:
        old = baseline_nodes.get(row["nodeid"])
        old_seconds = float(old.get("total_seconds", old.get("seconds", 0.0))) if old else 0.0
        if old is None or old_seconds < threshold_seconds:
            new_slow.append(row)
    crossings: list[dict[str, Any]] = []
    for row in node_rows:
        old = baseline_nodes.get(row["nodeid"])
        if not old:
            continue
        old_category = old.get("advisory_category", old.get("category", "other"))
        new_category = row["advisory_category"]
        if old_category != new_category:
            crossings.append(
                {
                    "nodeid": row["nodeid"],
                    "from": old_category,
                    "to": new_category,
                    "total_seconds": row["total_seconds"],
                }
            )

    warnings: list[dict[str, Any]] = []
    for row in node_rows:
        item = node_metadata.get(row["nodeid"], {})
        fixture_bytes = item.get("fixture_bytes")
        source_load_bytes = item.get("source_load_bytes")
        source_load_files = item.get("source_load_files")
        if isinstance(fixture_bytes, int) and fixture_bytes >= fixture_bytes_threshold:
            warnings.append(
                {
                    "nodeid": row["nodeid"],
                    "kind": "oversized_fixture",
                    "bytes": fixture_bytes,
                    "threshold_bytes": fixture_bytes_threshold,
                }
            )
        if isinstance(source_load_bytes, int) and source_load_bytes >= source_load_bytes_threshold:
            warnings.append(
                {
                    "nodeid": row["nodeid"],
                    "kind": "oversized_source_load",
                    "bytes": source_load_bytes,
                    "threshold_bytes": source_load_bytes_threshold,
                    "files": source_load_files,
                }
            )

    by_category: dict[str, dict[str, Any]] = {}
    for row in node_rows:
        bucket = by_category.setdefault(row["category"], {"nodes": 0, "seconds": 0.0})
        bucket["nodes"] += 1
        bucket["seconds"] += row["total_seconds"]
    for bucket in by_category.values():
        bucket["seconds"] = round(float(bucket["seconds"]), 6)

    # Static source clues require explicit classification metadata.  Without
    # rules, the reporter cannot know which nodes are fast and emits no clues.
    static_warnings = (
        scan_source_clues((row["nodeid"] for row in node_rows), repo_root=repo_root, rules=categories)
        if repo_root is not None and categories
        else []
    )
    if run_exit_code or int(effective_summary.get("failed", 0)) > 0:
        benchmark_status = "failed"
    elif not effective_summary or effective_summary.get("duration_coverage") == "partial":
        benchmark_status = "incomplete"
    else:
        benchmark_status = "complete"
    return {
        "schema": SCHEMA,
        "benchmark_status": benchmark_status,
        "pytest_exit_code": run_exit_code,
        "advisory_only": True,
        "summary": effective_summary,
        "thresholds": {
            "slow_seconds": threshold_seconds,
            "small_seconds": small_seconds,
            "fixture_bytes": fixture_bytes_threshold,
            "source_load_bytes": source_load_bytes_threshold,
        },
        "nodes": node_rows,
        "category_totals": by_category,
        "findings": {
            "slow": slow,
            "new_slow": new_slow,
            "category_crossings": crossings,
            "metadata_warnings": warnings,
            "static_source_clues": static_warnings,
        },
    }


def render_summary(report: Mapping[str, Any]) -> str:
    """Render a compact deterministic human summary."""

    summary = report.get("summary", {})
    findings = report.get("findings", {})
    lines = [
        f"Research test performance ({report.get('schema', SCHEMA)})",
        f"Wall time: {summary.get('wall_seconds', 'unknown')}s; nodes: {len(report.get('nodes', []))}; "
        f"coverage: {summary.get('duration_coverage', 'complete')}",
        f"Status: {report.get('benchmark_status', 'unknown')} (advisory only); "
        f"slow nodes: {len(findings.get('slow', []))}; new slow: {len(findings.get('new_slow', []))}; "
        f"category crossings: {len(findings.get('category_crossings', []))}; "
        f"metadata warnings: {len(findings.get('metadata_warnings', []))}; "
        f"static source clues: {len(findings.get('static_source_clues', []))}",
        "",
        "Top node totals:",
    ]
    for row in list(report.get("nodes", []))[:10]:
        lines.append(f"  {float(row['total_seconds']):.2f}s [{row.get('advisory_category', 'other')}] {row['nodeid']}")
    for warning in list(findings.get("static_source_clues", []))[:5]:
        lines.append(f"  source clue: {warning['clue']} ({warning['nodeid']})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="pytest output containing --durations=0 --durations-min=0 lines")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-text", type=Path)
    parser.add_argument("--baseline", type=Path, help="a prior report JSON for new-slow/category comparisons")
    parser.add_argument("--category-rules", type=Path, help="JSON list of {pattern, category} rules")
    parser.add_argument("--metadata", type=Path, help="JSON mapping nodeids to fixture/source-load metadata")
    parser.add_argument("--slow-seconds", type=float, default=1.0)
    parser.add_argument("--small-seconds", type=float, default=0.1)
    parser.add_argument("--exit-code", type=int, default=0, help="pytest exit code captured by the wrapper")
    parser.add_argument("--repo-root", type=Path, help="repo root for heuristic source clues")
    parser.add_argument("--fixture-bytes", type=int, default=1_000_000)
    parser.add_argument("--source-load-bytes", type=int, default=10_000_000)
    args = parser.parse_args(argv)
    if args.slow_seconds < 0 or args.small_seconds < 0 or args.fixture_bytes < 0 or args.source_load_bytes < 0:
        parser.error("thresholds must be non-negative")

    durations, summary = parse_durations(args.input.read_text(encoding="utf-8"))
    rules = _load_json(args.category_rules, [])
    if not isinstance(rules, list):
        parser.error("category rules must be a JSON list")
    metadata = _normalise_metadata(_load_json(args.metadata, {}))
    baseline = _load_json(args.baseline, None)
    if baseline is not None and not isinstance(baseline, Mapping):
        parser.error("baseline must be a JSON object")
    report = build_report(
        durations,
        summary,
        threshold_seconds=args.slow_seconds,
        small_seconds=args.small_seconds,
        category_rules=rules,
        baseline=baseline,
        metadata=metadata,
        fixture_bytes_threshold=args.fixture_bytes,
        source_load_bytes_threshold=args.source_load_bytes,
        run_exit_code=args.exit_code,
        repo_root=args.repo_root,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    text = render_summary(report)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(encoded, encoding="utf-8")
    if args.output_text:
        args.output_text.parent.mkdir(parents=True, exist_ok=True)
        args.output_text.write_text(text, encoding="utf-8")
    if not args.output_json and not args.output_text:
        sys.stdout.write(text)
    # Preserve the actual test outcome for wrappers.  Threshold findings and
    # advisory warnings never alter this code.
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
