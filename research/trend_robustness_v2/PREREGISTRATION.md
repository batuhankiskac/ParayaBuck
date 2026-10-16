# Trend robustness v2 — pre-registration (2026-10-11)

Written before any arm below was simulated. Seen before writing: the
`stress_robustness_v1` results (VT35 on synthetic QLD, 1999-12 → 2026-09) and
the coverage of `data/historical/stress_robustness_v1_daily.csv` (QQQ from
1999-03-10, SPY from 1999-01-04, real QLD from 2006-06-21, VOO from
2010-09-09, all to 2026-09-29). No signal, return or drawdown of E1, M1 or M2
was computed.

## Question

VT35 depends on one parameter (200-session SMA) and one index (Nasdaq-100).
SMA-length and check-cadence variants move QLD-trend CAGR between about 17%
and 32% (`profit_max_v1`), and VT35 loses to SPY in most short-persistence
block-bootstrap paths (`stress_robustness_v1`). Does an SMA ensemble, a second
index, or both, make the rule more robust without giving up long-run profit?
The owner's goal is maximum long-run profit above index funds and interest.

## Data

Only the committed files `data/historical/stress_robustness_v1_daily.csv` and
`stress_robustness_v1_dtb3.csv` (SHA-256 in
`research/stress_robustness_v1/DATA_MANIFEST.json`). No download, no network.

## Instruments

- **QLD\*** synthetic: daily-reset 2x on QQQ total return,
  expense 0.95%/yr + DTB3 financing — exactly `stress_robustness_v1`'s
  `synthetic_2x`.
- **SSO\*** synthetic: the same construction on SPY total return with SSO's
  expense ratio 0.89%/yr + DTB3. Not calibrated; reported tracking checks
  only (no real SSO bars exist in the data).
- All arms, including the control, trade the synthetic instruments, so arms
  are compared on identical construction.

## Arms (four, no others)

All weights are fractions of NAV. `vt(vol) = min(1, 0.35 / vol)` where `vol`
is the annualized sample volatility of the 20 most recent daily returns of the
held instrument (as VT35). `trend_n(X) = 1` when X's completed close is above
its n-session SMA, else 0. The total target is capped at the allocation
0.98 by scaling the legs proportionally if the sum exceeds 0.98 (cap, never
multiply up).

- **C0 — VT35 control.** QLD\* at `trend_200(QQQ) · vt(vol_QLD*)`.
- **E1 — SMA ensemble.** `s = mean(trend_n(QQQ) for n in {100, 150, 200, 250})`;
  QLD\* at `s · vt(vol_QLD*)`.
- **M1 — two indices.** QLD\* at `0.5 · trend_200(QQQ) · vt(vol_QLD*)` plus
  SSO\* at `0.5 · trend_200(SPY) · vt(vol_SSO*)`.
- **M2 — ensemble on two indices.** As M1 with each leg's `trend_200` replaced
  by that leg's ensemble score `s` over {100, 150, 200, 250}.

## Mechanics (unchanged from `stress_robustness_v1`)

Signal on the completed close, trade at the next open, rebalance all legs to
target when any leg's weight drifts more than 0.5 pp from its target, cost
`4 bps × m` per unit of traded notional, idle cash earns zero. Dividends are
in the total-return construction.

**Common window.** The measured window starts at the first session at which
every arm has full warm-up (250 prior QQQ and SPY closes and 20 returns of
both synthetic instruments) and ends at the last common bar. All gated
numbers use this window. C0 is also reported on the `stress_robustness_v1`
window as a harness check: it must reproduce that file's VT35 CAGR and max DD
to 0.01 pp at the same scale/cost before any arm result is read.

## Statistics

Full-window CAGR, max drawdown, Calmar (CAGR / max DD), Sharpe (daily,
annualized, rf = 0), annual turnover, number of trades, worst calendar year,
2000–2002 and 2007–2009 total return and max DD, longest underwater period,
and CAGR on 2017-01-03 → end. SPY total-return buy-and-hold at 1.0 is the
benchmark.

**Block bootstrap.** Stationary block bootstrap (mean block 20 sessions) of
whole historical sessions, resampling the QQQ and SPY overnight/intraday
returns, dividends and DTB3 of the same session jointly; 5,000 paths, seed
20261011; each path a 250-session warm-up plus 2,520 measured sessions. Every
arm and SPY run on the same paths. Reported: median CAGR, 5th percentile
CAGR, median max DD, and the share of paths where the arm's CAGR exceeds
SPY's.

## Decision rule (fixed now)

At **2x costs (m = 2)** on the common window, an arm passes iff all hold:

- **G1** CAGR ≥ C0 CAGR − 0.5 pp.
- **G2** max DD ≤ C0 max DD.
- **G3** CAGR > C0 CAGR, **or** Calmar ≥ 1.10 × C0 Calmar.
- **G4** 2017-01-03 → end CAGR ≥ C0's − 2.0 pp.
- **G5** bootstrap median CAGR ≥ C0's **and** share of paths beating SPY ≥ C0's.
- **G6** G1–G3 also hold at 1x costs (m = 1).
- **G7** annual turnover ≤ 2 × C0's.

Selection: among passing arms, the highest full-window CAGR at 2x costs
(tie: higher Calmar). If no arm passes, **VT35 stays unchanged** and the
campaign closes.

A passing arm is **not** promoted by this campaign. Promotion would need a
separate pre-registered step: production implementation, a Stage B replay on
the production engine against VT35, and parity tests. No parameter in this
file is tuned after results are seen; no fifth arm is added.
