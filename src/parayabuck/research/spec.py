"""YAML experiment spec parsing and deterministic grid expansion."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from parayabuck.core.models import Market
from parayabuck.research.models import ResearchTier

DEFAULT_MAX_TRIALS = 500


def _parse_date(value: Any, *, end_of_day: bool = False) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        if end_of_day and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            # Keep as is; caller may want end_of_day handling
            pass
        return dt
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Try ISO
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            if end_of_day:
                # Move to end of day if date only
                if "T" not in s and " " not in s:
                    dt = dt.replace(hour=23, minute=59, second=59, tzinfo=UTC)
            return dt
        except ValueError as exc:
            raise ValueError(f"Invalid date {value!r}: {exc}") from exc
    raise ValueError(f"Invalid date {value!r}")


def _slugify(name: str) -> str:
    import re

    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "experiment"


@dataclass(frozen=True)
class GridSpec:
    """User grid definition."""

    grid: dict[str, list[Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentSpec:
    """Validated user-facing experiment spec (YAML)."""

    name: str
    market: Market = Market.EQUITY
    strategy: str | None = None
    tier: ResearchTier = ResearchTier.QUICK
    workers: int | str = 1
    dataset: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    base_parameters: dict[str, Any] = field(default_factory=dict)
    trials: list[dict[str, Any]] = field(default_factory=list)
    grid: dict[str, list[Any]] = field(default_factory=dict)
    baseline: str | None = None
    tags: list[str] = field(default_factory=list)
    max_trials: int = DEFAULT_MAX_TRIALS
    fail_fast: bool = False
    initial_cash: float | None = None
    corporate_actions: str | None = None
    config_path: str | None = None
    output_dir: str | None = None
    # Raw for debugging
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return _slugify(self.name)


def _coerce_workers(value: Any) -> int | str:
    if value is None:
        return 1
    if isinstance(value, int):
        if value < 1:
            raise ValueError("workers must be >=1")
        return value
    if isinstance(value, str):
        w = value.strip().lower()
        if w in {"auto", "1", "2", "4", "8"}:
            if w == "auto":
                return "auto"
            return int(w)
        try:
            iv = int(w)
            if iv < 1:
                raise ValueError()
            return iv
        except Exception:
            if w == "auto":
                return "auto"
            raise ValueError(f"Invalid workers {value!r}") from None
    raise ValueError(f"Invalid workers {value!r}")


def parse_experiment_spec(path: str | Path, *, allow_large: bool = False) -> ExperimentSpec:
    """Parse and validate YAML/JSON spec file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Spec file not found: {p}")
    raw_text = p.read_text(encoding="utf-8")
    if p.suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(raw_text)
    else:
        data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise ValueError("Spec must be a mapping")
    return _validate_spec_dict(data, allow_large=allow_large)


