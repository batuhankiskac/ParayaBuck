"""Generate diagnostic artifacts from persisted, parity-gated canonical evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research.position_exit_giveback_audit_v1.capture import read_json, write_json
from research.position_exit_giveback_audit_v1.diagnostics import LEVELS, distribution, reconstruct, summarize
from research.position_exit_giveback_audit_v1.verify import verify

OUT = Path(__file__).resolve().parent


def table(headers: list[str], rows: list[list[Any]]) -> str:
    def fmt(v: Any) -> str:
        if v is None:
            return "N/A"
        return f"{v:,.4f}" if isinstance(v, float) else str(v)

    return (
        "| "
        + " | ".join(headers)
        + " |\n| "
        + " | ".join("---" for _ in headers)
        + " |\n"
        + "\n".join("| " + " | ".join(map(fmt, row)) + " |" for row in rows)
        + "\n"
    )


def put(name: str, text: str) -> None:
    (OUT / name).write_text(text.rstrip() + "\n")


def main() -> None:
    parity = read_json(OUT / "EQUITY_REPLAY_PARITY.json")
    if parity["status"] != "PASS":
        raise ValueError("Equity parity gate failed; no interpretation permitted")
    raw = read_json(OUT / "EQUITY_RAW.json.gz")
    diag = reconstruct(raw)
    verification = verify(raw, diag)
    if verification["status"] != "PASS" or abs(diag["reconciliation"]["residual"]) > 1e-6:
        raise ValueError(verification)
    write_json(OUT / "EQUITY_EPISODE_DIAGNOSTICS.json", diag)
    all_es = diag["episodes"]
    es = [e for e in all_es if not e["censored"]]
    censored = [e for e in all_es if e["censored"]]
    summary = summarize(es)
    periods = {
        "full": es,
        "development": [e for e in es if e["exit_timestamp"][:10] <= "2021-12-31"],
        "validation": [e for e in es if e["entry_timestamp"][:10] >= "2022-01-03"],
    }
    periods["cross_boundary"] = [e for e in es if e not in periods["development"] and e not in periods["validation"]]
    yearly = {str(y): [e for e in es if e["exit_timestamp"].startswith(str(y))] for y in range(2017, 2027)}
    reason_groups = {r: [e for e in es if e["exit_reason"] == r] for r in sorted({e["exit_reason"] for e in es})}
    summary["periods"] = {k: summarize(v) for k, v in periods.items()}
    summary["yearly_exit_cohorts"] = {k: summarize(v) for k, v in yearly.items()}
    summary["exit_reasons"] = {k: summarize(v) for k, v in reason_groups.items()}
    summary["censored"] = summarize(censored)
    post_peak = [e for e in es if any(d["timestamp"] > e["mfe_timestamp"] and d["selected"] for d in e["decisions"])]
    summary["continued_selected_after_peak_count"] = len(post_peak)
    summary["time_above"] = {
        str(l): distribution([e["levels"][str(l)]["days_above_left_mark"] for e in es]) for l in (0.0, *LEVELS)
    }
    summary["peak_to_negative_days"] = distribution(
        [e["peak_to_negative_days"] for e in es if e["peak_to_negative_days"] is not None]
    )
    write_json(OUT / "EQUITY_SUMMARY.json", summary)
    crypto = read_json(OUT / "CRYPTO_REPLAY_PARITY.json")
    if crypto["status"] != "AUDIT_INCONCLUSIVE_REPLAY_MISMATCH":
        raise ValueError("Review crypto gate before regenerating this report")
    write_json(
        OUT / "CRYPTO_EPISODE_DIAGNOSTICS.json",
        {
            "strategy": "crypto_low_variance_hysteresis_v1",
            "status": crypto["status"],
            "interpretation_permitted": False,
            "episodes": None,
            "metrics": None,
            "reason": "Historical canonical control mismatch. Current deterministic raw replay is quarantined in CRYPTO_RAW.json.gz; no episode economics interpreted.",
        },
    )
    write_json(
        OUT / "INDEPENDENT_VERIFICATION.json",
        {
            "equity": verification,
            "account_reconciliation": diag["reconciliation"],
            "crypto": {"status": "NOT_APPLICABLE_REPLAY_GATE_FAILED", "reason": crypto["status"]},
        },
    )
    decision = {
        "equity": "MIXED_PROFIT_RETENTION_BEHAVIOR",
        "crypto": "AUDIT_INCONCLUSIVE",
        "crypto_replay_status": crypto["status"],
        "overall": "EXIT_AUDIT_INCONCLUSIVE",
        "equity_exit_research_justified": False,
        "crypto_exit_research_justified": None,
        "equity_rationale": "Broad positive-MFE erosion persists, but meaningful winners usually remain profitable, the largest excursions retain most gains, and the right tail funds net economics. Current evidence does not establish a systematic large-winner retention defect.",
        "alternative_exits_tested": False,
        "production_changed": False,
        "paper_case": "CURRENT_PAPER_TRADE_HISTORY_NOT_AUDITED",
    }
    write_json(OUT / "AUDIT_DECISION.json", decision)
    put(
        "EPISODE_DEFINITION.md",
        """# Episode methodology

