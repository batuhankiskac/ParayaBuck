# exit_timing_v2 — exit-leg tax timing under K (idea_survey_v4 §I4), on the Numba engine

> Evidence: tag `archive/research/exit-timing-v2-2026-10` (not on `main`).

**Verdict: no arm adopted; the axis is closed.** X1 (December exit deferral), X2 (gain-heavy exit
staging) and X3 (X1 + X2) all fail at least one pre-registered gate against production **K**:
X1 fails G4/T1/T2/T3, X2 fails G7/T1/T2/T3, X3 fails G4/G7/T1. The devices act on a *handful* of
sessions in 26.5 years (X1: 24 deferred exits; X2: 10 staging events over 10 staged sessions —
6 released by the early-conditions gate, 4 taken over by the ordinary rule, **0** by the
21-session cap), and the sign of the effect flips inside
the sample: X1 gains +0.14/+0.28 pp after tax on the 2000/2010 windows and loses −0.19 pp on 2017+;
X3 clears the two bootstrap share gates (T2/T3, share > 0 ≈ 56 %) but loses −0.72 pp on 2017+.
The pre-registered expectation (X2's anti-correlation: the gain-richness that triggers staging is
what makes the early conditions false) is confirmed exactly — X2 stages and is released by the
gate in the next decision close, never by its 21-session cap (pre-tax main path: 10 staging events,
10 staged sessions, 6 gate releases, 4 ordinary-rule takeovers, **0** cap releases — v1's
pre-commit probe through the old harness, reproduced event for event by the engine).

This is the redo of `exit_timing_v1` on the mandatory Numba research engine
(`research/fast_engine`, `docs/research/fast_engine_v1.md`). Arms, parameters, stages, seeds,
gates and the selection rule are v1's, unchanged (`research/exit_timing_v2/PREREGISTRATION.md`,
committed before any non-control arm ran on real data; v1's old-engine pre-registration is
preserved in tag `archive/research/exit-timing-v1-oldengine-prereg-2026-10`; v1's partial results
were never reviewed and are not used).

## Method

