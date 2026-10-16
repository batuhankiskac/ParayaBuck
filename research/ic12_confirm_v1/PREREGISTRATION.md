# ic12_confirm_v1 — confirmation study of IC12 (financial-conditions / NFCI veto)

Status at commit: **pre-registration only.** No non-control arm has been simulated at the time this
file and `screen.py` were committed. `RESULTS.json` / `SUMMARY.txt` / `docs/research/ic12_confirm_v1.md`
are written later, from this registered plan, and are appended in later commits.

Branch: `research/ic12_confirm_v1`, forked from `research/batch_run_v1` at `abe822fb`.

---

## 0. Provenance — read this before the numbers

This study exists because of a **post-hoc selection**. In `batch_bank_v2` (36 ideas / 108 arms, own
document `docs/research/batch_bank_v2.md`) the NFCI veto `IC12` was the nearest miss: `IC12/A` and
`IC12/B` passed Stage 1 and failed Stage 2 on exactly one cell, the after-tax **2017** window
(A also missed the rolling-start gate E2). The deflation over the whole campaign ledger rejected
every arm: `N = 215` at the time of the batch's deflation, `N = 223` distinct
`(idea, arm, spec_hash)` trials in the merged ledger at this branch point
(`research/batch_screen/REGISTRY.jsonl`, 276 lines) — the deflated Sharpe of the best arm
(`IC12/B`, advantage-Sharpe 0.419) was **0.000** against `sr0 = 0.984`.

Consequences, stated plainly and binding on the interpretation of everything below:

* the prior is **low**; a confirmation study is not a fresh blind trial;
* the arms were chosen *after* seeing bank_v2's numbers, so their Stage-1 edge carries no
  independent information;
* therefore **only new evidence counts**, and the new evidence must be of a kind bank_v2 could not
  produce. This study's new evidence is E1: the signal rebuilt from ALFRED **vintage** data, so
  each session only sees the NFCI value and the NFCI history that had been *published* by then.
  bank_v2 used the current (fully revised) FRED series for every session of every window.
* even a full pass is reported as **"candidate for owner decision"**, never as an adopted change.

## 1. Question

Does the IC12 NFCI veto keep its Stage-1 after-tax edge — and lose the after-tax 2017-window
shortfall — when the signal is built only from information published at the time? Equivalently:
was the 2017 failure a revision artefact, or is it the arm's real 2017–2026 behaviour?

## 2. Arms — FIXED, no parameter is chosen here

| arm | definition |
|---|---|
| `C0` | **control** = production K = E2 (2-of-3 vote) + early re-entry, sized `min(1, 0.35/vol20)`, cap 0.98, under the K fill rule (A1u gain band + H2 December deferral). `research/ideas/k_control.py`, arm `K`. |
| `IC12/A` | the committed plugin `research/ideas/IC12.py`, arm `A` — veto to weight 0 while the NFCI state is on. Imported, unmodified. |
| `IC12/B` | the committed plugin, arm `B` — the same state scales the weight by 0.5. Imported, unmodified. |
| `IC12rt/A` | **new arm**: the same rule, the same parameters, the same execution rule, on the **real-time (vintage) state series** of §4. Rule parameters are *literally* `research.ideas.IC12.ARMS["A"]` (imported, not retyped). |
| `IC12rt/B` | as `IC12rt/A` with `research.ideas.IC12.ARMS["B"]`. |

`IC12/C` is not run: it failed Stage 1 in bank_v2 (after-tax 2000 window −0.22 pp) and is not a
nearest miss.

The two `rt` arms are **not** a new parameter search. The rule (`lag_weeks` 1, `median_weeks` 156,
`mom_weeks` 4, `mult` 0.0 / 0.5, `mode` "veto", `rule` K, `leg` 2x) is imported unchanged from
`research/ideas/IC12.py`; only the *input series* changes, and it changes in the strictly
less-informed direction (a point-in-time series cannot know more than the final revised one).

Also reported, not gated: `IC12/A`, `IC12/B` re-run through this study's stage calls, as a
reproduction check against `research/batch_screen/results/bank_v2/IC12__{A,B}.json`.

## 3. New evidence E1 — real-time data

**Upstream.** ALFRED (the archival FRED) serves the NFCI **vintage** that was current on a given
date: `https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=NFCI&vintage_date=YYYY-MM-DD`.
The list of vintage dates is the `<select name="form[selected_vintage_dates][]">` of
`https://alfred.stlouisfed.org/series/downloaddata?seid=NFCI`.

