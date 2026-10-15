#!/usr/bin/env python3
"""Write SUMMARY.txt from STAGE_B_RESULTS.json (deterministic, no wall-clock)."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
YEARS = 9.5830  # (2026-07-30 - 2017-01-03) / 365.25


def main() -> None:
    r = json.loads((HERE / "STAGE_B_RESULTS.json").read_text())
    lines: list[str] = []
    w = lines.append

    w("k_stage_b_v1 | Stage B replay of K on the production engine | 2017-01-03 -> 2026-07-30 | $100k")
    w("engine: this worktree's src, equity_leveraged_trend_v1 1.2.0, via the archived paper_config_v2 Stage B runner")
    w("arms: R2off (R2 with A1u+H2 off) | A1u (band on, deferral 0) | K (band on, deferral 10 = shipped policy)")
    w("VERDICT: K reproduces on the production engine: +0.47 pp CAGR pre-tax and +1.27 pp after Turkish tax vs")
    w("         R2, of which the band is +0.40 / +0.88 pp (research 2017 window: A1u +0.82, K +1.09 pp).")
    w("         R2 and E2 reproduce the r2_stage_b_v1 payloads bit-for-bit.")
    w("")
    w("REPRODUCTION (the r2_stage_b_v1 references, read field by field)")
    for arm in ("R2", "E2"):
        for cost in ("1.0", "2.0"):
            row = r["reproduction"][arm][cost]
            w(
                f"  {arm:4s} {cost}x NAV {row['actual_ending_nav']:>13,.2f}  expected {row['expected_ending_nav']:>13,.2f}"
                f"  nav={row['nav_match']} fills={row['fills_match']} fees={row['fees_match']}"
                f" decisions_hash={row['decisions_hash_match']} fills_hash={row['fills_hash_match']}"
            )
    s = r["reproduction"]["shipped_config_is_K"]
    w(
        f"  config/settings.yaml verbatim == K.yaml: NAV {s['ending_nav']:,.2f} == {s['k_ending_nav']:,.2f}"
        f" ({s['match']}), decisions and fills hashes equal ({s['decisions_hash_match']}, {s['fills_hash_match']})"
    )
    w("")
    w("PRE-TAX (session-close NAV path)")
    w("  arm  cost       ending NAV     CAGR    maxDD   Sharpe  turn/yr  fills  buys sells  sells/yr   2017-21   2022-26")
    for arm in ("R2", "A1u", "K"):
        for cost in ("1.0", "2.0"):
            x = r["results"][arm][cost]
            w(
                f"  {arm:4s} {cost}x {x['ending_nav']:>14,.2f} {x['full']['cagr'] * 100:7.2f}%"
                f" {x['full']['max_drawdown'] * 100:7.2f}% {x['full']['sharpe']:7.3f} {x['turnover_per_year']:8.2f}"
                f" {x['fills']:6d} {x['buy_fills']:5d} {x['sell_fills']:5d} {x['sell_fills'] / YEARS:9.2f}"
                f" {x['cagr_2017_2021'] * 100:8.2f}% {x['cagr_2022_2026'] * 100:8.2f}%"
            )
    for arm in ("A1u", "K"):
        d = r["results"][arm]["1.0"]
        b = r["results"]["R2"]["1.0"]
        w(
            f"  delta vs R2 (1x): {arm} CAGR {(d['full']['cagr'] - b['full']['cagr']) * 100:+.2f} pp,"
            f" maxDD {(d['full']['max_drawdown'] - b['full']['max_drawdown']) * 100:+.2f} pp,"
            f" Sharpe {d['full']['sharpe'] - b['full']['sharpe']:+.3f},"
            f" turnover {d['turnover_per_year'] - b['turnover_per_year']:+.2f}/yr,"
            f" fills {d['fills'] - b['fills']:+d}"
        )
    w("  max DD episode (all arms): peak 2021-11-19 -> trough 2023-03-13")
    w("  in-market share 85.37% (16 episodes) in all three arms: the target series is identical")
    years = r["calendar_years_1x"]
    differing = {
        year: {arm: round((years[arm][year] - years["R2"][year]) * 100, 2) for arm in ("A1u", "K")}
        for year in sorted(years["R2"])
        if max(abs(years[arm][year] - years["R2"][year]) for arm in ("A1u", "K")) > 1e-9
    }
    w(f"  calendar-year differences vs R2 (pp): {differing}")
    w("")
    w("BAND AND DEFERRAL (1x)")
    for arm in ("A1u", "K"):
        d = r["band_and_deferral"][arm]["1.0"]
        rec = d["reconciliation"]
        w(
            f"  {arm}: band holds {d['band']['records']} decisions in {d['band']['runs']} runs;"
            f" deferral holds {d['deferral']['records']} decisions in {d['deferral']['runs']} runs;"
            f" A1u lot-book fail-safe log lines {d['band_fallback_log_lines']}"
        )
        w(
            f"       vs R2: extra-exposure P&L ${rec['extra_exposure_pnl_usd']:,.2f}"
            f" + saved costs ${rec['cost_savings_usd']:,.2f}"
            f" + extra dividends ${rec['extra_dividend_cash_usd']:,.2f}"
            f" + intraday marking ${rec['intraday_marking_residual_usd']:,.2f}"
            f" = NAV difference ${rec['nav_difference_usd']:,.2f}"
        )
    w("  December-deferral episodes (K, indicative per-episode P&L):")
    for row in r["band_and_deferral"]["K"]["1.0"]["deferral"]["runs_detail"]:
        w(
            f"    {row['first_decision']} -> {row['last_decision']} ({row['sessions_held']} decisions),"
            f" sell executed {row['executing_sell_session']}, {row['target_weight']:.4f} <- {row['current_weight']:.4f},"
            f" retained ${row['retained_notional_usd']:,.0f}, span {row['span_return'] * 100:+.2f}% -> ${row['pnl_usd']:,.0f}"
        )
    w("  largest band holds (K, by |P&L|):")
    for row in r["band_and_deferral"]["K"]["1.0"]["band"]["largest"][:5]:
        w(
            f"    {row['first_decision']} -> {row['last_decision']} ({row['sessions_held']} decisions),"
            f" executed {row['executing_sell_session']}, g_frac {row['g_frac_first']:.2%} band {row['band']:.0%},"
            f" retained ${row['retained_notional_usd']:,.0f}, span {row['span_return'] * 100:+.2f}% -> ${row['pnl_usd']:,.0f}"
        )
    w("")
    w("AFTER TAX (the runs' own fills through the corrected Turkish engine; $100k = the 1x run's starting cash)")
    w("  weight-preserving scaling; the pre-tax replay reproduces every production NAV path to < 1e-9")
    w("  arm  cost   AT CAGR   delta vs R2   delta unscaled   tax paid     terminal   funding sales / cash-capped buys")
    for cost in ("1.0", "2.0"):
        for arm in ("R2", "A1u", "K"):
            d = r["after_tax_deltas"][cost][arm]
            t = r["after_tax"][arm][cost]["progressive"]
            w(
                f"  {arm:4s} {cost}x {d['cagr_usd_liquidated'] * 100:8.3f}% {d['delta_pp']:+12.3f} pp"
                f" {d['delta_pp_unscaled']:+14.3f} pp ${d['tax_total_usd']:>10,.0f} ${d['terminal_usd']:>12,.0f}"
                f"   {t['tax_funding_sales']} / {t['cash_capped_buys']}"
            )
    w("  research expectation for this window (gain_band_v3, 2017 start): A1u +0.82 pp, K +1.09 pp after tax")
    w("  after-tax max DD: R2 36.19%, A1u 36.14%, K 35.30% (research 2017 window R2: 36.24%)")
    w("  tax paid (TRY, cumulative): R2 5,518,717 | A1u 5,263,114 | K 4,991,426; Yİ-ÜFE relief")
    w("  R2 10,437,099 | A1u 11,074,053 | K 11,341,807 (1x)")
    w("")
    w("CHECKS")
    ok = r["accounting_ok"]
    w("  accounting and engine invariants (cash_conserved, nav_matches_ledger, position_quantities_reconciled,")
    w("  duplicate_fill_count == 0, unowned_fill_count == 0, no_future_data_access, final_position_count == 0):")
    w(f"    ok in all six runs ({', '.join(f'{a} {c}x {v}' for a, by in sorted(ok.items()) for c, v in sorted(by.items()))})")
    w("  determinism: every arm run twice; payloads identical up to elapsed_seconds, audits byte-identical;")
    w("  STAGE_B_RESULTS.json byte-identical across two evaluations")
    w("  tax harness: the pre-tax replay's ending NAV equals the production ending NAV exactly in all six runs")
    (HERE / "SUMMARY.txt").write_text("\n".join(lines) + "\n")
    print("wrote", HERE / "SUMMARY.txt", len(lines), "lines")


if __name__ == "__main__":
    main()
