# ladder_v1 — the early-leg conviction ladder (`idea_survey_v3` I4) versus production K

Pre-registered 2026-10-15, **before any non-control arm of this study was simulated on real data
on this branch**. Working branch `research/ladder_v1`; code in `research/ladder_v1/`
(`screen.py`, `tax_engine.py`). Control identity: the production rule since 2026-10-15,
**K = R2 + A1u + H2** (`equity_leveraged_trend_v1` 1.2.0 plus the K execution layer).

## 0. Provenance of this harness

`screen.py` is a copy of `research/band_h2_combo_v1/screen.py` (the K control harness) with the
changes listed in section 6; `tax_engine.py` is a byte copy of
`research/band_h2_combo_v1/tax_engine.py`. The parent's published K/R2 cells are committed here as
`K_ANCHORS.json` (extracted from `research/band_h2_combo_v1/RESULTS.json`, itself committed on
branch `research/band_h2_combo_v1`) and every one of them is asserted in `--harness`.

## 1. Question

Production K enters the early re-entry path at a **constant** half of the production weight
(`0.5 × vt`, 164 sessions over 2000-03-09 → 2026-09-29, 32 episodes, three of which are 95 % of the
path's +19.72 pp of relative gain). Does sizing that leg by a volatility-normalized **conviction
metric** — more weight when the recovery is strong relative to its own vol, less when it is
marginal — beat K after Turkish income tax, without a materially worse drawdown or a worse OOS
drawdown profile? `idea_survey_v3` I4, arms fixed by that survey.

Known bounds stated up front (`idea_survey_v3` §5 I4): `fast_reentry_v1`'s R1 (the same path at
*full* weight) earned 16.17 % against R2's 15.48 % with a *lower* in-sample max DD (44.6 % vs
46.8 %) and failed only the OOS drawdown gate (+4.60 pp ^IXIC, +5.13 pp ^FTSE against a +3 pp
limit); `rule_variants_v1` D5 (an *on-state* strength band) lost 4.4 pp with 20.7×/yr turnover.
So the ladder's upside is bounded near the R1−R2 gap and its downside is R1's OOS behaviour.
**Every arm here raises exposure rather than removing realizations, so tax survival is the weakest
link: the arm wins after tax only if the extra pre-tax return exceeds the extra tax at the
27–35 % marginal bracket it pushes into.** The after-tax gates are therefore expected to bind.

## 2. Data

Nothing is downloaded. Every input is an existing committed research CSV in this worktree,
unchanged from `research/band_h2_combo_v1`; `DATA_MANIFEST.json` records the source, byte count and
SHA-256 of each file, and of the two pieces of **evidence** used for reproduction
(`K_ANCHORS.json`, `PARENT_K_RESULTS.json`).

