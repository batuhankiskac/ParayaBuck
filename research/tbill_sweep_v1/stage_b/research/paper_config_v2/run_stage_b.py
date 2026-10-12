#!/usr/bin/env python3
"""tbill_sweep_v1 copy of the e2_integration_v1 Stage B runner (E2 vs E2 + T-bill sweep).

Changes from the e2_integration_v1 copy (tag archive/research/e2-integration-v1-2026-10),
none touching the execution model or production code:

* ``--sweep {none,s1,s2_5,s2_10}`` wraps ``strategy.decide`` in a research-only
  shim (``_sweep_shim``) that adds BIL to the E2 target: 0.98 while the E2
  trend is down (S1 and S2); for S2 also the residual while the trend is up,
  kept inside ``[0.98 - w, 0.98 - w]`` widened down by X and reset to
  ``max(0, 0.98 - w - X/2)`` on breach (w = min(QLD weight, 0.98));
* BIL bars and dividends come from ``data/historical/tbill_sweep_v1_etf_*``
  (the rule_variants_v1 Alpaca SIP split-adjusted download); the profit_max
  campaign file is only read when present;
* the payload records the sweep mode.

Previous docstring follows.

e2_integration_v1 copy of the paper_config_v2 Stage B runner (VT35 vs E2 on the production engine).

Changes from the archived runner (tag archive/research/signal-ensemble-v1-2026-10,
research/paper_config_v2/run_stage_b.py), none touching the execution model:

* the canonical v5 equity bars/actions (882 MB, gitignored, not in any tag)
  are replaced by ``data/historical/substitute_v5_{bars,actions}_qqq_spy_qld.csv``:
  the QQQ/SPY/QLD rows of the profit_max_v1 campaign file (same Alpaca SIP
  split-adjusted source; VT35's universe reads only these three symbols);
* the decide() audit log records the strategy version and E2's votes;
* the payload records the strategy version.

Original docstring follows.

ARCHIVE-ONLY -- not runnable from this checkout. Stage B shared-ledger replay for paper_config_v2 (S0/S1/S2 arms).

``main()`` refuses to start and prints what is missing. This file is kept as
the record of how the campaign was produced; the committed evidence is
``RESULTS.json`` and ``PREREGISTRATION.md``.

Why it cannot run here
----------------------
Missing modules (deleted from ``research/``; restore from tag
``archive/research/sleeve-candidates-v2-v3-2026-09``):

* ``research/physical_availability_clock_v2/{run_historical,shared_replay,timing}.py``
* ``research/sleeve_candidates_v2/stage_b/{composite,run_stage_b}.py``

Missing data (gitignored, never committed, in no tag -- Alpaca SIP downloads
that need network access and credentials; re-fetch with the archive tag's
``research/sleeve_candidates_v2/download_data.py``):

* ``data/historical/equity_pit_symmetric_2016_2026_v5.csv`` -- 882,354,549
  bytes, sha256 ``bb7aaae8...`` (see
  ``data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json``)
* ``data/historical/equity_survivorship_full_corporate_actions_v5.csv``

Missing data (in tag ``archive/research/profit-max-v1-2026-09``):

* ``data/historical/profit_max_v1_daily_split_sip.csv`` (QLD campaign bars,
  split-adjusted, dividends restated to post-split shares)
* ``data/historical/profit_max_v1_corporate_actions.csv``

The S0 arm additionally needs the crypto code and data deleted in 2026-09; it
is not reproducible at all from current main. The BIL inputs are committed
under ``research/rule_variants_v1/data/`` and must be copied to
``data/historical/`` as ``rule_variants_v1_daily_split_sip.csv`` and
``rule_variants_v1_corporate_actions.csv``.

To reproduce
------------
Scratch root holding this checkout's ``src`` and ``config`` plus the restored
files above (~95 s, ~6 GB RAM):

    .venv/bin/python research/paper_config_v2/run_stage_b.py \
        --config <abs>/research/paper_config_v2/configs/S1.yaml \
        --cost 1.0 --out <dir>/S1_1.0.json.gz
    .venv/bin/python research/paper_config_v2/evaluate.py --runs <dir>

The arm configs carry the crypto keys that ``config/settings.yaml`` no longer
accepts, so S0 and S2 need ``archive/code/crypto-sleeve-2026-09`` as well.

Provenance of the committed numbers
-----------------------------------
``RESULTS.json`` (written by ``evaluate.py`` from run dumps that are not
committed) records arm S0 at 1x ending NAV 1,141,851.32 with 1,290 fills and
28.98% CAGR. That is the pre-crypto-removal audited run: crypto sleeve on,
equity allocation 0.93. It is **not** the current reference.

The current reference is arm S1 at 1x -- equity-only VT35 (hold QLD while QQQ
completed close > SMA200, vol target 0.35, equity allocation 0.98), ending NAV
1,173,537.61 with 615 fills and 29.35% CAGR. It was adopted as the paper
config and re-reproduced unchanged after the crypto code, config keys and
tests were deleted (``docs/research/crypto_removal_v1.md``). Arm S2 (BIL
sweep, 1,185,460.98 at 1x) was measured but not adopted.

Derivation from ``research/execution_model_audit_v1/run_stage_b_audit.py``
---------------------------------------------------------------------
Three changes, none of which touches the execution model:

* a config with ``strategy.crypto.enabled: false`` runs the equity market alone
  (the crypto replay input is not built or added to the shared ledger);
* equity symbols missing from both canonical v5 and the profit-max campaign
  file (BIL) come from ``data/historical/rule_variants_v1_*`` (copies of
  ``research/rule_variants_v1/data/``: split-restated SIP daily bars and
  dividends restated to post-split shares; BIL's 2017-11-30 1:2 reverse split
  is applied in the bars, no split action is replayed);
* the payload records traded notional per symbol (turnover).

Arm configs: ``research/paper_config_v2/configs/S{0,1,2}.yaml``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from parayabuck.backtest.lifetimes import load_entity_lifetimes  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.portfolio import reserve_opening_order_slots  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from  # noqa: E402

ARCHIVE_ONLY = """\
ARCHIVE-ONLY harness -- it cannot run from this checkout.