def _validate_spec_dict(data: dict[str, Any], *, allow_large: bool = False) -> ExperimentSpec:
    # Name required
    name = str(data.get("name") or data.get("experiment") or "experiment").strip()
    if not name:
        raise ValueError("Spec name is required")

    # Market
    market_raw = str(data.get("market", "equity")).strip().lower()
    try:
        market = Market(market_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid market {market_raw!r}") from exc

    # Strategy
    strategy = data.get("strategy")
    if strategy is not None:
        strategy = str(strategy).strip() or None

    # Tier
    tier_raw = str(data.get("tier", "quick")).strip().lower()
    try:
        tier = ResearchTier(tier_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid tier {tier_raw!r}") from exc

    # Workers
    workers = _coerce_workers(data.get("workers", 1))

    # Dataset
    dataset = data.get("dataset")
    if dataset is not None:
        dataset = str(dataset).strip() or None

    # Dates
    start = _parse_date(data.get("start"))
    end = _parse_date(data.get("end"), end_of_day=True)

    # Base parameters
    base_params = data.get("base_parameters") or data.get("baseParameters") or {}
    if base_params is None:
        base_params = {}
    if not isinstance(base_params, dict):
        raise ValueError("base_parameters must be a mapping")
    base_params = dict(base_params)

    # Trials explicit
    trials_raw = data.get("trials") or []
    if trials_raw is None:
        trials_raw = []
    if not isinstance(trials_raw, list):
        raise ValueError("trials must be a list")
    trials: list[dict[str, Any]] = []
    for idx, t in enumerate(trials_raw):
        if not isinstance(t, dict):
            raise ValueError(f"Trial {idx} must be a mapping")
        # Normalize
        label = str(t.get("label", f"trial_{idx}")).strip() or f"trial_{idx}"
        if not isinstance(label, str):
            label = str(label)
        entry: dict[str, Any] = {"label": label}
        if "parameters" in t and t["parameters"] is not None:
            if not isinstance(t["parameters"], dict):
                raise ValueError(f"Trial {idx} parameters must be mapping")
            entry["parameters"] = dict(t["parameters"])
        if "cost_multiplier" in t and t["cost_multiplier"] is not None:
            try:
                entry["cost_multiplier"] = float(t["cost_multiplier"])
            except Exception as exc:
                raise ValueError(f"Trial {idx} cost_multiplier invalid") from exc
        if "execution_overrides" in t and t["execution_overrides"] is not None:
            if not isinstance(t["execution_overrides"], dict):
                raise ValueError(f"Trial {idx} execution_overrides must be mapping")
            entry["execution_overrides"] = dict(t["execution_overrides"])
        if "metadata" in t and t["metadata"] is not None:
            if not isinstance(t["metadata"], dict):
                raise ValueError(f"Trial {idx} metadata must be mapping")
            entry["metadata"] = dict(t["metadata"])
        trials.append(entry)

    # Grid
    grid_raw = data.get("grid") or {}
    if grid_raw is None:
        grid_raw = {}
    if not isinstance(grid_raw, dict):
        raise ValueError("grid must be a mapping")
    grid: dict[str, list[Any]] = {}
    for k, v in grid_raw.items():
        if not isinstance(v, list):
            raise ValueError(f"grid[{k}] must be a list")
        if not v:
            raise ValueError(f"grid[{k}] must be non-empty")
        grid[str(k)] = list(v)

    # Baseline
    baseline = data.get("baseline")
    if baseline is not None:
        baseline = str(baseline).strip() or None

    # Tags
    tags_raw = data.get("tags") or []
    if tags_raw is None:
        tags_raw = []
    if not isinstance(tags_raw, list):
        raise ValueError("tags must be a list")
    tags = [str(x).strip() for x in tags_raw if str(x).strip()]

    # max_trials
    max_trials = data.get("max_trials") or data.get("maxTrials") or DEFAULT_MAX_TRIALS
    try:
        max_trials = int(max_trials)
        if max_trials < 1:
            raise ValueError()
    except Exception as exc:
        raise ValueError("max_trials must be >=1") from exc

    # fail_fast
    fail_fast = bool(data.get("fail_fast", False))

    # initial_cash
    initial_cash = data.get("initial_cash")
    if initial_cash is not None:
        try:
            initial_cash = float(initial_cash)
        except Exception as exc:
            raise ValueError("initial_cash must be a number") from exc

    # corporate_actions
    corporate_actions = data.get("corporate_actions")
    if corporate_actions is not None:
        corporate_actions = str(corporate_actions).strip() or None

    config_path = data.get("config_path")
    if config_path is not None:
        config_path = str(config_path).strip() or None

    output_dir = data.get("output_dir")
    if output_dir is not None:
        output_dir = str(output_dir).strip() or None

    # Validate grid vs trials and explosion guard
    expanded_grid = expand_grid(grid)
    total_trials = len(trials) + len(expanded_grid)
    # If both absent, will be 0, but caller will create default
    if total_trials > max_trials and not allow_large:
        raise ValueError(
            f"Grid expands to {total_trials} trials (explicit {len(trials)} + grid {len(expanded_grid)}) exceeds limit {max_trials}. "
            f"Use --allow-large or increase max_trials."
        )

    return ExperimentSpec(
        name=name,
        market=market,
        strategy=strategy,
        tier=tier,
        workers=workers,
        dataset=dataset,
        start=start,
        end=end,
        base_parameters=base_params,
        trials=trials,
        grid=grid,
        baseline=baseline,
        tags=tags,
        max_trials=max_trials,
        fail_fast=fail_fast,
        initial_cash=initial_cash,
        corporate_actions=corporate_actions,
        config_path=config_path,
        output_dir=output_dir,
        raw=dict(data),
    )


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Deterministic Cartesian expansion.

    Keys sorted for stability; values in given order.
    Returns list of trial dicts with label and parameters.
    Label derived from parameter values for readability.
    """
    if not grid:
        return []
    # Sorted keys for deterministic ordering
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    results: list[dict[str, Any]] = []
    for idx, combo in enumerate(itertools.product(*values)):
        params = {k: v for k, v in zip(keys, combo)}
        # Build label: grid_000__k=v__k=v
        parts = [f"{k}={_param_str(v)}" for k, v in zip(keys, combo)]
        label = f"grid_{idx:03d}__" + "__".join(parts) if parts else f"grid_{idx:03d}"
        # Truncate long labels
        if len(label) > 120:
            label = label[:120]
        results.append({"label": label, "parameters": params})
    return results


def _param_str(v: Any) -> str:
    if isinstance(v, float):
        # Stable float repr without scientific unless needed
        return f"{v:g}"
    return str(v).replace(" ", "_").replace("/", "_")[:30]


def resolved_trials(spec: ExperimentSpec) -> list[dict[str, Any]]:
    """Final resolved trial list: explicit + grid expanded, deterministic."""
    grid_expanded = expand_grid(spec.grid)
    # Explicit first, then grid in deterministic order
    all_trials = list(spec.trials) + grid_expanded
    # If still empty, caller will create single trial; but we return empty here
    return all_trials


def trials_to_research_trials(trial_dicts: list[dict[str, Any]]) -> list[Any]:
    """Convert dict trials to ResearchTrial objects."""
    from parayabuck.research.models import ResearchTrial

    out: list[Any] = []
    for td in trial_dicts:
        out.append(
            ResearchTrial(
                label=str(td.get("label", f"trial_{len(out)}")),
                parameters=td.get("parameters"),
                cost_multiplier=float(td.get("cost_multiplier", 1.0)),
                metadata=dict(td.get("metadata", {})),
                execution_overrides=td.get("execution_overrides"),
            )
        )
    return out
