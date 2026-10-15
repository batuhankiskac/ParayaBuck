#!/usr/bin/env python3
"""k_stage_b_v1 Stage B replay: production R2 vs A1u vs K, pre- and after tax.

Reads the run dumps written by ``stage_b/research/paper_config_v2/run_stage_b.py``
(the archived paper_config_v2 Stage B runner, this worktree's copy, driving this
worktree's ``src`` — ``equity_leveraged_trend_v1`` 1.2.0) and writes
``STAGE_B_RESULTS.json`` next to this file.

Metric definitions are the archived ``paper_config_v2/evaluate.py`` ones (loaded
by path), so they cannot drift from the earlier Stage B reports. Reproduction
references are the archived r2_stage_b_v1 payloads (tag
``archive/research/r2-stage-b-v1-2026-10``), embedded below as literals.

Sections: reproduction, results, band/deferral attribution, determinism,
invariants, after-tax. The payload carries no wall-clock field, so a re-run is
byte-identical (``elapsed_seconds`` is dropped).
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tax_replay  # noqa: E402

RUNS = HERE / "stage_b" / "runs"
_spec = importlib.util.spec_from_file_location("pc2_eval", HERE / "pc2_evaluate.py")
pc2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc2)

FULL = (date(2017, 1, 3), date(2026, 7, 30))
COSTS = ("1.0", "2.0")
# arm label -> (run prefix, repeat prefix)
ARMS = {"R2": ("R2off", "R2offrep"), "E2": ("E2", "E2rep"), "A1u": ("A1u", "A1urep"), "K": ("K", "Krep")}
# The reproduction references (tag archive/research/r2-stage-b-v1-2026-10,
# runs/{R2,E2}_{1.0,2.0}.json.gz, read field by field).
REFERENCES = {
    "R2": {
        "1.0": {
            "ending_nav": 1359064.1796735846,
            "fills": 644,
            "fees": 205.08163691187292,
            "decisions": "d69d8557b91596e464d95ae94377f88bc3894cb70d353751ae7053c1777bb914",
            "fills_hash": "4f365dabdbd83156595a3290129540a14b1e035bf0b63a4643c001d6484b34c5",
            "strategy": ["equity_leveraged_trend_v1", "1.2.0"],
        },
        "2.0": {
            "ending_nav": 1342001.2858307522,
            "fills": 645,
            "fees": 406.98344013840074,
            "decisions": "d69d8557b91596e464d95ae94377f88bc3894cb70d353751ae7053c1777bb914",
            "fills_hash": "697fb96151c93972af8db1033de95b4020476f1fa75e684253d268a6467cfb63",
            "strategy": ["equity_leveraged_trend_v1", "1.2.0"],
        },
    },
    "E2": {
        "1.0": {
            "ending_nav": 1329642.6232118441,
            "fills": 617,
            "fees": 186.45543252478112,
            "decisions": "ec634c459fa40af3e868bcda50427099a44dc895c32a1c16c36fbe725ecc65eb",
            "fills_hash": "6aadd3580ac737c681bebaf59aaf7011be77c450ba71c35cc56aaa10350eaff9",
            "strategy": ["equity_leveraged_trend_v1", "1.1.0"],
        },
        "2.0": {
            "ending_nav": 1313969.7379699536,
            "fills": 618,
            "fees": 370.2115201020008,
            "decisions": "ec634c459fa40af3e868bcda50427099a44dc895c32a1c16c36fbe725ecc65eb",
            "fills_hash": "9618f2fbaa3a0991dce01a3c6c4506856fb89de2e580c31c6aaa744e8dc32a0e",
            "strategy": ["equity_leveraged_trend_v1", "1.1.0"],
        },
    },
}
COMPARISON = ("R2", "A1u", "K")


def _run(name: str) -> dict:
    with gzip.open(RUNS / f"{name}.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _audit(name: str) -> dict:
    with gzip.open(RUNS / f"{name}_audit.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _audit_bytes(name: str) -> bytes:
    with gzip.open(RUNS / f"{name}_audit.json.gz", "rb") as handle:
        return handle.read()


def _nav_series(run: dict) -> dict[date, float]:
    return {datetime.fromisoformat(ts).date(): float(nav) for ts, nav in run["session_nav"]}


def _drawdown_episode(navs: dict[date, float], window: tuple[date, date]) -> dict:
    days = sorted(day for day in navs if window[0] <= day <= window[1])
    peak_day, peak_value, best = days[0], navs[days[0]], {"depth": 0.0}
    for day in days:
        value = navs[day]
        if value > peak_value:
            peak_day, peak_value = day, value
        depth = 1.0 - value / peak_value
        if depth > best["depth"]:
            best = {"depth": depth, "peak": peak_day.isoformat(), "trough": day.isoformat()}
    return best


def _calendar_years(navs: dict[date, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    days = sorted(navs)
    for index in range(1, len(days)):
        day = days[index]
        base = days[index - 1]
        out.setdefault(str(day.year), 1.0)
        out[str(day.year)] *= navs[day] / navs[base]
    return {year: value - 1.0 for year, value in out.items()}


def _runs_of_true(states: list[tuple[str, bool]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    start: str | None = None
    for day, held in states:
        if held and start is None:
            start = day
        elif not held and start is not None:
            out.append((start, day))
            start = None
    if start is not None:
        out.append((start, states[-1][0]))
    return out


# --------------------------------------------------------------------------- band / deferral attribution
def _hold_runs(
    records: list[tuple[str, dict, float, float]], day_index: dict[str, int]
) -> list[dict]:
    """Maximal runs of consecutive decision sessions sharing one held downsize.

    ``records`` are (decision day, hold, target weight, pre-trade weight) in
    decision order. A run ends when
    a decision session is missing a hold for the symbol or the held target
    changes, i.e. exactly when the postponed resize is re-planned differently.
    """
    runs: list[dict] = []
    for day, hold, target, current in records:
        index = day_index[day]
        previous = runs[-1] if runs else None
        if (
            previous is not None
            and previous["symbol"] == hold["symbol"]
            and day_index[previous["last"]] + 1 == index
        ):
            previous["last"] = day
            previous["sessions"] += 1
            previous["holds"].append(hold)
            previous["targets"].append(target)
            previous["currents"].append(current)
        else:
            runs.append(
                {
                    "symbol": hold["symbol"],
                    "first": day,
                    "last": day,
                    "sessions": 1,
                    "holds": [hold],
                    "targets": [target],
                    "currents": [current],
                    "first_hold": hold,
                }
            )
    return runs


def _fill(arm_prefix: str, audit: dict, session: str, side: str = "sell") -> dict | None:
    for event in audit["equity_events"]:
        if event["event"] == "fill" and event["side"] == side and event["timestamp"][:10] == session:
            return event
    return None


def _next_sell_session(audit: dict, after: str) -> str | None:
    sessions = sorted(
        {e["timestamp"][:10] for e in audit["equity_events"] if e["event"] == "fill" and e["side"] == "sell"}
    )
    for session in sessions:
        if session > after:
            return session
    return None



def _units_path(audit: dict, days: list[str]) -> dict[str, float]:
    """QLD units held at the end of each session, from the run's own fills."""
    by_day: dict[str, list] = defaultdict(list)
    for trade in tax_replay.trades_from_audit(audit):
        by_day[trade.day].append(trade)
    units = 0.0
    out: dict[str, float] = {}
    for day in days:
        for trade in by_day.get(day, ()):
            units = trade.prod_units_after
        out[day] = units
    return out