An episode begins at the first executed BUY from flat and ends only when quantity returns to flat. Partial fills, additions, reductions and rebalances stay in the same episode. IDs include strategy, canonical symbol and reopening ordinal. Equity entity IDs are resolved against canonical v5 lifetime intervals, rejecting ambiguous identities; terminal lifetime closes separate lifetimes. No ticker-only cross-lifetime pooling is used.

Primary economic P&L = net sale proceeds + credited dividends - all purchase outlays + remaining quantity × canonical completed-bar close. Fees enter cash flows once. Spread/slippage are already embedded in fill prices and are not subtracted twice. Splits change quantity but not total basis. The independent verifier instead capitalizes purchase fees in average-cost inventory and releases basis proportionally on sales. Realized includes dividends; unrealized deducts remaining purchase fees.

Return denominator is the FIXED total gross purchase outlay including purchase fees over the entire episode. This conservative turnover-sensitive normalization handles adds and partial sales without treating a changing position as a simple entry-price return. It is ex-post, not an online signal or time-weighted return: late additions dilute earlier marked percentage gains. It must not be equated to an anecdotal stock-price +10%. Dollar P&L/giveback is always retained. No returns or episodes are clipped.

Paths use completed daily NAV marks plus actual flat-exit settlement endpoints, including costs. Open fills are not intermediate extrema; a final actual exit is a realized endpoint, not a hypothetical intrabar opportunity. MAE is the minimum observed economic P&L (can be positive if every observed point is profitable); no synthetic zero is inserted. Ties use the first occurrence. No intraday highs/lows are used. Diagnostic timestamps are the engine's bar labels; fractional day differences reflect exchange timestamp/DST conventions, not intraday tick precision.

MFE is the maximum of that marked economic path. Giveback=MFE-final P&L and retention=final/MFE only when MFE>0; otherwise null. A tiny positive MFE can make negative retention very large in magnitude; the uncapped mean is therefore unstable and must be read with quantiles and meaningful-profit cohorts. Final return thresholds use the same fixed denominator. Threshold groups overlap and never simulate sales.

FINAL_BAR liquidation is right-censoring, not a strategy-selected exit. All 908 episodes reconcile to NAV; the primary exit tables exclude the 3 horizon-censored episodes and use 905 actual exits. Censored outcomes are separately persisted. Terminal lifecycle events, if present, are genuine lifecycle exits. No episode is synthetically split at a development/validation boundary. Development: exits by 2021-12-31; validation: entries from 2022-01-03; six crossing episodes are a separate stratum. Annual tables are exit-year cohorts, not calendar-year portfolio returns.

Days above levels use the duration to the next observation with the left observed mark above the level; they are mark-based occupancy estimates, not proof prices stayed above a level between observations. Time from peak to negative uses the first later negative observed economic P&L. The peak was not knowable at the time.

