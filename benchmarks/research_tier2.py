"""TIER 2 isolated economic research — rebalance band + minimum order notional.

Research-only: varies config.execution knobs in the production backtest engine.
No production code changes. Alpha, allocations, risk are untouched.

Candidate sets (predeclared, max 3 values incl. control):
- rebalance band:  0.005 (control) | 0.0075 | 0.01
- min order notional: 10 (control) | 25 | 50
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parayabuck.backtest.product import load_csv_bars, run_product_backtest
from parayabuck.config import Config, load_config
from parayabuck.core.models import Market

ROOT = Path(__file__).resolve().parents[1]
EQUITY_CSV = ROOT / "data" / "historical" / "equity_1200_2016_2026.csv"
CRYPTO_CSV = ROOT / "data" / "historical" / "crypto_18_1d_extended.csv"

EQUITY_START = "2019-01-01"
EQUITY_END = "2021-12-31"

BANDS = (0.005, 0.0075, 0.01)
MIN_NOTIONALS = (10.0, 25.0, 50.0)


def _metrics(run) -> dict[str, object]:
    result = run.result
    summary = run.report.summary
    fills = result.fills
    buys = sum(1 for fill in fills if fill.side.value == "buy")
    sells = sum(1 for fill in fills if fill.side.value == "sell")
    return {
        "net_return": summary["net_return"],
        "cagr": summary["cagr"],
        "sharpe": summary["sharpe"],
        "max_drawdown": summary["max_drawdown"],
        "turnover": summary["turnover"],
        "fee_total": summary["fee_total"],
        "slippage_cost_total": summary.get("slippage_cost_total"),
        "spread_cost_total": summary.get("spread_cost_total"),
        "fill_count": len(fills),
        "buys": buys,
        "sells": sells,
        "rebalance_count": summary["rebalance_count"],
        "average_holdings_count": summary["average_holdings_count"],
        "average_cash_ratio": summary["average_cash_ratio"],
    }


def _run(config: Config, market: Market, bars: list, label: str) -> dict[str, object]:
    kwargs = dict(
        market=market,
        output_dir=str(ROOT / "data" / "smoke" / "tier2" / label),
        persist_artifacts=False,
    )
    if market == Market.EQUITY:
        kwargs["strategy_name"] = "equity_residual_momentum_v2"
        kwargs["start"] = datetime.fromisoformat(EQUITY_START).replace(tzinfo=UTC)
        kwargs["end"] = datetime.fromisoformat(EQUITY_END).replace(tzinfo=UTC)
    else:
        kwargs["strategy_name"] = "crypto_low_variance_v2"
    run = run_product_backtest(config, bars, **kwargs)
    return _metrics(run)


def main() -> int:
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    equity_bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    crypto_bars = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)

    results: dict[str, object] = {"rebalance_band": {}, "minimum_order_notional": {}}
    for band in BANDS:
        cfg = replace(
            config, execution=replace(config.execution, rebalance_band_pct=band)
        )
        label = f"band_{band:.4f}".replace(".", "p")
        results["rebalance_band"][str(band)] = {
            "equity": _run(cfg, Market.EQUITY, equity_bars, f"eq_{label}"),
            "crypto": _run(cfg, Market.CRYPTO, crypto_bars, f"cr_{label}"),
        }
        print(f"band {band}: equity+crypto done", file=sys.stderr)
    for notional in MIN_NOTIONALS:
        cfg = replace(
            config, execution=replace(config.execution, minimum_order_notional=notional)
        )
        label = f"min_{notional:.0f}"
        results["minimum_order_notional"][str(notional)] = {
            "equity": _run(cfg, Market.EQUITY, equity_bars, f"eq_{label}"),
            "crypto": _run(cfg, Market.CRYPTO, crypto_bars, f"cr_{label}"),
        }
        print(f"min notional {notional}: equity+crypto done", file=sys.stderr)

    out = ROOT / "data" / "smoke" / "tier2_research.json"
    out.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