def _exposure_pnl(arm_units: dict[str, float], control_units: dict[str, float], closes: dict[str, float],
                  days: list[str]) -> float:
    """Exact P&L of the units the arm held beyond the control, over the window.

    ``sum_t (units_arm(t-1) - units_control(t-1)) * (close(t) - close(t-1))``:
    the extra exposure marked at the session closes the NAV path uses. Adding
    the fee and dividend differences to this and the NAV ratio it produces is
    the whole pre-tax effect of the rule.
    """
    total = 0.0
    for index in range(1, len(days)):
        previous, day = days[index - 1], days[index]
        total += (arm_units[previous] - control_units[previous]) * (closes[day] - closes[previous])
    return total



def _reconcile(arm_label: str, cost: str, payloads: dict, audits: dict, closes: dict[str, float]) -> dict:
    """Extra exposure + saved costs + extra dividends + residual = NAV difference.

    The residual is the intraday marking of the delayed trades: a session's
    extra P&L carries a ``Δunits × (close - fill price)`` term that the
    end-of-session unit path cannot see, which is exactly where a sale moved
    from one open to a later one shows up.
    """
    arm, control = payloads[arm_label][cost], payloads["R2"][cost]
    days = tax_replay.days_from_audit(audits[arm_label][cost])
    extra = _exposure_pnl(
        _units_path(audits[arm_label][cost], days), _units_path(audits["R2"][cost], days), closes, days
    )
    saved = -sum(arm["costs"][key] - control["costs"][key] for key in arm["costs"])
    dividends = (
        audits[arm_label][cost]
        and sum(
            float(e["cash_delta"]) for e in audits[arm_label][cost]["equity_events"] if e["event"] == "corporate_action"
        )
        - sum(float(e["cash_delta"]) for e in audits["R2"][cost]["equity_events"] if e["event"] == "corporate_action")
    )
    nav = arm["ending_nav"] - control["ending_nav"]
    residual = nav - extra - saved - dividends
    return {
        "extra_exposure_pnl_usd": round(extra, 2),
        "cost_savings_usd": round(saved, 2),
        "extra_dividend_cash_usd": round(dividends, 2),
        "intraday_marking_residual_usd": round(residual, 2),
        "nav_difference_usd": round(nav, 2),
    }


