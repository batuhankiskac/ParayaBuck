# fast_reentry_v2 pre-registration (2026-10-14)

Committed **before** any non-control arm was simulated on real data (only a synthetic-price
smoke test of the run path ran before this commit).

## Question

`fast_reentry_v1` (committed on this branch, report `docs/research/fast_reentry_v1.md`)
selected **R2**: production E2 plus an early re-entry path at half production weight
(enter while E2 is off if QQQ close > SMA50, QQQ 20-session vol < its trailing 252-session
median, and close > close 20 sessions ago; exit if close < SMA50; the path ends when E2's
own vote turns on). R2 passed all v1 gates (+0.81 pp base / +0.80 pp calibrated CAGR,
max DD 46.8% vs 49.6%, bootstrap median Δ +0.68 pp) but its out-of-sample median Δ was only
+0.07 pp and 95% of the gain came from 3 episodes (2001-10-31, 2002-11-04, 2023-01-20).

Two questions, asked before looking at any v2 result:

1. **Plateau (gate P).** Is R2's in-sample edge a property of the rule, or of the exact
   parameters (SMA 50, vol-median window 252, weight 1/2)? Tested by 6 one-parameter
   neighbours of R2.
2. **Independent instruments (gate I).** Does the E2+R2 rule, applied to 21 other equity
   instruments with their own signal and their own synthetic 2x leg, still add return over
   its own E2 control? This is the independent-instrument test of
   `range_vol_v2` (tag `archive/research/range-vol-v2-2026-10`), whose quality rule drops
   `EWC`.

Verdict: **CONFIRMED** iff gate P and gate I both pass (and the harness checks below pass).

## Control C0

Production E2 1.1.0 exactly: 2 of 3 votes on the instrument's own adjusted close
(close > SMA200; 252-session price return > 0, zero hurdle; close > 126-session max/min
midpoint); size `min(1, 0.35/vol20)` on the synthetic 2x leg, total cap 0.98; decide at the
completed close, fill at the next open; 0.5 pp resize band; cost 4 bps × m per unit traded.

## Arms (8; every parameter fixed here)

`spec = (SMA, vol-median window, early weight)`. All early-path arms share the v1 mechanics:
while E2 is off, enter if `close > SMA_n` **and** `vol20 < its own trailing MED-window
median` **and** `close > close[−20]`; exit if `close < SMA_n`; the path ends (and the
position becomes plain production) the moment E2's own vote is on. Weight of the early
position = `w × vt(vol20)` (production vol sizing), i.e. fractional production weight.

| Arm | SMA | MED | w | note |
|---|---:|---:|---:|---|
| C0 | — | — | — | production E2 control |
| **R2** | 50 | 252 | 1/2 | v1's selected arm, exactly |
| R2_s40 | 40 | 252 | 1/2 | plateau neighbour |
| R2_s60 | 60 | 252 | 1/2 | plateau neighbour |
| R2_m126 | 50 | 126 | 1/2 | plateau neighbour |
| R2_m504 | 50 | 504 | 1/2 | plateau neighbour |
| R2_w13 | 50 | 252 | 1/3 | plateau neighbour |
| R2_w23 | 50 | 252 | 2/3 | plateau neighbour |

The 6 neighbours each move exactly one parameter off R2. They are parameter neighbours
required by the pre-registered plateau test, not independent candidate strategies; no other
arm is run and no parameter is tuned after seeing results.

## Data

- Main window 2000-03-09 → 2026-09-29 (6,679 sessions), QQQ/SPY/QLD bars + DTB3 from
  `stress_robustness_v1_daily.csv` / `stress_robustness_v1_dtb3.csv`, as in
  `trend_robustness_v2` / `ewma_band_v1`. Synthetic QLD* = daily-reset 2x QQQ total
  return − 0.95%/yr expense − DTB3 (base) or − (DTB3 + 0.75%) (calibrated), 1-D
  bit-identical to `leverage_frontier_v1.synthetic_L(L=2, spread)`.
- Out-of-sample windows: `^GSPC` 1957-01-02 → 1999-12-31 and `e2_confirmation_v1`'s
  M1–M5 (^IXIC 1975–99, ^GSPC 1935–56, ^N225, ^GDAXI, ^FTSE), calibrated, m = 2.
