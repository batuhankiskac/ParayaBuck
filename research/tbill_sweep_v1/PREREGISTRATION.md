# T-bill sweep v1 — pre-registration (2026-10-12)

Written and committed before any arm other than the C0 control was simulated
on market data.

## Seen before writing

- `paper_config_v2`: the BIL sweep S2 (all idle budget, `1 − w`, in BIL on
  every session) added +0.14 pp CAGR at 1x on Stage B 2017–2026 but lost
  0.05 pp at 2x, because every daily vol resize of QLD forced a matching BIL
  trade (turnover 4.24 → 8.71/yr).
- `rule_variants_v1`: F-BIL / F-SGOV (VT35, budget in BIL/SGOV only while the
  SMA trend is off) passed every gate at 1x (+0.26 / +0.29 pp) and missed
  only "wins 2017–2021" at 2x by 0.13–0.14 pp (turnover 4.7 → 8.2/yr) on
  2017-01-03 → 2026-07-30. That is the S1 mechanism on VT35, on one decade.
- `leverage_frontier_v1`: its BILLS risk-off arm failed; `e2_integration_v1`,
  `signal_ensemble_v1`, `e2_confirmation_v1`, `stress_robustness_v1` results
  and gate sets as published in `docs/research/`.
- General history of US T-bill yields (near zero 2009–2015 and 2020–2021,
  4–5% in 2023–2025, high in the 1970s–80s) is common knowledge.
- The only market-data simulation run before this commit is the C0 harness
  (`screen.py --harness`, C0 only): it reproduces `e2_integration_v1`'s
  DEVIATION.json numbers for production E2 (14.67% / 49.6% at m = 2, base
  financing, 2000-03-09 → 2026-09-29; all eight CAGR / max-DD / turnover
  values) with difference 0.000000, and the two-leg simulator with an empty
  T-bill leg equals the archived single-leg engine bit for bit. The
  simulator's T-bill and cash code paths were exercised only on random
  synthetic toy series (no market data).

## Question

Production E2 is in cash about a quarter of sessions and idle cash earns
zero. Does holding a T-bill ETF **only while the trend is off** add return,
net of costs, without `paper_config_v2`'s turnover problem? Secondary: does
also sweeping the residual `1 − w` while the trend is on, re-sizing the bill
leg only when it leaves a wide band, add more?

## Data (all offline, byte copies of tagged files; SHA-256 in DATA_MANIFEST.json)

- Main: `data/historical/stress_robustness_v1_daily.csv` and
  `stress_robustness_v1_dtb3.csv` (already on main).
- OOS (gated): `data/historical/tbill_sweep_v1_gspc.csv` = `signal_ensemble_v1_gspc.csv`
  (tag `archive/research/signal-ensemble-v1-2026-10`).
- OOS (report only): `tbill_sweep_v1_index.csv`, `tbill_sweep_v1_tb3ms.csv`
  = `e2_confirmation_v1_{index,tb3ms}.csv` (tag `archive/research/e2-confirmation-v1-2026-10`).
- Real ETF check (report only): `tbill_sweep_v1_etf_{daily_split_sip,corporate_actions}.csv`
  = `research/rule_variants_v1/data/rule_variants_v1_*` (tag
  `archive/research/trend-robustness-v2-2026-10`): Alpaca SIP split-adjusted
  BIL (2016-01-04 → 2026-07-31) and SGOV (2020-05-28 →) daily bars and
  dividends restated to post-split shares.
- Engine: `vendor/trend_robustness_v2_screen.py`, byte copy (blob c9637ee) of
  `research/trend_robustness_v2/screen.py` from tag
  `archive/research/trend-robustness-v2-2026-10`; its `main()` is never run.

## Strategy and mechanics (unchanged from production E2 research model)

- Votes on QQQ split-adjusted price close: close > SMA200; 252-session price
  return > 0; close > midpoint of the 126-session max/min. E2 on when ≥ 2.
- QLD\* = daily-reset 2x of QQQ total return, 0.95%/yr expense, financing
  DTB3 (base) or DTB3 + 0.75% (calibrated).
- QLD\* weight `w* = min(0.98, [E2 on] · min(1, 0.35 / vol20(QLD*)))`.
- Decide at close t−1, fill at open t; a leg trades to its target when it
  drifts > 0.5 pp; cost 4 bps × m per unit traded on **every** leg (T-bill
  trades included; conservative for a ~1 bp-spread ETF); m = 2 primary,
  m = 1 also gated. Cash earns zero unless an arm says otherwise.

## T-bill instrument

- **BIL\*** (gated): synthetic; session i accrues `(DTB3[i−1] − 0.1356%)/252`
  between its open and close, open = previous close (holding from open t to
  open t+k earns k sessions). Can be negative when DTB3 < 0.1356%.
