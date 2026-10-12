# EWMA vol and resize band v1 (2026-10-12)

**No arm passes the pre-registered gate, so production E2
(`equity_leveraged_trend_v1` 1.1.0) stays unchanged.** No Stage B replay was
run, because Stage B was conditional on a pass.

Two changes were tested on top of production E2 (C0), alone and together:

- **EWMA vol (W1).** W1 replaces vol20 with RiskMetrics EWMA (λ 0.94).
  - It is the closest miss. It gains +0.04 pp CAGR, cuts max DD from 49.6% to
    47.4%, and improves out of sample (median +0.06 pp on six windows; pooled
    bootstrap +0.06 pp).
  - It fails two gates. G3: the 2017+ CAGR is 1.13 pp below C0 (31.31%
    against 32.44%), and the limit was −1 pp. G5: the main block-20 bootstrap
    median of W1 − C0 is −0.07 pp.
  - Most of the 2017+ loss is 2020 (−7.4 pp that year).
- **Wider resize band (B25 / B50 / B100: 2.5 / 5 / 10 pp).** The band cuts
  turnover by 5–16% and resizes by 56–89%. Yet every band value loses CAGR at
  both cost levels (−0.07 to −0.60 pp) and in the bootstrap (median −0.04 to
  −0.16 pp). The trading cost the band saves is smaller than what it loses by
  sizing to stale vol targets. No band value is plateau-eligible.
- **Both together (WB).** EWMA's lower drawdown carries over. The band's
  return loss and W1's 2017+ loss also both carry over. WB loses 0.12–0.47 pp
  CAGR and fails G1, G3, G5 and G6.

Evidence: `research/ewma_band_v1/` and the input copies
`data/historical/ewma_band_v1_{gspc,index,tb3ms}.csv`. They were removed from
main after the evidence commit and are preserved in tag
`archive/research/ewma-band-v1-2026-10`.

- `PREREGISTRATION.md`, `screen.py`, `DATA_MANIFEST.json`, the data copies and
  `E2_INTEGRATION_DEVIATION.json` were committed in `9d2da2c`, before any
  non-control arm was simulated.
- The outputs are `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`.
  The run is offline and takes about 26 s. Two runs gave byte-identical
  `RESULTS.json`.

## Method

- **Control (C0).** C0 is production E2 1.1.0: at least 2 of 3 votes on the
  QQQ price close (close > SMA200; 252-session price return > 0;
  close > 126-session channel midpoint). Size is `vt = min(1, 0.35/vol)`,
  with the total weight capped at 0.98.
- **Mechanics.** The same as `e2_integration_v1`'s deviation screen:
  - The instrument is synthetic QLD\*, daily-reset 2x on QQQ total return.
    Financing is either base (0.95% + DTB3) or calibrated (+0.75%).
  - Decide at the close and trade at the next open. Cost is 4 bps × m.
    Idle cash earns zero.
  - The window is 2000-03-09 → 2026-09-29.
- **EWMA.** Windowed RiskMetrics:
  - At each close, the window is the 252 instrument returns ending there.
  - The variance is seeded with the mean of the window's first 20 squared
    returns. It is then updated as σ² ← 0.94σ² + 0.06r² over the remaining
    232 returns, and vol = √(252σ²).
  - A fixed window keeps the value independent of how much history a
    process holds. It needs 253 closes, the same as E2's
    `minimum_history_bars`.
- **Band.** Entries and exits always trade in full. While the position is
  held and the trend is on, the strategy rebalances to target only when
  |target − held| > B. C0's B is the existing 0.5 pp.
- **Harness.** C0 reproduces `e2_integration_v1`'s production-E2 row exactly
  (difference 0.000000 pp). That covers CAGR and max DD under base and
  calibrated financing, each at m = 1 and m = 2. Two further checks passed:
  - The band simulator at 0.5 pp equals `trend_robustness_v2.simulate_multi`
    bit for bit.
  - The fast EWMA matches a direct per-window computation to within 7·10⁻¹⁶.
