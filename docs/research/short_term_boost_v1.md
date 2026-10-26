# short_term_boost_v1 — raise K's weight for a few sessions after a deep short-term drop

> Evidence: commit `c7e4e59c` (`research/short_term_boost_v1/`), removed from the tree in
> the following commit; not on `main`. Pre-registration, kernel and screen were committed (local
> research commit `737cc01c`, unchanged in the evidence commit) before any arm was simulated.

**Verdict (2026-10-23): FAIL. No arm passes; nothing is selected.** Buying more after an 8–10 %
pullback (to the 0.98 cap, or 1.5 × K's weight, for 5 or 10 sessions, while the E2 vote is on)
does not pay, and it is dangerous:

- **The drawdown explodes.** Max DD rises from 46.7 % to **54–71 %** in every arm (G2 fails
  everywhere). The damage comes from the spring-2000 crash: E2 was still on, the first −8 % drops
  of the bear market triggered boosts, and the worst boosted session was −13.9 % (−17.4 % for D3).
  The 15 % crash guard (D4) does not help (65.2 %).
- **The return does not come.** Pre-tax CAGR is 0.2–2.9 pp below K; after tax every arm loses in
  the 2000 window (−0.51…−2.48 pp) and both clean bootstraps (B1 median −0.27…−1.67 pp, 22–44 %
  of paths positive). The best arm (D5, 1.5 × w for 10 sessions) is −0.63 / −0.25 / −0.02 pp after
  tax with a 54.9 % max DD; D3 is the only one positive in one window (+0.18 pp, 2017+).
- **Correction to `short_term_restore_v1`.** That report's "+0.51…+0.62 %/session after an 8 %
  drop" was the mean over the sessions its deep-cut arms *stayed cut*, and those episodes end on a
  rebound, so the figure is conditioned on the rebound it describes. Measured over a fixed horizon
  here, the 2x leg earns **+0.32 %/session over the first 5 sessions** (D1, hit rate 56 %) and
  **−0.06 %/session over 10** (D2). The post-drop edge is real only for a few days and is far
  smaller than the risk of being at the cap when the drop is the start of a crash.

`dip_overlay_v1` (on E2, 5-day −5 % / RSI(2) triggers) reached the same place from a different
trigger: more 2017+ return, much deeper drawdown, a negative bootstrap. **Buying the dip on top of
K is closed.**

Control = production **K** reproduced exactly (pre-tax 15.7649 / 15.1785 %, DD 46.6698 /
46.7948 %; after tax 10.7533 / 15.2354 / 23.9720 %). Engine `research/fast_engine` (`parity
--quick` PASS); the only new code is the numba signal kernel `engine/fe_boost.py`.

## 1. Arms (fixed in `PREREGISTRATION.md`)

Trigger: E2 on, K's `w > 0`, QQQ close ≤ (1 − d) × trailing-20 high. Boosted target
`max(w, 0.98)` (or `max(w, min(0.98, 1.5 w))` for D5) for N sessions; ends early on E2 off / `w ≤ 0`
/ the guard; re-arms after the trigger clears. K's A1u band and H2 deferral govern every fill.

| arm | d | N | guard | size |
|---|---|---|---|---|
| D1 | 8 % | 5 | — | cap |
| D2 | 8 % | 10 | — | cap |
| D3 | 10 % | 10 | — | cap |
| D4 | 8 % | 10 | −15 % from the trigger high | cap |
| D5 | 8 % | 10 | — | 1.5 × w |
| D6 | 5 % | 5 | — | cap |

## 2. Results

Pre-tax main window 2000-03-09 → 2026-09-29 (m = 2), after tax at $100k (Δ vs K, pp), clean
bootstrap median Δ / share > 0:

| arm | base CAGR | cal CAGR | max DD | AT 2000 | AT 2010 | AT 2017 | B1 recent | B2 flat |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **K** | **15.76** | **15.18** | **46.67** | 10.75 | 15.24 | 23.97 | | |
| D1 | 15.01 | 14.43 | 55.37 | −0.80 | −0.82 | −0.95 | −0.27 / 44 % | −0.03 / 49 % |
| D2 | 12.87 | 12.29 | 70.79 | −2.48 | −1.27 | −1.38 | −1.02 / 34 % | −0.83 / 37 % |
| D3 | 14.48 | 13.90 | 64.89 | −1.19 | −0.27 | +0.18 | −0.54 / 36 % | −0.39 / 39 % |
| D4 | 13.62 | 13.03 | 65.21 | −1.87 | −1.27 | −1.38 | −1.01 / 31 % | −0.87 / 35 % |
| D5 | 15.13 | 14.54 | 54.94 | −0.63 | −0.25 | −0.02 | −0.48 / 31 % | −0.36 / 35 % |
| D6 | 15.54 | 14.94 | 53.99 | −0.51 | −0.72 | −1.67 | −1.67 / 22 % | −1.29 / 28 % |

Sub-period CAGR (P1 2000–08 / P2 2009–16 / P3 2017+): K 0.29 / 14.27 / 33.23 %; the boost costs
0.8–5.3 pp in P1 (the 2000–02 bear), 0.7–1.2 pp in P2, and is ±0.7 pp in P3.

Diagnostics (base model): boosts/yr 0.53–4.03, 1.7–7.1 % of sessions boosted, mean extra weight
+0.18…+0.45; the 2x leg's mean return on boosted sessions +0.32 % (D1), −0.06 % (D2), +0.26 % (D3),
+0.05 % (D4), −0.06 % (D5), +0.22 % (D6) against +0.099 % unconditionally.

OOS (six windows, calibrated): Δ CAGR mostly small and positive (median ≥ 0, G7 passes), but Δ DD
up to +5.9 pp (M4). The pooled OOS bootstrap median is **exactly 0** for D1–D5 because the
boost never fires on most resampled paths of the lower-volatility OOS markets, so G8's pass is
uninformative. October 2026: the drop never reached 8 % from the 20-session high on a close, so
no arm except D6 fired (D6: 5 sessions, no difference to K's −14.17 %).

## 3. Gates

| gate | D1 | D2 | D3 | D4 | D5 | D6 |
|---|---|---|---|---|---|---|
| G1 return | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G2 DD ≤ K + 1 pp | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G3 2017+ | pass | FAIL | pass | FAIL | pass | FAIL |
| G4 sub-periods | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G5 pre-tax bootstrap | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G6 m = 1 | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G7 OOS windows | pass | pass | pass | pass | pass | pass |
| G8 pooled OOS boot | pass* | pass* | pass* | pass* | pass* | FAIL |
| G9 turnover | pass | pass | pass | pass | pass | pass |
| G10 after tax | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G11 / G12 | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |

\* median exactly 0 (most paths never fire).

## 4. Caveats

Single history; synthetic QLD*; next-open fills (a boost decided on a crash close also bears the
overnight gap); the boost's end goes through A1u/H2 like any K downsize, which can keep the boosted
weight longer than N sessions when the drift is small (rare at +0.18…+0.45 extra weight). Same
after-tax convention as the series; not tax advice. No production file read or written.

## 5. Reproduction

`python -m research.short_term_boost_v1.screen --harness` (parity `--quick` PASS, K anchors within
4 × 10⁻⁵ pp, kernel vs Python reference for all six arms × both financings, never-firing identity,
target construction, `chunk_legs_pc`, bootstrap-extension parity: all PASS); `--run` in **94 s**;
two runs byte-identical (`RESULTS.json` SHA-256 `0e6a26411d86187d5dee8f27c72c470f1142baa6bca291116d94b62ffe5f9b6f`). Built on tag
`archive/research/short-term-reentry-v1-2026-10` plus `short_term_restore_v1`'s screen.
