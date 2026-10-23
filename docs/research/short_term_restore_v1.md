# short_term_restore_v1 — non-chasing restore, half cut and deep staged cut on K

> Evidence: commit `b9d6ecfa` (`research/short_term_restore_v1/`: PREREGISTRATION.md, screen.py,
> engine/fe_restore.py, RESULTS.json, SUMMARY.txt, DATA_MANIFEST.json), removed from the tree in
> the following commit; not on `main`. The pre-registration, kernel and screen were committed
> (local research commit `f12ec233`, unchanged in `b9d6ecfa`) before any arm was simulated.

**Verdict (2026-10-23): FAIL. None of the six pre-registered arms passes; nothing is selected.**
The three candidates named in `short_term_reentry_v1` §9 were each tested, and each one fixes
the thing it targets but still loses to K:

- **Candidate 1, bracket restore (A1, A2).** Restoring at the cut close instead of after a +4 %
  rebound shrinks the chase from +4.6 % to **+1.4…+1.5 %** (the gap between the cut close and
  the next-open fills remains), and the share of restores above the cut fill falls only to
  74–78 %. The lower leg (buy back 5 / 10 % lower) fires rarely (6 / 2 times) and A1's version
  buys into the 2000–02 decline (**max DD 53.4 %**, +6.8 pp). After tax: **−2.7…−7.1 pp/yr**.
- **Candidate 2, half cut (B1, B2).** Cutting to half weight instead of zero roughly halves the
  damage of the same trigger: B1 (`short_term_reentry_v1` arm D at half size) goes from −4.3 /
  −4.6 / −7.2 to **−1.9 / −2.1 / −3.9 pp** after tax. Still negative in every window, every
  sub-period but one, and both bootstraps.
- **Candidate 3, deep staged cut (C1, C2).** Firing only at −8 % / −12 % from the 20-session high
  removes the false alarms by construction (1.05 cuts/yr) and C2 is the closest arm of the study:
  pre-tax 15.52 % vs 15.76 %, max DD **43.7 %** (−2.9 pp), after tax **−0.35 / −0.56 / −0.71 pp**.
  But it loses in all three tax windows, both bootstraps (23–26 % of paths positive) and the
  pooled OOS bootstrap (−1.43 pp).

The diagnostic that explains all six: the 2x leg's mean return on the sessions the overlay is cut
is **above** the unconditional +0.099 %/session in every arm, and for the deep arms it is
**+0.51 % (C1) and +0.62 % (C2) per session** — six times the average. After an 8 % fall in under
20 sessions, the next sessions are the strongest of the whole history (mean reversion inside the
uptrend). Whatever the restore rule, a cut on a short-term pullback is on the wrong side of that.
**The short-term exit family on K is closed on all three remaining axes** (restore, size, depth).

Control = production **K** reproduced cell-for-cell (pre-tax 15.7649 / 15.1785 %, DD 46.6698 /
46.7948 %, after tax 10.7533 / 15.2354 / 23.9720 %; every anchor within 4 × 10⁻⁵ pp). Engine
`research/fast_engine` (`parity --quick` PASS); the only new code is a numba signal kernel
(`engine/fe_restore.py`) that builds the state mask.

## 1. Arms (fixed in `PREREGISTRATION.md` before any arm ran)

The overlay runs on QQQ closes on top of K's unchanged weight `w`; fills at the next open; K's A1u
band and H2 deferral govern every fill (a cut to half is an ordinary K downsize). `H` = trailing-20
high truncated at the in-stint start, `X` = the cut close, `L` = the post-cut low.

| arm | candidate | cut | restore |
|---|---|---|---|
| A1 | bracket restore | 0 at −5 % from `H` | close ≥ `X`, or close ≤ 0.95 `X` |
| A2 | bracket restore | 0 at −5 % | close ≥ `X`, or close ≤ 0.90 `X` |
| B1 | half cut | ½ `w` at −5 % | close ≥ 1.04 `L` |
| B2 | half cut | ½ `w` at −5 % | close ≥ `X`, or close ≤ 0.90 `X` |
| C1 | deep staged | ½ `w` at −8 %, 0 at −12 % | close ≥ 1.04 `L` |
| C2 | deep staged | ½ `w` at −8 %, 0 at −12 % | close ≥ `X` |