Portfolio_path contains realized economic P&L, unrealized P&L, their sum, NAV, and running maximum open unrealized profit. A fall in open unrealized profit also occurs when gains are realized or membership changes; it is NOT entirely surrendered money. active_episode_giveback sums each currently active episode's peak-minus-current economic P&L and is also membership-dependent. Final portfolio endpoint incorporates forced-liquidation costs and retains the pre-liquidation snapshot. Neither series is NAV drawdown nor a sum of simultaneously capturable peaks.
""",
    )
    dist_rows = [
        [k, *[summary[k][p] for p in ("mean", "p10", "p25", "median", "p75", "p90")]]
        for k in (
            "holding_days",
            "final_pnl",
            "final_return",
            "mfe",
            "mae",
            "mfe_return",
            "mae_return",
            "giveback",
            "retention",
            "time_to_mfe_days",
            "mfe_to_exit_days",
        )
    ]
    equity_report = f"""# Equity giveback report

`MIXED_PROFIT_RETENTION_BEHAVIOR`. Current evidence does not justify a dedicated exit-design campaign for V3. This does not prove the current exits are optimal.

905 actual-exit episodes; 781 reached positive economic MFE; 279 of those later lost money (35.72%). Median positive-MFE retention is 51.50%, but meaningful-winner cohorts are materially stronger: +10% cohort retains a median 78.82% and 90.29% exits profitably; every +20% cohort episode remains profitable (17 cases). The largest excursions usually remain large winners, while modest positive excursions often disappear. Thus the motivating concern is partially representative, not a description of the typical large winner.

All 908 episodes, including 3 horizon-censored liquidations, produce ${diag["reconciliation"]["episode_pnl"]:,.2f} net economic P&L; NAV profit is the same within $0.000001. Primary tables below exclude censoring. Dollar results start from the canonical $100,000 account; returns are decimal fractions of total episode purchases.

""" + table(["Metric", "Mean", "P10", "P25", "Median", "P75", "P90"], dist_rows)
    equity_report += "\nRetention survival (positive-MFE denominator):\n\n" + table(
        ["Retains at least", "Fraction"], [[k, v] for k, v in summary["retention_fractions"].items()]
    )
    equity_report += f"\n{len(post_peak)} episodes have at least one later decision still selecting the name after its eventual marked peak. This is descriptive evidence of continued signal membership, not a causal isolation of momentum, sizing, band or cadence effects.\n"
    equity_report += "\nTime above levels (days, left-mark occupancy):\n\n" + table(
        ["Level", "Mean", "Median", "P90"],
        [[k, v["mean"], v["median"], v["p90"]] for k, v in summary["time_above"].items()],
    )
    equity_report += "\nPeak to first subsequent negative economic mark (where observed):\n\n" + table(
        ["Mean days", "Median days", "P90 days"],
        [[summary["peak_to_negative_days"][k] for k in ("mean", "median", "p90")]],
    )
    put("EQUITY_GIVEBACK_REPORT.md", equity_report)
    crypto_report = f"""# Crypto giveback report

`AUDIT_INCONCLUSIVE` / `AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`.

The exact frozen input bytes were recovered and verified. Two fresh current-production runs (one instrumented, one uninstrumented) match exactly. However, the historical canonical universe hash and all six stored output hashes disagree. Fresh fill count {crypto["fill_count"]} vs historical {crypto["historical_fill_count"]}; decisions {crypto["rebalance_count"]} vs {crypto["historical_rebalance_count"]}. Historical ending equity $110,539.90 differs from fresh $105,481.52. These are parity evidence, not a giveback comparison or an alternative-exit backtest.

The dataset is crypto_expanded_1h.csv, aggregated into complete daily bars, with hourly PIT universe snapshots, 2021-04-01 through 2026-07-31. Frozen dataset SHA: {crypto["dataset_sha256"]}. See CRYPTO_REPLAY_PARITY.json and crypto_replay.py for source commit and reconstruction. The present output is quarantined; episode count, holding time, MFE, MAE, retention, round trips, exit-reason proportions, +20/+30/+40% outcomes and right-tail attribution are N/A, not zero. No shifted path is interpreted as the historical production control.

