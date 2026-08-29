"""Research service — read-only filesystem exposure of Mission 3 bundles (Mission 6).

Exposes list/detail/compare over research_runs/*/manifest.json + summary.json
with filesystem safety:
* resolve root, ensure is_relative_to, reject traversal
* limit 500 enforced per endpoint
* degrades to unavailable/UNKNOWN instead of 500 on missing root/DB

Reuses CLI listing logic (manifest+summary without loading full trials.jsonl).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.research")

_DEFAULT_ROOT = Path("research_runs")
_LIMIT_MAX = 500
_LIMIT_DEFAULT = 100
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _resolve_root(root: str | Path | None) -> Path:
    raw = Path(root) if root is not None and str(root).strip() else _DEFAULT_ROOT
    # allow absolute or relative; resolve for safety
    try:
        resolved = raw.resolve()
    except Exception:
        resolved = (Path.cwd() / raw).resolve()
    return resolved


def _ensure_relative(root: Path, candidate: Path) -> Path:
    """Resolve candidate and ensure it is within root; raise ValueError on traversal."""
    try:
        cand_resolved = candidate.resolve()
        root_resolved = root.resolve()
    except Exception as exc:
        raise ValueError(f"invalid path: {exc}") from exc
    try:
        cand_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("traversal rejected") from exc
    return cand_resolved


def _clamp_limit(limit: int | None, default: int = _LIMIT_DEFAULT) -> int:
    if limit is None:
        return default
    try:
        v = int(limit)
    except Exception:
        return default
    return max(1, min(v, _LIMIT_MAX))


def _is_safe_run_id(run_id: str) -> bool:
    # run_id is typically a directory basename like "2026-08-28_equity-v3-small-grid_26981590_080ede"
    # Allow alphanum, dash, underscore, dot; reject slash, traversal, empty
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        return False
    if ".." in run_id:
        return False
    # length bound to avoid huge inputs
    if len(run_id) > 256:
        return False
    # basic char check — allow broader than SAFE_ID to include mixed dir names
    if not re.match(r"^[A-Za-z0-9._\-]+$", run_id):
        # still allow because some runs contain extra? be permissive but reject traversal
        return False
    return True


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _discover_run_dirs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    candidates: list[Path] = []
    for child in root.iterdir():
        if child.is_dir() and (child / "manifest.json").exists():
            candidates.append(child)
        elif child.is_dir():
            # one level deeper (nested research_runs)
            try:
                for sub in child.iterdir():
                    if sub.is_dir() and (sub / "manifest.json").exists():
                        candidates.append(sub)
            except Exception:
                continue
    # also if root itself is a run dir
    if (root / "manifest.json").exists():
        candidates.append(root)
    return candidates


def list_runs(
    root: str | Path | None = None,
    limit: int | None = None,
    status: str | None = None,
    strategy: str | None = None,
    market: str | None = None,
    tag: str | None = None,
    sort: str = "created",
    **_extra: Any,
) -> dict[str, Any]:
    """List research runs (read-only, bounded limit 500)."""
    # Handle router positional _root() vs keyword calls
    if isinstance(root, int) and limit is None:
        limit = root  # type: ignore
        root = None
    bounded = _clamp_limit(limit)
    try:
        root_path = _resolve_root(root)
    except ValueError as exc:
        return {"runs": [], "count": 0, "total": 0, "limit": bounded, "error": str(exc), "unavailable": True}
    run_dirs = _discover_run_dirs(root_path)
    rows: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        try:
            _ensure_relative(root_path, run_dir)
        except ValueError:
            continue
        manifest = _load_json(run_dir / "manifest.json")
        if not isinstance(manifest, dict):
            continue
        summary_path = run_dir / "summary.json"
        trials_completed = 0
        trials_failed = 0
        best_sharpe = None
        best_cagr = None
        trial_count = manifest.get("trial_count", 0)
        run_status = manifest.get("status", "unknown")
        if summary_path.exists():
            summary = _load_json(summary_path)
            if isinstance(summary, dict):
                trials = summary.get("trials", []) if isinstance(summary.get("trials"), list) else []
                trials_completed = sum(1 for t in trials if isinstance(t, dict) and t.get("status") == "ok")
                trials_failed = sum(1 for t in trials if isinstance(t, dict) and t.get("status") == "failed")
                ok_trials = [t for t in trials if isinstance(t, dict) and t.get("status") == "ok"]
                if ok_trials:
                    try:
                        best = max(
                            ok_trials, key=lambda x: (x.get("sharpe") or float("-inf"), x.get("cagr") or float("-inf"))
                        )
                        best_sharpe = best.get("sharpe")
                        best_cagr = best.get("cagr")
                    except Exception:
                        pass
        # filters
        if status is not None and str(run_status) != str(status):
            continue
        if strategy is not None and str(manifest.get("strategy_name")) != str(strategy):
            continue
        if market is not None and str(manifest.get("market")) != str(market):
            continue
        if tag is not None:
            tags = manifest.get("tags") or []
            if str(tag) not in [str(t) for t in tags]:
                continue
        rows.append(
            {
                "created": manifest.get("created_at") or "",
                "status": run_status,
                "experiment": str(manifest.get("experiment_id", ""))[:8],
                "experiment_full": manifest.get("experiment_id", ""),
                "run_id": manifest.get("run_id", ""),
                "strategy": manifest.get("strategy_name", ""),
                "market": manifest.get("market", ""),
                "tier": manifest.get("tier", ""),
                "trials": trial_count,
                "completed": trials_completed,
                "failed": trials_failed,
                "best_sharpe": best_sharpe,
                "best_cagr": best_cagr,
                "run_dir": str(run_dir),
                "tags": ",".join(str(t) for t in (manifest.get("tags") or [])),
            }
        )
    # sorting
    if sort == "best_sharpe":
        rows.sort(
            key=lambda r: r.get("best_sharpe") if r.get("best_sharpe") is not None else float("-inf"), reverse=True
        )  # type: ignore
    elif sort == "best_cagr":
        rows.sort(key=lambda r: r.get("best_cagr") if r.get("best_cagr") is not None else float("-inf"), reverse=True)  # type: ignore
    elif sort == "status":
        rows.sort(key=lambda r: str(r.get("status") or ""))
    else:  # created default newest first
        rows.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    total = len(rows)
    rows = rows[:bounded]
    return {"runs": rows, "count": len(rows), "total": total, "limit": bounded, "root": str(root_path)}


def get_run_detail(run_id: str, root: str | Path | None = None) -> dict[str, Any]:
    """Detail for one run (manifest + summary + trials metadata). Traversal-safe."""
    if not run_id or not run_id.strip():
        return {"error": "run id required", "unavailable": True}
    raw = run_id.strip()
    # reject traversal / absolute
    if "/" in raw or "\\" in raw or raw.startswith(".") or ".." in raw:
        raise ValueError("traversal rejected")
    if len(raw) > 256:
        raise ValueError("run id too long")
    # allow either basename or full path? Treat as basename within root.
    # If raw contains path separator we already rejected; so it's basename.
    if not _is_safe_run_id(raw):
        raise ValueError("invalid run id")
    try:
        root_path = _resolve_root(root)
    except ValueError as exc:
        return {"error": str(exc), "unavailable": True}
    # Candidate paths: first try direct child, then nested one level
    candidates = [root_path / raw]
    # also search via discovery to match run_id full (manifest run_id) vs dirname
    run_dirs = _discover_run_dirs(root_path)
    matched: Path | None = None
    for cand in candidates:
        try:
            resolved = _ensure_relative(root_path, cand)
        except ValueError:
            return {"error": "traversal rejected", "unavailable": True}
        if resolved.exists() and (resolved / "manifest.json").exists():
            matched = resolved
            break
    if matched is None:
        # Try to match by run_id suffix or manifest run_id
        for d in run_dirs:
            try:
                _ensure_relative(root_path, d)
            except ValueError:
                continue
            manifest = _load_json(d / "manifest.json")
            if isinstance(manifest, dict) and (
                str(manifest.get("run_id")) == raw or d.name == raw or d.name.endswith(raw)
            ):
                matched = d
                break
            if d.name == raw:
                matched = d
                break
    if matched is None:
        return {"run_id": raw, "error": "run not found", "unavailable": True}
    manifest = _load_json(matched / "manifest.json")
    if not isinstance(manifest, dict):
        return {"run_id": raw, "error": "manifest unreadable", "unavailable": True}
    summary = _load_json(matched / "summary.json")
    # Load trials (summary contains trials; if missing, try trials.jsonl capped)
    trials: list[dict[str, Any]] = []
    if isinstance(summary, dict) and isinstance(summary.get("trials"), list):
        trials = summary.get("trials", [])  # type: ignore
    else:
        tpath = matched / "trials.jsonl"
        if tpath.exists():
            try:
                # cap at 1000 lines to avoid huge read; detail still capped? but service limit is 500 for list
                for idx, line in enumerate(tpath.read_text(encoding="utf-8").splitlines()):
                    if idx >= 1000:
                        break
                    if line.strip():
                        trials.append(json.loads(line))
            except Exception:
                trials = []
    return {
        "run_id": manifest.get("run_id", raw),
        "run_dir": str(matched),
        "manifest": manifest,
        "summary": summary,
        "trials": trials[:500],
        "trial_count": len(trials),
    }


def compare_runs(run_a: str, run_b: str, root: str | Path | None = None) -> dict[str, Any]:
    """Compare two runs by trial_id, returning deltas (read-only, no promotion)."""
    if not run_a or not run_b:
        return {"error": "run_a and run_b required", "unavailable": True}
    # traversal check via detail resolver
    a_detail = get_run_detail(run_a, root=root)
    if a_detail.get("unavailable") and a_detail.get("error") in {"traversal rejected", "invalid run id"}:
        raise ValueError("traversal rejected for run_a")
    b_detail = get_run_detail(run_b, root=root)
    if b_detail.get("unavailable") and b_detail.get("error") in {"traversal rejected", "invalid run id"}:
        raise ValueError("traversal rejected for run_b")
    if a_detail.get("unavailable"):
        return {"error": f"run_a unavailable: {a_detail.get('error')}", "unavailable": True}
    if b_detail.get("unavailable"):
        return {"error": f"run_b unavailable: {b_detail.get('error')}", "unavailable": True}
    a_trials = {
        str(t.get("trial_id")): t for t in a_detail.get("trials", []) if isinstance(t, dict) and t.get("trial_id")
    }
    b_trials = {
        str(t.get("trial_id")): t for t in b_detail.get("trials", []) if isinstance(t, dict) and t.get("trial_id")
    }
    common_ids = set(a_trials.keys()) & set(b_trials.keys())
    only_a = sorted(set(a_trials.keys()) - common_ids)
    only_b = sorted(set(b_trials.keys()) - common_ids)
    deltas: list[dict[str, Any]] = []
    for tid in sorted(common_ids):
        ta = a_trials[tid]
        tb = b_trials[tid]
        entry: dict[str, Any] = {"trial_id": tid, "label": ta.get("label") or tb.get("label")}
        for metric in (
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "turnover",
            "costs",
            "fill_count",
            "elapsed_s",
            "total_return",
            "calmar",
            "final_equity",
        ):
            va = ta.get(metric)
            vb = tb.get(metric)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                entry[f"{metric}_a"] = va
                entry[f"{metric}_b"] = vb
                entry[f"{metric}_delta"] = float(vb) - float(va)
            else:
                entry[f"{metric}_a"] = va
                entry[f"{metric}_b"] = vb
                entry[f"{metric}_delta"] = None
        deltas.append(entry)
    return {
        "run_a": a_detail.get("run_dir"),
        "run_b": b_detail.get("run_dir"),
        "run_a_id": a_detail.get("run_id"),
        "run_b_id": b_detail.get("run_id"),
        "common_trials": len(common_ids),
        "only_in_a": only_a,
        "only_in_b": only_b,
        "deltas": deltas,
    }
