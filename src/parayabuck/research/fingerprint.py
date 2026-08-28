"""Deterministic fingerprints for research experiments.

All identity serialization is canonical: sorted keys, deterministic datetime,
stable float repr, SHA256 over canonical JSON.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from parayabuck.core.models import Market

SCHEMA_VERSION = 1
ENGINE_VERSION = "1.0.0"


def _normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        # Deterministic UTC ISO
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        # Normalize to POSIX, try relative to repo root for stability
        try:
            # Use absolute posix, but prefer relative if inside cwd
            cwd = Path.cwd()
            if value.is_absolute():
                try:
                    rel = value.relative_to(cwd)
                    return str(rel.as_posix())
                except ValueError:
                    return value.as_posix()
            return value.as_posix()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_value(v) for v in value]
    if isinstance(value, Market):
        return value.value
    # Fallback to string
    return str(value)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON with sorted keys and stable separators."""
    normalized = _normalize_value(obj)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sha256_short(obj: Any, length: int = 16) -> str:
    return sha256_hex(obj)[:length]


# ---------------------------------------------------------------------------
# Dataset fingerprint
# ---------------------------------------------------------------------------

_DATASET_HASH_CACHE: dict[str, str] = {}


def dataset_fingerprint(
    dataset_path: str | Path | None,
    market: Market | str | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    """Fast fingerprint for dataset; avoids SHA256 every run.

    Returns dict with kind, path, size, mtime_ns, market for content-addressing.
    For smoke/synthetic, returns synthetic marker.
    For strong, call dataset_fingerprint_strong if needed.
    """
    if dataset_path is None:
        # Synthetic or default
        return {
            "kind": "synthetic" if tier == "smoke" else "default",
            "market": _normalize_value(market) if market else None,
            "tier": tier,
        }
    p = Path(dataset_path)
    try:
        if not p.exists():
            return {
                "kind": "missing",
                "path": str(p.as_posix()),
                "market": _normalize_value(market) if market else None,
            }
        stat = p.stat()
        # Use normalized path for id, but keep absolute for debugging
        norm_path = p.as_posix()
        try:
            rel = p.resolve().relative_to(Path.cwd().resolve())
            norm_path = rel.as_posix()
        except Exception:
            pass
        return {
            "kind": "file",
            "path": norm_path,
            "absolute": str(p.resolve().as_posix()) if p.is_absolute() or p.exists() else None,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "market": _normalize_value(market) if market else None,
        }
    except Exception as exc:
        return {"kind": "error", "path": str(p), "error": str(exc)}


def dataset_fingerprint_id(fp: dict[str, Any]) -> str:
    return sha256_short(fp, 16)


def dataset_strong_hash(dataset_path: str | Path) -> str | None:
    """Optional strong SHA256, cached by fast fingerprint."""
    p = Path(dataset_path)
    if not p.exists() or not p.is_file():
        return None
    try:
        fp = dataset_fingerprint(dataset_path)
        cache_key = sha256_hex(fp)
        if cache_key in _DATASET_HASH_CACHE:
            return _DATASET_HASH_CACHE[cache_key]
        # Check disk cache
        cache_dir = Path("data/cache/research/dataset_hashes")
        cache_file = cache_dir / f"{cache_key}.txt"
        if cache_file.exists():
            try:
                txt = cache_file.read_text().strip()
                if txt:
                    _DATASET_HASH_CACHE[cache_key] = txt
                    return txt
            except Exception:
                pass
        # Compute
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        _DATASET_HASH_CACHE[cache_key] = digest
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(digest, encoding="utf-8")
        except Exception:
            pass
        return digest
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Universe fingerprint
# ---------------------------------------------------------------------------


def universe_fingerprint(
    dataset_fp: dict[str, Any],
    market: Market | str,
    timeframe: str | None,
    end: datetime | None,
    scanner_digest: str | None,
    scanner_rebalance: Any | None = None,
) -> dict[str, Any]:
    """Fingerprint for PIT universe (reuses cache key logic)."""
    return {
        "dataset_fp": dataset_fp,
        "market": _normalize_value(market),
        "timeframe": timeframe,
        "end": _normalize_value(end),
        "scanner_digest": scanner_digest,
        "scanner_rebalance": _normalize_value(scanner_rebalance),
    }


def universe_fingerprint_id(fp: dict[str, Any]) -> str:
    return sha256_short(fp, 16)


# ---------------------------------------------------------------------------
# Config digest (sanitized, semantic only)
# ---------------------------------------------------------------------------


def config_digest(config: Any) -> dict[str, Any]:
    """Sanitized digest of semantic config (no secrets).

    Includes only strategy, universe, execution, backtest.
    Excludes alpaca, live, backup, app, data secrets.
    """
    try:
        # Config is parayabuck.config.Config
        data: dict[str, Any] = {}
        # Strategy
        if hasattr(config, "strategy"):
            strat = config.strategy
            if hasattr(strat, "equity"):
                data["strategy_equity"] = {
                    "name": getattr(strat.equity, "name", None),
                    "parameters": getattr(strat.equity, "parameters", None),
                    "enabled": getattr(strat.equity, "enabled", None),
                    "timeframe": getattr(strat.equity, "timeframe", None),
                }
            if hasattr(strat, "crypto"):
                data["strategy_crypto"] = {
                    "name": getattr(strat.crypto, "name", None),
                    "parameters": getattr(strat.crypto, "parameters", None),
                    "enabled": getattr(strat.crypto, "enabled", None),
                    "timeframe": getattr(strat.crypto, "timeframe", None),
                }
        # Universe
        if hasattr(config, "universe"):
            uni = config.universe
            if hasattr(uni, "equity"):
                # Use scanner_config_digest if available, else dump
                try:
                    from parayabuck.universe.scanner import scanner_config_digest

                    data["universe_equity_digest"] = scanner_config_digest(uni.equity)
                except Exception:
                    data["universe_equity"] = _normalize_value(uni.equity)
            if hasattr(uni, "crypto"):
                try:
                    from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest

                    data["universe_crypto_digest"] = crypto_scanner_config_digest(uni.crypto)
                except Exception:
                    data["universe_crypto"] = _normalize_value(uni.crypto)
        # Execution
        if hasattr(config, "execution"):
            exe = config.execution
            data["execution"] = {
                "rebalance_band_pct": getattr(exe, "rebalance_band_pct", None),
                "minimum_order_notional": getattr(exe, "minimum_order_notional", None),
            }
        # Backtest
        if hasattr(config, "backtest"):
            bt = config.backtest
            data["backtest"] = {
                "initial_cash": getattr(bt, "initial_cash", None),
                "scanner_rebalance": getattr(bt, "scanner_rebalance", None),
                "ranking_metric": getattr(bt, "ranking_metric", None),
            }
        # Corporate actions not in config, handled separately
        return data
    except Exception as exc:
        return {"error": str(exc), "fallback": str(config)[:500]}


def config_digest_id(config: Any) -> str:
    return sha256_short(config_digest(config), 16)


# ---------------------------------------------------------------------------
# Corporate actions fingerprint
# ---------------------------------------------------------------------------


def corporate_actions_fingerprint(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"kind": "none"}
    p = Path(path)
    if not p.exists():
        return {"kind": "missing", "path": str(p.as_posix())}
    try:
        stat = p.stat()
        norm = p.as_posix()
        try:
            rel = p.resolve().relative_to(Path.cwd().resolve())
            norm = rel.as_posix()
        except Exception:
            pass
        return {"kind": "file", "path": norm, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except Exception as exc:
        return {"kind": "error", "path": str(p), "error": str(exc)}


# ---------------------------------------------------------------------------
# Trial identity
# ---------------------------------------------------------------------------


def trial_fingerprint(
    parameters: dict[str, Any] | None,
    execution_overrides: dict[str, Any] | None,
    cost_multiplier: float,
    metadata: dict[str, Any] | None = None,
    strategy_name: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Deterministic trial fingerprint; label included for uniqueness but not scientific identity."""
    payload: dict[str, Any] = {
        "parameters": parameters or {},
        "execution_overrides": execution_overrides or {},
        "cost_multiplier": float(cost_multiplier),
    }
    # Include strategy name if provided for cross-strategy uniqueness
    if strategy_name:
        payload["strategy_name"] = strategy_name
    # Include label for uniqueness when same economics but different human labels (e.g., explicit + grid duplicate)
    if label:
        payload["label"] = label
    # Include metadata only if non-empty (semantic)
    if metadata:
        payload["metadata"] = metadata
    return payload


def trial_id(
    parameters: dict[str, Any] | None,
    execution_overrides: dict[str, Any] | None,
    cost_multiplier: float,
    metadata: dict[str, Any] | None = None,
    strategy_name: str | None = None,
    label: str | None = None,
) -> str:
    fp = trial_fingerprint(parameters, execution_overrides, cost_multiplier, metadata, strategy_name, label)
    return sha256_short(fp, 12)


# ---------------------------------------------------------------------------
# Experiment identity
# ---------------------------------------------------------------------------


def experiment_fingerprint(
    strategy_name: str | None,
    strategy_version: str | None,
    market: Market | str,
    dataset_fp: dict[str, Any],
    start: datetime | None,
    end: datetime | None,
    universe_fp: dict[str, Any],
    corporate_actions_fp: dict[str, Any],
    config_digest_dict: dict[str, Any],
    tier: str | Market | Any,
    trial_fps: list[dict[str, Any]],
    execution_assumptions: dict[str, Any] | None = None,
    base_parameters: dict[str, Any] | None = None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Combine semantic inputs into experiment fingerprint."""
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "market": _normalize_value(market),
        "dataset_fp": dataset_fp,
        "start": _normalize_value(start),
        "end": _normalize_value(end),
        "universe_fp": universe_fp,
        "corporate_actions_fp": corporate_actions_fp,
        "config_digest": config_digest_dict,
        "tier": _normalize_value(tier),
        "base_parameters": base_parameters or {},
        "trials": sorted(trial_fps, key=lambda x: canonical_json(x)),
        "execution_assumptions": execution_assumptions or {},
        "engine_version": engine_version or ENGINE_VERSION,
    }


def experiment_id(fp: dict[str, Any]) -> str:
    return sha256_short(fp, 16)


def run_id() -> str:
    """Execution identity: timestamp + random."""
    import uuid

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rnd = uuid.uuid4().hex[:8]
    return f"{ts}-{rnd}"


# ---------------------------------------------------------------------------
# Git provenance
# ---------------------------------------------------------------------------


def git_provenance(cwd: Path | None = None) -> dict[str, Any]:
    """Capture git commit, dirty, branch. Never fails."""
    cwd = cwd or Path.cwd()

    def run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if out.returncode == 0:
                return out.stdout.strip()
            return None
        except Exception:
            return None

    commit = run(["rev-parse", "HEAD"])
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = None
    try:
        # porcelain --porcelain v1
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0:
            dirty = bool(out.stdout.strip())
        else:
            dirty = None
    except Exception:
        dirty = None

    # Fallback commit from env or file
    if commit is None:
        # Try to read from .git/HEAD?
        try:
            git_head = Path(cwd) / ".git" / "HEAD"
            if git_head.exists():
                txt = git_head.read_text().strip()
                if txt.startswith("ref:"):
                    ref = txt.split("ref:")[1].strip()
                    ref_path = Path(cwd) / ".git" / ref
                    if ref_path.exists():
                        commit = ref_path.read_text().strip()
        except Exception:
            pass

    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "git_branch": branch if branch != "HEAD" else None,
        "reproducibility": "degraded" if dirty else ("unknown" if commit is None else "full"),
    }


# ---------------------------------------------------------------------------
# Environment provenance
# ---------------------------------------------------------------------------


def environment_provenance() -> dict[str, Any]:
    import importlib.metadata

    def _ver(pkg: str) -> str | None:
        try:
            return importlib.metadata.version(pkg)
        except Exception:
            return None

    # Try to get package version
    pkg_ver = _ver("parayabuck")
    numpy_ver = _ver("numpy")
    pandas_ver = _ver("pandas")
    # Also try import
    try:
        import numpy

        numpy_ver = getattr(numpy, "__version__", numpy_ver)
    except Exception:
        pass
    try:
        import pandas

        pandas_ver = getattr(pandas, "__version__", pandas_ver)
    except Exception:
        pass

    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "parayabuck_version": pkg_ver,
        "numpy_version": numpy_ver,
        "pandas_version": pandas_ver,
        "executable": sys.executable,
    }