- **SGOV\*** (report only): the same with 0.09%.
- Check (report only): real BIL total return (bars + dividends on the
  ex-date open) vs BIL\* over 2017-01-03 → 2026-07-30, real SGOV vs SGOV\*
  over 2020-06-01 → 2026-07-30, and C0/S1 re-run with the real ETF as the
  bill leg on those windows.

## Arms

- **C0:** production E2, idle cash at zero (control).
- **S1:** C0 plus a bill leg at 0.98 on every session whose decision close
  has E2 off, 0 otherwise (bills sold on the open of the session after E2
  turns on; QLD and bills trade at the same open). Bill leg band 0.5 pp.
- **S2_5 / S2_10:** S1 while E2 is off. While E2 is on, the bill leg is kept
  inside `[0.98 − w* − X, 0.98 − w*]` and reset to `max(0, 0.98 − w* − X/2)`
  when it leaves that range; X = 5 pp / 10 pp. Total target never exceeds
  0.98.
- Report only:
  - **S1_SGOV:** S1 with SGOV\*.
  - **A_OFF:** Alpaca-style opt-in cash sweep: all cash earns
    `max(0, DTB3 − 0.5 pp)` on trend-off sessions only, zero trading.
  - **A_ALL:** the same on all idle cash every session.

## Windows

- Main: 2000-03-09 → 2026-09-29 (first session with the 252-session warm-up),
  base and calibrated financing, m = 1 and m = 2.
- 2017+: 2017-01-03 → 2026-09-29 (`curve_stats` 2017_end segment).
- OOS (gated, T6): ^GSPC 1957-01-02 → 1999-12-31, opens := previous close,
  DTB3 forward-filled, calibrated financing, m = 2 (signal_ensemble_v1 G8 setup).
- OOS (report only): e2_confirmation_v1 markets M1 ^IXIC 1975–1999, M2 ^GSPC
  1935–1956 (TB3MS before 1954), M3 ^N225, M4 ^GDAXI, M5 ^FTSE (US T-bills as
  a USD proxy for the non-US indices), calibrated, m = 2.
- Bootstrap: stationary block bootstrap, 5,000 paths, seed 20261011, mean
  block 20 (gated), 60 and 250 (report), 250 + 3 warm-up + 2,520 measured
  sessions, path draws identical to `signal_ensemble_v1`; signals on the
  resampled price level (no dividends), QLD\* on the resampled total-return
  level, bills on the resampled DTB3; m = 2; base and calibrated financing.

## Gates (each candidate; T1–T5 and T7 under both financing models)

| Gate | Rule |
|---|---|
| T1 | main-window CAGR at m = 2 ≥ C0 |
| T2 | main-window CAGR at m = 1 ≥ C0 |
| T3 | main-window max DD at m = 2 and m = 1 ≤ C0 + 0.5 pp |
| T4 | 2017-01-03 → end CAGR at m = 2 ≥ C0 |
| T5 | block-20 bootstrap: median over paths of CAGR(arm) − CAGR(C0) ≥ 0 (m = 2) |
| T6 | OOS ^GSPC 1957–1999: CAGR ≥ C0 and max DD ≤ C0 + 0.5 pp |
| T7 | main-window turnover/yr at m = 2 (all legs) ≤ 2 × C0 |

An arm passes when every gate passes. Harness first: if C0 does not
reproduce DEVIATION.json to 0.01 pp or the engine identity fails, the run
stops before any arm.

**Selection:** S1 if it passes. A passing S2 arm replaces it only if its base
m = 2 main-window CAGR beats S1's by ≥ 0.10 pp (premium for daily
residual-leg operations). If S1 fails, the passing S2 arm with the highest
base m = 2 CAGR. Selection does not change production; production
integration is a separate step.

## Stage B (only if an arm is selected)

Production-engine shared-ledger replay, 2017-01-03 → 2026-07-30, $100k, the
archived `e2_integration_v1` runner adapted inside this folder:

- Control: E2 (1.1.0) at 1x must reproduce NAV 1,329,642.62.
- Sweep arm: the same strategy wrapped in a research-only `decide()` shim
  that adds BIL at 0.98 when the E2 target has no QLD weight because the trend
  is off (selected S1); real BIL bars and dividends from
  `tbill_sweep_v1_etf_*`; BIL added to the arm config's equity universe.
  No production code changes.
- 1x and 2x costs; at most two runs in parallel. Reported, not gated:
  CAGR, max DD, turnover, fills, BIL traded notional, accounting invariants.
  If S2 were selected, Stage B would use the same shim with the S2 rule.

## Report-only

Calendar-year and segment differences (ZIRP 2009–2015, 2020–2021, 2022–2026),
bill trades and turnover, share of sessions in bills, bootstrap blocks 60/250,
the e2_confirmation markets, real ETF tracking, Alpaca sweep arms, and the
production-integration requirements.
