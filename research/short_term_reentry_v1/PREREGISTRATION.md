# short_term_reentry_v1 — tradeable short-term exit + rebound-confirmed re-entry on top of K

Status: **candidate study with gates.** This file was committed before any non-control arm was
simulated on real data. Nothing here is proposed for adoption on its own; a passing arm would be a
candidate for an owner decision.

Control: production **K** exactly (`equity_leveraged_trend_v1` 1.2.0 = R2 + A1u gain band + H2
December deferral), reproduced through `research/fast_engine`. R2 is reported as a reference row.
K's published anchors that `--harness` must reproduce within 0.01 pp: pre-tax CAGR 15.7649 %
base / 15.1785 % calibrated, max DD 46.6698 % / 46.7948 %, m = 1 16.0173 %; after tax at $100k,
progressive, liquidated, 10.7533 / 15.2354 / 23.9720 % on the 2000 / 2010 / 2017 windows;
K − R2 = +0.5933 / +0.5294 / +1.0859 pp. R2 reference: 15.4766 / 14.8961 % CAGR, DD 46.7584 /
46.8801 %, after tax 10.1600 / 14.7059 / 22.8861 %.

## 1. Question

`short_term_oracle_v1` measured the prize for a short-term drawdown detector that exits K's QLD
position on a 2–3 % QQQ pullback from the 20-session high and restores it 0–5 sessions after the
episode **low**: +4…+11 pp/yr after Turkish tax, and false alarms barely hurt, because the oracle's
restore still knows the low. The binding problem is therefore the **re-entry**, not the trigger.

This study tests the tradeable version: exit on the pullback, re-enter on a **rebound measured from
the post-exit low**, with no future data anywhere. It differs from the failed short-term families
(`intraday_stop_v1`: fixed cooldown re-entry → whipsaw; `dip_overlay_v1`: buying dips; `vote_
hysteresis_v1`; the drawdown-velocity/governor arms) in exactly that re-entry device.

## 2. Data and windows

All inputs are existing committed research files (see `DATA_MANIFEST.json`, SHA-256 recorded
there), plus one report-only Yahoo extension for the October 2026 episode (never a gate).

* QQQ open/close/dividend and SPY from `data/historical/stress_robustness_v1_daily.csv`; DTB3 from
  `stress_robustness_v1_dtb3.csv`.
* Synthetic daily-reset 2x QLD*: `fe_signals.synthetic_2x(QQQ open, close, dividend, DTB3 +
  spread, 0.0095)`, financing spread 0 (base) or 0.75 pp (calibrated).
* Turkish after-tax engine: CBRT USD/TRY buying rate, Yİ-ÜFE; 2026 tariff, indexation,
  instalments, FIFO, liquidated at the window end (`research/fast_engine/fe_tax.py`).
* Main window 2000-03-09 → 2026-09-29 (6,679 sessions). After-tax windows (progressive, $100k,
  base financing, m = 2): 2000-03-09 → 2026-09-29, 2010-01-01 → 2026-09-29,
  2017-01-03 → 2026-09-29.
* Cost 4 bp per side × m = 2. Idle cash earns zero.
* Clean bootstrap: fixed 2015-09 → 2026-09 calendar with its realised USD/TRY and Yİ-ÜFE; market
  returns resampled, seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths, $100k,
  B1-recent (progressive) and B2-flat (20 % on the annual net realised USD gain, losses carried
  forward).
* OOS: `ewma_band_v1`'s six windows (^GSPC 1957–1999 + M1–M5), calibrated, m = 2, R-USD rule, plus
  the pooled OOS bootstrap (M1–M5, 2,000 paths each, block 20, seed 20261011).
* October 2026 episode: report-only Yahoo download of QQQ/QLD daily bars
  (`short_term_reentry_v1_{qqq,qld}_2026q4.csv`, 2026-09-11 → 2026-10-22). Overlap with the
  committed bars: QQQ closes identical to 3e-5, no dividend differences. Pre-tax only, outside
  the gated window.

## 3. The overlay rule (fixed here, before any arm ran)

