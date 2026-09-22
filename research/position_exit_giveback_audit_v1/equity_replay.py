"""Fresh unchanged V3 1x control with observational event capture."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from research.month_end_first_open_correctness_v1 import replay_v3_parity as base
from research.position_exit_giveback_audit_v1.capture import capture_run, read_json, write_json

OUT = Path(__file__).resolve().parent


def main() -> None:
    config = base.load_config(base.CONFIG_PATH)
    print("loading canonical V5 bars", flush=True)
    bars = base.load_csv_bars(base.BARS_PATH, default_market=base.Market.EQUITY)
    lifetimes = base.load_entity_lifetimes(base.LIFETIMES_PATH)
    actions = base.load_csv_corporate_actions(base.CORPORATE_ACTIONS_PATH)
    selection = config.strategy.equity
    strategy = base.portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    universe = base.build_product_universe(
        config,
        bars,
        market=base.Market.EQUITY,
        end=base.END,
        timeframe=strategy.timeframe,
        observation_strategy=strategy,
    )
    prepared = base._prepared_window(bars, base.Market.EQUITY, strategy.timeframe, base.START, base.END)
    _, warmup, (ordered, _) = prepared
    numeric = base.build_numeric_history(warmup + ordered, market=base.Market.EQUITY, timeframe=strategy.timeframe)
    print("running canonical V3", flush=True)
    product, raw = capture_run(
        lambda: base.run_product_backtest(
            config,
            bars,
            market=base.Market.EQUITY,
            strategy_name=strategy.name,
            parameters=dict(selection.parameters),
            strategy_timeframe=strategy.timeframe,
            start=base.START,
            end=base.END,
            cost_multiplier=1.0,
            persist=False,
            persist_artifacts=False,
            corporate_actions=actions,
            universe=universe,
            prepared_inputs=prepared,
            numeric_history=numeric,
            terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
        )
    )
    current = base._run_receipt(1.0, product)
    ref = read_json(ROOT / "research/month_end_first_open_correctness_v1/V3_POST_FIX_REPLAY.json")["parity"]["runs"][
        "1.0"
    ]
    differences: list[dict[str, object]] = []
    base._compare_numbers(ref, current, "run", differences)
    raw.update(
        strategy=strategy.name,
        period={
            "start": base.START.isoformat(),
            "end": base.END.isoformat(),
            "development_end": base.DEVELOPMENT_END.isoformat(),
            "validation_start": base.VALIDATION_START.isoformat(),
        },
        entities=[
            base._jsonable(
                {
                    "symbol": r.canonical_symbol,
                    "entity_id": r.entity_id,
                    "valid_from": r.valid_from,
                    "valid_to": r.valid_to,
                }
            )
            for r in lifetimes.records
        ],
    )
    write_json(OUT / "EQUITY_RAW.json.gz", raw)
    write_json(
        OUT / "EQUITY_REPLAY_PARITY.json",
        {
            "status": "PASS" if not differences else "AUDIT_INCONCLUSIVE_REPLAY_MISMATCH",
            "differences": differences,
            "reference": "research/month_end_first_open_correctness_v1/V3_POST_FIX_REPLAY.json",
            "current": current,
            "input_hashes": {
                str(p.relative_to(ROOT)): base._file_sha256(p)
                for p in (base.BARS_PATH, base.LIFETIMES_PATH, base.CORPORATE_ACTIONS_PATH, base.CONFIG_PATH)
            },
        },
    )
    print(f"replay complete differences={len(differences)}", flush=True)


if __name__ == "__main__":
    main()
