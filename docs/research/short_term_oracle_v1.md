# short_term_oracle_v1 — the size of the short-term drawdown prize (DIAGNOSTIC)

**Verdict (2026-10-22).** The prize is **not small**. A detector with perfect foresight of
which QQQ pullbacks reach 8–10 % — and of where the low is — adds **+9.7 pp/yr pre-tax**
(25.49 % vs K's 15.76 %) and **+7.2 / +10.8 / +11.0 pp/yr after Turkish tax** on the
2000 / 2010 / 2017 windows at $100k, with a **4.6 pp lower** max drawdown (42.09 % vs
46.67 %), and it wins the clean bootstrap on **100 %** of paths (+9.24 pp B1-recent,
+10.10 pp B2-flat). Even the most conservative pre-registered cell (trigger 3 % below the
high, class 10 %, restore 5 sessions after the low) adds **+3.0 / +5.1 / +4.8 pp after tax**.
Turkish tax removes **22–29 %** of the prize, not the bulk, and a tax-free overlay that never
sells QLD does **not** do better after tax (§6). The precision of the trigger is **not** the
binding constraint in this measurement: firing on every 3 % dip (19.6 % of triggers are
real ≥10 % events) still earns +4.2 / +5.2 / +4.6 pp after tax, because the oracle also knows
the low. **The entire prize lives in the exit/ re-entry timing**, which is exactly the part
the ~30 causal short-term detectors could not supply. Their failure is therefore consistent
with a large prize that is inaccessible without bottom-timing — not with a small prize.

**No gates apply.** This is a diagnostic, not a candidate search: the arms are labelled
ORACLE, use future knowledge, are not tradeable, and nothing is proposed for adoption.
The pre-registered grid exists to map the prize, not to select a winner. See
[`PREREGISTRATION.md`](../../research/short_term_oracle_v1/PREREGISTRATION.md) (committed
before any non-control arm was simulated).

## 1. What was measured

Production control **K** exactly (`equity_leveraged_trend_v1` 1.2.0 = R2 + A1u gain band +
H2 December deferral) through `research/fast_engine`, with R2 as a reference row. The
question is the maximum a *short-term drawdown detector* could add, i.e. an upper bound on
the value of detecting the class of 5–15 % pullbacks that K rides through (Oct 2026:
QQQ −6 %, account −12 %).

The detector is granted the two things a real detector does not have: knowing that an
episode will reach class depth X, and knowing where its low is.

## 2. Method

**Episodes** (on QQQ closes, pre-registered before the arms): a session starts an episode
iff its close is a 20-session high, the E2 vote is on, and no episode is open. The episode
ends at the first close that regains the start high **or the first E2-off close**, whichever
comes first (the one interpretive choice; without it the March-2000 high defines a 15-year
episode and swallows 2003–2014). Depth = the maximum decline to the minimum close over the
episode; classes X ∈ {5, 8, 10, 15 %}. Nested starts are not allowed.

**Oracle arms.** For each episode of depth ≥ X, the target weight is set to 0 at the close
of the first session at least d % below the start high (fills at the next open), and restored
to K's own target k sessions after the episode low. Grid: d ∈ {2, 3 %} × k ∈ {0, 3, 5} ×
X ∈ {8, 10 %} = 12 cells. K's execution layer (A1u band, H2 deferral, 4 bp × m=2) governs
every other trade; an exit to zero and the restore from zero always fill. The cut intervals
are a union, so overlapping episodes compose.

**Precision sweep.** `O(3, 3, 10)` plus deterministic false alarms: the detector also fires
on the fraction f ∈ {0.25, 0.5, 0.75, 1.0} of episodes with 3 % ≤ depth < 10 %, selected in
time order by `floor((j+1)f) > floor(jf)` (exactly every 4th / 2nd / 3-of-4 / all).
f = 0 is the oracle cell. Trigger precision is reported as
`N(≥10 %) / (N(≥10 %) + f·N(3–10 %))`, i.e. 1.000 / 0.494 / 0.328 / 0.246 / 0.196.

**Tax-free variant.** The same intervals with no sell: the QLD leg's **daily total return is
set to zero** on the sessions the sell arm would be out (a costless, untaxed overlay), with
K's target series computed from the real path. Implemented as an effective price path (an
input transformation, not a new execution mechanism), so the engine is unchanged; an
all-zero mask reproduces the real path and K's NAV bit for bit (R5).