- **Gates.** Every gate must pass. "Both" means base and calibrated
  financing.
  - **G1 (m = 2, both):** CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 −
    0.5 pp.
  - **G2 (both):** max DD ≤ C0 + 1 pp.
  - **G3 (both):** 2017+ CAGR ≥ C0 − 1 pp.
  - **G4:** CAGR above C0 in at least 2 of 3 sub-periods: P1 2000–08, P2
    2009–16, P3 2017 → end.
  - **G5 (both):** block-20 bootstrap median of arm − C0 ≥ 0. The bootstrap
    uses seed 20261011 and 5,000 ten-year paths, with draws identical to
    `signal_ensemble_v1`.
  - **G6 (both):** G1 at m = 1.
  - **G7:** six out-of-sample windows: ^GSPC 1957–1999 and
    `e2_confirmation_v1`'s M1–M5, calibrated, m = 2. The median Δ CAGR must
    be ≥ 0. In at least 5 of 6 windows, Δ CAGR must be ≥ −0.5 pp and Δ DD
    ≤ +3 pp. Δ DD must never exceed +8 pp.
  - **G8:** pooled out-of-sample bootstrap over M1–M5 (2,000 paths each)
    with median Δ ≥ 0.
  - **G9:** turnover ≤ C0 for band arms, and ≤ 2 × C0 for W1.
- **Band selection.** A band value counts only if a neighbouring grid value
  also passes G1–G3 (plateau). WB uses the band x\* chosen by that rule. If
  no band qualifies, it falls back to the band with the best CAGR, which here
  is 5 pp.

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD | Calmar | 2017+ | P1 / P2 / P3 | m = 1 CAGR | Boot. median Δ | Turnover/yr |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| **C0 (prod. E2)** | **14.67%** | **14.10%** | **49.6%** | **0.296** | **32.44%** | −1.10 / 13.26 / 32.44 | **14.95%** | — | **5.97** |
| W1 EWMA | 14.71% | 14.14% | 47.4% | 0.311 | 31.31% | −0.16 / 13.38 / 31.31 | 14.98% | −0.071 pp | 5.76 |
| B25 | 14.57% | 14.00% | 49.6% | 0.294 | 32.27% | −1.14 / 13.16 / 32.27 | 14.83% | −0.044 pp | 5.66 |
| B50 | 14.60% | 14.02% | 49.4% | 0.295 | 32.08% | −1.16 / 13.46 / 32.08 | 14.85% | −0.090 pp | 5.42 |
| B100 | 14.12% | 13.56% | 50.3% | 0.281 | 31.69% | −1.25 / 12.42 / 31.69 | 14.35% | −0.163 pp | 5.01 |
| WB25 | 14.55% | 13.98% | 46.8% | 0.311 | 30.90% | −0.17 / 13.30 / 30.90 | 14.80% | −0.117 pp | 5.40 |
| WB50 | 14.53% | 13.94% | 47.8% | 0.304 | 30.89% | −0.32 / 13.44 / 30.89 | 14.76% | −0.166 pp | 5.10 |
| WB100 | 14.20% | 13.64% | 47.1% | 0.301 | 30.16% | −0.18 / 12.94 / 30.16 | 14.41% | −0.338 pp | 4.67 |

Max DD, Calmar, 2017+, sub-periods and the bootstrap use base financing; the
calibrated values are within 0.1 pp of max DD and give the same gate
results. Bootstrap medians are 7.16% for C0 and 7.17% for W1. W1 beats C0 on
46.5% of paths. The band arms beat C0 on 35–39% of paths.

### Out of sample (calibrated, m = 2)