Half of the QLD position is used for "≈ 1x exposure" (the single-instrument engine cannot hold
QQQ and QLD together); it is the tax-cheaper implementation of a QLD → QQQ switch, since it
realises only the half it sells.

## 2. Pre-tax, main window 2000-03-09 → 2026-09-29 (m = 2)

| arm | base CAGR | cal CAGR | base max DD | cal max DD | 2017+ CAGR | sells/yr | turnover/yr | cuts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **K** | **15.76** | **15.18** | **46.67** | **46.79** | 33.23 | 10.17 | 5.46 | — |
| R2 | 15.48 | 14.90 | 46.76 | 46.88 | | 29.56 | 5.99 | — |
| A1 | 13.37 | 12.82 | 53.42 | 53.53 | 28.68 | 14.50 | 9.95 | 95 |
| A2 | 13.57 | 13.02 | 42.42 | 42.55 | 27.43 | 14.08 | 9.82 | 93 |
| B1 | 13.93 | 13.40 | 42.19 | 42.31 | 30.19 | 12.39 | 6.66 | 78 |
| B2 | 14.89 | 14.33 | 43.94 | 44.07 | 30.52 | 14.42 | 7.52 | 93 |
| C1 | 14.24 | 13.67 | 45.64 | 45.77 | 30.49 | 10.84 | 5.92 | 28 |
| C2 | 15.52 | 14.94 | 43.73 | 43.86 | 32.73 | 10.92 | 5.95 | 28 |

At m = 1: K 16.02 %, A1 13.83, A2 14.02, B1 14.25, B2 15.24, C1 14.51, C2 15.80. Sub-period
Δ vs K (P1 2000–08 / P2 2009–16 / P3 2017+, base): A1 −1.77 / −0.84 / −4.55, A2 −0.18 / −0.84 /
−5.80, B1 −0.66 / −1.99 / −3.04, B2 −0.06 / +0.07 / −2.72, C1 −0.82 / −1.10 / −2.74,
C2 +0.08 / −0.36 / −0.50. Only C2 and B2 reach a Calmar at or above K's (1.05× and 1.00×), not the
1.1× G1 needs.

## 3. After Turkish tax ($100k, progressive, liquidated)

CAGR %, Δ vs K in parentheses; K = 10.75 / 15.24 / 23.97 %.

| arm | 2000 | 2010 | 2017 | sells/yr | tax paid (2000) |
|---|---:|---:|---:|---:|---:|
| R2 | 10.16 (−0.59) | 14.71 (−0.53) | 22.89 (−1.09) | 30.69 | 766,160 |
| A1 | 7.81 (−2.94) | 11.45 (−3.79) | 17.60 (−6.37) | 14.99 | 570,953 |
| A2 | 8.06 (−2.69) | 11.02 (−4.21) | 16.84 (−7.13) | 14.57 | 607,991 |
| B1 | 8.84 (−1.92) | 13.13 (−2.10) | 20.05 (−3.92) | 12.95 | 605,937 |
| B2 | 9.26 (−1.49) | 13.03 (−2.21) | 19.79 (−4.19) | 15.10 | 734,792 |
| C1 | 9.41 (−1.34) | 13.46 (−1.77) | 21.56 (−2.42) | 11.45 | 615,444 |
| C2 | 10.40 (−0.35) | 14.68 (−0.56) | 23.26 (−0.71) | 11.48 | 791,866 |

## 4. Clean bootstrap and OOS

Median Δ vs K (pp) / share of paths > 0 (seed 20261011, block 20; 5,000 pre-tax / 1,000 after-tax
paths), and the six OOS windows (calibrated, m = 2, median Δ CAGR) with the pooled M1–M5 bootstrap.

