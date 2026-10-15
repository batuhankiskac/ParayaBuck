# band_h2_combo_v1 — does the December vol-resize deferral (H2) add anything on top of the USD gain-scaled downsize band (A1u)?

Pre-registered 2026-10-15, **before any non-control arm of this study was simulated on real data
on this branch**. Working branch `research/band_h2_combo_v1`; the code lives in
`research/band_h2_combo_v1/` (`screen.py`, `tax_engine.py`), copied from
`research/gain_band_v3/` (not modified). Control identity: production **R2** =
`equity_leveraged_trend_v1` 1.2.0.

## 0. Why this study exists

The owner wants **both** devices in production if they help together:

- **A1u** — `gain_band_v3`'s implementable USD-only gain-scaled downsize band (being implemented
  now). v3 verified that A1u reproduces A1's after-tax edge on new evidence (E1 21 ETFs, E2 21
  rolling starts, the $30k/$300k scales) with a smaller pre-tax cost, and that it behaves like A1
  (52/6,679 differing fill decisions). v3's campaign verdict was nevertheless **FAIL as
  pre-registered**, on one bootstrap cell only: E3's block-10 flat-20 % tax run had a
  majority-positive share of 53.7 % against the pre-registered 55 % floor (median Δ +0.029 pp).
- **H2** — `tax_harvest_v2`'s year-end overlay: a downward vol resize whose *fill* session is one
  of the last 10 December sessions is postponed to the first January session (exits to zero are
  never deferred). H2 passes every gate of its own campaign except the pre-registered plateau
  gate, which fails on the shortest neighbour by 0.7 bp.

Both devices act on the same object — which downward resizes execute — so they can interact:
H2 defers *all* non-exit downward resizes in the December window, including the ones A1u's band
would already have suppressed, and A1u's band suppresses resizes in December that H2 would not
have touched (drift ≤ `B_down`). The question is not "is H2 good" (v2 answered that on top of R2)
but **"does H2 still add anything once the band is in place, and does the pair stay sound?"**
That composition has never been measured.

## 1. Question

Does **K = A1u + H2** beat **A1u alone** after Turkish income tax on the three standard windows
and on the two clean after-tax bootstraps, without a materially worse drawdown or a worse
turnover profile versus production R2? Secondary, for the owner's full picture: how do A1u, H2
and K compare with the incumbent C0 on every `gain_band_v3` stage?

## 2. Data and provenance