Uses: `stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars, DTB3),
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY conversion, Yİ-ÜFE indexation),
`ewma_band_v1_{gspc,index,tb3ms}.csv` (the six OOS windows). No instrument panel: this study's
stages run on the QQQ main series and the six OOS indices only.

## 3. Mechanics

### 3.1 The control (C0 = production K, exactly)

R2's target series + K's execution layer, through the single sequential engine
(`tax_engine.py`, the corrected `vt_level_tax_v1` / `r2_tbill_combo_v1` engine):

- **Target series (production R2).** E2: hold at least 2 of 3 votes on the QQQ price close
  (`close > SMA200`; 252-session price return > 0, zero hurdle; `close >` the 126-session
  max/min midpoint), sized `vt = min(1, 0.35/vol20)` with a **0.98** total cap; while E2 is off,
  an **early re-entry path** holds **0.5 × vt** if `close > SMA50` **and** `vol20 <` its trailing
  252-session median **and** `close > close 20 sessions ago`, exiting below SMA50; E2's own vote
  turns the early path off. Decide on the completed close, fill at the next open.
- **K execution layer.** (a) **A1u**: a discretionary downward resize (`0 < target < held`) fills
  only if the drift exceeds **0.5 / 3 / 8 pp** when the USD FIFO gain fraction of the units the
  sell would consume is ≤ 1 % / ≤ 5 % / > 5 % (`g_frac = max(0, Σ_FIFO take·(per_unit −
  lot_usd_cost)) / (units·per_unit)`, USD only). (b) **H2**: such a downsize whose **fill** session
  is one of the **last 10 December NYSE sessions** is postponed to January (tested first; a
  postponed fill is never re-tried, the rule is re-evaluated at every close). Entries, exits to
  zero and **up-resizes** keep the 0.5 pp band.
- Financing: synthetic QLD\* = daily-reset 2× on QQQ total return, 0.95 %/yr expense, financing
  DTB3 + {base 0.0 %, calibrated 0.75 %}/yr; cost 4 bps × m × |Δw|; idle cash earns zero.

### 3.2 The only change: the early-leg weight g(m)

While the early path is active, the leg is sized `g(m) × vt` instead of `0.5 × vt`. The metric is
read on the **QQQ decision close** (the series the votes and the entry filter already use):

```
m   = (close / SMA50 − 1) / (vol20 / sqrt(252))
m20 = (close / close[−20] − 1) / (vol20 / sqrt(252) · sqrt(20))
```

with `vol20` the **annualized** QQQ close vol20 the early path's vol filter already computes.

*Interpretation fixed in advance.* `idea_survey_v3` writes the m20 denominator as
`vol20 · sqrt(20)`; taken literally with the annualized `vol20` its scale would differ from `m` by
a factor `sqrt(252)` and the survey's "same 0.5 / 1.5 cuts" would be meaningless. The
scale-consistent reading — annualized vol20 / sqrt(252) = daily sigma, times sqrt(20) for a
20-session sigma — is the one implemented, and it is the only reading under which the two metrics
share a scale. Both formulas are asserted directly in `--smoke`.

| arm | survey name | rule | pre-registered parameters |
|---|---|---|---|
| **C0** | (control) | production K: `g = 0.5` constant | — |
| **L1** | E1 | three-step ladder | `g = 0.5 / 0.75 / 1.0` on `m < 0.5` / `0.5 ≤ m < 1.5` / `m ≥ 1.5` |
| **L2** | E2 | two-step ladder | `g = 0.5` for `m < 1.0`, else `1.0` |
| **L3** | E3 | continuous | `g = clip(m / 2, 0.5, 1.0)` |
| **L4** | E4 | momentum metric | L1's three steps with the same `0.5 / 1.5` cuts, keyed on `m20` |

The arm labels are renamed from the survey's E1–E4 because this harness already uses `E1`–`E4` for
its parent campaign's *stages* and `E2` for the pure-E2 anchor series; the mapping is exact and
fixed. Every other quantity — the early-path state (entry, exit, the E2-vote override), the E2
vote, the sizing `vt`/cap, the instrument, the band, the deferral, the cost, the capital — is
identical across arms, and **K's execution layer applies to every arm identically**: the ladder
also changes the *transfers* between the full weight and the early leg, and a down-size between
two positive targets is a discretionary downward resize (subject to the A1u band / H2 window) in
every arm, exactly as in the parent harness.

Note the direction of the pre-registered cuts, recorded before any result: on the control's 164
early sessions the metric distribution is `m` p25 1.19 / median 3.05 / max 8.02 and `m20` p25 0.26
/ median 0.80 / max 2.88, so L1/L2/L3 raise the early weight on most of the path (mean `g`
0.89/0.90/0.85) while L4 is the conservative one (mean 0.70). **The cuts are not tuned after
seeing this**; they are the survey's.

### 3.3 Sizing and cap

`sized_wants`: `raw = on_eff × factor × vt`, then a proportional scale-down so `raw ≤ 0.98`. For
an on-state session `factor = 1.0` in every arm (E2's own vote overrides the ladder), so the
ladder cannot touch the on-state weight.

## 4. Stages (all run by `screen.py --run`)

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions, session 253 onwards), m = 2, cost
4 bps × m × |Δw|, base financing unless stated, progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE where after tax, **liquidated** at the window end.

- **pre** — pre-tax main window, base/calibrated × m = 1/2/4, plus the R-USD row. G1–G4, G6, G9.
- **AT** — after tax at the $100k gate capital and, report-only, $10k/$30k/$300k/$1M, on the
  three windows 2000-03-09→2026-09-29, 2010-01-01→2026-09-29, 2017-01-03→2026-09-29. A1.
- **B12** — clean bootstrap on the fixed 2015-09-01 → 2026-09-30 calendar with its realised
  USD/TRY and Yİ-ÜFE, `seed = 20261011`, `block = 20`, **5,000 pre-tax** / **1,000 after-tax**
  paths, $100k, B1-recent (progressive) and B2 (flat 20 %), market returns block-resampled as in
  `tax_asym_v2` / `vt_level_tax_v1` / `band_h2_combo_v1`. G5 and A2.
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, m = 2,
  R-USD rule. G7.
- **OOSB** — pooled out-of-sample block-20 bootstrap over M1–M5, 2,000 paths each, `seed =
  20261011`. G8.
- **R2ROLL** (report) — 21 rolling January after-tax starts 2001…2021 at $100k, base, m = 2.
- **EP** (report) — per-episode early-path P&L and leave-one-episode-out for the three big
  episodes.
- **PLAT** — the pre-registered plateau check on the two "steps" arms (L1, L4).
- **E5** (report) — how often each ladder changes a fill decision on the main path.

## 5. Gates (pre-registered; every gate must pass)

Versus the K control, `ewma_band_v1`'s G1–G8 as it defines them:

- **G1 (m = 2, both fin.)** CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 − 0.5 pp.
- **G2 (both)** max DD ≤ C0 + 1 pp.
- **G3 (both)** 2017+ CAGR ≥ C0 − 1 pp.
- **G4** CAGR above C0 in at least 2 of 3 sub-periods (P1 2000–08, P2 2009–16, P3 2017→, base m = 2).
- **G5 (both)** block-20 bootstrap (seed 20261011, 5,000 ten-year paths) median of arm − C0 ≥ 0.
- **G6 (both)** G1 at m = 1.
- **G7** six OOS windows, calibrated, m = 2: median Δ CAGR ≥ 0; in ≥ 5 of 6 windows Δ CAGR ≥
  −0.5 pp **and** Δ DD ≤ +3 pp; Δ DD never > +8 pp.
- **G8** pooled OOS bootstrap over M1–M5 (2,000 paths each) median Δ ≥ 0.
- **G9** turnover/yr ≤ **2 × C0** (the ladder trades inside the early path; the pre-registered
  limit is the two-times one, as `ewma_band_v1` used for its W1).
- **A1** after tax at $100k: arm − C0 ≥ 0 in **all three** windows.
- **A2** clean bootstrap at $100k: B1-recent **and** B2 median arm − C0 ≥ 0 with share > 0 ≥ 55 %.

Plus the plateau gate the survey asks for:

- **P1** for each of L1 and L4, the sign of (i) the pre-tax base m = 2 Δ CAGR and (ii) the
  after-tax 2000-window Δ CAGR at $100k, both versus C0, must be **unchanged** under both
  alternative cut-sets **(0.25 / 1.0)** and **(1.0 / 2.0)**. A value inside ±0.01 pp counts as zero
  and cannot flip; a sign change of two non-zero values fails the gate.

**Selection rule.** The passing candidate with the highest base m = 2 CAGR; if none passes every
gate, production K stands unchanged.

**Reported but not gated.** Every arm & every stage cell; the R2 reference row (the pre-K rule,
band off, no deferral); the per-episode P&L; leave-one-episode-out; the rolling starts; the
$10k–$1M capitals; the m = 4 cost stress.

**Leave-one-episode-out (defined in advance).** Episodes are the maximal runs of the early path
being held (arm-independent), each extended by one session so it carries its reconciliation fill.
For the three pre-registered episodes — entries **2001-10-31**, **2002-11-04**, **2023-01-20** —
the arm's target series is replaced by the control's inside that span, and the remaining pre-tax
Δ CAGR versus C0 is reported. It answers "how much of the ladder's edge is that episode".

## 6. Deviations from the parent harness (all made before any run)

1. Arms and rules: `ARMS = (C0, L1, L2, L3, L4)`, `RULE[arm] = K_LAYER` for every arm
   (`band`, `usd_rule`, `defer` all on); the pre-K rule is kept as the reference label `R2`
   (band off) and `A1u`/`H2`/`K` remain only so `--smoke` can still pin the engine against the two
   reference pre-tax simulators.
2. Added `sma_val`, `conviction_metric`, `ladder_g`, `ladder_state`, `ladder_wants`, `wants_for`,
   `early_episode_spans`; `ladder_state` reproduces `r2_state` for the reference labels.
3. Every stage now drives each arm with **its own** target series (the parent drove every label
   with R2's); `main_inputs` additionally returns the QQQ price series and the early components.
4. Removed the parent's 21-ETF E1 stage, its quality rule, the rolling-start gate and the fresh
   E3 bootstraps and the `gain_band_v3` cell-by-cell reference check (not this study's evidence);
   added the ladder stages (`stage_episodes`, `stage_plateau`, `plateau_ok`, `gates_ladder`).
5. New anchor block: the control is asserted against **K**'s published cells (pre-tax
   CAGR/max DD/turnover at m = 1/2 under both financing models, the three after-tax windows at
   $100k, the six OOS windows, the B1/B2 medians) and the `R2` reference row against R2's, all from
   `K_ANCHORS.json`; the paired bootstrap is pinned by `R2_vs_C0` = −(the parent's `K_vs_C0`).

## 7. Honest risks

1. **The path is a three-episode bet** (2001-10-31 +8.58 pp, 2023-01-20 +6.75 pp, 2002-11-04
   +3.45 pp of v1's +19.72 pp), so a ladder is being fitted to three events; leave-one-episode-out
   is reported (`fast_reentry_v2`: R2 survives removing any one, +0.45/+0.52/+0.65 pp).
2. **OOS drawdown is R1's documented failure mode**; the +3 pp per-window limit is likely to bind
   for the aggressive arms (L1/L2/L3 all sit near full early weight on most of the path).
3. **Tax.** The ladder trades inside the early path (extra up/down-steps), raising the sell count
   and the realized gain; the after-tax windows and A2 are the binding test, not a formality.
4. **If all four land between R2 and R1 on CAGR and above R2 on DD**, the honest reading is "the
   early weight is a lever, not a signal", and the answer is to leave 0.5 alone.
5. The metric distribution check in §3.2 is a control-side, arm-independent inspection; no arm was
   simulated on real data before this file was committed.

## 8. Amendments (post-pre-registration; no arm result was observed before or during them)

The pre-registration (commit `d04343bd`) fixed the arms, parameters, gates and selection rule; none
of them changed. Three changes were needed to make `--run` work at all, and they were made with
**no candidate result written or read** (the aborted run below exited on an exception before
`RESULTS.json` or `SUMMARY.txt` existed):

1. **Print fix in `main()`** (report-only). The harness anchor block printed every check with
   `v['reference']`, but two checks hold a dict instead; the `--harness` run therefore crashed
   *after* all 60 anchors had reproduced (the printed block showed every one `ok`). The loop now
   prints dict-valued checks as dicts. No gated metric, arm or gate changed.
2. **The first `--run` attempt** (log kept out of the repo) passed the whole harness (60 anchors,
   0 outside tolerance) and the pre/after-tax/cost/OOS stages, then **aborted inside the OOS
   bootstrap on the L3 arm**: `^N225` (M3) block-resampling produces stretches of ~20 identical
   closes, where `vol20 == 0` and the conviction metric is undefined. No result was written.
3. **Degeneracy rule for an undefined metric (pre-committed here, before any arm result existed).**
   Where the early path is active but the metric is not finite, the arm falls back to the control's
   **0.5** early weight. Rationale: a zero-volatility stretch carries no conviction information, so
   0.5 is neutral by construction and can only damp, never flatter, a candidate. `ladder_g` now
   propagates a non-finite metric for **every** shape (a bare `np.where` comparison against NaN
   would have sent the `steps`/`twostep` shapes quietly to the top step — i.e. to full weight on a
   zero-vol session, the opposite of the rule). `ladder_state` tallies the sessions where the rule
   fired, and the tally is reported per bootstrap and in `RESULTS.json` under `degeneracy`.
   `--smoke` pins the contract: a non-finite metric reaches the fallback and yields exactly 0.5.