def _attribute(
    label: str,
    prefix: str,
    audit: dict,
    payload: dict,
    opens: dict[str, float],
    day_index: dict[str, int],
    kind: str,
) -> dict:
    """P&L of the units the rule kept exposed while the control would have sold.

    For every run: the intended sale would have filled at the next session's
    open; the rule keeps the drift exposed until the executing sell. The effect
    is the retained notional times the QLD open-to-open return over that span,
    i.e. the position the rule kept, marked at the prices the two ends actually
    traded at (fees excluded — they are paid either way).
    """
    key = "gain_band_holds" if kind == "band" else "december_deferral_holds"
    records: list[tuple[str, dict, float, float]] = []
    for record in audit["rebalances"]:
        targets = {symbol: weight for symbol, weight in record["target_weights"]}
        pre = {symbol: weight for symbol, weight in record["pre_trade_weights"]}
        for hold in record[key]:
            symbol = hold["symbol"]
            records.append(
                (record["decision_timestamp"][:10], hold, float(targets[symbol]), float(pre.get(symbol, 0.0)))
            )
    runs = _hold_runs(records, day_index)
    navs = _nav_series(payload)
    days = tax_replay.days_from_audit(audit)  # ISO session labels, same order as day_index
    out = []
    for run in runs:
        first_index = day_index[run["first"]]
        entry_session = days[first_index + 1] if first_index + 1 < len(days) else None
        exit_session = _next_sell_session(audit, run["last"])
        hold = run["first_hold"]
        last_hold = run["holds"][-1]
        target = run["targets"][0]
        current = run["currents"][0]
        gap = hold.get("gap")
        equity = navs[date.fromisoformat(run["first"])]
        retained_notional = max(current - target, 0.0) * equity
        entry_price = opens.get(entry_session) if entry_session else None
        exit_price = opens.get(exit_session) if exit_session else None
        span_return = None
        pnl = None
        if entry_price and exit_price and exit_session and entry_session:
            span_return = exit_price / entry_price - 1.0
            pnl = retained_notional * span_return
        out.append(
            {
                "kind": kind,
                "symbol": run["symbol"],
                "first_decision": run["first"],
                "last_decision": run["last"],
                "sessions_held": run["sessions"],
                "entry_fill_session": entry_session,
                "executing_sell_session": exit_session,
                "target_weight": round(target, 6),
                "current_weight": round(current, 6),
                "gap_first": round(gap, 6) if gap is not None else None,
                "gap_last": round(last_hold.get("gap"), 6) if last_hold.get("gap") is not None else None,
                "retained_notional_usd": round(retained_notional, 2),
                "g_frac_first": round(hold["g_frac"], 6) if "g_frac" in hold else None,
                "g_frac_last": round(last_hold["g_frac"], 6) if "g_frac" in last_hold else None,
                "band": hold.get("band"),
                "entry_open": entry_price,
                "exit_open": exit_price,
                "span_return": round(span_return, 6) if span_return is not None else None,
                "pnl_usd": round(pnl, 2) if pnl is not None else None,
            }
        )
    out.sort(key=lambda row: row["first_decision"])
    total = sum(row["pnl_usd"] or 0.0 for row in out)
    biggest = sorted((row for row in out if row["pnl_usd"] is not None), key=lambda r: abs(r["pnl_usd"]), reverse=True)
    return {
        "decisions_with_record": len({row[0] for row in records}),
        "records": len(records),
        "runs": len(out),
        "sessions_held": sum(row["sessions_held"] for row in out),
        "retained_notional_usd_total": round(sum(row["retained_notional_usd"] for row in out), 2),
        "attributed_pnl_usd": round(total, 2),
        "runs_detail": out,
        "largest": biggest[:10],
    }


