# short_term_oracle_v1 — DIAGNOSTIC: the size of the short-term-detection prize

Status: **diagnostic / upper-bound study. No gates. Nothing here is tradeable.**
This file was committed before any non-control arm was simulated on real data.

Control: production **K** exactly (`equity_leveraged_trend_v1` 1.2.0 = R2 + A1u gain
band + H2 December deferral), reproduced through `research.fast_engine` in `--harness`
(pre-tax 15.7649 % base / 15.1785 % calibrated, max DD 46.6698 % / 46.7948 %;
after tax at $100k, progressive, liquidated: 10.7533 / 15.2354 / 23.9720 % on the
2000 / 2010 / 2017 windows; K − R2 = +0.5933 / +0.5294 / +1.0859 pp).
R2 is reported as a reference row.

## 1. Question

K reacts slowly to a 5–15 % pullback *inside* an uptrend: the E2 vote needs 2 of 3
slow votes to turn, and the A1u band suppresses most vol-target trims. In Oct 2026
QQQ fell ~6 % and the account ~12 %. About 30 real short-term detectors (VIX filters,
8/12 % stops, dip/RSI overlays, vol10/EWMA/GARCH/HAR/range vol, vol-of-vol, CUSUM,
drawdown velocity/governor) have already failed.

This study does not search for a detector. It measures the **maximum** that any
short-term drawdown detector could add to K after Turkish tax, by giving the
detector perfect foresight and then degrading it in two controlled ways:

* **lag** — restore the position k sessions after the episode low (k = 0 / 3 / 5);
* **imperfection** — fire on a deterministic fraction f of the episodes that reach
  the trigger depth d but do not reach the depth class X (false alarms).

The answer is the size of the prize and how much of it Turkish tax removes. If the
after-tax prize is small at perfect foresight, no detector can be worth building;
if it is large, the precision frontier says how precise a real detector would have
to be.

## 2. Data and windows

All inputs are existing committed research files (see `DATA_MANIFEST.json`);
no network access, no new data. SHA-256 recorded there.

* QQQ open/close/dividend and SPY from `data/historical/stress_robustness_v1_daily.csv`;
  DTB3 from `stress_robustness_v1_dtb3.csv`.
* Synthetic daily-reset 2x QLD*: `fe_signals.synthetic_2x(QQQ open, close, dividend,
  DTB3 + spread, 0.0095)`, financing spread 0 (base) or 0.75 pp (calibrated).
* Turkish after-tax engine: CBRT USD/TRY buying rate from
  `after_tax_tr_v1_usdtry_cbrt.csv`, Yİ-ÜFE from `after_tax_tr_v1_yiufe.csv`;
  2026 tariff, indexation, instalments, FIFO, liquidated at the window end
  (`research/fast_engine/fe_tax.py`).
* Main window: 2000-03-09 → 2026-09-29 (session 253 → end; 26.56 years).
* After-tax windows (progressive, $100k, base financing, m = 2):
  2000-03-09 → 2026-09-29, 2010-01-01 → 2026-09-29, 2017-01-03 → 2026-09-29.
* Cost model: 4 bp per side × m = 2 (the production research convention).
* Clean bootstrap: fixed 2015-09 → 2026-09 calendar with its realised USD/TRY and
  Yİ-ÜFE; market returns (QQQ overnight/intraday/dividend + DTB3) resampled,
  seed 20261011, block 20, 1,000 paths, $100k, B1-recent (progressive) and
  B2-flat (20 % on the annual net realised USD gain, losses carried forward).
* No OOS windows, no 21-ETF stage, no cost-stress stage: this is a diagnostic on
  the production instrument and the three production windows. Stated here because
  a candidate study would have to run them; a diagnostic does not.

## 3. Episode definition (fixed here, before looking at arm results)

The unit is a **pullback episode on QQQ closes**, not on the 2x leg.

1. A session `t` **starts** an episode iff
   (a) `close[t]` is a 20-session high: `close[t] >= max(close[t-19..t])`;
   (b) the production E2 vote on QQQ is **on** at `t` (≥ 2 of close > SMA200,
       252-session price return > 0, close > midpoint of the 126-session channel);
   (c) no episode is open (episodes are non-overlapping and are scanned forward
       from the first measured session).