**Accounting.** After tax: $100k, 2026 tariff, Yİ-ÜFE indexation, instalments, FIFO,
liquidated at the window end (base financing, m = 2; `fe_tax`). Bootstrap: fixed
2015-09 → 2026-09 calendar with the realised USD/TRY and Yİ-ÜFE, market returns resampled,
seed 20261011, block 20, 1,000 paths, B1-recent (progressive) and B2-flat. No OOS, no
21-ETF, no cost-stress stage: pre-registered as not applicable to a diagnostic.

## 3. Episode census (2000-03-09 → 2026-09-29, 6,679 sessions)

592 episodes start; most are one-session non-events (a 20-session high followed by a higher
close). K is below its running peak on 93.6 % of sessions.

| class | count | median depth | median duration | median duration to low | share of sessions | share of K's drawdown time |
|---|---|---|---|---|---|---|
| ≥ 5 % | 34 | 8.1 % | 35 | 17 | 18.4 % | 19.4 % |
| ≥ 8 % | 18 | 10.3 % | 35 | 16 | 10.4 % | 10.9 % |
| ≥ 10 % | 11 | 10.9 % | 38 | 17 | 7.5 % | 7.9 % |
| ≥ 15 % | 1 (2020-02-19, 18.3 %) | 18.3 % | 13 | 13 | 0.2 % | 0.2 % |

The 10 % class (11): 2000-03-09, 2012-04-02, 2015-07-20, 2018-01-26, 2018-03-12,
2019-05-03, 2020-02-19, 2021-02-12, 2021-12-27, 2023-07-18, 2026-06-02. Three are
*unresolved by E2-off* (2015-07-20, 2020-02-19, 2021-12-27), i.e. the short-term
detector's exit is where K's own trend exit begins.

**Where the prize comes from** (descriptive: K's weight at the cut × the avoided 2x decline;
no extra simulation):

| cell | episodes ending by regain | episodes ending at E2-off |
|---|---|---|
| X = 8 %, d = 2 %, k = 0 | n = 11, **57 %** of the avoided loss | n = 7, 43 % |
| X = 8 %, d = 3 %, k = 3 | n = 11, 47 % | n = 7, 53 % |
| X = 10 %, d = 2 %, k = 0 | n = 8, **63 %** | n = 3, 37 % |
| X = 10 %, d = 3 %, k = 3 | n = 8, 55 % | n = 3, 45 % |

So the majority of the 10 %-class prize comes from pullbacks that **regain their high** —
genuine inside-uptrend episodes, not only from the start of bear markets.

## 4. Pre-tax results (main window, m = 2)

| arm | base CAGR | cal CAGR | base max DD | cal max DD | sells/yr | turnover/yr |
|---|---|---|---|---|---|---|
| **K (control)** | 15.76 | 15.18 | 46.67 | 46.79 | 10.17 | 5.46 |
| R2 (reference) | 15.48 | 14.90 | 46.76 | 46.88 | 29.56 | 5.99 |
| O(2,0,8) | **25.49** | 24.90 | **42.09** | 42.22 | 9.56 | 6.03 |
| O(2,0,10) | 22.31 | 21.71 | 46.88 | 47.01 | 9.56 | 5.83 |
| O(2,3,10) | 21.05 | 20.47 | 46.88 | 47.01 | 9.79 | 5.78 |
| O(3,0,10) | 22.21 | 21.62 | 46.88 | 47.01 | 9.56 | 5.83 |
| O(3,3,10) | 20.96 | 20.37 | 46.88 | 47.01 | 9.79 | 5.78 |
| O(3,5,10) | 19.75 | 19.18 | 46.67 | 46.79 | 9.98 | 5.78 |
| O(2,5,8) | 22.46 | 21.90 | 41.86 | 41.99 | 9.83 | 5.97 |
| O(3,5,8) | 21.59 | 21.02 | 41.86 | 41.99 | 9.83 | 5.97 |