| arm | pre base | pre cal | B1 recent | B2 flat | OOS median Δ | pooled OOS boot |
|---|---:|---:|---:|---:|---:|---:|
| R2 | +0.01 | +0.01 | −0.29 / 25 % | −0.13 / 36 % | | |
| A1 | −0.86 | −0.83 | −1.37 / 28 % | −0.77 / 37 % | −0.59 | −0.90 |
| A2 | −1.20 | −1.17 | −1.62 / 27 % | −1.14 / 35 % | −0.87 | −1.23 |
| B1 | −0.57 | −0.53 | −0.70 / 36 % | −0.46 / 41 % | −0.20 | −0.78 |
| B2 | −0.49 | −0.48 | −0.87 / 26 % | −0.47 / 37 % | −0.43 | −0.41 |
| C1 | −0.78 | −0.77 | −0.87 / 27 % | −0.82 / 29 % | −0.35 | −1.30 |
| C2 | −0.74 | −0.72 | −0.80 / 23 % | −0.70 / 26 % | −0.10 | −1.43 |

Per-window OOS Δ CAGR / Δ DD for every arm is in `SUMMARY.txt`. M1 (Nasdaq 1975–99) is the only
window where the −5 % arms gain (+1.5…+2.8 pp); M2 costs them 2.7–5.4 pp with up to +8 pp DD.

## 5. Why: diagnostics (base model, main window)

| arm | cuts/yr | false alarms (< 8 %) | median depth | 2nd cuts | lower-leg restores | mean restore vs cut fill | above cut | cut-session ret |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A1 | 3.58 | 83 % | 6.01 % | — | 6 | +1.48 % | 74 % | +0.169 % |
| A2 | 3.50 | 84 % | 6.01 % | — | 2 | +1.44 % | 78 % | +0.037 % |
| B1 | 2.94 | 63 % | 7.43 % | — | — | +4.60 % | 82 % | +0.142 % |
| B2 | 3.50 | 84 % | 6.01 % | — | 2 | +1.44 % | 78 % | +0.037 % |
| C1 | 1.05 | 0 % | 10.15 % | 6 | — | +4.23 % | 76 % | **+0.506 %** |
| C2 | 1.05 | 0 % | 9.58 % | 2 | — | +2.37 % | 83 % | **+0.619 %** |