2. The **episode ends** at the first later session `e > t` with
   `close[e] >= close[t]` (the start high is regained) **or** the E2 vote off
   (`e2[e] == False`), whichever comes first. If neither occurs before the data
   end, the episode is *open* and ends at the last session.
   *The E2-off end condition is the one interpretive choice made here.* A pullback
   "inside an uptrend" cannot be measured after the uptrend vote has turned and K
   is already out; without the clause the March-2000 high would define a 15-year
   "episode" and swallow every 2003–2014 pullback. It is fixed now, not after
   seeing any arm. Episodes ended by E2-off are flagged as *unresolved*.
3. The **low** is the index of the minimum close over `[t, e]`.
4. **Depth** = `1 - close[low] / close[t]`.
5. **Duration** = `e - t` sessions; the *duration to low* `low - t` is also reported.
6. Classes by depth: **X ∈ {5 %, 8 %, 10 %, 15 %}** (depth ≥ X).
7. A **nested start is not allowed**: after an episode ends, scanning resumes at
   `e + 1`. (The degeneracy of "every close is a 20-session high in an uptrend"
   is removed by the no-open-episode rule.)
8. Episode detection runs over the window being measured: the full main series
   [2000-03-09, 2026-09-29] for the main and after-tax windows, and each bootstrap
   path's own reconstructed QQQ closes over its measured sessions for B1/B2.
   The target array for a later-starting after-tax window is the full-history array;
   only its in-window sessions are executed.