**Control C0 = production K** (`equity_leveraged_trend_v1` 1.2.0): E2 2-of-3 vote on the QQQ close
+ the `0.5·vt` early re-entry leg, sized `vt = min(1, 0.35/vol20)`, cap 0.98, with A1u's
gain-scaled downsize band (0.5/3/8 pp at ≤1 %/≤5 %/>5 % USD FIFO gain fraction) and H2's December
deferral. Everything else: decide on the completed close, fill at the next open, symmetric 0.5 pp
band, entries/exits-to-zero/up-resizes always fill, idle cash zero, cost 4 bps × m × |Δw|,
synthetic QLD\* = daily-reset 2× on QQQ total return, 0.95 %/yr expense, financing DTB3 + {0.0 %
base, 0.75 % calibrated}/yr; Turkish after-tax engine = `fe_tax` (the `vt_level_tax_v1` corrected
one), progressive 2026 tariff deflated by the previous October's Yİ-ÜFE, liquidated at the window
end. Report-only reference rows: **C0 row = production R2** (K's execution layer off), plus A1u and
H2 alone (the deep reproduction block).

**Arms** (every parameter fixed in advance, PREREGISTRATION.md §4):

| Arm | Rule |
|---|---|
| **X1** | a fill that would be an exit to zero whose **fill** session is one of the last `DEC_TAIL = 10` December sessions is not executed; the target is re-evaluated every close, so it fills at the first January decision (never postponed twice) |
| **X2** | an exit to zero whose consumed FIFO lots have a USD gain fraction > `X2_GAIN = 5 %` sells only down to the current early-leg weight `0.5·vt`; the remainder is held while the early leg's three-condition set holds, at most `X2_CAP_SESSIONS = 21` sessions after the staging fill, and exits in full at the first decision close at which it fails; a positive ordinary target takes the position over; one staging per exit episode |
| **X3** | X1 + X2, X1's December window tested first |

Implementation: the X options are an additive, default-off kernel block in a **copy** of the engine
(`research/exit_timing_v2/engine.py`; `research/fast_engine/` is never edited). `screen.py` only
builds target-weight arrays (vectorised numpy) and aggregates; all simulation, tax assessment and
bootstrap paths run through `fe_sim`/`fe_tax`/`fe_boot` semantics in numba.

### Verification (all PASS)

* `python -m research.fast_engine.parity --quick` → **PASS** (run by `--harness`; the CLI's
  `PARITY.json` is restored afterwards, so the engine's tree is left untouched).
* This study's own parity cells: with the X options off, `engine.simulate_x` reproduces
  `fe_sim.simulate` for all four base arms × three modes on the real main window **bit-for-bit**
  (max |NAV diff| = 0, every scalar key equal), and `engine.boot_x` reproduces `fe_boot.boot` on
  every shared cell (126 cells, 0 mismatches); `boot_x`'s only extras are the pre-tax paired
  `{arm}_vs_{ctrl}` cells its G5 input needs.
* K anchors: pre-tax 15.7649 / 15.1785 % CAGR (m = 2), m = 1 16.0173 / 15.4303 %, max DD
  46.6698 / 46.7948 %, turnover 5.456136; R-USD row; after tax at $100k 10.753 / 15.235 / 23.972 %
  with the postponement counters (1,836 band + 81 December); m = 4 cost stress; clean-bootstrap
  medians B1 3.8577 % / B2 4.0376 % — all reproduced (harness PASS, 0 outside tolerance).
* Reference labels: the "C0" row reproduces `fast_reentry_v1`'s R2 anchors and after-tax cells, the
  pure-E2 row `e2_integration_v1`'s (so a label drift fails the harness).
* Deep reproduction block: **1,893 cells, 0 mismatches**, max |diff| 5.0e-07 — A1u and C0 subtrees
  of pre-tax main, after-tax windows, m = 4 cost stress, the seed-20261011 clean bootstrap, the six
  OOS windows, the 21 R-USD instruments, E1/E2/E3 versus `research/gain_band_v3/RESULTS.json`, and
  H2 versus `tax_harvest_v2`'s tagged cells. (The control-relative `delta_*_pp` cells of the
  E1/E2/OOS/instrument stages are rebased on this study's own C0 row before comparison, because
  `gain_band_v3`'s control was C0 while this study's is K; every absolute cell is compared as
  published.)
* Smoke identities on synthetic prices: `X1` with no December window is K bit-for-bit, `X2` with an
  unreachable gain threshold is K bit-for-bit, `X3` with X1 off is X2 and with X2 off is X1
  bit-for-bit, an engineered December exit is postponed by X1/X3 and not by K, an engineered
  gain-rich exit stages and is released by the gate, by the cap and by a target takeover.

## Results

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions), m = 2, base financing, gate capital
$100k; Δ is versus K. `--run` wall clock ≈ 11–12 s; the `--harness` stage adds ≈11–13 s for the engine's
parity CLI (both durations are in `SUMMARY.txt`); `RESULTS.json` is byte-identical across two runs
(no wall-clock value is stored in it, by construction).

| arm | pre-tax CAGR base / cal | max DD base / cal | turn/yr | Δ after tax 2000 / 2010 / 2017 (pp) | B1 med / share | B2 med / share |
|---|---|---|---|---|---|---|
| **K** | 15.76 / 15.18 % | 46.67 / 46.79 % | 5.46 | — | — | — |
| C0 (R2) | 15.48 / 14.90 % | 46.76 / 46.88 % | 5.99 | −0.593 / −0.529 / −1.086 | −0.286 / 25.0 % | −0.131 / 36.0 % |
| A1u | 15.72 / 15.12 % | 47.19 / 47.31 % | 5.49 | −0.139 / −0.133 / −0.271 | −0.042 / 35.1 % | −0.025 / 36.6 % |
| H2 | 15.52 / 14.94 % | 46.25 / 46.38 % | 5.95 | −0.450 / −0.396 / −0.811 | −0.210 / 27.1 % | −0.088 / 38.5 % |
| **X1** | 15.92 / 15.33 % | 47.27 / 47.39 % | 5.35 | **+0.144 / +0.278 / −0.191** | +0.000 / 48.5 % | +0.000 / 48.9 % |
| **X2** | 15.97 / 15.38 % | 46.67 / 46.79 % | 5.34 | **−0.103 / −0.228 / −0.545** | +0.000 / 44.0 % | +0.000 / 44.5 % |
| **X3** | 16.13 / 15.54 % | 47.27 / 47.39 % | 5.23 | **+0.051 / +0.056 / −0.720** | +0.058 / 56.0 % | +0.068 / 56.7 % |

Gates (pre-registered; PREREGISTRATION.md §6 — G1–G9 pre-tax + turnover vs K, T1–T3 after tax at
$100k, no non-inferiority relaxation):

| Arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | T1 | T2 | T3 | adopted |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| X1 | P | P | P | **F** (1/3) | P (+0.041 pp) | P | P | P | P (0.98×) | **F** | **F** (48.5 %) | **F** (48.9 %) | no |
| X2 | P | P | P | P (2/3) | P (0.000) | P | **F** (−0.112 pp) | P | P (0.98×) | **F** | **F** (44.0 %) | **F** (44.5 %) | no |
| X3 | P | P | P | **F** (1/3) | P (+0.103 pp) | P | **F** (−0.027 pp) | P | P (0.96×) | **F** | P (56.0 %) | P (56.7 %) | no |

Detail: G2 Δ max DD = +0.60 pp for X1/X3 (base and calibrated), +0.00 for X2 — inside the +1 pp
bound but in the direction the pre-registration flagged. T1's worst cell is X3's 2017+ window
(−0.720 pp; Δ max DD +3.32 pp there). T2/T3 medians are exactly 0.000 pp for X1 and X2 — on most
bootstrap paths these devices touch no session at all, which is the "expected-share caveat" of
PREREGISTRATION.md §6, not merely a sign statement. G7 fails on the six-window **median** — X2 −0.112 pp
(5/6 windows within the −0.5 pp CAGR bound, M1 is −0.657 pp), X3 −0.027 pp (6/6 within, median
negative) — i.e. the OOS losses (M1, M2, M3) outweigh the gains (M5, M4, ^GSPC); G8 pooled OOS
medians are +0.000 (X1), +0.000 (X2) and +0.043 pp (X3).

### Drawdown and the named worst episodes (the reason the task asks for them)

Both devices hold *more* exactly when the vote wants out. Whole-path pre-tax max DD: K 46.670 %,
X1 and X3 47.265 % (+0.595 pp), X2 46.670 % (+0.000 pp). Episode table (main window, base, m = 2,
pre-tax; Δ versus K):

| episode | K return / max DD | X1 Δret / ΔDD | X2 Δret / ΔDD | X3 Δret / ΔDD |
|---|---|---|---|---|
| 2000-03 → 2002-12 (dot-com) | −39.86 % / 40.50 % | **−1.024 / +0.742 pp** | +0.000 / +0.000 | **−1.024 / +0.742 pp** |
| 2007-10 → 2009-03 (GFC) | −32.28 % / 32.41 % | +0.000 / −0.000 | +0.000 / −0.000 | −0.000 / +0.000 |
| 2015-12 → 2016-01 | −15.22 % / 16.26 % | +0.000 / +0.000 | −0.000 / +0.000 | −0.000 / +0.000 |
| 2018-12 | 0.00 % / 0.00 % | +0.000 / +0.000 | +0.000 / +0.000 | +0.000 / +0.000 |
| 2021-12 → 2022-01 | −10.46 % / 16.74 % | +0.000 / +0.000 | **−2.755 / +2.562 pp** | **−2.755 / +2.562 pp** |

Readings: the GFC, Dec-2015/Jan-2016 and Dec-2018 episodes are untouched — in those windows the
target was already zero (Dec-2018 is NAV-flat because the account sat in idle cash at 0 % yield, so
the episode return and DD are exactly zero for every arm). X1's cost is the dot-com leg
(−1.02 pp of return, +0.74 pp of DD) where it held a full position across the year-end; X2's is
2021-12 → 2022-01 (−2.76 pp of return, +2.56 pp of DD), where it staged a gain-heavy exit at the
top and kept half the position into the January drawdown.

### Mechanism counters (what the devices actually did)

Main window, $100k, progressive, m = 2 (pre-tax whole path in parentheses):

| arm | exit deferrals (X1) | staged events (X2) | staged sessions | released by gate / cap / takeover | sells/yr |
|---|---|---|---|---|---|
| K | 0 | 0 | 0 | 0 / 0 / 0 | 10.66 (9.54 on 2017+) |
| X1 | **24** | 0 | 0 | — | 10.58 |
| X2 | 0 | 9 (10 pre-tax) | 9 (10) | **5 / 0 / 4** (pre-tax **6 / 0 / 4**) | 10.81 |
| X3 | **24** | 9 (10 pre-tax) | 9 (10) | 5 / 0 / 4 (pre-tax 6 / 0 / 4) | 10.73 |

