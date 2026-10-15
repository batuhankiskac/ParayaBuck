# deriv_vote_v1 pre-registration (2026-10-15)

Committed **before** any candidate arm (C1–C4) was simulated on real data. Only the
synthetic-price smoke test (`screen.py --smoke`, no real data) and the control-only
`--harness` mode (C0 only: production R2 against its published anchors) ran before this
commit.

## Question

Idea **I2** of `docs/research/idea_survey_v3.md`: replace the *functional form* of the E2
trend vote with **derivative** votes (moving-average crossover, OLS log-price slope, rising
channel midpoint, risk-adjusted trend hurdle), leaving production **R2**'s half-weight
early re-entry path untouched.

Every trend vote in the repo's record is a *level* comparison (a price level against a
level-derived threshold: `close vs SMA n`, `n`-session return above a fixed hurdle,
`close vs the 126-session max/min midpoint`). A derivative vote flips only when the trend
*turns*, which is rarer; rarer flips mean fewer resizes, fewer FIFO lots consumed, and a
smaller progressive Turkish tax base — at a cost in lag. The survey's prior is
−0.4 … +0.5 pp pre-tax on form alone and **+0.2 … +0.9 pp after tax**, because 91 % of R2's
realized taxable gain comes from vol-driven trims rather than from trend exits.

Verdict rule: a candidate is worth recommending **only** if it passes every gate in §Gates
below (pre-tax G1–G9, the 21-instrument gate I, all three after-tax windows, B1-recent and
B2). The survey's §6 caveat 1 offers a two-tier "exposure-neutral" relaxation for arms whose
effect is measured in realizations (I3, I5) — **this study does not use it**: C1–C4 change
*when the position is held*, not only how often it is traded, so the full G1–G9 bar
(including the pre-tax bootstrap G5) applies. This decision is made before any result.

## Control C0 = production R2 exactly

`equity_leveraged_trend_v1` 1.2.0 on the QQQ price close (the harness's synthetic QLD\*
instrument):

- **Vote (E2):** at least 2 of 3 — `close > SMA200`; 252-session price return > 0 (zero
  hurdle); `close >` the midpoint of the 126-session close max/min.
- **Size:** `vt = min(1, 0.35/vol20)` of the held instrument's realized vol, total weight
  capped at 0.98.