Census (reported, not a gate): per class, the count, median depth, median duration,
median duration to low, the number of unresolved episodes, the share of sessions
inside an episode of that class, and the share of **K's total drawdown time**
(sessions where K's NAV is below its running peak) that lies inside the union of
that class's episode spans.

## 4. Detection and oracle arms

A detector with trigger depth `d` fires at the close of the **first** session
`c_e ≥ t` whose close satisfies `close[c_e] <= close[t] × (1 − d)`.
The engine fills at the next open (production convention), so setting the target
weight to 0 at index `c_e` means sold at the open of `c_e + 1`.

Restore: `r_e = max(low_e + k, c_e + 1)`; the target at index `r_e` is K's own
target weight (`w_r2[r_e]`), so the position is re-established at the open of
`r_e + 1`. The cut interval is the half-open index set `[c_e, r_e)`;
`target[t] = 0` there and `target[t] = w_r2[t]` elsewhere. Overlapping intervals
from different episodes are unioned. A cut to zero is an "exit to zero" in K's
execution layer and always fills; the restore is an up-resize from zero and always
fills. K's A1u band and H2 deferral continue to govern every other resize.

**Oracle grid (12 cells, fixed in advance).**
`O(d, k, X)`: `d ∈ {2 %, 3 %}`, `k ∈ {0, 3, 5}`, `X ∈ {8 %, 10 %}`.
An oracle fires on every episode of class depth ≥ X.
Labels: `O_d{2|3}_k{0|3|5}_X{8|10}`.

**Precision sweep (sell-based).** `O(3, 3, 10)` plus false alarms: the detector also
fires on a deterministic fraction `f ∈ {0.25, 0.5, 0.75, 1.0}` of the episodes whose
depth is `d ≤ depth < X` (d = 3 %, X = 10 %). Selection is by the episode's ordinal
`j = 0, 1, 2, …` among those shallow episodes in time order:
`selected(j) = floor((j+1)·f + 1e-12) > floor(j·f + 1e-12)` — exactly every 4th
episode for f = 0.25, every 2nd for 0.5, 3 of every 4 for 0.75 (j = 0, 1, 3, 4, …),
and all for 1.0. f = 0 is the oracle cell `O_d3_k3_X10`.
Labels: `S_f{25|50|75|100}` (f in percent).
The detector's precision at each f is reported as
`P(event | fire) = N_{depth ≥ X} / (N_{depth ≥ X} + f · N_{d ≤ depth < X})`.

**Tax-free variant (virtual hedge).** The same intervals and the same firing rule,
but the QLD position is kept: on the sessions the sell-based arm would be out
(`c_e + 1 … r_e`, i.e. the session immediately after the cut close through the
restore close) the 2x leg's **daily total return is set to zero** — a costless,
untaxed overlay with no fills, no realisations and no change to the target series.
Implementation: an effective QLD price path
`P_eff[s] = P_eff[s−1] · (r[s] if s not masked else 1)`, with
`o_eff[s] = P_eff[s−1] · o[s]/c[s−1]` on unmasked sessions and
`o_eff[s] = c_eff[s] = P_eff[s−1]` on masked sessions. K's target series is computed
from the **real** path, so the mask changes only the account's market returns, not
K's signal. This is an input transformation, not a new execution mechanism, so the
engine is unmodified; the harness asserts that an all-False mask reproduces the real
path bit-for-bit and that K on the masked path with an all-False mask equals K's NAV
exactly. Caveat (documented, not a gate): a later sale by K is priced on the
effective path, so the hedge payout enters the realised gain; this can only *raise*
the tax on the hedge arm, i.e. the upper bound is conservative. Alignment note:
the sell arm bears the overnight gap into `c_e + 1` and misses the overnight gap into
`r_e + 1`; the hedge covers close `c_e` → close `r_e`. The difference is one overnight
gap at each end.
Labels: `H_f{0|25|50|75|100}`; `H_f0` is the tax-free `O(3, 3, 10)`.

**Reported per arm:** pre-tax main-window CAGR (base and calibrated, m = 2), max DD,
turnover and sells/yr; after-tax CAGR and max DD on the three windows and the deltas
vs K; clean-bootstrap B1/B2 median Δ vs K and share of paths > 0; pre-tax block
bootstrap (seed 20261011, block 20, 1,000 paths, base/calibrated) median Δ vs K;
extra QLD sells/yr caused by the arm. The final answer also reports the realised
tax paid (USD) on the main window and the number of trigger episodes per arm.

## 5. Gates

**None.** This is a diagnostic, not a candidate search: no arm is proposed for
adoption, no deflation is applied, and no multiple-testing ledger entry is claimed.
The fixed grid exists to map the prize, not to select a winner. The only pass/fail
conditions are reproducibility conditions:

* R1 `python -m research.fast_engine.parity --quick` must PASS (it is run by `--harness`);
* R2 K's anchors in §0 must reproduce within 0.01 pp through this study's code;
* R3 `--run` executed twice must produce byte-identical `RESULTS.json`;
* R4 the bootstrap extension with every arm's target equal to K's must reproduce
  `fe_boot.boot` bit-for-bit (a parity cell proving the extension is inert);
* R5 the virtual-hedge all-False mask must reproduce K's NAV bit-for-bit.

## 6. Selection rule / what would change the conclusion

None — the study's output is a measurement, not a decision. The report will state,
for each depth class, the after-tax prize at perfect foresight, the price of a
0/3/5-session restore lag, the break-even precision (the f at which the after-tax
delta crosses 0), and the fraction of the pre-tax prize that Turkish tax removes.
If the after-tax prize at perfect foresight is ≤ 0 for a class, no detector of that
class is worth building; if it is positive, the precision frontier is the bar.

## 7. Implementation and reproducibility

* Engine: `research/fast_engine` (mandatory). Simulation, tax and bootstraps go
  through `fe_sim.sim_one` / `fe_sim.simulate` / `fe_tax`; the per-path bootstrap
  draws are `fe_boot.draw_positions` and the market reconstruction is a verbatim
  copy of `fe_boot.chunk_legs` extended to return the QQQ closes (asserted equal to
  the original). The only new kernel is
  `research/short_term_oracle_v1/engine/fe_boot_targets.py`, which runs a **given**
  per-path target array (the oracle masks are built in numba, signal code) through
  `fe_sim.sim_one` under `prange`; its control path with K's target arrays is
  asserted bit-identical to `fe_boot.boot`.
* `screen.py --harness` runs parity --quick and every check R1–R5; `--run` runs the
  stages and writes `RESULTS.json` / `SUMMARY.txt`; `--smoke` runs synthetic checks
  only.
* Every stage is deterministic; the bootstrap seeds are fixed above. Wall-clock of
  `--run` is recorded in `SUMMARY.txt`.
* No change to `src/`, `config/`, `tests/`, `scripts/`, `benchmarks/`, `.env*`,
  `data/runtime/`, `logs/`, or `research/fast_engine/`.
