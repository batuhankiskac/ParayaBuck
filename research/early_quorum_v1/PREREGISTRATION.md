# early_quorum_v1 — does a 2-of-3 quorum on the early re-entry leg's trigger beat production K after Turkish tax?

Pre-registered 2026-10-15, **before any non-control arm of this study was simulated on real
data on this branch**. Working branch `research/early_quorum_v1`; the code lives in
`research/early_quorum_v1/` (`screen.py`, `tax_engine.py`, copied from
`research/band_h2_combo_v1/`). Control identity: production **K** =
`equity_leveraged_trend_v1` 1.2.0 = E2 + R2's early re-entry + A1u + H2.

## 0. Why this study exists

`docs/research/idea_survey_v4.md` §4 ranks **I2 ("Early-path trigger quorum")** second of the five
untested ideas, with an expected after-tax gain of +0.20 … +0.60 pp and a pre-tax +0.20 … +0.80 pp.
The argument is structural, not a historical accident: R2's early leg — half the production weight
while the vote is off — produced **+0.81 pp of CAGR and −2.80 pp of max drawdown** against E2 over
26.5 years from only **164 sessions (2.46 %)** and 32 episodes, of which three produce **95.3 %** of
the gain (`fast_reentry_v1`, `fast_reentry_v2`). The leg is entered by *buying* (nothing is
realised) and its lots are days-to-months old, so the sales that end an episode consume young,
low-gain units which fall in A1u's 0.5 pp tier (`g_frac ≤ 1 %`) — the tier that fills immediately.
A quorum of two of the three conditions keeps the leg active far more often while changing nothing
else about the system. The survey's stated honest test is the **plateau** comparison (Q2 vs Q3 vs
`fast_reentry_v2`'s SMA-40 neighbour), and its stated reporting requirement is
**leave-one-episode-out** for the three named episodes (2001-10-31, 2002-11-04, 2023-01-20).

## 1. Question

Does replacing the early leg's **AND-of-3** entry condition with a **2-of-3 quorum** (and, for Q3,
a hysteretic exit) beat production **K** after Turkish income tax at $100k on the three standard
windows and on the two clean after-tax bootstraps, without a materially worse drawdown, turnover,
out-of-sample or instrument-generalisation profile? Secondary: how do the three pre-registered
configurations compare with the six `fast_reentry_v2` one-parameter neighbours of the *production*
early path (the plateau comparison), and how much of each arm's edge is the single best episode
(leave-one-episode-out)?

## 2. Data and provenance