| Window | C0 | W1 | B50 | WB50 |
|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.98% / 39.0% | 9.83% / 39.0% | 9.97% / 39.0% | 9.85% / 39.0% |
| M1 ^IXIC 1975–1999 | 20.68% / 38.6% | 20.50% / 38.7% | 20.64% / 38.2% | 20.44% / 39.0% |
| M2 ^GSPC 1935–1956 | 11.26% / 62.5% | 11.81% / 59.3% | 11.24% / 61.8% | 11.98% / 58.4% |
| M3 ^N225 1966–2026 | 6.33% / 67.2% | 6.46% / 64.6% | 6.43% / 66.7% | 6.48% / 63.3% |
| M4 ^GDAXI 1989–2026 | 9.41% / 58.6% | 9.40% / 59.2% | 9.43% / 58.9% | 9.42% / 58.3% |
| M5 ^FTSE 1985–2026 | −0.05% / 82.7% | 0.08% / 81.9% | −0.07% / 82.8% | 0.10% / 81.8% |
| Median Δ CAGR (6 windows) | — | +0.06 pp | −0.02 pp | +0.08 pp |
| Pooled bootstrap median Δ (10,000 paths) | — | +0.061 pp | +0.018 pp | +0.073 pp |

Every arm keeps Δ CAGR ≥ −0.5 pp and Δ DD ≤ +3 pp in all six windows. G7
fails only for the band-only arms, whose median Δ is slightly negative.

### Gates

| Gate | W1 | B25 | B50 | B100 | WB25 | WB50 | WB100 |
|---|---|---|---|---|---|---|---|
| G1 return (base / cal.) | pass / pass | fail / fail | fail / fail | fail / fail | fail / fail | fail / fail | fail / fail |
| G2 DD ≤ C0 + 1 pp | pass | pass | pass | pass | pass | pass | pass |
| G3 2017+ ≥ C0 − 1 pp | **fail** (−1.13 / −1.10) | pass | pass | pass | fail | fail | fail |
| G4 sub-periods | pass (P1, P2) | fail (0/3) | fail (P2) | fail (0/3) | pass | pass | fail (P1) |
| G5 bootstrap Δ ≥ 0 | **fail** (−0.071 / −0.068) | fail | fail | fail | fail | fail | fail |
| G6 G1 at m = 1 | pass | fail | fail | fail | fail | fail | fail |
| G7 OOS windows | pass | fail | fail | fail | pass | pass | pass |
| G8 OOS bootstrap | pass | pass | pass | pass | pass | pass | pass |
| G9 turnover | pass | pass | pass | pass | pass | pass | pass |
| Plateau | — | fail | fail | fail | fail | fail | fail |
| **Result** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** |

Band x\* = 5 pp, by fallback (WB = WB50). Selected arm: **none**.

## Trading activity (report-only; relevant to Turkish taxation)

These figures use base financing and m = 2. A round trip is a completed
in-market episode. Sells are exits plus downward resizes; each sell is a
realization event.

| Arm | Round trips/yr | Resizes/yr | Sells/yr | Turnover/yr (entries+exits / resizes) | 2017+: round trips / sells / trades per yr | 2017+ turnover/yr |
|---|---:|---:|---:|---|---|---:|
| **C0** | **2.94** | **51.5** | **27.4** | **5.97 (4.27 / 1.70)** | **1.23 / 32.4 / 66.7** | **3.75** |
| W1 | 2.94 | 61.4 | 20.5 | 5.76 (4.25 / 1.52) | 1.23 / 23.8 / 80.5 | 3.57 |
| B25 | 2.94 | 22.8 | 13.7 | 5.66 (4.27 / 1.40) | 1.23 / 14.8 / 31.1 | 3.36 |
| B50 | 2.94 | 13.6 | 9.3 | 5.42 (4.26 / 1.16) | 1.23 / 9.6 / 20.5 | 3.12 |
| B100 | 2.94 | 5.8 | 5.6 | 5.01 (4.25 / 0.76) | 1.23 / 5.3 / 11.2 | 2.75 |
| WB25 | 2.94 | 27.0 | 11.2 | 5.40 (4.23 / 1.17) | 1.23 / 12.4 / 38.2 | 3.14 |
| WB50 | 2.94 | 12.5 | 7.4 | 5.10 (4.22 / 0.87) | 1.23 / 7.9 / 20.0 | 2.80 |
| WB100 | 2.94 | 3.9 | 4.3 | 4.67 (4.20 / 0.46) | 1.23 / 3.6 / 8.6 | 2.30 |