All 12 grid cells (and the per-window pre-tax and after-tax max-DD deltas) are in
`RESULTS.json`; the table shows the corners. Two facts matter:
the oracle **does not add turnover** (9.6–10.0 sells/yr, at or below K's 10.2; turnover
1.10× K at most), because the forced exit replaces K's own vol-target trims while the position
is out; and only the 8 % class improves the max drawdown (−4.6 pp), because the 10 %-only
detector acts too late for the 2000–02 decline.

Pre-tax Δ vs K per window (pp) and the number of fired episodes on the main window:

| arm | fired | 2000 | 2010 | 2017 |
|---|---|---|---|---|
| O(2,0,8) | 18 | +9.73 | +14.63 | +15.49 |
| O(2,0,10) | 11 | +6.54 | +10.69 | +12.71 |
| O(3,0,10) | 11 | +6.45 | +10.53 | +12.35 |
| O(3,3,10) | 11 | +5.19 | +8.41 | +9.59 |
| O(3,5,10) | 11 | +3.98 | +6.76 | +6.57 |
| O(2,5,8) | 18 | +6.70 | +10.18 | +9.46 |

The restore lag is expensive but not fatal: 0 → 3 → 5 sessions after the low costs about
1.8–3.0 pp pre-tax on the X = 8 cells and 1.2–2.5 pp on X = 10 (2000 window).

## 5. After-tax results ($100k, progressive, liquidated)

CAGR with Δ vs K in parentheses, and the 2000-window max-DD Δ:

| arm | 2000 | 2010 | 2017 | Δ max DD (2000) |
|---|---|---|---|---|
| **K** | 10.75 | 15.24 | 23.97 | — |
| R2 | 10.16 (−0.59) | 14.71 (−0.53) | 22.89 (−1.09) | +0.09 |
| O(2,0,8) | **17.96 (+7.21)** | **25.98 (+10.75)** | **34.97 (+11.00)** | **−4.58** |
| O(2,0,10) | 15.72 (+4.96) | 23.20 (+7.97) | 33.26 (+9.29) | +0.21 |
| O(3,0,10) | 15.76 (+5.01) | 23.28 (+8.05) | 33.28 (+9.30) | +0.21 |
| O(3,3,10) | 14.81 (+4.05) | 21.68 (+6.45) | 31.23 (+7.26) | +0.21 |
| O(2,3,10) | 14.76 (+4.00) | 21.60 (+6.36) | 31.12 (+7.15) | +0.21 |
| O(3,5,10) | 13.77 (+3.02) | 20.29 (+5.06) | 28.80 (+4.83) | 0.00 |
| O(2,5,10) | 13.72 (+2.96) | 20.20 (+4.96) | 28.68 (+4.71) | 0.00 |
| O(2,5,8) | 15.39 (+4.64) | 22.35 (+7.11) | 30.02 (+6.05) | −4.81 |

Clean bootstrap, median Δ vs K (pp) / share of paths > 0:

| arm | pre-tax base | pre-tax cal | B1 recent | B2 flat |
|---|---|---|---|---|
| O(2,0,8) | +11.83 | +11.84 | +9.24 / 100 % | +10.10 / 100 % |
| O(2,0,10) | +8.89 | +8.89 | +6.92 / 100 % | +7.60 / 100 % |
| O(3,3,10) | +7.04 | +7.04 | +5.54 / 100 % | +6.03 / 100 % |
| O(3,5,10) | +6.75 | +6.75 | +5.29 / 100 % | +5.84 / 100 % |
| S_f100 | +5.05 | +5.09 | +3.32 / 83 % | +4.25 / 89 % |
| H_f0 | +7.26 | +7.26 | +5.67 / 100 % | +6.20 / 100 % |