**Nothing is downloaded.** Every input is an existing committed research file in this worktree,
restored from `research/band_h2_combo_v1` (the parent harness), unchanged: `DATA_MANIFEST.json`
records each file's source, byte count and SHA-256. Uses:
`stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars, DTB3),
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY conversion, Yİ-ÜFE indexation),
`ewma_band_v1_{gspc,index,tb3ms}.csv` (the six OOS windows), `range_vol_v2_*.csv` (19) +
`range_vol_v1_{spy,dia,iwm}.csv` (the 21 quality-passing ETFs; `EWC` excluded by
`range_vol_v2`'s fixed quality rule), and the two evidence files used for reproduction
(`research/gain_band_v3/RESULTS.json`, `research/early_quorum_v1/H2_ANCHORS.json`). The
TRY/Yİ-ÜFE calendar starts 2000-01 / 1999-01, so no after-tax stage starts before 2000-03-09.

## 3. Control (C0 of the survey = **K**)

**Production K, bit for bit**, through the single sequential engine `tax_engine.py` (a byte copy
of `band_h2_combo_v1/tax_engine.py` = `gain_band_v3/tax_engine.py` = `tax_harvest_v2/tax_engine.py`
= the `vt_level_tax_v1` / `r2_tbill_combo_v1` corrected engine): production R2's target series
(E2's 2-of-3 vote on the QQQ close + the 0.5·vt early re-entry leg, `vt = min(1, 0.35/vol20)`, cap
0.98) with the production execution layer (decide on the completed close, fill at the next open,
0.5 pp resize band, entries and exits to zero always fill, idle cash zero, cost 4 bps × m × |Δw|,
synthetic QLD\* = daily-reset 2× on the QQQ total return, 0.95 %/yr expense, financing DTB3 +
{base 0.0 %, calibrated 0.75 %}/yr) **plus A1u** (gain-scaled downsize band, `B_down` = 0.5 / 3 /
8 pp at USD FIFO `g_frac` ≤ 1 % / ≤ 5 % / > 5 %) **plus H2** (a non-exit downward resize whose
*fill* session is one of the last 10 December sessions is postponed; H2's window tested first).

**Anchors asserted in `--harness`** (pre-tax TOL 1e-4, after-tax 0.005 pp, as in v3): pre-tax
**15.7649 / 15.1785 %** CAGR (m = 2, base / calibrated) and 16.0173 / 15.4303 % (m = 1), max DD
**46.6698 / 46.7948 %**, turnover **5.4561/yr**; after tax at $100k **10.7533 / 15.2354 /
23.9720 %** with max DD 46.6698 / 44.6272 / 34.9774 %; clean-bootstrap $100k medians
**3.8577 / 4.0376 %** with deltas **+0.2856 pp (75.0 %)** and **+0.1311 pp (64.0 %)** versus R2, and
pre-tax leg medians 7.7761 / 7.3137 % (deltas −0.0071 / −0.0117 pp) — all read out of
`research/band_h2_combo_v1/RESULTS.json`. The label **"C0"** (K's execution layer *off*) is the
report-only reference row and is asserted against production R2's own anchors
(15.4766 / 14.8961 %, max DD 46.7584 / 46.8797 %, turnover 5.9915; after tax 10.160 / 14.706 /
22.886 %). The pure-E2 series is asserted separately under its own label "E2".
The engine's K path is asserted against `simulate_band_overlay` with the tail-10 December window;
`early_state` with the production spec is asserted **bit for bit** against production `r2_state` and
against its own session-by-session reference for every spec; the deep harness re-runs the parent
campaigns' published cells (A1u against `gain_band_v3/RESULTS.json`, H2 against `H2_ANCHORS.json`)
cell by cell.

## 4. Arms (5 labels + 6 plateau neighbours; every parameter fixed, no tuning)

The **early leg** is the only object any candidate touches. All arms keep K's execution layer
(A1u + H2), the E2 vote, the sizing (`vt = min(1, 0.35/vol20)`, cap 0.98), the 0.5 pp resize band,
the close-decision / next-open fill, the cost model and the instrument. The three production early
conditions are `sma` (close > SMA50), `vol` (`vol20` < its trailing 252-session median) and `mom`
(close > close 20 sessions ago); "hold" counts how many are true.

| Arm | Early-leg rule |
|---|---|
| **K** | production, verbatim: enter when **all 3** hold; exit when the close is at or below SMA50; weight 0.5·vt. |
| **C0** | the **report-only reference row**: production R2's early leg (identical to K's) with K's execution layer **off** (band off, no December deferral). |
| **Q1** | enter when **≥ 2 of the 3** hold; **exit when the close is at or below SMA50** (production's exit); weight 0.5·vt. |
| **Q2** | the `mom` condition **dropped**: enter when `sma` **and** `vol` hold; exit when the close is at or below SMA50; weight 0.5·vt. |
| **Q3** | Q1's entry (≥ 2 of 3) with a **hysteretic exit**: leave only when **≥ 2 of the 3 fail** (i.e. at most 1 still holds); weight 0.5·vt. |
| **N40 / N60** | K's early leg with the SMA length 50 → **40** / **60** (plateau neighbour, report-only). |
| **N126 / N504** | K's early leg with the vol-median window 252 → **126** / **504** (plateau neighbour, report-only). |
| **W13 / W23** | K's early leg with the early weight 0.5 → **1/3** / **2/3** (plateau neighbour, report-only). |

Resolutions taken in the pre-registration, before any run:

1. **Q1's exit is the SMA50 exit.** The survey writes both "same exit below SMA50" / "the exit
   condition stays SMA50 for Q1/Q2" and "re-entry and exit both use the 2-of-3 majority"; the
   second reading would make Q1 and Q3 identical, since Q3 is defined as "Q1 plus hysteresis: enter
   at 2-of-3, exit only when ≥ 2 conditions fail". Q1 = 2-of-3 entry with the production SMA50
   exit, and Q3 = the same entry with the 2-of-3 exit, is the only reading that keeps all three
   sentences and Q3 distinct from Q1.
2. **E2 always overrides**: the early leg is off while E2 is on, and E2's entry takes the position
   over immediately (production behaviour).
3. **The entry quorum needs a finite vol median** (`can_enter` requires `¬isnan(med)`), exactly as
   production does, so `vol` cannot be "true" through a missing median.
4. The three Q arms are **not nested in general**: the production early set ⊆ Q2's (Q2 drops
   `mom`) ⊆ Q1's (the quorum superset of Q2's AND-of-2; both keep the SMA50 exit), while **Q3 and
   Q1 cross** — Q3 holds through a one-condition dip that ends a Q1 episode, but Q1 can be
   re-entered while Q3 is inactive after a majority of conditions has failed. Asserted on the real
   main series in `--harness`.
5. The neighbour arms are **not candidates**; they exist only for the plateau comparison the survey
   names, and are measured on the main window only.

## 5. Stages (all pre-registered before any non-control arm was simulated on real data)

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions, session 253 onwards), m = 2, cost
4 bps × m × |Δw|, base financing unless stated, progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE where after tax, **liquidated** at the window end. Every arm label runs its own
target series; the fill rule is K's for every label except "C0".

- **pre** — pre-tax main window (base/calibrated × m = 1/2/4) plus the R-USD row, all seven study
  arms.
- **PLT** — the plateau block: the three Q arms + the six neighbours on K's execution layer, main
  window, base, m = 2, pre-tax.
- **AT** — after tax at the $100k gate capital on the three windows
  2000-03-09→2026-09-29, 2010-01-01→2026-09-29, 2017-01-03→2026-09-29.
- **B12** — clean bootstrap, `seed = 20261011`, `block = 20`, 5,000 pre-tax / 1,000 after-tax
  paths, $100k ($1M report-only), B1-recent (progressive) and B2 (flat 20 %), on the fixed
  2015-09-01 → 2026-09-30 calendar with its realised USD/TRY and Yİ-ÜFE and block-resampled QQQ
  market returns.
- **EI** — the 21 quality-passing ETFs, pre-tax, **calibrated**, m = 2, R-USD rule, each with its
  own E2, its own early path and its own synthetic 2× leg; plus the pooled instrument bootstrap
  (`seed 20261011`, `block 20`, **500 paths per instrument**, 10,500 paths).
- **E1** — after tax on the same 21 ETFs under R-INDEX (real USD/TRY + Yİ-ÜFE), $100k, calibrated,
  m = 2 — report-only.
- **E2** — 21 rolling January starts 2001…2021 on the QQQ main series, after tax at $100k, base,
  m = 2, each run to 2026-09-29 — report-only.
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, m = 2,
  R-USD rule, plus the pooled OOS block-20 bootstrap (2,000 paths per market).
- **LOEO** — leave-one-episode-out on the main window: for each of the three episodes the survey
  names, the candidate's early-leg active flag is **replaced by the control K's** over the
  **published** episode span (`fast_reentry_v1`'s per-episode table: 2001-10-31 → 2001-12-05,
  2002-11-04 → 2002-12-02, 2023-01-20 → 2023-02-01, mapped to sessions by date), and the main
  window is recomputed. The span is fixed by the published table rather than by this study's own
  episode detection, so the three cells are the same objects the survey and `fast_reentry_v1`
  discuss; the number of control early sessions inside each span is reported — report-only.
- **E5** — report only: fill-decision traces, the early-session counts and the m = 4 cost stress.

`E3` (the parent campaign's fresh seed-20261015 bootstraps at blocks 10/40) is **not** run in this
study and is not a gate here; B12 is the pre-registered bootstrap.

## 6. Gates (pre-registered; every candidate arm vs K)


**Every** gate below must hold for a candidate to be adopted; there is no "most gates" reading.

| Gate | Requirement |
|---|---|
| **G1** | pre-tax main window, m = 2, **base and calibrated**: CAGR ≥ K's, **or** Calmar ≥ 1.10 × K's with CAGR ≥ K − 0.50 pp. |
| **G2** | pre-tax main-window max DD ≤ K + 1.0 pp (base and calibrated, m = 2). |
| **G3** | 2017+ CAGR ≥ K − 1.0 pp (base and calibrated). |
| **G4** | CAGR above K in ≥ 2 of the 3 sub-periods P1 2000–08 / P2 2009–16 / P3 2017→ (base, m = 2). |
| **G5** | clean bootstrap, block 20, seed 20261011, 5,000 pre-tax paths: median (arm − K) ≥ 0, **base and calibrated**. |
| **G6** | G1 at m = 1 (base and calibrated). |
| **G7** | six OOS windows: median ΔCAGR ≥ 0; in ≥ 5 of 6 windows ΔCAGR ≥ −0.5 pp **and** ΔDD ≤ +3 pp; ΔDD never above +8 pp. |
| **G8** | pooled OOS bootstrap (M1–M5, 2,000 paths each, block 20, seed 20261011) median Δ ≥ 0. |
| **G9** | turnover ≤ 2 × K (the ratio to K is also reported). |
| **EI** | 21 ETFs (pre-tax, calibrated, m = 2, R-USD): median ΔCAGR ≥ 0; share ΔCAGR > 0 ≥ 60 %; share ΔDD ≤ +3 pp ≥ 80 %; pooled instrument bootstrap median ≥ 0. |
| **T1** | after tax at $100k, progressive, liquidated: (arm − K) ≥ 0 in **all three** windows. |
| **T2** | B1-recent (progressive) at $100k: median ≥ 0 **and** share > 0 ≥ 55 %. |
| **T3** | B2 (flat 20 %) at $100k: median ≥ 0 **and** share > 0 ≥ 55 %. |
| **PL** | plateau: **every** Q arm has pre-tax main-window base-m2 ΔCAGR > 0 **and** ΔmaxDD ≤ +1 pp. |

**Which gates decide.** The common protocol's gate set (G1–G9, OOS, 21 ETFs, the three after-tax
windows at $100k, B1-recent/B2) decides; the survey pre-registers arms but no different selection
rule, so nothing is relaxed. **PL is added as a pre-registered gate** because the survey names the
plateau comparison as the honest test of I2: it requires the device's edge to be of the same sign
across all three of its pre-registered configurations. PL's two components are, per arm, implied by
G1 and G2, so PL can only fail where G1 or G2 already fails; the neighbour table it reports (N40 …
W23) is **not** gated, because the SMA-40 neighbour's known +1.77 pp DD breach is a property of the
*incumbent* early device already documented in `fast_reentry_v2`.

**Selection rule.** `adopted` = the candidate(s) passing **all** gates; if more than one passes,
the tie-break is the highest mean of the three after-tax $100k deltas, then the higher B1 median.
If none passes, production keeps **K** unchanged. No arm is dropped or added after seeing data, and
no gate is relaxed; the study makes no production change in any case (research only).

**Reproduction requirement.** `--harness` must reproduce, to 2e-6, the parent campaigns' published
cells (A1u against `gain_band_v3/RESULTS.json`, H2 against `H2_ANCHORS.json`) in the deep part, and
the control's K anchors (pre-tax base/calibrated × m = 1/2, the three after-tax windows at $100k,
the clean-bootstrap medians and their deltas versus R2) within the pre-registered tolerances. Any
mismatch is a bug in this study, not a result.

## 7. Differing conventions (disclosed)

1. The control label is **"K"**, not "C0" as in `band_h2_combo_v1`; **"C0"** is production R2 and
   is report-only. The deep reproduction block still uses band_h2's own labels (C0/A1u/H2/K) with
   band_h2's own seed and arm configuration, so its cells compare like for like.
2. The harness's cheap bootstrap check runs 1,000 paths (as in band_h2); the 5,000-path clean
   bootstrap is the deep one, which `--run` reuses.
3. The instrument bootstrap of gate EI uses 500 paths per instrument (10,500 pooled) instead of
   `fast_reentry_v2`'s 2,000, to keep the run inside the study's compute budget. The gate threshold
   is unchanged.
4. Only the $100k gate capital is run after tax; $300k/$30k/$10k are not (the protocol's after-tax
   requirement is the three windows at $100k), and the $1M cells exist only inside the shared
   bootstrap, for the H2 anchor comparison.
5. The pre-tax main-window and bootstrap runs use the $100k gate capital (scale-free in `pre_tax`
   mode, no tax); the after-tax windows at $100k are the gate cells.

## 8. Run protocol and pre-registered expectations

`--smoke` (synthetic prices only: the rule-gain probes, the K/H2 overlay identities, the production
spec vs `r2_state`, the quorum pinning, finiteness and after-tax ≤ pre-tax) → write this
pre-registration and `screen.py`/`DATA_MANIFEST.json` and **commit** → `--harness` (K and R2
anchors, deep reference block) → `--run` (stages, gates, RESULTS.json + SUMMARY.txt), executed
**twice** with byte-identical `RESULTS.json`.

Pre-registered expectations, stated before the run:

1. The deep reference block reproduces the parent campaigns' cells with **0 mismatches**; any
   mismatch is a bug in this study.
2. **The Q arms hold more exposure than K.** Q1's active set strictly contains Q2's and the
   production set, Q3 dilates Q1's episodes; expect a multiple of the production 164 sessions in all
   three arms, and the extra sessions to be the marginal recoveries the survey warns about.
3. Pre-tax, the honest prior is **+0.2 … +0.8 pp CAGR and +1 … +4 pp max DD** (the survey's band):
   some of R2's −2.80 pp DD advantage over E2 should be given back. G2 (≤ K + 1 pp) and PL are the
   gates most likely to fail.
4. After tax, the extra realisations are the cheapest in the system (young, low-gain lots in A1u's
   0.5 pp tier), so the after-tax delta should keep a larger share of the pre-tax delta than the
   ~0.7 ROM factor; T1 should pass where the pre-tax DD cost stays small.
5. **Three-episode concentration is the main risk to the verdict**: the LOEO table should show each
   Q arm losing a visible fraction of its ΔCAGR when a single episode is replaced by K's behaviour,
   and the plateau table should show whether the Q device's neighbourhood is as broad and flat as
   the incumbent's.
6. G9 is expected to pass comfortably (the early leg trades small notional, and the band/December
   layer is unchanged).

## 9. Caveats and limits

As `gain_band_v3` §7 / `band_h2_combo_v1` §9 / `fast_reentry_v2` §Caveats: one history per cell, no
true after-tax out-of-sample (the TRY/Yİ-ÜFE calendar starts 2000), the fixed 2015-09 → 2026-09
bootstrap calendar cannot price another FX/inflation regime, the liquidated convention charges
deferred tax at the window end, FIFO with per-year netting and no loss carry-forward, the 2026
tariff deflated by the previous October's Yİ-ÜFE, instalments funded by selling. The early leg's
edge is concentrated in a handful of episodes, and the quorum's extra sessions are exactly the class
of marginal recoveries that `vote_hysteresis_v1` Y1–Y3 found unprofitable on the *main* vote. **Not
tax advice.**

## 10. Amendments

**Disclosure of the pre-commit / post-commit activity (all before any real run).**

*Before the pre-registration commit `af40c6f3`:* `--smoke` (synthetic prices only) and two
`--harness` attempts on real data (the first was terminated by a 300 s command cap inside the deep
bootstrap; the second reached only the deep bootstrap's first 500 paths). **No candidate arm was
simulated and no gate outcome, SUMMARY or RESULTS.json existed at the time of the commit.**
*After the commit, before the first real `--run`:* several **code-path probes** executed `harness`
and `run` with deliberately tiny resample counts (`PRE_BOOT_PATHS` 30–40, `TAX_BOOT_PATHS` 10–12,
`OOS_PATHS_PER_MARKET` 10–20, `EI_PATHS` 6–10) and wrote only to `/tmp/…eq_probe_*`, so they could
not touch `RESULTS.json`; their deterministic stages (pre-tax main window, after-tax windows, OOS,
21 instruments, E1, E2, the plateau block, LOEO) were full-size and therefore did produce
candidate-arm numbers before this section was written. **No arm, parameter, stage, seed, threshold
or gate in sections 4–6 was changed in response**; the only content change after the commit is the
structural-claim correction below, which is a statement about the device's shape, not a gate.

**A1. The structural claim in §4.4 was made directional and measured, not asserted.** §4.4 as
committed claimed "production ⊆ Q2 ⊆ Q1, while Q3 and Q1 cross". The gating part is unchanged and
logical (same exit rule, strictly larger entry sets ⇒ production ⊆ Q2 ⊆ Q1, asserted on the real
series). The Q1/Q3 part was **not** asserted in general and is now **reported, not gated**: on the
main path Q3's active set is a strict subset of Q1's (Q3-only sessions 0, Q1-only sessions 6 —
Q3 holds through one-condition dips, while Q1 can stay active through a session Q3 has left), and
in principle neither contains the other. `device_structure` records both counts.

**A2. Post-commit bug fixes and plumbing corrections** (none touches an arm, a parameter, a stage,
a seed, a gate threshold or the selection rule; each was found by `--smoke`, `--harness` or a
`/tmp` code-path probe):

1. `main()`'s harness print loop crashed on the boolean checks (`"reference" in v` on a bool).
2. The harness's engine-identity check was wrong: it compared the **K** engine against
   `simulate_band_overlay`, but that simulator implements H2 alone (no A1u band). It now asserts
   the **H2** engine against the overlay simulator (bit for bit) and, separately, that K and C0
   share the same target series and differ only by the fill rule.
3. `boot`'s pre-tax block now stores one delta cell **per baseline** (`{arm}_vs_{ctrl}`) and
   re-exports the parent campaign's five cell names as the arm-versus-`C0` (= production R2)
   comparison, which is what `gain_band_v3/RESULTS.json` published. (The control of *this* study is
   K, so the old single "vs C0" naming would have silently compared K-relative deltas against
   R2-relative reference cells.)
4. `wants_by_arm`/`sized_pair` return the full `[P, T]` target array (the batched OOS and
   instrument bootstraps index path rows), and the now-unused `wants_for` helper was removed.
5. `boot_instrument` unpacked `load_instrument`'s six return values incorrectly.
6. `stage_loeo` no longer requires the control's own early episode to start exactly on the
   published entry date: it uses the published span directly (the dated spans are the objects the
   survey and `fast_reentry_v1` discuss).
7. `early_state` is computed as the vectorised "last entry / last mandatory exit" recurrence, with
   `_early_state_loop` kept as the session-by-session reference; `--smoke` asserts the two agree
   for **every** spec on both a random-walk and an adversarial flicker series, and `--harness`
   asserts the production spec equals `r2_state` bit for bit.
8. The OOS and instrument bootstraps simulate only the control and the candidates, and the
   pre-registration's own `--harness` bootstrap check was folded into the deep block, which now
   asserts K's bootstrap anchors directly (pre-tax medians and $100k deltas versus R2).
9. `--smoke`'s structural checks were corrected to the provable inclusions
   (production ⊆ Q2 ⊆ Q1) — see A1.

**A3. Report-path fixes found by the first real `--run` (report-only; no arm, parameter, stage,
seed, gate threshold, gate outcome or the selection rule changed).** The first `--run` computed
every stage at full size and wrote `RESULTS.json`, then died in `main()`'s SUMMARY writer. The
fixes below were made after that run's numbers existed; the gate booleans and `ADOPTED` are
**identical before and after** (checked cell by cell, not assumed: 0 changed gate booleans of the
3 × 14 cells).

1. **The three reporting stages read the wrong series.** `stage_plateau`'s `early_sessions`,
   `stage_e5_decisions`' `early_sessions` and the entire LOEO block computed the early leg's flag
   on the **synthetic 2x close** instead of the **QQQ close** (the series production
   `equity_leveraged_trend_v1` evaluates the vote and the early conditions on, and the series
   `main_inputs` sizes from). The measured difference is decisive: the production early path is
   active in **164** measured sessions on the QQQ close — production's own count
   (`fast_reentry_v1`: 164 of 6,679, 2.46 %) — against **126** on the synthetic close, and §8
   expectation 2 pre-registered "a multiple of the production 164 sessions". Fixed with a cached
   accessor `qqq_price()`; the harness's structural checks and its `device_structure` counts now
   also run on the QQQ close (the inclusions are series-independent and still hold). Corrected
   counts: **K 164, Q1 425, Q2 178, Q3 419** (plateau neighbours: N40 171, N60 141, N126 275,
   N504 115, W13 164, W23 164). **No gate reads any of these cells** — PL reads only the Q rows'
   ΔCAGR and Δmax DD, which were always computed from the arms' own target series — and the LOEO
   block is report-only by §5.
2. **The full reproduction block compared two different conventions.** `reference_checks(res,
   full=True)` compared this study's E1/E2/OOS/instrument **arm-minus-K** deltas against the
   parents' published **arm-minus-C0** cells, flagging 138 of 1,015 cells (63 `e1` + 21 `e2` +
   12 `oos` + 42 `instruments`). Every one of the 138 vanishes under the parents' convention
   (`arm-K - (C0-K) = arm-C0`), verified cell by cell *before* the fix was written; the new
   `_parent_convention` helper performs the conversion — the same convention fix A2.3 applied to
   the bootstrap block. The full block now reports **1,015 cells, 0 mismatches, max|diff|
   5.0e-07 → PASS**, so it is now a genuine reproduction of the parents' E1/E2/OOS/instrument
   stages as well as the deep part's pre-tax/after-tax/cost/bootstrap cells.
3. **Two crashes in `main()`'s SUMMARY writer** (print path only): the reference row's B1/B2
   column indexed `{arm}_vs_K` for `C0`, which the tax block does not publish (C0 is the parents'
   own baseline there, published as `K_vs_C0`); and the T1 control cell indexed
   `win[TAX_WINDOWS_LAST]` (a *window* key) where `ctrl[...]` (the gate *capital*) is meant.
4. **`device_structure` was computed but not recorded.** §4.4/A1 report the Q1/Q3 relation as the
   pre-registered measurement of the device's shape; it is now exported (`harness.device_structure`)
   and printed on the SUMMARY's device-structure line: **Q1-only 6 sessions, Q3-only 0** on the
   QQQ close (unchanged from A1's numbers).
5. Nothing else in the pipeline changed: the pre-tax main window, the three after-tax windows,
   both clean bootstraps, the six OOS windows, the 21 instruments, E1/E2 and the plateau Δ cells
   are bit-identical to the first run's.

## 11. Runs

The two final runs of the frozen code (`research/early_quorum_v1/screen.py`) produced
**byte-identical** `RESULTS.json` and `SUMMARY.txt`; both exited 0, with the deep harness block
(379 cells) and the full reproduction block (1,015 cells) at 0 mismatches.

| run | `RESULTS.json` SHA-256 |
|---|---|
| 1 | `32037decf41d539233109729a43cc05aef59a576bdfde86519a2dc0cc861b143` |
| 2 | `32037decf41d539233109729a43cc05aef59a576bdfde86519a2dc0cc861b143` |

`SUMMARY.txt` is also byte-identical between the two runs
(`762f03ec413cd6f1b9748139cc39363def7f4624ed6839e66523fa1b3d29bf6d`), and so is the two runs'
stdout. Each run: `harness PASS (0 outside tolerance)`, deep block 379 cells / 0 mismatches /
max|diff| 4.99e-07, full block 1,015 cells / 0 mismatches / max|diff| 5.00e-07, and
`ADOPTED: K -- no candidate clears every gate`.

