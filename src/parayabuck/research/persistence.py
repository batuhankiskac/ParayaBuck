"""Filesystem bundle for reproducible research runs.

Directory layout (example):
    research_runs/
      2026-08-29_equity-v3-beta-grid_a91e38/
        manifest.json
        job.json
        trials.jsonl        # all trials, appended incrementally (parent-only)
        failures.jsonl      # failed trials subset
        summary.json        # snapshot, atomic
        summary.csv         # snapshot, atomic
        environment.json    # snapshot (also in manifest)

All snapshot files use atomic_write. JSONL is append-only with fsync per line.
Parent process owns all persistence; workers only calculate.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.utils.io import atomic_write

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _atomic_json(path: Path, obj: Any) -> None:
    text = json.dumps(obj, indent=2, sort_keys=True, default=str)
    atomic_write(path, text + "\n")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_jsonl(path: Path, obj: Any) -> None:
    """Append one JSON line, fsync immediately, parent-only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, default=str)
    # Use low-level append with fsync
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    # Also fsync directory for durability
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(path.parent, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


class ExperimentStore:
    """Owns a single research run directory."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).resolve()
        self.manifest_path = self.run_dir / "manifest.json"
        self.job_path = self.run_dir / "job.json"
        self.trials_path = self.run_dir / "trials.jsonl"
        self.failures_path = self.run_dir / "failures.jsonl"
        self.summary_json_path = self.run_dir / "summary.json"
        self.summary_csv_path = self.run_dir / "summary.csv"
        self.environment_path = self.run_dir / "environment.json"

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        base_dir: str | Path | None,
        spec: Any,
        resolved: Any,
        config: Any = None,
    ) -> ExperimentStore:
        """Create a new bundle directory and write initial manifest.

        base_dir: parent research_runs dir (default research_runs)
        spec: ExperimentSpec
        resolved: ResolvedExperiment (contains ids, fingerprints)
        config: Config for provenance (optional)
        """
        parent = Path(base_dir) if base_dir else Path("research_runs")
        _ensure_dir(parent)
        # Directory name: 2026-08-29_<slug>_<exp_short>
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        slug = getattr(spec, "slug", "experiment")
        exp_short = resolved.experiment_id[:8] if hasattr(resolved, "experiment_id") else "unknown"
        # Avoid collision: add run_id suffix
        run_short = resolved.run_id.split("-")[-1][:6] if hasattr(resolved, "run_id") else "run"
        dirname = f"{date_str}_{slug}_{exp_short}_{run_short}"
        # If exists, add counter
        run_dir = parent / dirname
        counter = 0
        while run_dir.exists():
            counter += 1
            run_dir = parent / f"{dirname}_{counter}"
        _ensure_dir(run_dir)

        store = cls(run_dir)
        # Build initial manifest
        manifest = store._build_manifest(spec, resolved, config, status="running")
        _atomic_json(store.manifest_path, manifest)
        # Job
        job_dict = store._serialize_job(resolved.job if hasattr(resolved, "job") else None)
        _atomic_json(store.job_path, job_dict)
        # Environment
        env = manifest.get("environment", {})
        _atomic_json(store.environment_path, env)
        # Empty trials if not exists
        if not store.trials_path.exists():
            store.trials_path.write_text("", encoding="utf-8")
        if not store.failures_path.exists():
            store.failures_path.write_text("", encoding="utf-8")
        # Initial summary
        _atomic_json(store.summary_json_path, {"schema_version": SCHEMA_VERSION, "trials": [], "status": "running"})
        # CSV header will be written on first append via snapshot
        return store

    def _build_manifest(self, spec: Any, resolved: Any, config: Any, status: str = "running") -> dict[str, Any]:
        from parayabuck.research.fingerprint import environment_provenance, git_provenance

        git = git_provenance()
        env = environment_provenance()

        # Strategy version attempt from config
        strategy_version: str | None = None
        try:
            if hasattr(resolved, "job") and resolved.job is not None:
                # Try to get strategy version from portfolio factory
                from parayabuck.portfolio import portfolio_strategy_from

                s_name = resolved.job.strategy_name or getattr(spec, "strategy", None)
                if s_name:
                    strat = portfolio_strategy_from(s_name, parameters={})
                    strategy_version = getattr(strat, "version", None) or getattr(strat, "strategy_version", None)
        except Exception:
            strategy_version = None

        # Dataset fingerprint already in resolved
        dataset_fp = getattr(resolved, "dataset_fingerprint", None) or getattr(resolved, "dataset_fp", None)
        universe_fp = getattr(resolved, "universe_fingerprint", None) or getattr(resolved, "universe_fp", None)
        corporate_fp = getattr(resolved, "corporate_fingerprint", None) or getattr(
            resolved, "corporate_actions_fp", None
        )
        config_digest = getattr(resolved, "config_digest", None)

        # Workers
        workers_requested = getattr(spec, "workers", None)
        workers_resolved = getattr(resolved, "workers_resolved", workers_requested)

        # Timestamps
        created_at = _now_iso()

        # Build manifest
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": getattr(resolved, "experiment_id", None),
            "run_id": getattr(resolved, "run_id", None),
            "name": getattr(spec, "name", None),
            "slug": getattr(spec, "slug", None),
            "tags": getattr(spec, "tags", []),
            "baseline": getattr(spec, "baseline", None),
            "git_commit": git.get("git_commit"),
            "git_dirty": git.get("git_dirty"),
            "git_branch": git.get("git_branch"),
            "reproducibility": git.get("reproducibility"),
            "strategy_name": getattr(spec, "strategy", None) or getattr(resolved.job, "strategy_name", None)
            if hasattr(resolved, "job")
            else getattr(spec, "strategy", None),
            "strategy_version": strategy_version,
            "market": str(getattr(spec, "market", "")),
            "timeframe": getattr(resolved, "timeframe", None),
            "base_parameters": getattr(spec, "base_parameters", {}),
            "grid": getattr(spec, "grid", {}),
            "execution_policy": config_digest.get("execution") if isinstance(config_digest, dict) else None,
            "initial_cash": getattr(spec, "initial_cash", None),
            "dataset_path": getattr(spec, "dataset", None),
            "dataset_fingerprint": dataset_fp,
            "dataset_fingerprint_id": getattr(resolved, "dataset_fingerprint_id", None),
            "universe_fingerprint": universe_fp,
            "universe_fingerprint_id": getattr(resolved, "universe_fingerprint_id", None),
            "corporate_actions_fingerprint": corporate_fp,
            "start": str(getattr(spec, "start", "")) if getattr(spec, "start", None) else None,
            "end": str(getattr(spec, "end", "")) if getattr(spec, "end", None) else None,
            "tier": str(getattr(spec, "tier", "")),
            "config_digest": config_digest,
            "config_digest_id": getattr(resolved, "config_digest_id", None),
            "engine_version": getattr(resolved, "engine_version", None) or "1.0.0",
            "workers_requested": workers_requested,
            "workers_resolved": workers_resolved,
            "environment": env,
            "created_at": created_at,
            "completed_at": None,
            "status": status,
            "trial_count": len(getattr(resolved, "trials", []) or []),
            "trials": [
                {"trial_id": tid, "label": t.get("label"), "parameters": t.get("parameters")}
                for tid, t in zip(
                    getattr(resolved, "trial_ids", []) or [],
                    getattr(resolved, "trials", []) or [],
                )
            ],
            "output_dir": str(self.run_dir),
        }
        return manifest

    def _serialize_job(self, job: Any) -> dict[str, Any]:
        if job is None:
            return {"schema_version": SCHEMA_VERSION, "job": None}
        try:
            # ResearchJob is dataclass
            d = asdict(job)

            # Convert datetime to iso, Market to str, etc.
            def _conv(v: Any) -> Any:
                if isinstance(v, datetime):
                    return v.isoformat()
                if hasattr(v, "value") and isinstance(v, type(job.market)) if hasattr(job, "market") else False:
                    return str(v)
                if isinstance(v, dict):
                    return {k: _conv(vv) for k, vv in v.items()}
                if isinstance(v, (list, tuple)):
                    return [_conv(x) for x in v]
                return v

            # Use canonical conversion
            from parayabuck.research.fingerprint import _normalize_value

            normalized = _normalize_value(d)
            return {"schema_version": SCHEMA_VERSION, "job": normalized}
        except Exception as exc:
            return {"schema_version": SCHEMA_VERSION, "job": str(job), "error": str(exc)}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, run_dir: str | Path) -> ExperimentStore:
        p = Path(run_dir).resolve()
        if not p.exists() or not p.is_dir():
            raise FileNotFoundError(f"Run directory not found: {p}")
        store = cls(p)
        if not store.manifest_path.exists():
            raise FileNotFoundError(f"manifest.json missing in {p}")
        return store

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"manifest.json missing in {self.run_dir}")
        data = _load_json(self.manifest_path)
        if data.get("schema_version") != SCHEMA_VERSION:
            # Allow forward compat but warn
            if data.get("schema_version") is None:
                raise ValueError(f"Missing schema_version in {self.manifest_path}")
            # For now, strict check: only 1 supported
            if data["schema_version"] != SCHEMA_VERSION:
                raise ValueError(f"Unsupported schema_version {data['schema_version']} (expected {SCHEMA_VERSION})")
        return data  # type: ignore[no-any-return]

    def load_job(self) -> dict[str, Any]:
        if not self.job_path.exists():
            return {}
        return _load_json(self.job_path)  # type: ignore[no-any-return]

    def load_trials(self) -> list[dict[str, Any]]:
        if not self.trials_path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for line in self.trials_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip corrupted lines but log
                    continue
        except FileNotFoundError:
            return []
        return out

    def completed_trial_ids(self) -> set[str]:
        trials = self.load_trials()
        return {str(t.get("trial_id")) for t in trials if t.get("status") == "ok" and t.get("trial_id") is not None}

    def failed_trial_ids(self) -> set[str]:
        trials = self.load_trials()
        return {str(t.get("trial_id")) for t in trials if t.get("status") == "failed" and t.get("trial_id") is not None}

    def all_trial_ids(self) -> set[str]:
        trials = self.load_trials()
        return {str(t.get("trial_id")) for t in trials if t.get("trial_id") is not None}

    # ------------------------------------------------------------------
    # Persistence (parent-only)
    # ------------------------------------------------------------------

    def append_trial(self, result: dict[str, Any]) -> None:
        """Append one trial result (dict) to trials.jsonl and failures if needed.

        result should contain trial_id, label, status, etc.
        """
        _append_jsonl(self.trials_path, result)
        if result.get("status") == "failed":
            _append_jsonl(self.failures_path, result)
        # Update snapshot summaries
        self._update_snapshots()

    def _update_snapshots(self) -> None:
        trials = self.load_trials()
        # summary.json
        summary = {
            "schema_version": SCHEMA_VERSION,
            "run_dir": str(self.run_dir),
            "trial_count": len(trials),
            "completed": sum(1 for t in trials if t.get("status") == "ok"),
            "failed": sum(1 for t in trials if t.get("status") == "failed"),
            "trials": trials,
        }
        _atomic_json(self.summary_json_path, summary)
        # summary.csv
        self._write_summary_csv(trials)

    def _write_summary_csv(self, trials: list[dict[str, Any]]) -> None:
        if not trials:
            return
        # Determine all parameter keys for flattening
        param_keys: set[str] = set()
        for t in trials:
            params = t.get("parameters") or {}
            if isinstance(params, dict):
                param_keys.update(str(k) for k in params.keys())
        param_keys_sorted = sorted(param_keys)

        # CSV columns: trial_id, label, status, then params, then metrics
        base_fields = ["trial_id", "label", "status"]
        metric_fields = [
            "total_return",
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "calmar",
            "turnover",
            "costs",
            "trade_count",
            "fill_count",
            "rebalance_count",
            "final_equity",
            "elapsed_s",
            "worker_pid",
            "error",
        ]
        fieldnames = base_fields + [f"param_{k}" for k in param_keys_sorted] + metric_fields
        # Build rows
        rows: list[dict[str, Any]] = []
        for t in trials:
            row: dict[str, Any] = {}
            for f in base_fields:
                row[f] = t.get(f)
            params = t.get("parameters") or {}
            for k in param_keys_sorted:
                row[f"param_{k}"] = params.get(k) if isinstance(params, dict) else None
            for f in metric_fields:
                row[f] = t.get(f)
            rows.append(row)
        # Write atomically via temp + atomic_write (build CSV string)
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        content = buf.getvalue()
        atomic_write(self.summary_csv_path, content)

    def update_manifest(self, updates: dict[str, Any]) -> None:
        """Atomic update of manifest.json with given fields."""
        manifest = self.load_manifest()
        manifest.update(updates)
        _atomic_json(self.manifest_path, manifest)

    def mark_completed(self, status: str = "completed") -> None:
        now = _now_iso()
        self.update_manifest({"status": status, "completed_at": now})

    def validate_resume(self, new_experiment_id: str) -> None:
        """Raise if new experiment_id does not match stored."""
        manifest = self.load_manifest()
        stored = manifest.get("experiment_id")
        if stored is None:
            raise ValueError("Stored manifest missing experiment_id")
        if stored != new_experiment_id:
            raise ValueError(
                f"Resume rejected: experiment_id mismatch.\n"
                f"  Stored: {stored}\n"
                f"  New:    {new_experiment_id}\n"
                f"This is not the same scientific experiment (params/dataset/universe changed). "
                f"Create a new run instead."
            )

    def inspect(self) -> dict[str, Any]:
        """Read-only inspection without running backtests."""
        manifest = self.load_manifest()
        trials = self.load_trials()
        # Best by Sharpe or total_return
        best = None
        if trials:
            ok = [t for t in trials if t.get("status") == "ok"]
            if ok:
                # Prefer sharpe, fallback to total_return
                try:
                    best = max(
                        ok,
                        key=lambda x: (
                            x.get("sharpe") if x.get("sharpe") is not None else float("-inf"),
                            x.get("total_return") or float("-inf"),
                        ),
                    )
                except Exception:
                    best = ok[0]
        return {
            "manifest": manifest,
            "trials": trials,
            "completed": sum(1 for t in trials if t.get("status") == "ok"),
            "failed": sum(1 for t in trials if t.get("status") == "failed"),
            "pending": manifest.get("trial_count", 0) - len(trials),
            "best": best,
        }