Code confirms retention of eligible holdings through rank four, new vacancies filled from the best-ranked candidates, equal weights and a 96-hour cadence. It intentionally delays replacement in return for lower turnover. Vacancies do not rescue a holder that fails the rule; trade bands, minimum notional and next-bar execution can delay actual flattening. How often hysteresis extends holdings beyond eventual peaks cannot be stated on an accepted replay in this audit.

Historical half-year walk-forward folds overlap the promotion dataset; they are not untouched post-promotion validation. Repairing/explaining canonical reproducibility is necessary before an empirical crypto exit campaign can be justified. No strategy was changed to force parity.
"""
    put("CRYPTO_GIVEBACK_REPORT.md", crypto_report)
    level_report = (
        "# Profit-level diagnostics\n\nEquity actual exits only. Overlapping descriptive cohorts, fixed total-purchases return denominator; values are decimal fractions. No exit at any level is simulated.\n\n"
        + table(
            [
                "Ever reached",
                "N",
                "Final median",
                "Final mean",
                "Profitable fraction",
                "Median retention",
                "Exit <=0 fraction",
                "Median crossing→exit days",
            ],
            [
                [
                    k,
                    v["count"],
                    v["final_median_return"],
                    v["final_mean_return"],
                    v["profitable_exit_fraction"],
                    v["median_retention"],
                    v["exit_nonpositive_fraction"],
                    v["median_crossing_to_exit_days"],
                ]
                for k, v in summary["profit_levels"].items()
            ],
        )
    )
    level_report += "\nCrypto: N/A — AUDIT_INCONCLUSIVE_REPLAY_MISMATCH.\n"
    put("PROFIT_LEVEL_DIAGNOSTICS.md", level_report)
    tail = (
        "# Right-tail attribution\n\nEquity actual exits; no trades removed and no strategy rerun. Top 10% uses ceil(905×0.10)=91 episodes. Net contribution shares may exceed 100% because other episodes lose money.\n\n"
        + table(
            ["Group", "N", "P&L $", "Share positive P&L", "Share net P&L"],
            [
                [k, v["episode_count"], v["pnl"], v["share_positive_pnl"], v["share_net_pnl"]]
                for k, v in summary["right_tail"].items()
            ],
        )
    )
    tail += (
        "\nLargest ten contributors; full top-decile identities and excursions are in EQUITY_SUMMARY.json:\n\n"
        + table(
            ["Episode", "Final P&L $", "MFE return", "Final return"],
            [
                [e["episode_id"], e["final_pnl"], e["mfe_return"], e["final_return"]]
                for e in summary["right_tail"]["top10"]["episodes"]
            ],
        )
    )
    tail += "\nThe top decile supplies 54.68% of positive P&L and 185.17% of net P&L. Long-run net economics depend materially on the right tail. Aggressive early profit taking could truncate those trends; this is a structural risk, not a tested claim about an exit rule. The +20% cohort has 17 profitable exits, median final return 23.58% and median retention 90.17%; +40% has five cases, median final 42.48%, median retention 93.36%. Some winners continued well beyond early diagnostic levels. Small tail samples prevent broad certainty. Crypto: N/A pending parity.\n"
    put("RIGHT_TAIL_ATTRIBUTION.md", tail)
    reasons = (
        "# Exit-reason audit\n\nClassification uses the actual final sell decision timestamp and captured post-overlay target plus preserved strategy metadata. An originally selected name removed by the overlay is risk_overlay; an explicit global risk_off_detail is eligibility_gate_failure; otherwise an omitted equity target is alpha_no_longer_selected. The latter cannot distinguish individual eligibility loss from rank displacement with selected-only metadata. No cause is guessed. A remaining positive target would be rebalance_sizing. Terminal policies distinguish lifetime events from horizon censoring.\n\n"
        + table(
            [
                "Category",
                "N",
                "Median MFE $",
                "Median MFE return",
                "Median final $",
                "Median final return",
                "Median giveback $",
                "Median retention",
                "Median hold days",
                "Median peak→exit days",
            ],
            [
                [
                    k,
                    v["count"],
                    v["mfe"]["median"],
                    v["mfe_return"]["median"],
                    v["final_pnl"]["median"],
                    v["final_return"]["median"],
                    v["giveback"]["median"],
                    v["retention"]["median"],
                    v["holding_days"]["median"],
                    v["mfe_to_exit_days"]["median"],
                ]
                for k, v in summary["exit_reasons"].items()
            ],
        )
    )
    reasons += f"\nHorizon-censored liquidation count: {len(censored)} (excluded above). The zero-count categories are not evidence that the code cannot produce them. Risk overlay count is not equivalent to drawdown forced sales: count caps can remove a selected name, while drawdown is shadowed in this canonical environment. Crypto proportions are N/A pending parity.\n"
    put("EXIT_REASON_AUDIT.md", reasons)
    stability = "# Period stability\n\nEstablished equity split: development through 2021-12-31; validation begins 2022-01-03; full through 2026-07-30. Fully contained episodes only in period cohorts; six boundary-crossing episodes remain in full and in a separate cohort. No new split was chosen from outcomes. Validation here is the established canonical research period, not newly untouched data.\n\n"
    rows = []
    for k, s in summary["periods"].items():
        rows.append(
            [
                k,
                s["count"],
                s["mfe_return"]["median"],
                s["final_return"]["median"],
                s["retention"]["median"],
                s["giveback"]["median"],
                s["positive_mfe_to_loss_fraction"],
                s["mfe_to_exit_days"]["median"],
            ]
        )
    stability += table(
        [
            "Period",
            "N",
            "Median MFE return",
            "Median final return",
            "Median retention",
            "Median giveback $",
            "Positive→loss fraction",
            "Median peak→exit days",
        ],
        rows,
    )
    stability += "\nAll predeclared profit levels by period:\n\n" + table(
        [
            "Period",
            "Level",
            "N",
            "Final median",
            "Median retention",
            "Nonpositive exit fraction",
            "Median crossing→exit days",
        ],
        [
            [
                p,
                l,
                v["count"],
                v["final_median_return"],
                v["median_retention"],
                v["exit_nonpositive_fraction"],
                v["median_crossing_to_exit_days"],
            ]
            for p, s in summary["periods"].items()
            for l, v in s["profit_levels"].items()
        ],
    )
    stability += "\nAnnual actual-exit cohorts (2026 partial year):\n\n" + table(
        [
            "Year",
            "N",
            "Median holding days",
            "Median MFE return",
            "Median final return",
            "Median retention",
            "Positive→loss fraction",
            "Median peak→exit days",
        ],
        [
            [
                y,
                s["count"],
                s["holding_days"]["median"],
                s["mfe_return"]["median"],
                s["final_return"]["median"],
                s["retention"]["median"],
                s["positive_mfe_to_loss_fraction"],
                s["mfe_to_exit_days"]["median"],
            ]
            for y, s in summary["yearly_exit_cohorts"].items()
        ],
    )
    stability += "\nBroad positive-MFE retention weakens from development to validation, but meaningful winners do not show the same collapse: +10% nonpositive exit frequency is 13.46% development versus 6.00% validation; median retention is 78.21% versus 80.30%. Both periods retain profitable outcomes for every +20% case, with only 7 and 10 cases. Dollar giveback comparisons also reflect account growth and changing notional sizing. Crypto periods: N/A pending replay parity; historical walk-forward folds are overlapping diagnostics.\n"
    put("PERIOD_STABILITY.md", stability)
    extremes = {
        "largest_winner": max(es, key=lambda e: e["final_pnl"]),
        "largest_giveback": max((e for e in es if e["giveback"] is not None), key=lambda e: e["giveback"]),
        "longest_hold": max(es, key=lambda e: e["holding_days"]),
        "largest_roundtrip": max((e for e in es if e["final_pnl"] < 0 and e["mfe"] > 0), key=lambda e: e["mfe"]),
    }
    put(
        "EXTREME_EPISODES.md",
        "# Independently verified examples\n\n"
        + table(
            [
                "Case",
                "Episode",
                "Entry",
                "Exit",
                "MFE $",
                "Final $",
                "Giveback $",
                "Retention",
                "MFE return",
                "Hold days",
                "Exit reason",
            ],
            [
                [
                    k,
                    e["episode_id"],
                    e["entry_timestamp"],
                    e["exit_timestamp"],
                    e["mfe"],
                    e["final_pnl"],
                    e["giveback"],
                    e["retention"],
                    e["mfe_return"],
                    e["holding_days"],
                    e["exit_reason"],
                ]
                for k, e in extremes.items()
            ],
        )
        + "\nAll paths/fills/quantity changes remain in the episode JSON. All 103 equity +10% episodes and every winner-to-loser/long-duration episode were verified, not only the displayed examples.\n",
    )
    final = (
        """# Executive conclusion