- **Instruments (21, gate I).** The 19 `range_vol_v2_*` symbols minus `EWC` plus the 3
  `range_vol_v1_*` symbols (SPY, DIA, IWM): XLB XLE XLF XLI XLK XLP XLU XLV XLY, EWJ EWG
  EWU EWA EWH EWS EWL, EFA EEM, SPY DIA IWM. Each instrument keeps its own adjusted close
  (adjusted open = raw open × adjclose/close) and gets **its own** E2 vote, **its own** R2
  early path and **its own** 2x daily-reset synthetic leg (calibrated financing +0.75%,
  expense 0.95%/yr, m = 2, 4 bps cost). The `range_vol_v2` quality rule
  (high == low, or missing/invalid open, in > 5% of bars) is applied to every instrument
  **before** any simulation; `EWC` is known to fail it (5.6% bad bars) and is excluded. Any
  other instrument failing the rule would be excluded as well and named in RESULTS.json.
- After-tax (report only): FX (`after_tax_tr_v1_usdtry_cbrt.csv`) and Yi-UFE
  (`after_tax_tr_v1_yiufe.csv`) inputs, restored from branch `research/vt_level_tax_v1`
  with SHA-256 verified against its `DATA_MANIFEST.json`.
- All inputs are recorded with source, date range and SHA-256 in `DATA_MANIFEST.json`.

## Mechanics

Identical to `fast_reentry_v1` / `ewma_band_v1`: decide at the completed close, trade at
the next open, cost 4 bps × m per unit of traded notional, 0.5 pp resize band, idle cash
earns zero, no shorting, no borrow.

## Gates

Pre-registered v2 gates (the verdict):

- **Gate P (plateau, main window, base financing, m = 2).**
  (a) at least 5 of the 6 neighbours have CAGR > C0's CAGR;
  (b) **no** neighbour has max DD > C0's max DD + 1 pp.
- **Gate I (independent instruments, calibrated financing, m = 2).** For each passing
  instrument, Δ = (its own E2+R2 arm) − (its own E2 control), same instrument, same
  synthetic leg:
  (a) median Δ CAGR ≥ 0;
  (b) share of instruments with Δ CAGR > 0 ≥ 60%;
  (c) share of instruments with Δ max DD ≤ +3 pp ≥ 80%;
  (d) pooled stationary block-20 bootstrap over all instruments (2,000 paths per
  instrument, seed 20261011, length `252 + 2520` sessions, `SMA_MAX + 1 + EXT` warm-up,
  identical draw machinery to `fast_reentry_v1.boot_oos_market`) median Δ CAGR ≥ 0.

Additionally reported (v1 confirmation, not part of the verdict): the unchanged
`ewma_band_v1` gates G1–G9 for all 8 arms, and the reproduction of v1's C0 and R2 rows.

Harness checks that must pass before anything else (a failure aborts the run):
C0 reproduces `e2_integration_v1`'s production-E2 deviation row to 0.01 pp (base/calibrated
× m = 1/m = 2); the band simulator at 0.5 pp equals `trend_robustness_v2.simulate_multi`
bit for bit; the fast rolling-median helper equals the v1 loop reference bit for bit; and
the recomputed C0 / R2 main-window rows equal `research/fast_reentry_v1/RESULTS.json` to
0.01 pp (CAGR base/calibrated, m = 1/m = 2, max DD, turnover).

## Selection rule

No production decision is taken here. If gates P and I pass, the verdict is CONFIRMED and
R2 stands as a candidate for a later Stage B replay; otherwise the verdict is FAIL and R2's
v1 pass is read as a 3-episode artefact.

## Report-only analyses (fixed in advance, not gated)

1. **Leave-one-episode-out** on the main window (base, m = 2): for each of R2's 3 largest
   early episodes (by Δ vs C0), re-simulate R2 with that episode's decisions replaced by
   C0's (the episode treated as flat), and report the CAGR Δ against C0 that remains.
2. **After tax** at $100k, Turkish regime, corrected `vt_level_tax_v1` engine: pre-tax and
   progressive after-tax CAGR on 2000-03-09 → 2026-09-29, 2010-01-01 → end and
   2017-01-03 → end for C0 and R2.
3. Per-episode P&L of the early positions (as v1), trade counts and cost stress m = 4.

## Amendments

Anything changed after this commit is recorded in an "Amendments" section of the report,
with the reason; gates, arms and parameters are never changed.