def main() -> None:
    payloads = {arm: {cost: _run(f"{ARMS[arm][0]}_{cost}") for cost in COSTS} for arm in ARMS}
    audits = {arm: {cost: _audit(f"{ARMS[arm][0]}_{cost}") for cost in COSTS} for arm in ARMS}
    opens, closes = tax_replay.qld_open_close()

    # ---------------------------------------------------------------- reproduction
    reproduction: dict[str, dict] = {}
    for arm in ("R2", "E2"):
        for cost in COSTS:
            expected = REFERENCES[arm][cost]
            actual = payloads[arm][cost]
            reproduction.setdefault(arm, {})[cost] = {
                "expected_ending_nav": expected["ending_nav"],
                "actual_ending_nav": actual["ending_nav"],
                "nav_match": round(actual["ending_nav"], 2) == round(expected["ending_nav"], 2),
                "expected_fills": expected["fills"],
                "actual_fills": actual["counts"]["fills"],
                "fills_match": actual["counts"]["fills"] == expected["fills"],
                "expected_fees": expected["fees"],
                "actual_fees": actual["costs"]["fees"],
                "fees_match": abs(actual["costs"]["fees"] - expected["fees"]) < 1e-6,
                "expected_decisions_hash": expected["decisions"],
                "decisions_hash_match": actual["hashes"]["decisions"] == expected["decisions"],
                "expected_fills_hash": expected["fills_hash"],
                "fills_hash_match": actual["hashes"]["fills_without_client_ids"] == expected["fills_hash"],
                "strategy": actual["strategy"],
            }
    # The shipped config/settings.yaml, run verbatim, must be the same run as K.yaml.
    shipped = _run("Kshipped_1.0")
    reproduction["shipped_config_is_K"] = {
        "ending_nav": shipped["ending_nav"],
        "k_ending_nav": payloads["K"]["1.0"]["ending_nav"],
        "match": round(shipped["ending_nav"], 2) == round(payloads["K"]["1.0"]["ending_nav"], 2),
        "decisions_hash_match": shipped["hashes"]["decisions"] == payloads["K"]["1.0"]["hashes"]["decisions"],
        "fills_hash_match": shipped["hashes"]["fills_without_client_ids"]
        == payloads["K"]["1.0"]["hashes"]["fills_without_client_ids"],
    }

    # ---------------------------------------------------------------- results
    results: dict[str, dict] = {}
    for arm in COMPARISON:
        for cost in COSTS:
            row = pc2._summary(payloads[arm][cost])
            row["strategy"] = payloads[arm][cost]["strategy"]
            row["hashes"] = payloads[arm][cost]["hashes"]
            row["decisions"] = payloads[arm][cost]["counts"]["decisions"]
            sells = [
                e for e in audits[arm][cost]["equity_events"] if e["event"] == "fill" and e["side"] == "sell"
            ]
            buys = [e for e in audits[arm][cost]["equity_events"] if e["event"] == "fill" and e["side"] == "buy"]
            row["sell_fills"] = len(sells)
            row["buy_fills"] = len(buys)
            results.setdefault(arm, {})[cost] = row

    navs = {arm: _nav_series(payloads[arm]["1.0"]) for arm in COMPARISON}
    episodes = {arm: _drawdown_episode(navs[arm], FULL) for arm in COMPARISON}
    years = {arm: _calendar_years(navs[arm]) for arm in COMPARISON}
    in_market = {
        arm: [(row["label"][:10], bool(row["weights"])) for row in audits[arm]["1.0"]["decide_log"]]
        for arm in COMPARISON
    }

    # ---------------------------------------------------------------- band / deferral
    day_index = {day: index for index, day in enumerate(tax_replay.days_from_audit(audits["R2"]["1.0"]))}
    control_units = {
        cost: _units_path(audits["R2"][cost], tax_replay.days_from_audit(audits["R2"][cost])) for cost in COSTS
    }
    attribution = {
        arm: {
            cost: {
                "band": _attribute(arm, ARMS[arm][0], audits[arm][cost], payloads[arm][cost], opens, day_index, "band"),
                "deferral": _attribute(
                    arm, ARMS[arm][0], audits[arm][cost], payloads[arm][cost], opens, day_index, "deferral"
                ),
                "band_fallback_log_lines": len(audits[arm][cost]["band_fallbacks"]),
                # exact, non-overlapping: the extra units the arm held beyond
                # the control, marked at the session closes of the NAV path
                "extra_exposure_pnl_usd": round(
                    _exposure_pnl(
                        _units_path(audits[arm][cost], tax_replay.days_from_audit(audits[arm][cost])),
                        control_units[cost],
                        closes,
                        tax_replay.days_from_audit(audits[arm][cost]),
                    ),
                    2,
                )
                if arm != "R2"
                else 0.0,
                "nav_difference_vs_R2_usd": round(
                    payloads[arm][cost]["ending_nav"] - payloads["R2"][cost]["ending_nav"], 2
                ),
                "cost_difference_vs_R2_usd": {
                    key: round(payloads[arm][cost]["costs"][key] - payloads["R2"][cost]["costs"][key], 2)
                    for key in payloads[arm][cost]["costs"]
                },
                "reconciliation": _reconcile(arm, cost, payloads, audits, closes),
            }
            for cost in COSTS
        }
        for arm in COMPARISON
    }

    # ---------------------------------------------------------------- determinism
    determinism: dict[str, dict] = {}
    for arm, (prefix, repeat) in ARMS.items():
        for cost in COSTS:
            first, second = _run(f"{prefix}_{cost}"), _run(f"{repeat}_{cost}")
            first.pop("elapsed_seconds", None)
            second.pop("elapsed_seconds", None)
            determinism.setdefault(arm, {})[cost] = {
                "payload_identical": json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True),
                "audit_sha256": hashlib.sha256(_audit_bytes(f"{prefix}_{cost}")).hexdigest(),
                "audit_identical": hashlib.sha256(_audit_bytes(f"{prefix}_{cost}")).hexdigest()
                == hashlib.sha256(_audit_bytes(f"{repeat}_{cost}")).hexdigest(),
            }

    # ---------------------------------------------------------------- after tax
    tax = tax_replay.load_tax_engine()
    after_tax: dict[str, dict] = {}
    for arm in COMPARISON:
        for cost in COSTS:
            audit = audits[arm][cost]
            payload = payloads[arm][cost]
            days = tax_replay.days_from_audit(audit)
            prod_nav = {ts[:10]: float(value) for ts, value in payload["session_nav"]}
            trades = tax_replay.trades_from_audit(audit)
            dividends = tax_replay.dividends_from_audit(audit)
            block: dict[str, dict] = {}
            for kind in ("pre_tax", "progressive"):
                block[kind] = tax_replay.replay(
                    tax,
                    days=days,
                    trades=trades,
                    dividends=dividends,
                    opens=opens,
                    closes=closes,
                    prod_nav=prod_nav,
                    capital=float(payload["start_nav"]),
                    kind=kind,
                    scaling=True,
                )
            # sensitivity: literal production quantities (free leverage on the tax paid)
            block["progressive_unscaled"] = tax_replay.replay(
                tax,
                days=days,
                trades=trades,
                dividends=dividends,
                opens=opens,
                closes=closes,
                prod_nav=prod_nav,
                capital=float(payload["start_nav"]),
                kind="progressive",
                scaling=False,
            )
            harness = block["pre_tax"]
            block["harness_check"] = {
                "production_ending_nav": payload["ending_nav"],
                "pretax_replay_ending_nav": harness["ending_nav_usd"],
                "ending_nav_match": abs(harness["ending_nav_usd"] - payload["ending_nav"]) < 1e-4,
                "max_nav_path_diff": max(
                    abs(harness["_nav"][index + 1] - prod_nav[day]) for index, day in enumerate(days)
                ),
                "production_path_reproduced": max(
                    abs(harness["_nav"][index + 1] - prod_nav[day]) for index, day in enumerate(days)
                )
                < 1e-6,
            }
            for key in ("pre_tax", "progressive", "progressive_unscaled"):
                block[key].pop("_nav", None)
            after_tax.setdefault(arm, {})[cost] = block

    deltas = {}
    for cost in COSTS:
        control = after_tax["R2"][cost]["progressive"]["cagr_usd_liquidated"]
        control_un = after_tax["R2"][cost]["progressive_unscaled"]["cagr_usd_liquidated"]
        deltas[cost] = {
            arm: {
                "cagr_usd_liquidated": after_tax[arm][cost]["progressive"]["cagr_usd_liquidated"],
                "delta_pp": (after_tax[arm][cost]["progressive"]["cagr_usd_liquidated"] - control) * 100,
                "delta_pp_unscaled": (
                    after_tax[arm][cost]["progressive_unscaled"]["cagr_usd_liquidated"] - control_un
                )
                * 100,
                "tax_total_usd": after_tax[arm][cost]["progressive"]["tax_total_usd_at_payment_fx"],
                "terminal_usd": after_tax[arm][cost]["progressive"]["ending_nav_usd"],
            }
            for arm in COMPARISON
        }

    payload = {
        "window": [FULL[0].isoformat(), FULL[1].isoformat()],
        "engine": "this worktree's src (equity_leveraged_trend_v1 1.2.0) via the archived paper_config_v2 Stage B runner",
        "bars": "data/historical/substitute_v5_plus_warmup_bars_qqq_spy_qld.csv "
        "(Yahoo warm-up 2014-01-02..2015-12-31 + the Alpaca SIP substitute rows)",
        "arm_configs": {
            "R2off": "configs/R2off.yaml = the archived R2 arm with downsize_gain_band: null and deferral 0 (1.2.0)",
            "E2": "configs/E2.yaml = 1.1.0, needs --drop-early-reentry, band and deferral explicitly off",
            "A1u": "configs/A1u.yaml = R2off + the shipped A1u band schedule, deferral 0",
            "K": "configs/K.yaml = R2off + the A1u band schedule + deferral 10 (the shipped execution policy)",
        },
        "reproduction": reproduction,
        "results": results,
        "max_drawdown_episodes": episodes,
        "calendar_years_1x": years,
        "decisions_in_market_share": {
            arm: sum(held for _day, held in in_market[arm]) / len(in_market[arm]) for arm in COMPARISON
        },
        "in_market_episodes": {arm: len(_runs_of_true(in_market[arm])) for arm in COMPARISON},
        "band_and_deferral": attribution,
        "determinism": determinism,
        "accounting_ok": {arm: {cost: results[arm][cost]["accounting_ok"] for cost in COSTS} for arm in COMPARISON},
        "invariants": {arm: payloads[arm]["1.0"]["invariants"] for arm in COMPARISON},
        "after_tax": after_tax,
        "after_tax_deltas": deltas,
    }
    out = HERE / "STAGE_B_RESULTS.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")

    # ---------------------------------------------------------------- console
    print(f"window {FULL[0]} -> {FULL[1]}")
    for arm in ("R2", "E2"):
        for cost in COSTS:
            row = reproduction[arm][cost]
            print(
                f"repro {arm:4s} {cost}x NAV {row['actual_ending_nav']:>13,.2f} vs {row['expected_ending_nav']:>13,.2f}"
                f"  nav={row['nav_match']} fills={row['fills_match']} fees={row['fees_match']}"
                f" dec_hash={row['decisions_hash_match']} fill_hash={row['fills_hash_match']}"
            )
    print("shipped config == K:", reproduction["shipped_config_is_K"]["match"],
          reproduction["shipped_config_is_K"]["fills_hash_match"])
    for arm in COMPARISON:
        for cost in COSTS:
            s = results[arm][cost]
            print(
                f"{arm:4s} {cost}x NAV {s['ending_nav']:>13,.2f}  CAGR {s['full']['cagr'] * 100:6.2f}%"
                f"  DD {s['full']['max_drawdown'] * 100:5.2f}%  Sharpe {s['full']['sharpe']:.3f}"
                f"  turn/yr {s['turnover_per_year']:.2f}  fills {s['fills']} (sells {s['sell_fills']})"
                f"  17-21 {s['cagr_2017_2021'] * 100:6.2f}%  22-26 {s['cagr_2022_2026'] * 100:6.2f}%"
                f"  acct {s['accounting_ok']}"
            )
    for arm in ("A1u", "K"):
        for cost in COSTS:
            band = attribution[arm][cost]["band"]
            defer = attribution[arm][cost]["deferral"]
            print(
                f"{arm} {cost}x band holds {band['records']} in {band['runs']} runs,"
                f" deferral holds {defer['records']} in {defer['runs']} runs,"
                f" fallbacks {attribution[arm][cost]['band_fallback_log_lines']}"
            )
    print("determinism:", {arm: {c: (determinism[arm][c]["payload_identical"], determinism[arm][c]["audit_identical"]) for c in COSTS} for arm in ARMS})
    print("after tax (weight-preserving, $100k, liquidated):")
    for cost in COSTS:
        for arm in COMPARISON:
            row = deltas[cost][arm]
            print(
                f"  {cost}x {arm:4s} CAGR {row['cagr_usd_liquidated'] * 100:6.3f}%  delta {row['delta_pp']:+6.3f} pp"
                f"  (unscaled {row['delta_pp_unscaled']:+6.3f} pp)  tax ${row['tax_total_usd']:,.0f}"
                f"  terminal ${row['terminal_usd']:,.0f}"
            )
    print("wrote", out)


if __name__ == "__main__":
    main()