Equity shows **mixed profit retention**, not a demonstrated systematic collapse of large winners. Small unrealized gains often disappear, but large winners usually stay profitable and support long-run net P&L. Crypto cannot be judged because the historical replay parity gate failed. Overall: `EXIT_AUDIT_INCONCLUSIVE`.

# Production strategy exit behavior

## V3

Weekly completed-bar decisions; positive long-horizon residual-momentum eligibility; multi-horizon consensus ranking; inverse-volatility sizing; eight raw selected names subject to the six-position account cap; turnover band/minimum notional; next-bar execution. Recent price reversal need not remove a still-eligible high-ranked name. No fixed take profit, trailing stop, profit lock, break-even stop or position-profit-dependent exit in this portfolio strategy path.

## Crypto

22-day low realized-volatility ranking, two equal-weight slots, 96-hour cadence; eligible existing holdings may stay through rank four, vacancies filled by rank. No position-profit-dependent exit. Runtime risk caps, order execution and canonical drawdown-shadow semantics are separate from signal exits. See CURRENT_STRATEGY_EXIT_AUDIT.md for exact code references and full distinctions.

# Episode methodology

Flat-to-flat inventory; resizing stays in the episode. Net cash-flow-plus-mark P&L includes canonical dividends, fees and embedded execution costs. Completed daily marks plus actual final settlement endpoints; no intraday highs. Returns divide by fixed total episode purchase outlay, including fees. This conservative ex-post normalization differs from price appreciation. All 908 equity episodes reconcile to NAV; the 3 horizon-censored final liquidations are excluded from 905 actual-exit diagnostics. See EPISODE_DEFINITION.md.

