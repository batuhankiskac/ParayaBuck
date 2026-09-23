"""Reproduce V3 observed lifecycle diagnostics from the pinned causal replay."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from episodes import reconstruct, summarize
from verify import verify

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = ROOT / "maintenance/crypto_canonical_causal_execution_v1"
RAW = BASE / "CORRECTED_REPLAY_A.json.gz"
EXPECTED = {
    "config": "414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353",
    "source": "73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e",
    "daily": "92f9b745eb925e9994c184b712ac500e82be973be38839b8bd93aab7a60d54f9",
    "universe": "e3692166c51a1e84aba7662e3c6156cdc1961989df8f46564caff70d898529c6",
    "decisions": "e2919b031944c1a08a8fbecc4019007c21c34218840d3c1ffa10feb420f7f58a",
    "orders": "6d90738ef71a49b02e71532f732763ed837f9686cf595779416ca049b23012ab",
    "price_events": "ff858d0fe3019731daa7436b406f99fa63a0c60a8f15c877263acb8f1fdb6242",
    "curve_timestamps": "80ee49be42c294a2c6a6836a27416b2b285341ec0f87364f389596e85d6567e1",
    "equity_curve": "e2a89843121f43fe3fe642dc1c3140761efccca246e0bef6e900322908a069c3",
    "fills": "645d444ff3c95ba2f5eab1f0162acc903275e4642c70c79eb03b8248829e1fba",
    "snapshots": "6a71d86a6f6ac35d18ebf5a47de615cffe4eb45e43c03b8b69d3a389e3087c13",
    "final_state": "7689e62a2e80a0452e42f7359c9722d49e25b21cfe38632901bb29bd953ad5ef",
    "terminal_events": "7ad9de1ae9bc69eca5794436e0e45868066a49960de3de8d9d86183192d51fba",
    "payload": "cdc3004dbbfe1e13e8f9f9deb5ec80ecbe628236210649a68a8f47aa55f33697",
    "gzip": "8ec31d0aa7fd350bf011c5a00ac25cd64fb9be20240a001496bb6e8353792cae",
}


def sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def write(name: str, value: Any) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def main() -> None:
    compressed = RAW.read_bytes()
    payload = gzip.decompress(compressed)
    raw = json.loads(payload)
    actual = {
        "config": hashlib.sha256((ROOT / "config/settings.yaml").read_bytes()).hexdigest(),
        "source": hashlib.sha256((ROOT / "data/historical/crypto_expanded_1h.csv").read_bytes()).hexdigest(),
        "daily": sha(raw["daily_bars"]),
        "universe": raw["universe_sha256"],
        "decisions": sha(raw["decisions"]),
        "orders": sha(raw["orders"]),
        "price_events": sha(raw["price_events"]),
        "curve_timestamps": sha(raw["curve_timestamps"]),
        "equity_curve": sha(raw["equity_curve"]),
        "fills": sha(raw["fills"]),
        "snapshots": sha(raw["snapshots"]),
        "final_state": sha(raw["final_state"]),
        "terminal_events": sha(raw["terminal_events"]),
        "payload": hashlib.sha256(payload).hexdigest(),
        "gzip": hashlib.sha256(compressed).hexdigest(),
    }
    if raw["config_sha256"] != actual["config"]:
        raise AssertionError("replay config hash mismatch")
    if actual != EXPECTED:
        raise AssertionError({"actual": actual, "expected": EXPECTED})
    if (
        raw["counts"]["decisions"] != 454
        or raw["counts"]["fills"] != 406
        or len(raw["decisions"]) != 454
        or len(raw["fills"]) != 406
    ):
        raise AssertionError("headline counts")
    if raw["final_state"]["holdings"] or raw["final_state"]["pending_orders"]:
        raise AssertionError("nonflat final state")
    receipt = {
        "status": "PASS",
        "authority_commit": "b135d73743ec2652f79b8c840df50bd3f67166e8",
        "decision": "CRYPTO_CANONICAL_BASELINE_CAUSAL_REPAIR_ACCEPTED",
        "replay": "maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz",
        "hashes": actual,
        "counts": raw["counts"],
        "strategy": raw["final_state"]["strategy_name"],
        "parameters": {
            "volatility_lookback_days": 22,
            "top_n": 2,
            "held_rank_retention": 4,
            "decision_cadence_hours": 96,
            "sleeve_fraction": 0.05,
            "rebalance_band": 0.005,
            "volume_lookback_positive_days": 20,
            "participation": 0.10,
            "slippage_bps": 2,
            "full_spread_bps": 2,
            "crypto_taker_fee_bps": 25,
            "fixed_commission": 0,
        },
        "execution_contract": "complete daily UTC bar available next midnight; physical decision and order at availability; first observed same-symbol hourly open strictly after order creation; one participation-limited attempt per UTC day; common terminal open 2026-07-31T00:00:00Z",
        "metrics": raw["metrics"],
        "terminal_at": raw["terminal_at"],
        "terminal_events": len(raw["terminal_events"]),
        "final_holdings": raw["final_state"]["holdings"],
        "final_pending_orders": raw["final_state"]["pending_orders"],
        "final_cash": raw["final_state"]["cash"],
    }
    write("CAUSAL_BASELINE_RECEIPT.json", receipt)
    d = reconstruct(raw)
    eps = d["episodes"]
    natural = [e for e in eps if not e["censored"]]
    open_eps = [e for e in eps if e["censored"]]
    decisions = {v["label_time"]: v for v in raw["decisions"]}
    for e in eps:
        if e["censored"]:
            e["natural_exit"] = {
                "state": "ENDPOINT_OPEN_CENSORED",
                "reason": "PRECOMMITTED_TERMINAL_LIQUIDATION",
                "source_decision": None,
            }
        else:
            sales = [f for f in e["fills"] if f["side"] == "sell" and f.get("type") != "terminal"]
            source = sales[-1]["decision_timestamp"]
            decision = decisions[source]
            symbol = e["symbol"]
            ranked = [s for _, s in decision["scores"]]
            selected = decision["alpha_target"]["metadata"].get("selected_symbols", [])
            weight = sum(w["weight"] for w in decision["target"]["weights"] if w["symbol"] == symbol)
            reason = (
                "SCANNER_INELIGIBLE"
                if symbol not in decision["eligible_symbols"]
                else "QUALIFIED_HISTORY_LOSS"
                if symbol not in ranked
                else "RANK_ABOVE_RETENTION"
                if ranked.index(symbol) >= 4
                else "RETENTION_CAPACITY_DISPLACEMENT"
                if symbol not in selected
                else "RISK_OVERLAY_ZERO_TARGET"
                if weight <= 0
                else "SIZE_REBALANCE_TO_FLAT"
            )
            e["natural_exit"] = {
                "state": "NATURAL_STRATEGY_FLAT",
                "reason": reason,
                "source_decision": source,
                "last_sale_timestamp": sales[-1]["timestamp"],
                "rank_at_source_decision": ranked.index(symbol) + 1 if symbol in ranked else None,
                "target_weight_at_source_decision": weight,
            }
    verification = verify(raw, d)
    write("INDEPENDENT_EPISODE_VERIFICATION.json", verification)
    if (
        verification["status"] != "PASS"
        or abs(d["reconciliation"]["final_residual"]) > 1e-7
        or abs(d["reconciliation"]["maximum_daily_nav_residual"]) > 1e-7
    ):
        raise AssertionError("independent verification or NAV reconciliation failed")
    write("EPISODE_RECONSTRUCTION.json", d)
    sn = summarize(natural, net_strategy_pnl=d["reconciliation"]["account_profit"])
    sa = summarize(eps, net_strategy_pnl=d["reconciliation"]["account_profit"])
    sc = summarize(open_eps, net_strategy_pnl=d["reconciliation"]["account_profit"])
    cohort = [e for e in natural if e["mfe_return"] >= 0.30]
    diagnostics = {
        "natural": sn,
        "all": sa,
        "endpoint_open": sc,
        "naturally_closed_count": len(natural),
        "endpoint_open_count": len(open_eps),
        "all_final_positive_count": sum(e["final_pnl"] > 0 for e in eps),
        "natural_final_positive_count": sum(e["final_pnl"] > 0 for e in natural),
        "natural_positive_mfe_to_negative_final": [
            {
                "episode_id": e["episode_id"],
                "symbol": e["symbol"],
                "mfe_return": e["mfe_return"],
                "final_return": e["final_return"],
                "retention": e["retention"],
                "holding_days": e["holding_days"],
            }
            for e in natural
            if e["mfe"] > 0 and e["final_pnl"] < 0
        ],
        "cohort_30pct": [
            {
                "episode_id": e["episode_id"],
                "symbol": e["symbol"],
                "mfe_return": e["mfe_return"],
                "final_return": e["final_return"],
                "retention": e["retention"],
                "holding_days": e["holding_days"],
            }
            for e in cohort
        ],
        "natural_exit_reasons": dict(Counter(e["natural_exit"]["reason"] for e in natural)),
        "by_symbol": {
            s: {
                "episodes": len([e for e in natural if e["symbol"] == s]),
                "positive_mfe_to_negative_final": sum(
                    e["mfe"] > 0 and e["final_pnl"] < 0 for e in natural if e["symbol"] == s
                ),
                "cohort_30pct": sum(e["mfe_return"] >= 0.30 for e in natural if e["symbol"] == s),
            }
            for s in sorted({e["symbol"] for e in eps})
        },
        "account_reconciliation": d["reconciliation"],
    }
    write("GIVEBACK_DIAGNOSTICS.json", diagnostics)
    decision = {
        "research_decision": "CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED",
        "behavior_class": "MIXED_PROFIT_RETENTION_BEHAVIOR",
        "alternative_exits_tested": "NONE",
        "exact_next_action": "CLOSE_AND_ARCHIVE_CORRECTED_CRYPTO_GIVEBACK_AUDIT",
        "rationale": "The corrected natural >=30% MFE cohort remains 15/15 profitable with median retention above 87%; mixed small-peak round trips persist without a systematic large-winner collapse. Hindsight MFE does not establish executable peak capture.",
    }
    write("AUDIT_DECISION.json", decision)
    print(
        json.dumps(
            {
                "episodes": len(eps),
                "natural": len(natural),
                "endpoint_open": len(open_eps),
                "cohort30": len(cohort),
                "median_retention30": sn["profit_levels"]["0.3"]["retention"]["median"],
                "independent": verification["status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