Missing modules, restore from tag archive/research/sleeve-candidates-v2-v3-2026-09:
  research/physical_availability_clock_v2/{run_historical,shared_replay,timing}.py
  research/sleeve_candidates_v2/stage_b/{composite,run_stage_b}.py
Missing data, gitignored and in no tag (Alpaca SIP; network + credentials):
  data/historical/equity_pit_symmetric_2016_2026_v5.csv (sha256 bb7aaae8...)
  data/historical/equity_survivorship_full_corporate_actions_v5.csv
Missing data, in tag archive/research/profit-max-v1-2026-09:
  data/historical/profit_max_v1_{daily_split_sip,corporate_actions}.csv
The S0 arm also needs the deleted crypto code and data (tag
archive/code/crypto-sleeve-2026-09). See the module docstring for the commands.
"""


def _load_archive_modules() -> None:
    """Bind the archived harness modules, or refuse loudly without them."""
    global rh, shared, sleeve, stage_b
    global equity_action_effective_open, equity_daily_available_at, equity_session_open
    try:
        from research.physical_availability_clock_v2 import run_historical as rh
        from research.physical_availability_clock_v2 import shared_replay as shared
        from research.physical_availability_clock_v2.timing import (
            equity_action_effective_open,
            equity_daily_available_at,
            equity_session_open,
        )
        from research.sleeve_candidates_v2.stage_b import composite as sleeve
        from research.sleeve_candidates_v2.stage_b import run_stage_b as stage_b
    except ModuleNotFoundError as exc:
        raise SystemExit(f"{ARCHIVE_ONLY}\nnot importable: {exc}") from exc


def build_numeric_history(*a, **k):  # removed from main in da6190b; engine no longer reads it
    return None


# Campaign ETF file: split-adjusted bars with dividend rates restated to
# post-split shares (see download_data.py).
SLEEVE_BARS = ROOT / "data/historical/profit_max_v1_daily_split_sip.csv"
SLEEVE_ACTIONS = ROOT / "data/historical/profit_max_v1_corporate_actions.csv"
EXTRA_BARS = ROOT / "data/historical/tbill_sweep_v1_etf_daily_split_sip.csv"
EXTRA_ACTIONS = ROOT / "data/historical/tbill_sweep_v1_etf_corporate_actions.csv"
SWEEP_SYMBOL = "BIL"
SWEEP_BANDS = {"s2_5": 0.05, "s2_10": 0.10}


def _sweep_shim(decide, mode: str, cap: float):  # type: ignore[no-untyped-def]
    """Research-only T-bill sweep around the unchanged E2 decide()."""
    import dataclasses

    from parayabuck.portfolio.models import RebalanceReason, TargetWeight

    def shim(context):  # type: ignore[no-untyped-def]
        target = decide(context)
        state = target.metadata.get("trend_state")
        if state not in ("uptrend", "downtrend"):
            return target
        if not context.history.get(SWEEP_SYMBOL) or not context.in_universe(SWEEP_SYMBOL):
            return target
        hold = sum(item.weight for item in target.weights if item.symbol != SWEEP_SYMBOL)
        w = min(hold, cap)
        price = context.prices.get(SWEEP_SYMBOL)
        current = context.holdings.get(SWEEP_SYMBOL, 0.0) * price / context.equity if price else 0.0
        if state == "downtrend":
            bills = cap
        elif mode == "s1":
            bills = 0.0
        else:
            x = SWEEP_BANDS[mode]
            hi = cap - w
            bills = current if hi - x <= current <= hi + 1e-12 else max(0.0, hi - x / 2)
        if bills <= 0:
            return target
        weights = (*target.weights, TargetWeight(SWEEP_SYMBOL, bills, reason="tbill_sweep", rank=2))
        return dataclasses.replace(
            target,
            weights=weights,
            cash_weight=max(0.0, 1.0 - sum(item.weight for item in weights)),
            rebalance_reason=RebalanceReason.SCHEDULED,
            metadata={**target.metadata, "tbill_sweep": {"mode": mode, "bills": bills, "current": current}},
        )

    return shim


def _admission(exempt: tuple[str, ...]):  # type: ignore[no-untyped-def]
    def admit(orders, account, maximum_positions, reserved_position_keys):  # type: ignore[no-untyped-def]
        requests = tuple(
            shared.PortfolioOrderRequest(
                client_order_id=order.client_order_id,
                decision_id=order.client_order_id,
                symbol=order.symbol,
                market=order.market,
                side=shared.PortfolioOrderSide(order.side.value),
                quantity=order.qty,
                reference_price=0.0,
                decision_timestamp=order.decision_timestamp,
                strategy_name="historical-replay",
                strategy_version="2",
            )
            for order in orders
        )
        admitted = reserve_opening_order_slots(
            requests,
            account,
            maximum_positions=maximum_positions,
            reserved_position_keys=reserved_position_keys,
        )
        admitted_ids = {order.client_order_id for order in admitted}
        return tuple(order for order in orders if order.client_order_id in admitted_ids)

    return admit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--cost", required=True, type=float, choices=(1.0, 2.0, 3.0))
    parser.add_argument("--out", required=True)
    parser.add_argument("--sweep", default="none", choices=("none", "s1", *SWEEP_BANDS))
    args = parser.parse_args()
    _load_archive_modules()
    rh.EQUITY_BARS = ROOT / "data/historical/substitute_v5_bars_qqq_spy_qld.csv"
    rh.EQUITY_ACTIONS = ROOT / "data/historical/substitute_v5_actions_qqq_spy_qld.csv"
    started = time.monotonic()
    config = load_config(args.config)
    if config.risk.max_positions != 8:
        raise ValueError("Validation freezes the shipped cap of 8")
    sleeve_symbols = ()
    sleeve.configure(sleeve_symbols, cash_equivalent=False)  # reporting only (is_etf_key)
    build = stage_b._engine_factory(args.cost)
    replay = shared.SharedAccountReplay(
        starting_nav=config.backtest.initial_cash,
        maximum_positions=config.risk.max_positions,
        allow_test_strategies=True,  # product identities outside the frozen migration arms
    )
    crypto_input = None
    # Crypto support was removed after this study (docs/research/crypto_removal_v1.md).
    # Current code runs the adopted equity-only config (config/settings.yaml,
    # equivalent to S1); the S0/S1/S2 arm configs carry crypto keys and, like
    # the crypto branch below, need tag archive/code/crypto-sleeve-2026-09.
    if getattr(config.strategy, "crypto", None) is not None and config.strategy.crypto.enabled:
        with patch.object(rh, "_engine", build):
            crypto_input, _crypto_identity = rh._crypto_input(config, replay)

    bars = load_csv_bars(rh.EQUITY_BARS, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(rh.EQUITY_LIFETIMES)
    actions = load_csv_corporate_actions(rh.EQUITY_ACTIONS)
    needed = set(sleeve_symbols) | set(config.universe.equity.symbols)
    if needed - {bar.symbol for bar in bars}:
        # ETFs absent from canonical v5 (SCHD/GLD/OUNZ, VOO, QLD, TQQQ) come from
        # the campaign file; symbols present in v5 keep their v5 bars.
        missing = needed - {bar.symbol for bar in bars}
        if SLEEVE_BARS.exists():
            bars = bars + [
                bar for bar in load_csv_bars(SLEEVE_BARS, default_market=Market.EQUITY) if bar.symbol in missing
            ]
            actions = actions + [
                action for action in load_csv_corporate_actions(SLEEVE_ACTIONS) if action.symbol in missing
            ]
        missing = needed - {bar.symbol for bar in bars}
        if missing:
            bars = bars + [
                bar for bar in load_csv_bars(EXTRA_BARS, default_market=Market.EQUITY) if bar.symbol in missing
            ]
            actions = actions + [
                action for action in load_csv_corporate_actions(EXTRA_ACTIONS) if action.symbol in missing
            ]
        if needed - {bar.symbol for bar in bars}:
            raise ValueError(f"No bars for {sorted(needed - {bar.symbol for bar in bars})}")
    selection = config.strategy.equity
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    if args.sweep != "none":
        strategy.decide = _sweep_shim(strategy.decide, args.sweep, selection.maximum_allocation)  # type: ignore[method-assign]
    _decide_log: list[dict] = []
    _orig_decide = strategy.decide

    def _audit_decide(context):  # type: ignore[no-untyped-def]
        target = _orig_decide(context)
        md = dict(target.metadata)
        _decide_log.append(
            {
                "label": context.decision_timestamp.isoformat(),
                "weights": [[w.symbol, w.weight] for w in target.weights],
                "trend_state": md.get("trend_state"),
                "trend_signals": md.get("trend_signals"),
                "version": target.strategy_version,
                "signal_close": md.get("signal_close"),
                "signal_sma": md.get("signal_sma"),
                "vol": md.get("realized_volatility"),
                "equity": context.equity,
                "qld_price": context.prices.get("QLD"),
                "observed": len(context.history),
            }
        )
        return target

    strategy.decide = _audit_decide  # type: ignore[method-assign]
    import logging

    _skips: list[str] = []

    class _SkipHandler(logging.Handler):
        def emit(self, record):  # type: ignore[no-untyped-def]
            msg = record.getMessage()
            if "incomplete" in msg:
                _skips.append(msg)

    logging.getLogger().addHandler(_SkipHandler())
    for _name in ("parayabuck", "parayabuck.backtest", "parayabuck.backtest.portfolio_engine"):
        logging.getLogger(_name).addHandler(_SkipHandler())
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=rh.EQUITY_END,
        timeframe=strategy.timeframe,
    )
    filtered, warmup, (ordered, decision_bars) = _prepared_window(
        bars, Market.EQUITY, strategy.timeframe, rh.EQUITY_START, rh.EQUITY_END
    )
    engine = build(
        replay,
        config,
        Market.EQUITY,
        universe,
        hash_point_in_time_universe(universe),
        strategy=strategy,
        terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
    )
    engine._numeric_history = build_numeric_history(
        warmup + ordered, market=Market.EQUITY, timeframe=strategy.timeframe
    )
    del filtered
    labels = {bar.timestamp for bar in ordered}
    equity_input = shared.MarketReplayInput(
        engine,
        ordered,
        warmup,
        decision_bars,
        actions,
        {label: equity_daily_available_at(label.date()) for label in labels},
        {action.timestamp: equity_action_effective_open(action.timestamp.date()) for action in actions},
        final_close_at=max(equity_daily_available_at(label.date()) for label in labels),
        execution_schedule={label: equity_session_open(label.date()) for label in labels},
    )
    closes = sorted({equity_daily_available_at(label.date()) for label in labels})
    if crypto_input is not None:
        replay.add_market(crypto_input)
    replay.add_market(equity_input)

    admission = patch.object(shared, "_admit_synthetic_orders", _admission(()))
    admission.start()
    try:
        result = replay.run(liquidate_at_end=True)
    finally:
        admission.stop()

    stream_sizes = sorted({len(snapshot.observation_set) for snapshot in universe.snapshots})
    pinned_sets = sorted({tuple(snapshot.pinned_symbols) for snapshot in universe.snapshots})
    violations = [event for event in result.events if event["event"] == "slot_claim_violation"]
    max_non_etf_claims = 0
    for event in violations:
        claims = {(row[0], row[1]) for row in event["positions"]} | {
            (row[0], row[1]) for row in event["pending_orders"]
        }
        non_etf = {key for key in claims if not sleeve.is_etf_key((Market(key[0]), key[1]))}
        max_non_etf_claims = max(max_non_etf_claims, len(non_etf))
    fills = [event for event in result.events if event["event"] == "fill"]
    decisions = [event for event in result.events if event["event"] == "decision"]
    sleeve_fills = [event for event in fills if event.get("symbol") in sleeve_symbols]
    payload = {
        "arm": "P" if sleeve_symbols else "A",
        "config": args.config,
        "sweep": args.sweep,
        "strategy": [strategy.name, strategy.version],
        "cost_multiplier": args.cost,
        "config_sha256": rh._sha256_file(Path(args.config)),
        "elapsed_seconds": time.monotonic() - started,
        "start_nav": result.start_nav,
        "ending_nav": result.ending_nav,
        "session_nav": stage_b._session_nav(result, closes),
        "invariants": rh._plain(result.invariants),
        "structural": {
            "slot_claim_violation_events": len(violations),
            "max_non_etf_slot_claims_in_violations": max_non_etf_claims,
            "observation_stream_sizes": stream_sizes,
            "pinned_symbol_sets": [list(item) for item in pinned_sets[:3]],
            "sleeve_fill_count": len(sleeve_fills),
            "sleeve_fill_months": len({event["timestamp"][:7] for event in sleeve_fills if "timestamp" in event}),
        },
        "hashes": {
            "decisions": rh._digest(decisions),
            "fills_without_client_ids": rh._digest(
                [{key: value for key, value in event.items() if key != "client_order_id"} for event in fills]
            ),
            "equity_decisions": rh._digest([event for event in decisions if event["market"] == "equity"]),
        },
        "counts": {"decisions": len(decisions), "fills": len(fills)},
        "crypto_enabled": crypto_input is not None,
        "traded_notional": {
            symbol: sum(abs(float(e["qty"])) * float(e["fill_price"]) for e in fills if e.get("symbol") == symbol)
            for symbol in sorted({str(e.get("symbol")) for e in fills})
        },
        "etf_pnl": stage_b._etf_pnl(result),
        "costs": {
            "fees": sum(float(event["fee"]) for event in fills),
            "slippage": sum(float(event["slippage_cost"]) for event in fills),
            "spread": sum(float(event["spread_cost"]) for event in fills),
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    audit = {
        "decide_log": _decide_log,
        "skips": _skips,
        "equity_events": [
            e
            for e in result.events
            if e.get("market") == "equity"
            and e["event"] in ("decision", "fill", "final_liquidation", "corporate_action")
        ],
        "deferred": [e for e in result.events if e["event"] == "buy_fill_deferred"][:2000],
    }
    with gzip.open(args.out.replace(".json.gz", "_audit.json.gz"), "wt", encoding="utf-8") as handle:
        json.dump(audit, handle, sort_keys=True, default=str)
    print(
        f"{payload['arm']} {args.cost}x: ending_nav={result.ending_nav:.2f} fills={len(fills)} "
        f"sleeve_fills={len(sleeve_fills)} violations={len(violations)} streams={stream_sizes} "
        f"elapsed={payload['elapsed_seconds']:.0f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