# Equity results

"""
        + equity_report.split("\n", 1)[1]
        + """
# Crypto results

"""
        + crypto_report.split("\n", 1)[1]
        + """
# Profit-level diagnostics

"""
        + level_report.split("\n", 1)[1]
        + """
# Right-tail dependence

"""
        + tail.split("\n", 1)[1]
        + """
# Exit-reason analysis

"""
        + reasons.split("\n", 1)[1]
        + """
# Peak-to-exit delay

Across actual equity exits the median is six calendar days; the 90th percentile is about 22 days. “After the peak” is hindsight. Weekly decision timing, persistent eligibility/ranking and execution constraints explain why a position may remain held through reversals. Captured selected-name metadata demonstrates continued selection but does not causally allocate giveback among cadence, ranking, inverse-vol sizing and turnover bands. Portfolio-level realized/unrealized paths are persisted separately from episode giveback and NAV drawdown.

# Development vs validation stability

"""
        + stability.split("\n", 1)[1]
        + """
# Equity conclusion

`MIXED_PROFIT_RETENTION_BEHAVIOR`.

The evidence does not currently justify a dedicated V3 profit-protection optimization campaign: large-winner retention remains strong in validation, while the right tail contributes more than total net profits. Modest-gain erosion is real and persistent; it is not proof that protecting those gains improves the strategy.