**Availability — measured, not assumed.** ALFRED's NFCI archive begins at **2011-05-25**; every
earlier `vintage_date` returns HTTP 404, and the 2011-05-25 vintage itself begins its history at
1973-01-05 (earlier weeks were added by later re-estimates). There are no NFCI vintages before
2011-05-25. FRED's own graph CSV ignores `vintage_date`/`realtime_*` (verified: it returns the
2026 series for `vintage_date=2005-01-15`), so ALFRED is the only source.

**E1 is usable, but only from 2011-05-25.** The pre-registered consequence:

* the **pure real-time** cells are the 2017 after-tax window (`2017-01-03` → `2026-09-29`, entirely
  inside the vintage era) and the rolling January starts **2011–2021** (11 of the 21);
* the 2000 and 2010 windows and the 2001–2010 rolling starts are **mixed**: real-time from
  2011-05-25, and before that they carry the *fallback* of §4, which is the committed
  current-revision signal. No stricter fallback is available — there is no vintage data to be
  strict with — and inventing an extra lag would change a registered parameter. The mixed cells are
  reported, and gated exactly as the task specifies (§6 E2), but the **pure** cells are the ones
  that answer §1 and are gated separately and additionally (E2p).

## 4. Real-time state series — exact construction

The committed plugin's rule (`research/plugins_c2/native.py` + `research/ideas/IC12.py:weekly_states`)
is reproduced verbatim; only the series it is applied to changes.

For a session date `D`:

* `V(D)` = the latest ALFRED vintage date `≤ D`. If `V(D)` exists (`D ≥ 2011-05-25`), the state is
  the plugin's state evaluated on `V(D)`'s own observations, read as of `D` — i.e. the state of
  `V(D)`'s newest observation, computed from `V(D)`'s own lag-1 series and its own trailing
  156-observation median (the committed `IC12.weekly_states`, called unchanged).
  This is causal by construction: `V(D) ≤ D`, so no value published after `D` enters.
* otherwise (`D < 2011-05-25`) the state is the committed plugin's own state on the committed
  current-revision series (`data/historical/plugins_c2_nfci.csv`), read as of `D`. On that segment
  the `rt` state is *identical* to `IC12`'s own state — asserted, see §7.

The result is one `(date, state)` row per **weekday** from the first NFCI observation to the last
session of the data, so that every panel session and every bootstrap drawn source session resolves
(`native.session_labels`), and the plugin looks it up with the framework's own `native.asof`.

**Mutation check.** A `rt` arm's targets are produced by the committed weight construction, with
the same `fe_signals.e2_up` / `r2_state` / `helpers.capped` pipeline and the same execution rule.
If the `rt` state series is replaced by the committed one, the output must be **bit-identical** to
`IC12.targets` — asserted in `--harness` (§7), which is what makes "no parameter change" checkable
rather than asserted.

## 5. Data

| file | source | role |
|---|---|---|
| `data/historical/ic12_confirm_v1_nfci_vintages.csv` | `https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=NFCI&vintage_date=<d>`, all 800 vintage dates | the vintage panel (trailing 210 observations per vintage — enough to recompute the state exactly) |
| `data/historical/ic12_confirm_v1_nfci_vintage_dates.csv` | `https://alfred.stlouisfed.org/series/downloaddata?seid=NFCI` | the vintage calendar + per-vintage raw-response SHA-256 |
| `data/historical/ic12_confirm_v1_nfci_rt_state.csv` | derived, from the two above | the session-keyed real-time state series of §4 |
| `data/historical/plugins_c2_nfci.csv` | already committed by `plugins_c2` | the current-revision NFCI series (fallback + the `IC12` reproduction) |

URL, date range, rows and SHA-256 of each input are in `DATA_MANIFEST.json`. Market, tax, FX,
YI-ÜFE and instrument inputs are the campaign's committed files, already hashed in
`research/batch_screen/DATA_MANIFEST.json` and `research/plugins_c2/DATA_MANIFEST.json`; this study
does not re-download them.

## 6. Gates — FIXED before any non-control arm runs

Every Δ is `arm − C0` in percentage points, on paired paths. `K` blocks: seed/bock as named.
The framework's own gate implementations are used (`research/batch_screen/stages.py`), unchanged;
`E2p` and `E3` are this study's own additions.