All decisions are on the **QQQ price close**; the engine fills at the next open. K's own weight
path (`w[t]`, R2's target: `min(1, 0.35/vol20) × factor`, capped at 0.98) runs underneath,
unchanged. The overlay only replaces `w[t]` with 0 while it is **out**.

State per path: `st ∈ {in, out}`, `L` (post-exit low), `stint_start` (first session of the current
in-stint), `exit_t`, `H_exit`.

1. **Reference high.** `H[t]` is the highest close over the trailing 20 sessions ending at `t`
   (`t−19 … t` inclusive), **truncated at `stint_start`**: `H[t] = max(close[max(stint_start,
   t−19) … t])`. A new in-stint begins (a) at the overlay's re-entry and (b) after a session with
   `w[t] ≤ 0` (K re-entry). At the first measured session the stint starts at `i0 − 19`, i.e. the
   full trailing-20 window is used.
   *This is the single interpretive choice of the study.* With an untruncated rolling high, a
   re-entry is followed by an immediate re-exit on the next close whenever the price is still `d` %
   below the pre-exit high — which is the normal case — so the rebound rule would be inert and the
   arm would be a one-way "sell and stay out until near-full recovery" rule. Truncating at the
   re-entry makes each re-entry a fresh arm of the detector; a further `d` % decline is a fresh
   exit. Fixed before any arm was simulated.
2. **K is out.** If `w[t] ≤ 0`: `st = in`, new stint at `t`, target 0. If the overlay was out, the
   episode ends here without a buy (recorded as a K-reset, not a re-entry).
3. **Exit.** While `st = in` and `w[t] > 0`: if `close[t] ≤ (1 − d) × H[t]` then `st = out`,
   `L = close[t]`, `exit_t = t`, `H_exit = H[t]`, target 0. Otherwise target `w[t]`.
4. **Re-entry.** While `st = out` (so `w[t] > 0`): update `L = min(L, close[t])` (the exit close is
   included), then at the first session `t > exit_t` with
   * rebound arms: `close[t] ≥ (1 + r) × L`, or
   * SMA arms: `close[t] > SMA10[t]`, `SMA10[t]` = mean of the 10 closes ending at `t` (inclusive;
     the condition is false before 10 closes exist),
   set `st = in`, new stint at `t`, target `w[t]`. Until then the target is 0.
5. A re-entry is never evaluated at the exit session itself, so the minimum time out is one session
   (`exit_t + 1`). Comparisons are exact float comparisons as written (`≤` for the exit, `≥` for
   the rebound, `>` for SMA10).
6. Overlays can fire repeatedly over the window. K's A1u band, H2 deferral and the 0.5 pp resize
   band govern every other resize; an exit to zero and a re-entry from zero always fill (K's
   execution layer). The overlay sells/rebuilds the **whole** position, early leg included.

## 4. Arms (6, fixed; no others)

| arm | `d` | re-entry | label |
|---|---|---|---|
| **A** | 3 % | rebound `r` = 2 % above `L` | `A_d3_r2` |
| **B** | 3 % | rebound `r` = 4 % above `L` | `B_d3_r4` |
| **C** | 5 % | rebound `r` = 2 % above `L` | `C_d5_r2` |
| **D** | 5 % | rebound `r` = 4 % above `L` | `D_d5_r4` |
| **E** | 3 % | `close > SMA10` | `E_d3_sma10` |
| **F** | 5 % | `close > SMA10` | `F_d5_sma10` |

Control `K`; reference row `R2`.

**Tax-free diagnostic variants (non-selectable).** For every arm, a variant that never sells: K's
target series and the **same overlay intervals**, but the 2x leg's daily total return is set to
zero on the sessions the sell arm is out (a costless, untaxed overlay). Implemented as an effective
price path (an input transformation, the existing `fe_boot_targets.masked_prices` kernel), with the
mask shifted one session (`hm[1:] = mask[:-1]`, i.e. zero the return on `exit+1 … re-entry`), the
same alignment as `short_term_oracle_v1`. K's target series is computed from the real path.
Labels `Ah … Fh`; they are **diagnostic upper bounds for an option overlay, not candidates**, are
exempt from the gates and are excluded from selection. The all-zero-mask identity (K's NAV bit for
bit) is a harness check.

## 5. Gates (fixed in advance)

Pre-tax, versus K, `ewma_band_v1`'s definitions with C0 → K:

* **G1** (m = 2, base **and** calibrated): CAGR ≥ K, **or** Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp.
* **G2** (both financings): max DD ≤ K + 1 pp.
* **G3** (both): 2017+ CAGR ≥ K − 1 pp.
* **G4**: CAGR above K in at least 2 of 3 sub-periods P1 2000–08, P2 2009–16, P3 2017→end (base).
* **G5** (both): clean pre-tax bootstrap (seed 20261011, block 20, 5,000 paths) median of
  arm − K ≥ 0.
* **G6**: G1 at m = 1 (both).
* **G7**: six OOS windows, calibrated, m = 2: median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp **and** Δ DD
  ≤ +3 pp in at least 5 of 6 windows; Δ DD never > +8 pp.
* **G8**: pooled OOS bootstrap over M1–M5 (2,000 paths each): median Δ CAGR ≥ 0.
* **G9**: turnover ≤ 2 × K.

After Turkish tax at $100k (progressive, liquidated, base financing, m = 2):

* **G10**: after-tax CAGR > K in **all three** windows.
* **G11**: clean bootstrap B1-recent (1,000 paths): median Δ ≥ 0 **and** share of paths > 0 ≥ 55 %.
* **G12**: B2-flat: median Δ ≥ 0 and share > 0 ≥ 55 %.

Every gate must pass on both financings where stated. All six windows and both bootstraps are
usable for a close-only rule, so no reduced gate is pre-registered.

**Selection rule.** Among arms passing G1–G12, select the one with the highest after-tax CAGR on
the 2000-03 → 2026-09 window; ties by pre-tax base m = 2 CAGR, then by label. If none passes, no
arm is selected and the study reports the measured trade-off. The diagnostic hedge variants are
never selected. No arm is adopted by this study; a selected arm would be a candidate for an owner
decision (it changes the production strategy, which this study does not).

## 6. Reported diagnostics (report-only, no gate)

Per arm, main window, base model: exits per year; share of exits that were **false alarms**
(the episode's pullback from `H_exit` to its minimum close before the episode ends was < 8 %),
with the depth distribution (<2, 2–3, 3–5, 5–8, 8–10, ≥10 %); the mean and median fill-price
change from exit to re-entry (`open[re+1] / open[exit+1] − 1`) and the share of re-entries above
the exit fill; episodes still out at the data end (and those ended by K going to zero) are
reported separately and excluded from the price statistics. Realised tax (USD) and sells/year are
reported for every arm. October 2026: the extended-series pre-tax return of K and each arm from
the episode's peak close, their maximum drawdown inside the episode, and the overlay state/exits
during it.

## 7. Implementation and reproducibility

* Engine: `research/fast_engine` (mandatory). Simulation, tax and bootstraps go through
  `fe_sim.sim_one` / `fe_sim.simulate` / `fe_tax`; bootstrap draws are `fe_boot.draw_positions`,
  the market reconstruction is the oracle study's `fe_boot_targets.chunk_legs_pc` (a verbatim
  copy of `fe_boot.chunk_legs` that also returns the QQQ closes). Only the **target-weight arrays**
  are new (`research/short_term_reentry_v1/engine/fe_overlay.py`, a numba signal kernel) — no
  simulation, tax or bootstrap loop is written here, and `research/fast_engine/` is not modified.
  An arm is therefore (price arrays, target array, K's fill rule), exactly as in the oracle study.
* `--harness` runs `python -m research.fast_engine.parity --quick` (must PASS), K's anchors and
  K−R2 deltas above, the overlay kernel against a Python reference for all six specs on the real
  QQQ series (mask and every episode record), the never-firing identity (mask 0 ⇒ exactly K's
  target array and K's NAV bit for bit), the hedge all-zero-mask price identity and a zeroed
  return on a masked session, `chunk_legs_pc` vs `fe_boot.chunk_legs`, and the bootstrap extension
  with K's targets vs `fe_boot.boot` on 200 paths (every cell bitwise).
* `--run` runs harness + every stage and writes `RESULTS.json` / `SUMMARY.txt`; the wall clock is
  in `SUMMARY.txt`. Two consecutive runs must give byte-identical `RESULTS.json`.
* No change to `src/`, `config/`, `tests/`, `scripts/`, `benchmarks/`, `.env*`, `data/runtime/`,
  `logs/`, `research/fast_engine/`. No CLI run.

## Amendments (after the run; no arm, parameter or gate changed)

1. **Pre-run implementation fix (hedge price assembly).** The committed code assembled the
   six diagnostic hedge price paths with a 1-D output buffer where the kernel expects a
   `(1, T)` row; the arrays came back length 1. The harness caught it before any graded run
   (the NAV-validity sweep over every arm and window). Fixed by allocating the buffer as
   `np.empty_like(o)[None, :]`, and `R5` was extended with an assembled-hedge-path check
   (length, zero return on exactly the masked sessions, the real return elsewhere, the hedge
   target equal to K's) — 4,958 masked sessions verified. The registered arms, the rules,
   the gates and the selection rule are untouched; the pre-registration commit `27bc4e22`
   remains the fixed specification.
2. **Report-only diagnostics added after the run.** The diagnostics block also reports the
   2x leg's mean and compounded close-to-close return on the sessions the tax-free variant
   masks ("out-day ret"), and the SUMMARY's after-tax table also shows sells/yr and tax
   paid. Neither is a gate; both describe the pre-registered rule.
3. **Report-only sensitivity (not an arm).** The reference-high truncation (§3.1) was the
   one interpretive choice. The untruncated variant (a full trailing-20 high after every
   re-entry) gives pre-tax CAGR 4.89 / 8.95 / 10.20 % for A / C / F against the registered
   7.59 / 10.56 / 11.61 % — the registered convention is the *generous* one, so the verdict
   does not depend on it.

