# range_vol_v2 audit and out-of-sample test

## Verdict

**PASS — arm `G3x` (Parkinson 20-session vol, expanding-scaled)** passes every gate
against both C0 and C0q, including the fixed independent ETF OOS and plateau gates.
`G1x` (Yang-Zhang 20) passes v1-style gates and the bootstrap but fails the new
ETF-instrument test (only 10 of 18 instruments positive; threshold 11). Selection
rule: `G3x` by higher G8' median Δ vs C0 (+0.201 pp vs +0.171 pp).

## Audit of range_vol_v1

- **Look-ahead:** signal uses only prices through t. Range estimators use OHLC
  through t. The `G3x` expanding scaling ratio uses cumulative statistics through
  `t-1` for session `t`. No `t+1` data is used. OOS windows similarly.
- **OHLC adjustment:** high/low/open are scaled by `adjclose/close`; close is
  `adjclose`. Consistent with total-return adjustment.
- **Expanding ratio (G3x):** cumulative close-to-close and Parkinson means are
  taken over sessions `1..t-1`, so no look-ahead. The first 20-40 sessions have
  unstable variance but do not use future data.
- **Yang-Zhang bug (Amendment A1):** v1's `range_vol_yz` produced all-NaN vol
  because `_rvar(on, n)`'s first window contained the leading NaN overnight
  return; `cumsum` propagated NaN. Consequently v1's stored "G1x" result was not
  produced by a live Yang-Zhang estimate. v2 fixes the alignment so YZ is valid
  from session 20. After the fix, Yang-Zhang recovers the previously claimed
  main-window numbers (14.80% CAGR / 46.8% DD) but fails the independent ETF
  instrument share gate.
- **G3x OOS in v1** used plain Parkinson, not the scaled estimator. v2 uses the
  scaled estimator on every window and ETF.
- **v1 data-quality rule** was implemented as a simple high==low / missing-open
  check, narrower than the pre-registered text. v2 uses the full rule
  (high==low or invalid open) and excludes only `EWC`.
- **Warm-up / start equality:** main start remains 2000-03-09 (session 253).
  Volume and vol estimates are NaN for the first 20-40 sessions, so early vol
  targeting is neutral/full exposure until the first complete window; C0 and the
  new controls share the same date window.
- **Selection rule:** v1 had no pre-registered tie/selection rule; v2 pre-registers
  nearest-neighbour plateau and G8'-median selection.

## Basis confound

`C0q` (2 × close-to-close vol20) is essentially identical to C0 on both main CAGR
and bootstrap (difference 0.0002 pp CAGR; bootstrap deltas differ by <0.003 pp).
The range estimators' gains are therefore range information, not the 2 × basis.

## Main window (2000-03-09 to 2026-09-29, 6679 sessions)

| Arm | CAGR (base) | max DD | 2017+ CAGR | turn/yr | boot20 med Δ vs C0 |
|---|---:|---:|---:|---:|---:|
| C0 | 14.67% | 49.56% | 32.44% | 5.97 | 0.000 pp |
| C0q | 14.68% | 49.56% | 32.44% | 5.97 | 0.000 pp |
| **G1x (YZ 20)** | 14.80% | 46.84% | 31.66% | 5.44 | +0.411 pp |
| **G3x (Parkinson scaled 20)** | 14.98% | 48.50% | 32.72% | 5.39 | +0.275 pp |

Calibrated financing: C0 14.10%, G1x 14.23%, G3x 14.40%. Plateau neighbours
(YZ 15/30 and Parkinson-scaled 15/30) all beat C0 on base CAGR; reported in
RESULTS.json.

## Independent ETF OOS (18 quality-passing instruments)

`EWC` failed the fixed quality rule (5.6% invalid/flat bars); 18 pass. Each
instrument carries its own E2 votes, its own 2× daily-reset synthetic leg, and
its own OHLC vol estimate. Calibrated, m=2:

| Arm | median Δ CAGR | share Δ > 0 | share Δ DD ≤ +3 pp | pooled boot median Δ |
|---|---:|---:|---:|---:|
| G1x | +0.19 pp | 10/18 (55.6%) | 17/18 | +0.17 pp |
| G3x | +0.32 pp | 16/18 (88.9%) | 17/18 | +0.20 pp |

G7' threshold: median ≥ 0, share > 0 ≥ 60%, DD share ≥ 80%. G1x misses the
share threshold (10/18); G3x passes.

## Gates

All v1 G1-G6 and G9 pass against both C0 and C0q for both candidates. G1x
fails only G7' (new ETF instrument share) and G8' is positive. G3x passes G1-G9
against both controls, the plateau gate, G7' and G8'.

## Selection

Passing candidates: `G3x` only. Selected by the pre-registered rule: higher
G8' median Δ vs C0 (G3x +0.201 pp vs G1x +0.171 pp; difference within
0.03 pp, but only G3x passes everything).

## Caveats

- Live YZ in v1 was effectively undefined due to the NaN bug; v2 corrects it.
- G3x's main-window edge is modest (+0.31 pp CAGR, +1.06 pp 2017+, DD -1.06 pp).
- ETF sample is still overlapping in calendar time and may share market-wide
  shocks; it is independent by instrument, not by time.
- Results assume zero idle-cash return and the research 4 bps × m cost model.
- v2 changes no `src/` or `config/`.