- **The screen's C0 matches production E2's Stage B.** Stage B has 617 fills
  in 9.57 years: 64.5 per year, of which 31.6 are sells. The screen gives
  66.7 trades and 32.4 sells per year since 2017.
- **Production E2 trades almost every session.** In 2017+, 2.2 of its 3.75
  turnover/yr come from vol resizes.
- **Neither change alters entries or exits.** Round trips are identical in
  every arm, because the trend vote is the same.
- **EWMA resizes more often but sells less.** EWMA vol decays a little on
  every calm day, so the target creeps up in many small buys; it jumps on
  shocks, so it sells in fewer, larger steps.

## Findings beyond the gate

- **A wider band costs return rather than saving it.** At m = 2 the trading
  cost the band saves is about 8 bps × 0.3–1.0 turnover/yr ≈ 0.03–0.08 pp/yr.
  Holding a stale weight costs more: when vol falls in a rising market, the
  band delays re-levering. That loss is visible as negative bootstrap means
  and a less than 50% share of paths beating C0, for every band value. A
  band only pays if trading costs are much higher than modelled, or taxes
  are charged per sale (see below).
- **EWMA's advantage is structural on drawdowns, and its cost is 2017+.**
  Over 2000–2026 and out of sample, EWMA lowers max DD (49.6% → 47.4%; M2
  −3.2 pp, M3 −2.6 pp) and wins P1 and P2. Since 2017 it trails by about
  1.1 pp/yr, mostly in 2020 (−7.4 pp) and 2025 (−3.3 pp). That is the same
  pattern `timesfm_v1` found on VT35, so moving the control to E2 did not
  change it.
- **Over the Stage B window, both changes lose CAGR.** For 2017-01-03 →
  2026-07-30 (screen, base, m = 2), CAGR / max DD is:

  | Arm | CAGR | Max DD |
  |---|---:|---:|
  | C0 | 31.42% | 32.5% |
  | W1 | 30.35% | 32.5% |
  | B50 | 31.10% | 32.2% |
  | WB50 | 29.91% | 32.2% |
- **Possible tax angle (not gated, not modelled here).** A 5 pp band cuts
  sells from about 32 to about 10 per year since 2017, at a pre-tax cost of
  about 0.3 pp/yr on that window (0.07 pp over 2000–2026). If the Turkish
  tax treatment taxes each sale's gain at realization, with no loss
  offsetting across years, fewer sells could be worth more than 0.3 pp/yr.
  That question belongs to `after_tax_tr_v1`, and the B_x rows above are
  its inputs.

## Caveats

- **Single history.** EWMA's 2017+ cost was seen in `timesfm_v1` before
  pre-registration.
- **Where the band is checked.** The screen checks the band after the
  overnight move. The production engine checks it at the decision close.
- **OOS data.** The out-of-sample windows are price-only, and many fills
  there are at the previous close.

## What a production change would have required

None is proposed. This is recorded for a future campaign.

- **Band.** It is expressible without strategy code, through the shared
  `execution.rebalance_band_pct`, which is checked per symbol in the backtest
  and live executors.
  - That setting applies to entries and exits too. Since 2000 no in-trend
    target was below 10% (0 sessions), so for bands ≤ 10 pp it equals the
    strategy-level rule.
  - Changing it changes the configuration and live-authorization digests,
    but not the strategy version.
  - It would need a Stage B replay with the new `rebalance_band_pct`, and
    golden/parity tests.
- **EWMA.** It needs a new strategy parameter, for example
  `vol_estimator: ewma` with `ewma_lambda`, `ewma_window` and `ewma_seed`.
  - It means a strategy version bump to 1.2.0, carried by receipts, digest
    and idempotency keys.
  - `minimum_history_bars` stays 253.
  - It would need EWMA unit tests against the research function, a golden
    backtest, and a Stage B replay reproducing 1.1.0 under the default.

## Decision

Production E2 stays unchanged and the campaign is closed. No code,
configuration or runtime changed.