(Unconditional 2x-leg mean: +0.099 %/session. B2 shares A2's trigger and restore dates.)

1. **The bracket halves the chase but cannot remove it.** The restore is decided at a close ≥ `X`
   and filled at the next open, the cut at the open after the cut close; both gaps run against
   the arm, so the restore fill is still above the cut fill three times in four.
2. **Half size halves the loss, it does not change its sign.** B1 vs `short_term_reentry_v1` D is a
   clean comparison (same trigger, same restore): −1.9…−3.9 pp instead of −4.3…−7.2 pp after tax.
3. **Depth makes it worse per session, not better.** The deep trigger is never a false alarm in
   the < 8 % sense, but the sessions after an 8 % 20-session drop are the best in the sample
   (+0.5–0.6 %/session on the 2x leg). C2 survives only because its restore usually fires the next
   session (the first bounce regains the cut close), i.e. it is out for ~1 session per cut.
4. **October 2026** (report only, Yahoo extension): K −14.17 % from the 2026-10-06 peak. B1
   −12.29 % (cut 5 sessions), A1/A2 −13.26 %, B2 −13.70 % (1 session), C1/C2 do not fire
   (−14.17 / −14.13 %). The rule that best protects October costs 1.9–3.9 pp/yr elsewhere.

## 6. Gates

| gate | A1 | A2 | B1 | B2 | C1 | C2 |
|---|---|---|---|---|---|---|
| G1 return | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G2 DD ≤ K + 1 pp | FAIL | pass | pass | pass | pass | pass |
| G3 2017+ ≥ K − 1 pp | FAIL | FAIL | FAIL | FAIL | FAIL | pass |
| G4 ≥ 2 of 3 sub-periods | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G5 pre-tax bootstrap | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G6 G1 at m = 1 | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G7 six OOS windows | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G8 pooled OOS bootstrap | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G9 turnover ≤ 2 × K | pass | pass | pass | pass | pass | pass |
| G10 after tax, 3 windows | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G11 B1-recent | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G12 B2-flat | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| **Result** | **FAIL** | **FAIL** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |

Selected: **none**.

## 7. Interpretation

With `short_term_oracle_v1` and `short_term_reentry_v1`, this closes the question the owner asked
("fix the short-term weakness") for overlays on K's daily signal:

* The prize exists only with knowledge of the low. Every causal way to cut K on a pullback —
  any trigger depth (3 / 5 / 8 %), any size (all / half / staged), any restore (rebound, SMA10,
  bracket at the cut price, buy lower) — is on the wrong side of the post-pullback rebound, which
  is the strongest return pattern in the sample.
* The drawdown benefit is real (up to −4.5 pp max DD, −1.9 pp in October 2026) and has a price.
  In-sample, after tax on the 2000 window per pp of pre-tax max DD removed: A2 0.63, B1 0.43,
  B2 0.55, C1 1.3 pp/yr; `vt_level_k_v1`'s lower vol target costs ~0.4. C2 looks cheap (0.12:
  −0.35 pp for −2.9 pp DD), but its DD gain is not robust (OOS DD +1.7 / +1.9 pp in M2 / M3) and
  it loses the bootstraps and the pooled OOS test (−1.43 pp), so lowering the vol target remains
  the cleaner risk knob if the owner wants a shallower drawdown.
* The short-term weakness is therefore the price of K's edge, not a fixable defect. What is left
  is a risk-preference choice (vol target), not an alpha search.

## 8. Caveats

* Single history and instrument (QQQ / synthetic QLD*), 6,679 sessions; October 2026 is outside
  the gated window and report-only.
* Candidate 2 is half of the QLD position, not a QLD → QQQ switch: the switch would realise all of
  the QLD gain on every cut and pay a 0.20 % instead of 0.95 % expense ratio on the held half while
  cut (≤ 0.08 pp/yr at 10 % of sessions); the tax effect is against the switch, so the switch
  would not do better after tax.
* All fills at the next open; a resting intraday limit order at the cut price is not modelled.
  It would remove part of the A arms' +1.4…+1.5 % mean chase (the gap component), but not the
  extra turnover and tax; not simulated.
* Same after-tax convention as the research series: 2026 tariff, Yİ-ÜFE indexation, FIFO,
  instalments, liquidated at the window end, $100k, zero idle-cash yield, 4 bp × m. Not tax advice.
* No production file was read or written; K stays the production rule.

## 9. Reproduction

* `python -m research.short_term_restore_v1.screen --harness`: `parity --quick` **PASS**, K
  anchors (pre-tax, after tax, K − R2) within 4 × 10⁻⁵ pp, the kernel vs a Python reference for all
  six arms × both financings (masks and every episode record), the never-firing identity (targets
  and NAV bitwise), target construction, `chunk_legs_pc`, and the bootstrap extension vs
  `fe_boot.boot` (8/8 cells, exact).
* `--run`: harness + every stage in **85 s**; two consecutive runs give byte-identical
  `RESULTS.json` (SHA-256 `b0d1feb9b652a5734a840bb53859a2bcdca1bfa81ea37d540c6300219b8e356a`).
* `--smoke`: synthetic-only kernel/reference checks.
* Built on tag `archive/research/short-term-reentry-v1-2026-10` (engine, data, the reentry study's
  screen and `fe_overlay.rolling_max`, the oracle study's `fe_boot_targets`); numba 0.68.0,
  numpy 2.5.2, pandas 3.0.5, Python 3.13.
