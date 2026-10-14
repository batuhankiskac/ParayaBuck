#!/usr/bin/env python3
"""r2_stage_b_v1 Stage B metrics: E2 (production engine, 1.1.0) vs R2 (1.2.0) and VT35.

Reproduction rows (substitute bars only) and the comparison rows (substitute
bars plus the Yahoo warm-up extension, ``build_warmup_extension.py``) both come
from this worktree's copy of the archived Stage B runner. Metric definitions
are the archived ``paper_config_v2/evaluate.py`` ones (session-close NAV path,
window 2017-01-03 -> 2026-07-30), loaded by path so the definitions cannot drift.

Writes ``STAGE_B_RESULTS.json`` next to this file. The payload's
``elapsed_seconds`` is a wall-clock field and is dropped, so a re-run is
byte-identical.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import statistics
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "stage_b" / "runs"
_spec = importlib.util.spec_from_file_location("pc2_eval", HERE / "archive/research/paper_config_v2/evaluate.py")
pc2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc2)

FULL = (date(2017, 1, 3), date(2026, 7, 30))
# Comparison arms (warm-up-extended bars) and the reproduction arms (substitute only).
COMPARISON = {"VT35": "VT35x", "E2": "E2x", "R2": "R2"}
REPRODUCTION = {"VT35": "VT35", "E2": "E2"}
COSTS = ("1.0", "2.0")
# e2_integration_v1 reference (tag archive/research/e2-integration-v1-2026-10).
E2_REFERENCE = {
    "E2": {
        "1.0": {"ending_nav": 1329642.62, "fills": 617, "fees": 186.45543252478112,
                "decisions": "ec634c459fa40af3e868bcda50427099a44dc895c32a1c16c36fbe725ecc65eb",
                "fills_hash": "6aadd3580ac737c681bebaf59aaf7011be77c450ba71c35cc56aaa10350eaff9"},
        "2.0": {"ending_nav": 1313969.74, "fills": 618},
    },
    "VT35": {"1.0": {"ending_nav": 1173537.61, "fills": 615,
                     "fees": 193.53,
                     "decisions": "c9adec34e823b0a745297c28408cccef374852160b14d792a31dbb3ae1b04829",
                     "fills_hash": "8c00d14fb567edb96a4fd159a9339bdab080e5814a8178a5ab34d237eb74a9f0"}},
}


def _run(name: str) -> dict:
    with gzip.open(RUNS / f"{name}.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _audit(name: str) -> dict:
    with gzip.open(RUNS / f"{name}_audit.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _audit_bytes(name: str) -> bytes:
    return gzip.open(RUNS / f"{name}_audit.json.gz", "rb").read()


def _nav_series(run: dict) -> dict[date, float]:
    return {datetime.fromisoformat(ts).date(): float(nav) for ts, nav in run["session_nav"]}


def _drawdown_episode(navs: dict[date, float], window: tuple[date, date]) -> dict[str, float | str]:
    """Peak/trough of the deepest drawdown of the session-close NAV path in the window."""
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
        if day.year == days[index - 1].year:
            base = days[index - 1]
        else:  # first session of the year: from the previous year's last close
            base = days[index - 1]
        out.setdefault(str(day.year), 1.0)
        out[str(day.year)] *= navs[day] / navs[base]
    return {year: value - 1.0 for year, value in out.items()}


def _in_market(rows: list[dict]) -> list[tuple[str, bool]]:
    return [(row["label"][:10], bool(row["weights"])) for row in rows]


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


def _weight(row: dict) -> list[list]:
    return [[symbol, round(float(weight), 12)] for symbol, weight in row["weights"]]


def main() -> None:
    payloads = {arm: {cost: _run(f"{name}_{cost}") for cost in COSTS} for arm, name in COMPARISON.items()}
    reproduction = {arm: {cost: _run(f"{name}_{cost}") for cost in COSTS} for arm, name in REPRODUCTION.items()}
    diagnostic = _run("R2noext_1.0")

    results = {
        arm: {cost: pc2._summary(payloads[arm][cost]) for cost in COSTS} for arm in COMPARISON
    }
    for arm in COMPARISON:
        for cost in COSTS:
            results[arm][cost]["strategy"] = payloads[arm][cost]["strategy"]
            results[arm][cost]["hashes"] = payloads[arm][cost]["hashes"]
            results[arm][cost]["decisions"] = payloads[arm][cost]["counts"]["decisions"]

    navs = {arm: _nav_series(payloads[arm]["1.0"]) for arm in COMPARISON}
    eps = {arm: _drawdown_episode(navs[arm], FULL) for arm in COMPARISON}
    years = {arm: _calendar_years(navs[arm]) for arm in COMPARISON}

    # --- reproduction ---
    repro: dict[str, dict] = {}
    for arm, reference in E2_REFERENCE.items():
        for cost, expected in reference.items():
            actual = reproduction[arm][cost]
            row = {
                "expected_ending_nav": expected["ending_nav"],
                "actual_ending_nav": actual["ending_nav"],
                "nav_match": round(actual["ending_nav"], 2) == expected["ending_nav"],
                "expected_fills": expected["fills"],
                "actual_fills": actual["counts"]["fills"],
                "decisions_hash": actual["hashes"]["decisions"],
                "fills_hash": actual["hashes"]["fills_without_client_ids"],
                "fees": actual["costs"]["fees"],
                "strategy": actual["strategy"],
            }
            if "decisions" in expected:
                row["expected_decisions"] = expected["decisions"]
                row["decisions_match"] = actual["hashes"]["decisions"] == expected["decisions"]
            if "fills_hash" in expected and len(expected["fills_hash"]) == 64:
                row["expected_fills_hash"] = expected["fills_hash"]
                row["fills_match"] = actual["hashes"]["fills_without_client_ids"] == expected["fills_hash"]
            if "fees" in expected:
                row["expected_fees"] = expected["fees"]
                row["fees_match"] = abs(actual["costs"]["fees"] - expected["fees"]) < 0.01
            repro.setdefault(arm, {})[cost] = row

    # --- R2 vs E2 on the identical bars ---
    e2_rows, r2_rows = _audit("E2x_1.0")["decide_log"], _audit("R2_1.0")["decide_log"]
    assert [row["label"] for row in e2_rows] == [row["label"] for row in r2_rows]
    differing = [
        {
            "date": r2["label"][:10],
            "e2_state": e2["trend_state"],
            "r2_state": r2["trend_state"],
            "e2_weights": _weight(e2),
            "r2_weights": _weight(r2),
            "votes_up": (r2.get("trend_signals") or {}).get("votes_up"),
            "sma_up": ((r2.get("trend_signals") or {}).get("sma") or {}).get("up"),
            "tsm_up": ((r2.get("trend_signals") or {}).get("tsm") or {}).get("up"),
            "don_up": ((r2.get("trend_signals") or {}).get("don") or {}).get("up"),
            "early_reentry": {
                key: (r2.get("early_reentry") or {}).get(key)
                for key in ("active", "sma50", "above_sma50", "vol20", "vol20_median252", "momentum20")
            },
        }
        for e2, r2 in zip(e2_rows, r2_rows, strict=True)
        if _weight(e2) != _weight(r2)
    ]

    # --- early re-entry episodes (contiguous early_reentry decisions) ---
    # A run's end marker is the first decision that leaves the early state; the
    # early position is rebalanced at the NEXT session's open, so the episode's
    # P&L span runs from the first early decision's close to the close of that
    # fill session (the last overnight the early position is exposed).
    states = [(row["label"][:10], row["trend_state"] == "early_reentry") for row in r2_rows]
    sessions = [day for day, _active in states]
    episode_rows: list[dict] = []
    for first, exit_session in _runs_of_true(states):
        fill_session = sessions[sessions.index(exit_session) + 1] if exit_session in sessions[:-1] else exit_session
        first_day, fill_day = date.fromisoformat(first), date.fromisoformat(fill_session)
        r2_span = navs["R2"][fill_day] / navs["R2"][first_day] - 1.0
        e2_span = navs["E2"][fill_day] / navs["E2"][first_day] - 1.0
        after = [day for day, held in _in_market(e2_rows) if held and day >= exit_session]
        early_days = [day for day, active in states if active and first <= day < exit_session]
        episode_rows.append(
            {
                "early_first_decision": first,
                "early_last_decision": early_days[-1],
                "exit_session": exit_session,
                "fill_session": fill_session,
                "early_sessions": len(early_days),
                "r2_span_return": r2_span,
                "e2_span_return": e2_span,
                "delta_pp": (r2_span - e2_span) * 100.0,
                "e2_entry_session": after[0] if after else None,
            }
        )
    episode_rows.sort(key=lambda row: row["early_first_decision"])

    # --- determinism: identical payload (minus elapsed_seconds) and identical audit ---
    determinism = {}
    for cost in COSTS:
        first, second = _run(f"R2_{cost}"), _run(f"R2rep_{cost}")
        first.pop("elapsed_seconds"), second.pop("elapsed_seconds")
        determinism[cost] = {
            "payload_identical": json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True),
            "audit_identical": hashlib.sha256(_audit_bytes(f"R2_{cost}")).hexdigest()
            == hashlib.sha256(_audit_bytes(f"R2rep_{cost}")).hexdigest(),
            "audit_sha256": hashlib.sha256(_audit_bytes(f"R2_{cost}")).hexdigest(),
        }

    payload = {
        "window": [FULL[0].isoformat(), FULL[1].isoformat()],
        "engine": "production portfolio backtest engine via the archived paper_config_v2 Stage B runner",
        "bars": {
            "reproduction": "data/historical/substitute_v5_bars_qqq_spy_qld.csv (2016-01-04 .. 2026-07-31)",
            "comparison": "data/historical/substitute_v5_plus_warmup_bars_qqq_spy_qld.csv "
            "(Yahoo warm-up extension 2014-01-02 .. 2015-12-31 + the substitute rows)",
        },
        "arm_configs": {
            "VT35": "configs/VT35.yaml (variant 1.0.0)",
            "E2": "configs/E2.yaml (shipped settings minus early_reentry_weight) + --drop-early-reentry (1.1.0)",
            "R2": "configs/R2.yaml (shipped config/settings.yaml verbatim, early_reentry_weight: 0.5) (1.2.0)",
        },
        "reproduction": repro,
        "warmup_extension": {
            "required_history_bars": 397,
            "qqq_sessions_before_start_substitute_only": 252,
            "qqq_sessions_before_start_combined": 756,
            "r2_without_extension": {
                "ending_nav": diagnostic["ending_nav"],
                "decisions": diagnostic["counts"]["decisions"],
                "first_strategy_decision": _audit("R2noext_1.0")["decide_log"][0]["label"][:10],
            },
            "e2_reproduces_on_combined_bars": round(payloads["E2"]["1.0"]["ending_nav"], 2)
            == E2_REFERENCE["E2"]["1.0"]["ending_nav"],
            "vt35_reproduces_on_combined_bars": round(payloads["VT35"]["1.0"]["ending_nav"], 2)
            == E2_REFERENCE["VT35"]["1.0"]["ending_nav"],
        },
        "results": results,
        "max_drawdown_episodes": eps,
        "calendar_years_1x": years,
        "decisions_in_market_share": {
            arm: sum(held for _day, held in _in_market(_audit(f"{name}_1.0")["decide_log"]))
            / len(_in_market(_audit(f"{name}_1.0")["decide_log"]))
            for arm, name in COMPARISON.items()
        },
        "in_market_episodes": {
            arm: len(_runs_of_true(_in_market(_audit(f"{name}_1.0")["decide_log"])))
            for arm, name in COMPARISON.items()
        },
        "r2_vs_e2": {
            "differing_decisions": differing,
            "early_episodes": episode_rows,
            "episode_count": len(episode_rows),
            "early_sessions": sum(row["early_sessions"] for row in episode_rows),
            "sum_delta_pp": sum(row["delta_pp"] for row in episode_rows),
            "median_delta_pp": statistics.median([row["delta_pp"] for row in episode_rows]) if episode_rows else None,
            "terminal_ending_nav": {
                cost: {
                    "e2": payloads["E2"][cost]["ending_nav"],
                    "r2": payloads["R2"][cost]["ending_nav"],
                    "r2_minus_e2": payloads["R2"][cost]["ending_nav"] - payloads["E2"][cost]["ending_nav"],
                    "r2_over_e2": payloads["R2"][cost]["ending_nav"] / payloads["E2"][cost]["ending_nav"] - 1.0,
                    "cagr_pp": (results["R2"][cost]["full"]["cagr"] - results["E2"][cost]["full"]["cagr"]) * 100,
                    "max_dd_pp": (results["R2"][cost]["full"]["max_drawdown"]
                                  - results["E2"][cost]["full"]["max_drawdown"]) * 100,
                }
                for cost in COSTS
            },
        },
        "accounting_ok": {
            arm: {cost: results[arm][cost]["accounting_ok"] for cost in COSTS} for arm in COMPARISON
        },
        "determinism": determinism,
        "invariants": {
            arm: payloads[arm]["1.0"]["invariants"] for arm in COMPARISON
        },
    }
    out = HERE / "STAGE_B_RESULTS.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    print("wrote", out)
    for arm in COMPARISON:
        for cost in COSTS:
            s = results[arm][cost]
            print(
                f"{arm:5s} {cost}x NAV {s['ending_nav']:>13,.2f}  CAGR {s['full']['cagr'] * 100:6.2f}%"
                f"  DD {s['full']['max_drawdown'] * 100:5.2f}%  Sharpe {s['full']['sharpe']:.3f}"
                f"  turn/yr {s['turnover_per_year']:.2f}  fills {s['fills']}"
                f"  2017-21 {s['cagr_2017_2021'] * 100:6.2f}%  2022-26 {s['cagr_2022_2026'] * 100:6.2f}%"
                f"  {s['strategy']}  acct {s['accounting_ok']}"
            )
    print("early episodes:", len(episode_rows), "sessions", payload["r2_vs_e2"]["early_sessions"],
          "sum delta pp", round(payload["r2_vs_e2"]["sum_delta_pp"], 2),
          "terminal excess %", round((payloads["R2"]["1.0"]["ending_nav"] / payloads["E2"]["1.0"]["ending_nav"] - 1) * 100, 3))
    for row in episode_rows:
        print("   ", row)
    print("differing decisions:", len(differing))


if __name__ == "__main__":
    main()