The 100 % shares are not evidence of robustness of a *strategy*: the oracle has future
knowledge on every resampled path by construction. They say the prize is not a few-path
artifact of the historical window.

## 6. The precision frontier and the tax-free overlay

After-tax Δ vs K (pp) on the three windows, sell-based and virtual-hedge:

| f | trigger precision | sell 2000/2010/2017 | hedge 2000/2010/2017 | sell min |
|---|---|---|---|---|
| 0.00 | 1.000 | +4.05 / +6.45 / +7.26 | +3.98 / +6.39 / +7.40 | +4.05 |
| 0.25 | 0.494 | +3.46 / +5.83 / +5.33 | +3.17 / +5.42 / +5.07 | +3.46 |
| 0.50 | 0.328 | +2.71 / +4.65 / +4.65 | +2.46 / +4.24 / +4.67 | +2.71 |
| 0.75 | 0.246 | +3.81 / +5.26 / +4.40 | +3.32 / +4.63 / +4.27 | +3.81 |
| 1.00 | 0.196 | +4.21 / +5.22 / +4.63 | +3.72 / +4.66 / +4.62 | +4.21 |

**There is no break-even precision** (the frontier never reaches 0). This is structural, not
a numerical accident: every pre-registered "false alarm" is still a ≥3 % pullback whose low
the oracle knows, so selling at the trigger and buying at that low is profitable on its own;
the sweep measures the cost of extra round trips (turnover 6.35 → 7.88/yr, tax on the extra
realisations) rather than the cost of an imprecise trigger. The non-monotonicity in f comes
from the fixed Bresenham subsets (f = 0.5 skips deeper shallow episodes that f = 1.0 catches),
not from precision itself. **The binding constraint is the exit, not the entry.**

**The tax-free overlay does not beat the sell.** `H_f0` (no QLD sale ever, untaxed hedge)
earns +3.98 / +6.39 / +7.40 pp after tax vs `O(3,3,10)`'s +4.05 / +6.45 / +7.26 — a wash.
The account pays roughly the same tax either way (hedge +$1.30 M, sell +$1.36 M on the 2000
window) because under the liquidation convention the higher terminal wealth is eventually
realised; the hedge defers the tax but does not avoid it. The tax wedge on the prize is
**26 / 27 / 29 %** for `O(2,0,8)` and **22–24 %** for `O(3,3,10)`. Turkish tax is a real
25 % haircut, not the explanation for why the detectors failed.

## 7. Interpretation — why ~30 detectors failed

1. **The prize is large** (≥ +3 pp after tax even for the most lagged cell; +7–11 pp for a
   perfect one). "No detector worked" is not evidence that there was nothing to win.
2. **Precision is not the bottleneck** in the measured design: a 19.6 %-precise trigger still
   earns +4–5 pp if it exits at the low. A detector that only decides *whether* a pullback is
   real, without an exit rule, is aiming at the part of the problem this study shows is cheap.
3. **The bottleneck is the exit.** The oracle's advantage is being out for the descent and
   back in at the low. A 3/5-session delay after the low costs 1.2–3.0 pp; a detector that
   misses the low by more than that would lose the rest. The failed detector families
   (VIX, stops, RSI, vol estimators, CUSUM, drawdown velocity) were all entry/trigger
   devices; none of them solved the re-entry timing, and several (stops) actively sell the
   low.
4. **Tax is a 22–29 % haircut, and not avoidable by overlay.** The tax-free hedge is no
   better after tax than selling; deferral does not dodge the liquidation tax.
5. **The prize is concentrated in a few percent of the time**: the ≥10 % class occupies
   7.9 % of K's drawdown time (11 episodes in 26.5 years); the ≥8 % class 10.9 %.
   A real detector must be right on ~11–18 occasions, not continuously.