# Crypto conclusion

`AUDIT_INCONCLUSIVE` with `AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`. Reproducibility must be resolved before interpreting profit retention.

# Overall decision

`EXIT_AUDIT_INCONCLUSIVE`. The apparent issue cannot be assigned mainly to equity, crypto, both or neither while crypto is unverified. Equity alone does not substantiate a general large-winner round-trip defect.

# Interpretation

The equity anecdote is partially representative: 10 of 103 normalized +10% episodes exit nonpositive, but the typical episode in that cohort retains 78.82% of peak economic P&L. All 17 +20% cases exit profitably. An individual price-return anecdote is not directly comparable to a resized episode's capital-normalized economic return. The crypto anecdote remains unclassified. CURRENT_PAPER_TRADE_HISTORY_NOT_AUDITED; broker/account state was not accessed.

# What this audit does NOT prove

MFE is hindsight. Peak profit was not necessarily capturable. No alternative exit was tested. This audit establishes neither optimality of existing exits nor improvement from any profit-protection rule. Descriptive thresholds were supplied in advance, not fitted. Small extreme cohorts, normalization sensitivity, repeated symbols and shared market regimes mean these observations are not independent statistical proof. Canonical offline drawdown is shadowed; these results are not a claim of identical PAPER account behavior.

# Potential future research categories

No exit-design campaign is recommended from this audit. The next necessary crypto work is canonical-replay reconciliation, not an exit change.

# Independent verification

All 908 equity episodes independently reconstructed with fee-capitalized average-cost accounting against the primary cash-flow method; includes every extreme, every +10% case, resizing, dividends, splits and final costs. All persisted path points, final P&L, MFE/MAE timestamps, giveback, retention and exit reasons checked. Account reconciliation residual is below $0.000001. See INDEPENDENT_VERIFICATION.json and VALIDATION.json for exact executed checks. Crypto economic verification is not applicable after its replay gate failure.

# Production state

Source base, local main and freshly fetched origin/main: 754f4060b1eb38593201b2a269e9593d083eb534. Research branch created directly from clean main; schedule-13d branch was not merged or used as a base. Production strategy, configuration, risk, PAPER/LIVE, persistence and broker orders unchanged. Scripts write only this research directory, tests use synthetic fixtures. No alternative-exit result files.

# Files

All required artifacts reside in research/position_exit_giveback_audit_v1/. EQUITY_EPISODE_DIAGNOSTICS.json contains complete fills, decisions, actions, paths and metrics. EQUITY_SUMMARY.json contains full-period, split-period, yearly, exit-reason and tail distributions. Gzipped raw observations permit independent reconstruction. CRYPTO_EPISODE_DIAGNOSTICS.json records unavailable metrics as null, never fabricated zero. Replay scripts and parity receipts retain reproducible provenance. EXTREME_EPISODES.md provides audit examples.

Reproduce: `.venv/bin/python research/position_exit_giveback_audit_v1/equity_replay.py`; `.venv/bin/python -m research.position_exit_giveback_audit_v1.crypto_replay`; `.venv/bin/python -m research.position_exit_giveback_audit_v1.build_reports`. Historical CSVs are existing local canonical inputs, identified by receipt SHA256; they are not copied into this branch.

# Final research branch SHA

The final committed research SHA is supplied in the delivery message and obtainable with `git rev-parse research/position-exit-giveback-audit-v1`. A commit cannot contain its own resulting SHA; the source/base SHA above is not presented as the deliverable SHA.
"""
    )
    put("FINAL_AUDIT_REPORT.md", final)
    print(
        {
            "equity": decision["equity"],
            "crypto": decision["crypto"],
            "episodes": len(all_es),
            "verification": verification["status"],
        }
    )


if __name__ == "__main__":
    main()