**Nothing is downloaded.** Every input is an existing committed research file in this worktree
(`data/historical/`, 29 CSVs + the 6 the OOS/instrument stages need), unchanged from
`gain_band_v3`; `DATA_MANIFEST.json` records the source, byte count and SHA-256 of each file,
plus the two **evidence** files used for reproduction (`research/gain_band_v3/RESULTS.json`,
whose A1u cells are compared cell-by-cell, and `H2_ANCHORS.json`, the H2/R2 cells copied from
`tax_harvest_v2`'s tagged `RESULTS.json`).

Uses: `stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars, DTB3),
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY conversion, Yİ-ÜFE indexation),
`ewma_band_v1_{gspc,index,tb3ms}.csv` (the six OOS windows), `range_vol_v2_*.csv` (19) +
`range_vol_v1_{spy,dia,iwm}.csv` (the 21 quality-passing ETFs; `EWC` excluded by
`range_vol_v2`'s fixed quality rule). The TRY/Yİ-ÜFE calendar starts 2000-01 / 1999-01, so no
after-tax stage starts before 2000-03-09.

## 3. Control (C0)

**Production R2, bit for bit**, through the single sequential engine `tax_engine.py` (a byte copy
of `gain_band_v3/tax_engine.py` = `tax_harvest_v2/tax_engine.py` = the `vt_level_tax_v1` /
`r2_tbill_combo_v1` corrected engine): R2's target series (E2 2-of-3 vote on the QQQ close +
the 0.5·vt early re-entry leg, `vt = min(1, 0.35/vol20)`, cap 0.98) with the production execution
layer (decide on the completed close, fill at the next open, symmetric 0.5 pp band, entries and
exits to zero always fill, idle cash zero, cost 4 bps × m × |Δw|, synthetic QLD* = daily-reset 2×
on QQQ total return, 0.95 %/yr expense, financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr).

**Anchors asserted in `--harness`** (pre-tax TOL 1e-4, after-tax 0.005 pp, as in v3): pre-tax
15.48 / 14.90 % CAGR (m = 2) and 15.75 / 15.17 % (m = 1), max DD 46.76 / 46.88 %, turnover
5.9915; after tax at $100k 10.16 / 14.71 / 22.89 % CAGR with max DD 46.76 / 43.78 / 36.24 %;
B1/B2 bootstrap medians 3.5155 / 3.9678 %; `C0 engine == simulate_band` to 1e-9; the control NAV
path differs from the pure-E2 anchor series (14.67 / 14.10 %).

## 4. Arms (4 labels; every parameter fixed, no tuning)

| Arm | Rule |
|---|---|
| **C0** | production R2 (control). |
| **A1u** | **`gain_band_v3`'s A1u, verbatim.** On a discretionary downward resize, compute `g_frac = max(0, Σ_FIFO take·(per_unit − lot_usd_cost)) / (units·per_unit)` over the live lot book in **USD only** (no FX, no Yİ-ÜFE indexation, no per-lot floor beyond `gain ≥ 0`), and suppress the resize iff `drift ≤ B_down(g_frac)` with `B_down` = **0.5 pp** if `g_frac ≤ 1 %`, **3 pp** if `1 % < g_frac ≤ 5 %`, **8 pp** if `g_frac > 5 %`. Entries, exits to zero and up-resizes keep the production 0.5 pp band. A suppressed resize is re-evaluated at every later close. |
| **H2** | **`tax_harvest_v2`'s H2, verbatim**, on R2's execution layer: a fill that would be a downward resize (`target > 0` **and** `target < held weight`) and whose fill session is one of the **last 10 December sessions** of the calendar year is **not executed**; the ordinary rule is re-evaluated at every later close (a postponed fill is never re-tried); exits to zero and up-resizes always fill. |
| **K** | **A1u + H2**, composed as follows (this is the pre-registered composition): on a pending discretionary downward resize — (1) **H2's December window is tested FIRST**: if the fill session is in the last 10 December sessions and `0 < target < held weight`, the resize is postponed; (2) **otherwise A1u's band decides**. Exits to zero (`target ≤ 0`) and up-resizes always fill. `Δw` and the fee of the decision are the same as C0's; a postponed or suppressed resize leaves the position unchanged, and the target is re-evaluated at every close. |

**Why the ordering does not matter, stated up front.** A1u's band can only suppress a resize that
H2's window would also postpone (H2's test is about the *session*, not the size; A1u's is about
the size, not the session). So "H2 first, then A1u" and "A1u first, then H2" block the same set of
trades; the ordering only changes which counter records the postponement. Implemented as
H2-first, which is exactly tax_harvest_v2's code path inside the window.

**Shared mechanics.** Every arm runs the *same* target series (production R2's `wq`); only the
fill decision differs. The rule's decision state (`g_frac`, and v3's indexation arithmetic where
it exists) is computed read-only from the live lot book, so the pre-tax and after-tax runs follow
the same trade path.

## 5. Stages (all pre-registered before any non-control arm was simulated on real data)

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions, session 253 onwards), m = 2, cost
4 bps × m × |Δw|, base financing unless stated, progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE where after tax, **liquidated** at the window end. Every arm label runs R2's
target series.

- **pre** — pre-tax main window (base/calibrated × m = 1/2/4) plus the R-USD row.
- **AT** — after tax at the $100k gate capital (and $10k/$30k/$300k/$1M report-only) on the three
  windows 2000-03-09→2026-09-29, 2010-01-01→2026-09-29, 2017-01-03→2026-09-29.
- **B12** — clean bootstrap, `seed = 20261011`, `block = 20`, 1,000 after-tax / 5,000 pre-tax
  paths, $100k (+$1M report-only), B1-recent (progressive) and B2 (flat 20 %), fixed
  2015-09-01 → 2026-09-30 calendar with its realised USD/TRY and Yİ-ÜFE, QQQ market returns
  block-resampled. The pre-tax leg is E4.
- **E1** — after tax on the 21 quality-passing ETFs under R-INDEX (real USD/TRY + Yİ-ÜFE), own E2 +
  own R2 early path, own synthetic QLD* leg (calibrated, m = 2), $100k, start = max(warm-up,
  2000-03-09), liquidated at the ETF's own end.
- **E2** — 21 rolling January starts 2001…2021 on the QQQ main series, after tax at $100k, base,
  each run to 2026-09-29.
- **E3** — fresh after-tax bootstraps, `seed = 20261015`, blocks 10 and 40, 1,000 paths, $100k,
  progressive and flat.
- **E4** — the pre-tax leg of B12 (base and calibrated) plus the main-window pre-tax max DD.
- **E5** — report only: the `$10k`–`$1M` after-tax windows, the m = 4 cost stress, and the
  fill-decision traces of all four arms on the main path.
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, m = 2,
  R-USD rule.

E1–E4 and the six OOS windows are the **parent campaign's** stages; here they are *reporting*
cells for every arm against C0. They do not gate this study.

## 6. Gates (pre-registered)

**Adoption of K over A1u alone (primary; all four must hold).**

| Gate | Requirement |
|---|---|
| **K1** | after tax at $100k, `K − A1u ≥ 0` in **all three** windows (2000/2010/2017). |
| **K2** | clean bootstrap B12 at $100k: for **both** B1-recent and B2-flat, median `K − A1u ≥ 0` **and** share of paths with `Δ > 0` **≥ 50 %**. |
| **K3** | pre-tax main-window max DD, `K ≤ A1u + 1.0 pp` (base and calibrated, m = 2). |
| **K4** | the 21 January rolling starts (E2 design): `K − A1u ≥ 0` in **≥ 60 %** of starts (13/21). |

**Soundness of the pair against the incumbent (also required for K; the standing production
gates of this research series, applied to K vs C0 on the main window, m = 2).**

| Gate | Requirement |
|---|---|
| **S1** | pre-tax CAGR, `K ≥ C0 − 0.10 pp` (base and calibrated). |
| **S2** | pre-tax max DD, `K ≤ C0 + 1.0 pp` (base and calibrated). |
| **S3** | turnover, `K ≤ 2 × C0` (G9). |

**Decision rule.** `adopted = K` iff `K1 ∧ K2 ∧ K3 ∧ K4 ∧ S1 ∧ S2 ∧ S3`; otherwise `A1u alone`.
No arm is dropped or added after seeing data. The A1u and H2 arms are reported with the same gate
arithmetic against A1u (H2 vs A1u) and against C0, so the owner can see whether either device
alone would have passed.

**Reproduction requirement.** `--harness` must reproduce, to 2e-6:
(a) the control's R2 anchors (as in v3), and
(b) **A1u's published cells**: every numeric leaf of every A1u cell in
`research/gain_band_v3/RESULTS.json` that this run produces (pre-tax main window, after-tax
windows at all five capitals, the m = 4 cost stress, the seed-20261011 clean bootstrap, the six
OOS windows, the 21 R-USD instruments, E1/E2/E3); and
(c) **H2's published cells** against `H2_ANCHORS.json` (pre-tax main window m = 1/2, the three
after-tax windows at $100k, the m = 4 cost stress, the seed-20261011 bootstrap at $100k/$1M).
Any mismatch is a bug in this study, not a result.

## 7. Differing conventions (disclosed)

1. **th2's pre-tax main window is published liquidated**; this study's `pre_stats.cagr` is the
   nav-based (unliquidated) convention that v3 uses. The H2 anchor comparison therefore reads
   `cagr_liquidated` (added to `pre_stats` for this purpose). Both engines liquidate at the window
   end identically; the published difference is the final sale's cost (≈ 0.003 pp of CAGR at
   m = 2).
2. **The postponement counter is split** into `blocked_band` (A1u's band) and `deferred` (the
   December window); `postponements` is their sum. For the H2-only arm their sum is th2's
   `deferrals` (its band is off), which is what the anchors compare.
3. **The engine is v3's, not th2's.** For arms C0 and A1u it is byte-identical to v3's code path
   (and therefore reproduces v3's published A1u cells exactly). H2's December branch is th2's
   `tax_run_overlay` branch verbatim (`trade and i in dec_tail and tgt > 1e-12 and tgt < w`), run
   from v3's loop; the surrounding tax arithmetic is the same engine, which is why the H2 anchors
   are expected to reproduce bit for bit.
4. **A2/A3 (the bracket budget) are not part of this study** and their code is not carried over,
   so `blocked_budget` does not exist here (v3's stored `blocked_budget` = 0 cells are recorded as
   "keys only in v3" by the reference check).
5. Pre-tax main-window runs use the $100k gate capital (v3's convention); th2 used capital 1.0.
   Both are scale-free in `pre_tax` mode (no tax), so this cannot change a cell.

## 8. Run protocol and pre-registered expectations

`--smoke` (synthetic prices only: probe identities, `C0 == simulate_band`,
`H2 == simulate_band_overlay` for the tail-10 window, `K with an empty window == A1u`, the
deferral and the band both firing) → commit → `--harness` (control anchors + the deep
reproduction block) → `--run` (stages, gates, RESULTS.json + SUMMARY.txt), executed **twice**
with byte-identical `RESULTS.json`.

Pre-registered expectations, stated before the run:

1. The deep reference block reproduces v3's A1u cells and th2's H2 cells with **0 mismatches**;
   any mismatch is a bug in this study.
2. **H2 adds little on top of the band.** H2 defers ~51 fills over 26.5 years on R2 (th2's
   measurement); A1u's band already suppresses ~1,289 downward resizes on the same path, and the
   overlapping December resizes are a small subset. So `K − A1u` should be a small positive
   number (order 0.02–0.15 pp after tax at $100k) with a bootstrap median of the same order and a
   share > 0 near 55–65 %. That is exactly the regime in which K2's 50 % share floor and the
   E3-style thresholds were the failing cells of the parent campaigns (v3's E3 block-10 flat
   share was 53.7 %).
3. **The composition cannot hurt the drawdown materially**: H2 holds *more* in December, A1u
   holds more all year; both effects are small (H2's measured Δ max DD on R2 was −0.50 pp, A1u's
   +0.43 pp), so K3 is expected to pass with room.
4. **H2 alone on top of R2 reproduces th2's verdict-level numbers** (it is a verbatim arm), and
   should also beat A1u on the December-heavy cells but by less than A1u beats C0.
5. K's own E1/E2/E3/E4-vs-C0 block should look like A1u's plus a small positive H2 increment; if
   K fails a vs-C0 cell that A1u passes, that is a finding about the composition, not a gate
   failure of this campaign (only K1–K4 and S1–S3 gate).

## 9. Caveats and limits

As `gain_band_v3` §7 / `tax_harvest_v2` — one history per cell, no true after-tax out-of-sample
(the TRY calendar starts 2000), the fixed 2015-09 → 2026-09 bootstrap calendar cannot price
another FX/inflation regime, the liquidated convention charges deferred tax at the window end,
FIFO with per-year netting and no loss carry-forward, the 2026 tariff deflated by the previous
October's Yİ-ÜFE, instalments funded by selling. **Not tax advice.** The H2 anchors are H2's
*own* published numbers, so they verify the code path, not this study's conclusion. The
January-gap risk that th2 measured (2015/2016 −0.70 pp, 2024/2025 −0.32 pp) is a property of H2
and is inherited by K unchanged in kind.

## 10. Amendments

*(none yet; any bug fix, convention change or disclosure made after the first complete run goes
here, with what was already visible at that point)*

**Pre-commit honesty note.** Before this commit, on this branch, the following ran:
(a) one **timing probe** that executed `engine_run` (imported from `research/gain_band_v3/screen.py`)
once for C0 (progressive), once for C0 (pre-tax) and once for **A1u** (progressive) on the main
window, used only to size the compute; A1u's value reproduced v3's published cell (0.106146), so
no information about this study's question (K vs A1u, H2) existed; and (b) `--smoke` (synthetic
random-walk prices only). **No K cell, no H2 cell, no stage, no gate outcome and no
reference-comparison result existed at the time of this commit.**