X2's 21-session cap **never binds**: every staging is released within one decision close by the
early conditions failing, or taken over by the ordinary rule — exactly the anti-correlation
PREREGISTRATION.md §4.3 predicted from the probe (the gain-rich book that triggers staging exists
only after a decline, i.e. with SMA50 already broken). Tax-side counters move the wrong way for the
tax base as often as the right way: realised TRY gain by reason at $100k (main window) K
`{liquidation 9.08 m, resize 58.35 m, tax 0.57 m}` versus X1 `{9.40 m, 60.21 m, 0.59 m}` and X3
`{9.17 m, 58.46 m, 0.60 m}`, with the negative (loss-offset) part moving from −3.18 m (K) to
−3.50 m (X1) / −3.55 m (X3) — deferring an exit shifts both the gain and its same-year loss offset
into the neighbour year. E1 (21 ETFs, report only) median Δ after tax: X1 +0.161 pp (13/21 > 0),
X2 +0.005 pp (11/21 > 0), X3 +0.137 pp (13/21 > 0); E2 rolling January starts: X1 +0.186 pp
(12/21 ≥ 0), X2 −0.277 pp (0/21 ≥ 0), X3 +0.043 pp (11/21 ≥ 0); m = 4 cost stress: X1
+0.149/+0.272/−0.183 pp, X3 +0.060/+0.069/−0.708 pp.

## Why the axis closes

1. **Frequency.** Exits to zero are 1–3 per year under K and only a small share of their *fill*
   sessions can cross a tax-year boundary: X1 finds 24 fill sessions in 6,679 (0.36 %) and 10 of
   them (E5) are the only sessions where its decision differs from K's (`X1_vs_K`: 10 sessions,
   0.15 % of the path; `X2_vs_K`: 7 sessions). A device that acts on 0.1–0.4 % of sessions cannot
   move a 26-year after-tax CAGR by more than a few tenths of a pp, and cannot clear a
   `share > 0 ≥ 55 %` bootstrap gate when ~half the resampled paths contain none of those sessions.
2. **The two devices are anti-correlated with their own trigger, as predicted.** X2 needs a
   gain-rich FIFO book (an old, long-held position) at the moment of the exit — and that state
   implies the exit is happening after a decline, i.e. with the early conditions already false, so
   staging is released immediately. The predicted "one-session fill split" is what the run shows.
3. **The sign is not stable in-sample.** X1 is positive on the 2000 and 2010 windows and negative
   on 2017+ (its dot-com episode cost −1.02 pp of return); X3 inherits that. Since the 2017+ window
   is where K's gap to buy-and-hold is decided, an arm that loses there cannot be adopted.
4. **K already owns the two mechanisms, on the events they can reach.** A1u's band covers every
   downsize between two positive weights and H2 covers the December ones; X1 is H2's window applied
   to the one event H2 exempts, and it buys ~0.14–0.28 pp on two windows and loses 0.19 pp on the
   third — i.e. inside the noise of a single history.

## Caveats and limits

As `band_h2_combo_v1` §7 / `gain_band_v3` §7: one history per cell, no true after-tax
out-of-sample (the TRY/Yİ-ÜFE calendar starts 2000), the fixed 2015-09 → 2026-09 bootstrap calendar
cannot price another FX/inflation regime, the liquidated convention charges deferred tax at the
window end, FIFO with per-year netting and no loss carry-forward, the 2026 tariff deflated by the
previous October's Yİ-ÜFE, instalments funded by selling. Idle cash earns nothing (this is what
makes the Dec-2018 episode exactly flat). **Not tax advice.** X2's reading of the brief's ambiguous
sentence is documented in PREREGISTRATION.md §4.3 (the three-condition set is the release clause,
the gain fraction the entry condition); the study's X2 row should be read as "this device, as
specified, cannot act", not as a test of every possible staging rule.

## Files

* `PREREGISTRATION.md` — v1's pre-registration, arms/parameters/gates unchanged, with the engine
  change and the OOS-bootstrap `hold` fix disclosed (A0) before any arm ran.
* `screen.py` — the study (arms, stages, gates, harness, smoke, report); `engine.py` — the kernel
  copy with the default-off X1/X2 options and the boot driver.
* `RESULTS.json` (byte-identical across two runs), `SUMMARY.txt` (includes the `--run` wall clock),
  `DATA_MANIFEST.json` (inputs: SHA-256, source, range; unchanged committed research files, nothing
  downloaded), `H2_ANCHORS.json` (H2's published cells, evidence).