- **Early re-entry path (R2's, half weight):** while the vote is **off**, hold
  `0.5 × vt` if `close > SMA50` **and** `vol20 <` its own trailing 252-session median
  **and** `close > close[-20]`; exit the early position when `close < SMA50`; the early path
  ends the moment the vote turns on (the position then becomes plain full production).
- **Execution:** decide at the completed close, fill at the next open, 0.5 pp resize band
  (entries and exits always trade in full), cost 4 bps × m per unit of traded notional,
  idle cash earns zero.

## Arms (5; every parameter fixed here)

Vote components are computed on the QQQ price close. `SMA n` is the mean of the `n` closes
ending at `t` (inclusive), and every comparison is **strict**; before a component's window
is full the component is **off** (False), exactly as production's `sma_up`/`tsm_up` treat
their warm-up.

| key | component (on when) |
|---|---|
| `sma200` | `close[t] > SMA200(t)` — production |
| `tsm252` | `close[t] / close[t-252] − 1 > 0` — production (zero hurdle) |
| `don126` | `close[t] >` midpoint of the 126 closes ending at `t` — production |
| `cross` | `SMA50(t) > SMA200(t)` (golden cross) |
| `slope252` | OLS slope of `log(close)` on `x = 0..251` over the 252 closes ending at `t` **> 0** |
| `midrise126` | midpoint of the 126 closes ending at `t` **>** the same midpoint 21 sessions earlier |
| `tsv` | `[close[t]/close[t-252] − 1] / vol252(t) > 0.25`, where `vol252(t)` = sample stdev (ddof = 1) of the 252 daily **log** returns ending at `t`, annualised (× √252); off when `vol252 = 0` or non-finite |

| Arm | votes (all must be read at `t`) | quorum | note |
|---|---|---|---|
| **C0** | `sma200`, `tsm252`, `don126` | ≥ 2 of 3 | production R2 — the control |
| **C1** | `cross`, `tsm252`, `don126` | ≥ 2 of 3 | golden cross (survey's C1) |
| **C2** | `cross`, `slope252`, `midrise126` | ≥ 2 of 3 | all derivative (survey's C2) |
| **C3** | `sma200`, `cross`, `slope252`, `midrise126` | ≥ 2 of 4 | mixed quorum (survey's C3) |
| **C4** | `sma200`, `tsv`, `don126` | ≥ 2 of 3 | risk-adjusted trend hurdle 0.25 (survey's C4) |

These are exactly the four arms of `idea_survey_v3.md` I2 with the survey's parameters
(`0.25` hurdle, 21-session lag, 252-session slope window, 2-of-4 quorum). No other arm is
run and no parameter is tuned after seeing a result.

**Early path (identical in all five arms).** Same SMA 50, same trailing 252-session median
of `vol20`, same 20-session momentum lag, same half weight, same exit rule. The only
freedom is *which* vote the path hangs off: the early path is active while **the arm's own
vote** is off, and it ends the moment **the arm's own vote** turns on. That is the direct
reading of the survey's "R2's early path unchanged": the early path's own rules are
untouched, and the "while E2 is off" condition refers to the trend vote the arm runs
(C0's vote *is* E2, so C0 reproduces R2 bit for bit). The alternative reading — gate the
early path on production E2's vote while the main position follows the arm's vote — would
let the early path be active while the arm's vote is on, which needs a new combination rule;
it is **not** run here and is recorded as a documented, unrun alternative.

## Data

Offline byte copies only (SHA-256 in `DATA_MANIFEST.json`); no network access was used.

- Main window: 2000-03-09 → 2026-09-29 (6,679 sessions), synthetic daily-reset 2× QLD\* on
  QQQ total return, 0.95 %/yr expense, financing DTB3 (base) or DTB3 + 0.75 pp (calibrated).
- Out of sample (gate G7/G8): ^GSPC 1957-01-02 → 1999-12-31 and `e2_confirmation_v1`'s
  M1 ^IXIC 1975-1999, M2 ^GSPC 1935-1956, M3 ^N225 1966-2026, M4 ^GDAXI 1989-2026,
  M5 ^FTSE 1985-2026, calibrated financing, `m = 2`.
- Gate I: the 21 instruments of `fast_reentry_v2` (19 `range_vol_v2` sector/country ETFs +
  SPY, DIA, IWM from `range_vol_v1`; **EWC is excluded** by `range_vol_v2`'s quality rule —
  > 5 % of bars with `high == low` or an invalid open — before any simulation).
- After tax: USD/TRY (CBRT buying) and Yİ-ÜFE from the `vt_level_tax_v1` inputs; the B1/B2
  bootstrap keeps the **real** 2015-09-01 → 2026-09-30 calendar (and its FX) fixed while
  resampling market blocks.

## Mechanics (unchanged from the harness)

Signal on the price close, instrument on the synthetic total-return leg; `t` decides,
`t+1` fills; 0.5 pp band; cost 4 bps × m; main-window `m = 2` gated (`m = 1` reported,
`m = 4` report-only cost stress). Main block-20 bootstrap: seed 20261011, 5,000 paths of
10 years, draws identical to `signal_ensemble_v1`. OOS bootstrap: 2,000 paths per market,
same seed/block. After-tax: $100k, base financing, `m = 2`, liquidated at the window end,
progressive 2026 tariff with Yİ-ÜFE indexation and FIFO lots; B1-recent = 1,000 block-20
paths of 10 years on the fixed 2015-09 calendar (progressive), B2 = the same paths under a
20 % flat tax with loss carry-forward.

## Harness (must pass before `--run`; it stops otherwise)

1. C0 (E2 with no early path) reproduces `e2_integration_v1`'s production-E2 deviation row
   (CAGR and max DD, base/calibrated × m = 1/2) to 0.01 pp.
2. C0's vote from the component machinery equals production `e2_up`; C0's targets equal
   production's `wants_for` where the vote is on and `0.5 × vt` / flat where it is off.
3. C0 reproduces R2's main-window rows from `research/fast_reentry_v1/RESULTS.json`
   (CAGR, max DD, turnover; base/calibrated × m = 1/2) to 0.01 pp — this is what makes
   C0 = production R2.
4. The 0.5 pp band simulator equals `trend_robustness_v2.simulate_multi` bit for bit, and
   the fast rolling-median helper equals the v1 loop reference bit for bit.
5. Each vote component equals a direct recomputation at 64 sampled sessions
   (`cross`/`sma200` against means, `slope252` against `np.polyfit`, `midrise126` against
   the max/min midpoints and their 21-session lag, `tsv` against `std(ddof=1)×√252`,
   `tsm252`/`don126` against their definitions).
6. C0's after-tax anchors: 10.16 / 14.71 / 22.89 % (three windows, $100k, liquidated) and
   the clean bootstrap medians B1 3.5155 % / B2 3.9678 %, all to 0.01 pp.

## Gates

Pre-tax, all against C0 = production R2 (`ewma_band_v1`'s set, unchanged; "both" = base and
calibrated financing, `m = 2` unless stated):

- **G1:** CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 − 0.5 pp.
- **G2:** max DD ≤ C0 + 1 pp.
- **G3:** 2017+ CAGR ≥ C0 − 1 pp.
- **G4:** CAGR above C0 in ≥ 2 of 3 sub-periods (P1 2000–08, P2 2009–16, P3 2017→end).
- **G5:** main block-20 bootstrap median of arm − C0 ≥ 0.
- **G6:** G1 at `m = 1`.
- **G7:** six OOS windows: median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in ≥ 5 of 6; Δ DD ≤ +3 pp in
  ≥ 5 of 6 and never > +8 pp.
- **G8:** pooled OOS bootstrap (M1–M5, 2,000 paths each) median Δ ≥ 0.
- **G9:** turnover/yr ≤ 2 × C0.
- **G_I (21 instruments):** each instrument runs its own C0 (E2 + R2's early path on that
  instrument, own synthetic 2× leg, calibrated, `m = 2`) and the arm's vote on that
  instrument's closes; gate passes when median Δ CAGR ≥ 0 **and** share of instruments with
  Δ CAGR > 0 ≥ 60 % **and** share with Δ DD ≤ +3 pp ≥ 80 % **and** the pooled bootstrap
  (2,000 paths per instrument, seed 20261011) median Δ ≥ 0.

After tax ($100k, base financing, `m = 2`, liquidated, progressive 2026 tariff):

- **T-2000 / T-2010 / T-2017:** candidate after-tax CAGR > C0's in **each** of the three
  windows (2000-03-09→, 2010-01-01→, 2017-01-03→ 2026-09-29).
- **B1 / B2:** clean bootstrap median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 55 %, in both
  tax models.

## Selection rule

`selected` = the candidate passing every gate above with the highest base `m = 2` CAGR. If
none passes, `selected = none` and production R2 stays unchanged. Nothing is re-tuned.

## Report-only (not gated)

Vote flips/yr (on-flips by direction, full window and 2017+); sells/yr and trade counts;
the share of the realized taxable TRY gain booked by trims (`resize`) versus exits (`exit`,
the down-trade to a zero target), tax-funding sales and the final liquidation, on the 2000
window; per-episode P&L of the early path; leave-one-episode-out on the three largest
episodes; `m = 4` cost stress; per-instrument Δ CAGR/Δ DD detail.

## Amendments

(After the pre-registration commit; bug fixes and reporting additions only. None may change
an arm parameter, a gate or the selection rule.)