A realistic reading: **a perfect detector is worth ~+7 pp/yr after tax; a detector with a
3–5-session exit lag ~+3–5 pp/yr; a perfect trigger with 20–30 % precision ~+4–5 pp/yr.**
The half of the prize that comes from bear starts (E2-off episodes, 37–43 % of the avoided
loss) is partly already captured by K's own trend exit; the other half (57–63 %) is the
inside-uptrend pullback this study was asked about.

## 8. Caveats

* **The oracle is not implementable.** It uses future knowledge of (a) whether an episode
  will reach X and (b) where the low is. It is an upper bound, labelled ORACLE throughout.
  Do not read the deltas as achievable returns.
* **All arms know the low.** The only exit-timing degradation measured is the k = 0/3/5
  restore lag. The false-alarm sweep perturbs the trigger, not the exit, which is why it has
  no break-even. A detector that mis-times the bottom is not measured here and would do
  worse; §7's "realistic" figures are an interpolation between the k = 0 and k = 5 arms, not
  a simulation of a wrong bottom.
* **Episode-end choice.** Ending an episode at the first E2-off close is an interpretive
  choice made in the pre-registration. Without it, the March-2000 high defines one 15-year
  episode and the 2003–2014 pullbacks disappear from the census. Episodes ended this way are
  reported (29 of 592; 3 of the 11 in the 10 % class).
* **After-tax model.** The published 2026 tariff with Yİ-ÜFE indexation, FIFO, instalments
  and liquidation at the window end; $100k. No new-resident exemption, no wealth tax, no
  account-level optimisations. Not tax advice. The virtual hedge's effective price path puts
  the hedge payout into later realisations, which can only raise its tax (conservative).
* **Bootstrap ≠ out-of-sample.** The clean bootstrap resamples QQQ returns on a fixed recent
  calendar with the realised TRY path; it supports the "not a few-path artifact" claim, not
  a forward-looking one. No OOS windows, 21-ETF or cost-stress stages were run: pre-registered
  as not applicable to a diagnostic.
* **Data end 2026-09-29.** The Oct-2026 episode that motivated the study is outside the
  sample. The last in-sample episode, 2026-06-02 (11.3 %, 77 sessions), is in the 10 % class.
* **No gates, nothing adopted.** Grid, mechanics and stages were fixed before the arms ran;
  no parameter was changed after seeing results. The descriptive prize attribution in §3 was
  added for the report from the same pre-registered episodes and is not an arm.

## 9. Reproduction

* `python -m research.short_term_oracle_v1.screen --harness` runs
  `python -m research.fast_engine.parity --quick` (**PASS**, all sections) and checks:
  K's anchors (pre-tax 15.7649 / 15.1785 % CAGR, 46.6698 / 46.7948 % max DD; after tax
  10.7533 / 15.2354 / 23.9720 % and K−R2 +0.5933 / +0.5294 / +1.0859 pp), all within
  0.01 pp; the episode-mask kernel against a Python reference for all 16 distinct specs;
  the virtual-hedge all-zero-mask identity and the never-firing-oracle identity; and the
  bootstrap extension against `fe_boot.boot` on raw per-path arrays (8/8 cells, exact).
* `--run` executes parity + every stage in **≈14 s** (wall clock in `SUMMARY.txt`) and is
  deterministic: two consecutive runs produce **byte-identical** `RESULTS.json`
  (SHA-256 `8909ec45…f43e6b`; verified with `cmp`).
* `--smoke` runs synthetic-only checks (episodes, masks, Bresenham, hedge zero-return).
* Files: `research/short_term_oracle_v1/{PREREGISTRATION.md, screen.py, RESULTS.json,
  SUMMARY.txt, DATA_MANIFEST.json}`, `engine/fe_boot_targets.py` (bootstrap with exogenous
  targets; the only new kernel, and inert when given K's targets). Inputs restored unchanged
  from the tags listed in `DATA_MANIFEST.json`; no production file was read or written, no
  network access, no CLI run.
