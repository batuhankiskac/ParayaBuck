# short_term_boost_v1 — raise K's weight after a deep short-term drop

Status: **candidate study with gates.** Committed before any non-control arm was simulated on
real data. Owner request 2026-10-23 ("try them all"), candidate 1 of the follow-up list.

## 1. Question

`short_term_restore_v1` §5 found the 2x leg's mean return on the sessions after a QQQ close 8 %
below its trailing-20 high is **+0.51…+0.62 %/session**, six times the unconditional +0.099 %.
Cutting there was the wrong side; this study tests the right side: while K is invested on the E2
vote, raise the target to the 0.98 cap (or 1.5 × w) for a few sessions after such a drop.
`dip_overlay_v1` (on E2, before K) tested 5-day −5 % and RSI(2) < 10 triggers, gained 2017+ but
deepened max DD to 56.8 / 58.5 % and failed G2/G5. Differences here: the trigger is depth from
the 20-session high (8 / 10 %), the boost is time-limited, an optional crash guard, and K
(A1u + H2) underneath.

## 2. Data, windows, engine, stages

Identical to `short_term_restore_v1` (same DATA_MANIFEST, same windows, same bootstraps, same OOS
windows and pooled OOS bootstrap, same October 2026 report-only extension). K anchors as there.

## 3. The boost rule (fixed here)

Decisions on QQQ closes, fills at the next open; K's weight `w[t]` unchanged underneath.
`max20[t]` = max close over `t−19 … t` (plain, not truncated). `e2[t]` = the E2 2-of-3 vote.

1. **Trigger**: `e2[t]` and `w[t] > 0` and `close[t] ≤ (1 − d) × max20[t]`, while armed. The
   boost starts at `t` with `H = max20[t]`; `t` is boosted session 1.
2. **Boosted target**: `max(w[t], min(0.98, mult × w[t]))`, or `max(w[t], 0.98)` when `mult = 0`.
3. **End**: after `N` boosted sessions; or earlier at the first session with `e2` off or `w ≤ 0`
   (that session is not boosted), or with `close ≤ (1 − g) × H` (guard; that session not boosted).
4. **Re-arm**: after a boost ends, a new boost needs one close with the trigger condition false.
5. Every fill goes through K's execution layer: the boost is an up-resize (0.5 pp band); its end
   is a downsize between positive weights, so the A1u gain band and H2 December deferral apply
   to it exactly as to any K downsize (no new exemption).

## 4. Arms (6, fixed)

| arm | `d` | `N` | guard `g` | size | label |
|---|---|---|---|---|---|
| D1 | 8 % | 5 | — | cap 0.98 | `D1_d8_n5_cap` |
| D2 | 8 % | 10 | — | cap 0.98 | `D2_d8_n10_cap` |
| D3 | 10 % | 10 | — | cap 0.98 | `D3_d10_n10_cap` |
| D4 | 8 % | 10 | 15 % | cap 0.98 | `D4_d8_n10_guard15` |
| D5 | 8 % | 10 | — | 1.5 × w, cap 0.98 | `D5_d8_n10_x1.5` |
| D6 | 5 % | 5 | — | cap 0.98 | `D6_d5_n5_cap` |

## 5. Gates and selection

Exactly `short_term_restore_v1` §5 (G1–G9 pre-tax vs K, G10 after tax all three windows, G11/G12
clean B1/B2 median ≥ 0 and share > 0 ≥ 55 %). All must pass. Selection: highest after-tax 2000
CAGR among passers; none → nothing selected.

## 6. Diagnostics (report only)

Boosts/yr, share of sessions boosted, mean extra weight, the 2x leg's mean return / hit rate /
worst session while boosted, how boosts end; October 2026.

## 7. Implementation

`engine/fe_boost.py` (numba signal kernel), `screen.py` (copy of `short_term_restore_v1/screen.py`
with the kernel swapped). Harness: parity `--quick`, K anchors, kernel vs Python reference (all
arms × both financings, masks and records), never-firing identity (targets and NAV bitwise),
target construction (boost only on E2-on, `w > 0` sessions, never above 0.98), `chunk_legs_pc`,
bootstrap extension parity. Two `--run`s byte-identical.

## Amendments

(none yet)
