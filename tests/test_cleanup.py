from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from parayabuck.portfolio import PRODUCT_STRATEGY_NAMES, portfolio_strategy_from

LEGACY_STRATEGY_NAMES = (
    "stock_trend_pullback",
    "stock_mean_reversion",
    "crypto_trend_breakout",
    "crypto_range_reversion",
)

LEGACY_MODULES = (
    "parayabuck.strategies.stock_trend",
    "parayabuck.strategies.stock_mean_reversion",
    "parayabuck.strategies.crypto_trend",
    "parayabuck.strategies.crypto_range",
    "parayabuck.strategies.specs",
    "parayabuck.backtest.sweep",
    "parayabuck.backtest.challengers",
)


def test_legacy_strategies_are_not_registered() -> None:
    registered = set(PRODUCT_STRATEGY_NAMES)
    assert not registered & set(LEGACY_STRATEGY_NAMES)
    for name in LEGACY_STRATEGY_NAMES:
        with pytest.raises(ValueError, match="Unsupported portfolio strategy"):
            portfolio_strategy_from(name)


def test_legacy_strategy_modules_are_gone() -> None:
    for module in LEGACY_MODULES:
        try:
            spec = importlib.util.find_spec(module)
        except ModuleNotFoundError:
            # The parent package itself no longer exists; the module is gone.
            continue
        assert spec is None, f"{module} still importable"


def test_no_source_imports_of_legacy_strategies_remain() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in (*LEGACY_STRATEGY_NAMES, *LEGACY_MODULES)):
            offenders.append(str(path))
    assert offenders == []


def test_active_configs_do_not_reference_legacy_strategies() -> None:
    root = Path(__file__).resolve().parents[1] / "config"
    offenders: list[str] = []
    for path in root.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        if any(name in text for name in LEGACY_STRATEGY_NAMES):
            offenders.append(str(path))
    assert offenders == []


def test_cli_no_longer_exposes_legacy_strategy_options() -> None:
    from parayabuck.cli import _parser

    usage = _parser().format_help()
    assert "--sweep" not in usage
    assert "--experiment-spec" not in usage
    assert "stock_trend_pullback" not in usage


def test_new_strategies_are_registered() -> None:
    registered = set(PRODUCT_STRATEGY_NAMES)
    # The PAPER product surface is equity-only.
    assert registered == {"equity_leveraged_trend_v1"}


@pytest.mark.parametrize(
    "name", ("crypto_low_variance_v1", "crypto_low_variance_v2", "crypto_low_variance_hysteresis_v1")
)
def test_removed_crypto_strategies_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="Unsupported portfolio strategy"):
        portfolio_strategy_from(name)


# Legacy strategy names are allowed ONLY inside migration/audit documentation;
# active docs, README, web and runtime paths must not reference them as live
# features.
_ALLOWED_LEGACY_DOCS: set[str] = set()


def _scan_paths(root: Path, extensions: tuple[str, ...], skip: set[str]) -> list[str]:
    skip_names = {Path(item).name for item in skip}
    offenders: list[str] = []
    for path in root.rglob("*"):
        if path.is_dir() or path.suffix not in extensions:
            continue
        if path.name in skip_names:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(name in text for name in (*LEGACY_STRATEGY_NAMES,)):
            offenders.append(str(path.relative_to(root)))
    return offenders


def test_active_docs_and_web_do_not_reference_legacy_strategies() -> None:
    repo = Path(__file__).resolve().parents[1]
    skip = set(_ALLOWED_LEGACY_DOCS)
    offenders = _scan_paths(repo / "docs", (".md",), skip=skip)
    assert offenders == []
    web_offenders = _scan_paths(repo / "src" / "parayabuck" / "web", (".html", ".js"), skip=set())
    assert web_offenders == []


def test_readme_mentions_legacy_strategies_only_in_migration_section() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    lines = readme.splitlines()
    allowed_section = False
    for index, line in enumerate(lines):
        if line.startswith("## "):
            heading = line.lower()
            allowed_section = "migration" in heading or "audit" in heading
        if any(name in line for name in LEGACY_STRATEGY_NAMES):
            assert allowed_section, f"legacy name outside migration/audit section: {line}"


def test_web_backtest_payload_no_longer_carries_sweep_flags() -> None:
    from parayabuck.web.server import BacktestPayload

    fields = set(BacktestPayload.model_fields)
    assert "sweep" not in fields
    assert "walk_forward" not in fields
    assert "walk_forward_optimize" not in fields


def test_runtime_reports_operational_strategy_status() -> None:
    from parayabuck.runtime import RuntimeHealth

    assert RuntimeHealth.strategy_status == "configured"