**Reproduction (not a gate; a check that the stage calls are the framework's).** `IC12/A`,
`IC12/B` must reproduce `results/bank_v2/IC12__{A,B}.json` to ≤ 0.01 pp in: after-tax 2000/2010/2017
Δ, B1/B2 median Δ and share, E1 median Δ and share, E2 median Δ and share, max-DD Δ, turnover ratio.

**E1 (usability).** Usable iff the vintage reconstruction exists and is causal (asserted: state at
`D` depends only on vintages dated `≤ D`; the pre-2011-05-25 segment equals the committed signal
bit-for-bit). Reported: vintages 800, first 2011-05-25, sessions where `rt ≠ live` state.

**E2 (after tax, $100k, real-time signal).**
* `E2` = the task's cells on the **mixed** `rt` series: after-tax $100k Δ > 0 in **≥ 2 of 3**
  windows (2000/2010/2017) **AND** the 21 rolling January starts 2001–2021 have median Δ > 0 with
  share(Δ > 0) ≥ 0.60. (share(Δ ≥ 0) is also reported, the framework's E2 statistic.)
* `E2p` (**pure** real-time, additional and required): the 2017 window Δ > 0 **AND** the rolling
  starts 2011–2021 (11 starts) have median Δ > 0 with share(Δ > 0) ≥ 0.60. This is the cell bank_v2
  failed, evaluated with no pre-vintage session in it.

**E3 (fresh after-tax bootstraps).** Seed **20261017**, blocks **10 / 20 / 40**, 1,000 paths,
$100k, modes progressive (B1-recent) and flat (B2). Six cells. Gate: **median Δ ≥ 0 and share(Δ > 0)
≥ 0.55 in all six cells**.

**E4 (instruments).** The framework's 21 quality-passing ETFs after tax at $100k: **median Δ > 0 and
share ≥ 0.60** (the framework's own E1 gate).

**Pre-tax.** G1–G9 vs K exactly as `ewma_band_v1` defines them and `stages.pre_tax_gates`
implements them, including G9 turnover ≤ 2 × K. Reported for all four arms; the four Stage-1 gates
are also reported. The `rt` arms must additionally satisfy the Stage-1 rule itself.

**After-tax (the protocol's standing rule, and stricter than E2).** All three after-tax windows
Δ > 0 at $100k **and** B1-recent / B2-flat (seed 20261011, block 20) median Δ ≥ 0 with
share(Δ > 0) ≥ 0.55. This is `stages.after_tax_gates` ("AT" below); it is reported for every arm
and is **required**, so E2's "≥ 2 of 3" can never be the binding condition on its own.

**Selection rule (fixed).** The study is a **Pass** iff E1 is usable **and both** `IC12rt/A` and
`IC12rt/B` pass E2, E2p, E3 and E4, **and** both clear the Stage-1 rule, the pre-tax G1–G9
(including the holdout-adequacy check) **and** the after-tax gate AT. Requiring *both* arms is
deliberate: choosing the better of A and B after seeing bank_v2 is exactly the selection this study
exists to correct, so a single-arm pass is reported as `partial`, not a pass. Any other outcome is
a `fail`. Gates are never adjusted after the run; bug fixes go to "Amendments" at the bottom of
this file and to `docs/research/ic12_confirm_v1.md`.

**E5 (report only, no gate).** How often the veto bites (share of sessions in state, episodes,
mean/median episode length, by decade), for `live` and `rt`, on the main window. For the 2017–2026
window: the shortfall decomposition — daily log-NAV difference vs K summed per calendar year and per
veto episode, naming the episodes that cost the window.

## 7. Harness (`--harness`, before any arm is simulated)

1. `python -m research.fast_engine.parity --quick` must print PASS.
2. K's anchors through the engine: pre-tax m = 2 CAGR base 15.7649 / calibrated 15.1785 %, max DD
   46.6698 / 46.7948 %; after tax $100k 10.7533 / 15.2354 / 23.9720 % (2000/2010/2017). Tolerance
   0.01 pp.
3. R2 reference row: 15.4773 / 14.8927 base... — reported from `fe_study.pre_tax_main` under the
   `C0` rule (the campaign's published R2 row 15.48 / 14.90 %, max DD 46.76 %).
4. `IC12/A`, `IC12/B` reproduction of §6 against the committed bank_v2 records.
5. `rt` parity: replacing the `rt` state by the committed state reproduces `IC12.targets`
   bit-for-bit; the pre-vintage segment of the `rt` series equals the committed state on every
   session of the main window.

## 8. Determinism and cost

Every number is rounded to 6 dp and every container sorted before serialising, so `--run` twice
reproduces `RESULTS.json` byte for byte. Wall clock of `--run` is recorded in `SUMMARY.txt`.
Simulation, after-tax accounting and bootstraps all go through `research.fast_engine`
(`fe_sim` / `fe_tax` / `fe_boot` via the framework's `boot.py`); no per-day or per-path Python loop
is written here. No engine file is edited.

## Amendments

*(none)*
