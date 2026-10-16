# ic12_confirm_v1 — confirmation of IC12 (NFCI veto) on a real-time (ALFRED vintage) signal

> Evidence: tag `archive/research/ic12-confirm-v1-2026-10` (not on `main`).

> Evidence: `research/ic12_confirm_v1/` — `PREREGISTRATION.md` (committed at `680bd2ae`, before any
> non-control arm was simulated), `screen.py` + `ic12_rt.py` + `rt_state.py`, `fetch_vintages.py`,
> `DATA_MANIFEST.json`, `RESULTS.json` (sha256 `e2ed6766…`, reproduced byte-identically),
> `SUMMARY.txt`, `HARNESS.json` (27/27). Inputs: `data/historical/ic12_confirm_v1_nfci_*`.
> Control K = `research/ideas/k_control.py`; the arms are `research/ideas/IC12.py` (committed,
> unmodified) and `research/ic12_confirm_v1/ic12_rt.py` (the same rule on a point-in-time series).
> Engine: `research/fast_engine` (parity `--quick` PASS), stages: `research/batch_screen.stages`,
> unmodified.

**Verdict: FAIL — all four IC12 arms are rejected, and the real-time signal is what kills them.**
On the signal a real-time observer could actually compute (ALFRED NFCI vintages, §2), `IC12rt/A`
and `IC12rt/B` are **worse than K in all three after-tax windows** (2000/2010/2017 Δ = −0.56 /
−1.86 / −4.25 pp for A and −0.12 / −0.84 / −2.13 pp for B), and **every one of the 21 rolling
January starts is negative** for both (share Δ > 0 = 0.00; the pure-real-time 2011–2021 subset
likewise 0.00). The Stage-1 edge bank_v2 measured (+0.71 / +0.70 pp on the main window, B1 median
+1.41 / +0.85 pp) is **an artefact of using the fully revised NFCI series**; it does not survive the
point-in-time reconstruction, and the 2017-window failure bank_v2 treated as the arms' only defect
gets *worse*, not better (the 2017 window lies entirely inside the vintage era, so it is a
*strictly* real-time test).

Because this idea was selected *after* seeing `batch_bank_v2` (215 trials at that batch's
deflation; 223 distinct `(idea, arm, spec_hash)` in the merged ledger at this branch point) and its
deflated Sharpe there was 0.000, this negative result is the study's only sound content. **No
production change is recommended, and none is a candidate for owner decision.**

| arm | pre-tax CAGR base / cal | max DD base / cal | ΔCAGR base / cal | turn/K | B1 med / share | B2 med / share | OOS med | AT 2000 / 2010 / 2017 Δ | E2 med / share>0 | E2p med / share>0 | E4 med / share |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC12/A` (committed) | 17.03 / 16.54 % | 36.76 / 36.87 % | +1.27 / +1.36 | 1.008 | +1.41 / 0.633 | +2.14 / 0.709 | +1.68 | **+0.71 / +0.19 / −1.35** | −0.03 / 0.38 | −0.26 / 0.36 | +2.22 / 0.81 |
| `IC12/B` (committed) | 16.61 / 16.07 % | 39.71 / 39.84 % | +0.85 / +0.89 | 1.004 | +0.85 / 0.647 | +1.24 / 0.734 | +0.97 | **+0.70 / +0.49 / −0.35** | +0.36 / 0.76 | +0.10 / 0.55 | +1.34 / 0.86 |
| `IC12rt/A` (real-time) | 15.35 / 14.84 % | 36.76 / 36.87 % | **−0.42 / −0.34** | 1.026 | +0.35 / 0.544 | +1.02 / 0.601 | +1.48 | **−0.56 / −1.86 / −4.25** | **−2.06 / 0.00** | **−3.81 / 0.00** | +0.40 / 0.57 |
| `IC12rt/B` (real-time) | 15.66 / 15.11 % | 39.71 / 39.84 % | **−0.11 / −0.07** | 1.010 | +0.30 / 0.574 | +0.66 / 0.640 | +0.81 | **−0.12 / −0.84 / −2.13** | **−0.93 / 0.00** | **−1.76 / 0.00** | +0.29 / 0.62 |
| K (control) | 15.765 / 15.179 % | 46.670 / 46.795 % | 0 | 1.000 | 0 | 0 | 0 | 10.753 / 15.235 / 23.972 % | 0 | 0 | 0 |

Δ in pp on the same paths; pre-tax and OOS cells at m = 2, base/calibrated financing; after-tax cells
at $100k, progressive, liquidated; the OOS median is over the 5 applicable windows (`M2` is `Not
Applicable`: NFCI's 156 lagged weeks have no value in 1935–1956). `E2` = the 21 rolling starts
2001–2021, `E2p` = the pure-real-time subset 2011–2021.

Failed gates (pre-registered set: Stage-1 rule, G1–G9, AT, E2, E2p, E3, E4, holdout adequacy):

| arm | pass | failed gates |
|---|---|---|
| `IC12/A` | no | AT (2017), E2, E2p |
| `IC12/B` | no | AT (2017), E2p |
| `IC12rt/A` | no | Stage 1, G3 (base **and** calibrated), AT (all three windows), E2, E2p, E3, E4 |
| `IC12rt/B` | no | Stage 1, G3 (base **and** calibrated), AT (all three windows), E2, E2p |

`G3` = the 2017-to-end CAGR within 1 pp of K's; it fails because the real-time arms are *below* K
there (the 2017 onwards period is exactly where the revised signal was doing its work). The two
committed arms pass G1–G9, AT's B1/B2 cells, E3 and E4 — the reproduction of bank_v2's record,
including its failures (§4).

## 1. Method

### 1.1 What was already known, and why this is not a fresh trial

`docs/research/batch_bank_v2.md`: of 105 simulated arms, `IC12/A` and `IC12/B` were the only two
with a material after-tax edge (+0.71 / +0.70 pp on the main window, −9.9 / −7.0 pp of max DD,
positive B1 *and* B2 with 63–73 % share, all 21 ETFs positive, a positive pooled OOS bootstrap,
and the fresh-seed cells), failing Stage 2 on exactly one cell — the after-tax 2017 window — with
`IC12/A` also missing the rolling-start gate. Over the whole campaign ledger (N = 215 then,
223 now) the deflated Sharpe rejected every arm (`IC12/B`'s advantage-Sharpe 0.419 against
`sr0` = 0.984 → 0.000). This study was chosen *because* of that; `PREREGISTRATION.md` §0 fixes the
consequences (low prior, only new evidence counts, no adopted change, and a single-arm pass may not
be declared).

### 1.2 The new evidence: real-time NFCI

NFCI is a *revised* index. bank_v2's arms read FRED's current series for every session of every
window, so a 2008 session saw the 2026 revision of the 2008 value and of the 156-week median behind
it. ALFRED (FRED's archive) serves the vintage current on any date:

```
https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=NFCI&vintage_date=YYYY-MM-DD
```

The vintage calendar comes from ALFRED's own download form
(`https://alfred.stlouisfed.org/series/downloaddata?seid=NFCI`). **ALFRED's NFCI archive begins
2011-05-25**: every earlier `vintage_date` returns HTTP 404, and that first vintage's own history
begins 1973-01-05. FRED's own graph CSV ignores `vintage_date`/`realtime_*` (it returns the 2026
series for `vintage_date=2005-01-15`), so ALFRED is the only source. 800 vintages were downloaded
(2011-05-25 → 2026-10-15); URLs, ranges, row counts and SHA-256 are in `DATA_MANIFEST.json`.

The pre-registered consequence (`PREREGISTRATION.md` §3–§4):

* **pure real-time** cells: the **2017 after-tax window** (2017-01-03 → 2026-09-29, wholly inside
  the vintage era) and the rolling January starts **2011–2021** (11 of 21) — the additional,
  required gate `E2p`;
* **mixed** cells: the 2000 and 2010 windows and the 2001–2010 starts are real-time from
  2011-05-25 and before that carry the committed current-revision signal. There is no vintage data
  to be stricter with, and adding an extra lag would change a registered parameter, so the fallback
  is the status quo; those cells keep the task's own E2 wording (≥ 2 of 3 windows) but are never
  binding on their own, because the after-tax gate AT (all three windows) is required as well.

The state series itself is the committed rule, unchanged: the committed `IC12.weekly_states` (lag
1 week, trailing 156-week median, 4-week momentum) is called on **the vintage current at the
session's date** from 2011-05-25 on — so nothing published later can enter — and on the committed
series before it. It is stored per **weekday** (`ic12_confirm_v1_nfci_rt_state.csv`, 14,546 rows) so
that every panel session and every bootstrap drawn source session resolves through the framework's
own `native.asof`. `screen.py --harness` asserts (a) the pre-vintage segment equals the committed
state on all 10,533 weekdays, (b) `IC12rt` with the committed state substituted returns
`IC12.targets` **bit for bit** for both arms, and (c) the real-time state differs on 354 of 6,932
main-window sessions (2011–2023).

## 2. What the real-time signal changes

| | live (revised) state | real-time state |
|---|---|---|
| main-window sessions in the veto state (% of defined) | 23.03 % | 19.73 % |
| 2000s / 2010s / 2020s decade shares | 29.24 / 18.80 / 20.25 % | 29.24 / 12.08 / 17.24 % |
| episodes / mean / median / max length (sessions) | 33 / 46.6 / 39 / 131 | 33 / 39.9 / 33 / 131 |
| sessions where the target weight differs from `IC12`'s | — | 280 of 6,932 |
| max weight change (A / B) | — | 0.98 / 0.49 of NAV |
| sessions where the state differs | — | 354 |

The 2000s decade share is *identical* (29.2426 % both) because the state series is identical before
2011-05-25 — a built-in consistency check of the construction. On the vintage era the revisions had
made the state look *more* on than it really was in the 2010s (18.80 % → 12.08 %) and the 2020s
(20.25 % → 17.24 %), and the *timing* of the episodes shifts: 280 sessions of the main window carry
a different target weight, and every one of them costs after tax.

The `rt` arms' max drawdown is **identical** to `IC12/A`/`B`'s (36.76 % base / 39.71 % for B) because
both are attained 2003-03-31, before the vintage era, where the two signals coincide. So the whole
DD advantage of IC12 over K is carried by the pre-2011 signal, which is the committed one — it is
not evidence about the real-time rule at all.

## 3. Gates

All gates were fixed in `PREREGISTRATION.md` §6 before any non-control arm was simulated, and are
implemented by `research/batch_screen/stages.py` unchanged (this study adds only `E2p` and its own
`E3`). Deltas are paired `arm − K` everywhere.

### 3.1 After-tax $100k — the decision cells

| window | K | `IC12/A` Δ | `IC12/B` Δ | `IC12rt/A` Δ | `IC12rt/B` Δ |
|---|---|---|---|---|---|
| 2000-03-09 → 2026-09-29 | 10.753 % | +0.71 | +0.70 | **−0.56** | **−0.12** |
| 2010-01-04 → end | 15.235 % | +0.19 | +0.49 | **−1.86** | **−0.84** |
| **2017-01-03 → end (pure real-time)** | 23.972 % | −1.35 | −0.35 | **−4.25** | **−2.13** |

Rolling January starts after tax ($100k), median Δ and share of starts with Δ > 0:

| set | `IC12/A` | `IC12/B` | `IC12rt/A` | `IC12rt/B` |
|---|---|---|---|---|
| all 21 starts (2001–2021) | −0.03 / 0.38 | +0.36 / 0.76 | **−2.06 / 0.00** | **−0.93 / 0.00** |
| 11 pure starts (2011–2021) | −0.26 / 0.36 | +0.10 / 0.55 | **−3.81 / 0.00** | **−1.76 / 0.00** |

Every real-time start is negative, in both arms, on both sets — including the 11 that need no
fallback at all. `E2p` requires the 2017 window Δ > 0 *and* the pure starts' median > 0 with share
≥ 60 %; it fails on all three counts for both arms.

### 3.2 E3 — fresh after-tax bootstraps (seed 20261017, $100k, 1,000 paths)

Median Δ (pp) / share > 0, against K, gate `median ≥ 0 and share > 0 ≥ 0.55` in all six cells:

| cell | `IC12/A` | `IC12/B` | `IC12rt/A` | `IC12rt/B` |
|---|---|---|---|---|
| block10 progressive (B1-recent) | +1.41 / 0.628 | +0.71 / 0.637 | +0.55 / 0.567 | +0.38 / 0.585 |
| block10 flat (B2) | +2.24 / 0.692 | +1.27 / 0.722 | +1.32 / 0.635 | +0.83 / 0.675 |
| block20 progressive | +1.50 / 0.635 | +0.83 / 0.664 | +0.37 / **0.541** | +0.34 / 0.568 |
| block20 flat | +2.17 / 0.704 | +1.29 / 0.731 | +1.04 / 0.593 | +0.66 / 0.626 |
| block40 progressive | +1.63 / 0.637 | +0.94 / 0.662 | +0.44 / **0.538** | +0.33 / 0.570 |
| block40 flat | +2.13 / 0.693 | +1.22 / 0.718 | +0.85 / 0.583 | +0.53 / 0.597 |
| **E3** | pass | pass | **fail** (2 cells) | pass |

E3 is where the arms disagree with themselves: **`IC12rt/B` still clears all six bootstrap cells
(median +0.33 … +0.83 pp, share 0.57 … 0.68)** while being negative in all three fixed windows and
in all 21 rolling starts. The bootstrap resamples 2015–2026 returns with reshuffled 20-session
blocks and a synthetic 253-session warm-up, so the veto lands on different days; the fixed window is
one particular path. The pre-registered rule requires **both** (AT *and* E2/E2p *and* E3), so the
disagreement does not rescue the arm — but it is the honest reading of "the bootstrap median alone
is not evidence about a specific period", and it is the one cell a reader should not over-read.

The framework's own fresh-seed gate (seed 20261016, blocks 10/40, the pipeline's Stage-2 criterion)
is **report only** here and says the same thing: pass for `IC12/A`, `IC12/B` and `IC12rt/B`, fail
for `IC12rt/A` on one cell (block40 progressive, share 0.506).

### 3.3 E4 — the 21 ETFs after tax ($100k)

Median Δ / share > 0, gate `median > 0, share ≥ 0.60`:

| `IC12/A` | `IC12/B` | `IC12rt/A` | `IC12rt/B` |
|---|---|---|---|
| +2.22 / 0.810 | +1.34 / 0.857 | +0.40 / **0.571** | +0.29 / 0.619 |

A and B pass; `IC12rt/A` fails on the 60 % share, `IC12rt/B` passes. Caveat: the ETF windows start
in 2000–2004 and only become real-time in 2011, so E4 is a **mixed** cell like the 2000/2010
windows — it is not the pure evidence the 2017 window and the 2011–2021 starts are.

### 3.4 Pre-tax G1–G9 (vs K, `ewma_band_v1`'s definitions)

`IC12/A` and `IC12/B` pass all fourteen G-cells; the real-time arms pass all but `G3` (base and
calibrated): their 2017-to-end CAGR is more than 1 pp below K's. G7 medians: +1.48 pp (`rt/A`),
+0.81 (`rt/B`), +1.68 (`A`), +0.97 (`B`) — the pre-2000 and 1975–1999 windows are identical between
the committed and real-time arms by construction, and M3/M4/M5 (which run past 2011) move by up to
0.65 pp. G8 (+0.96 / +0.70 pp) and G9 (turnover 1.026 / 1.010 of K) pass for both. Both real-time
arms also fail the **Stage-1 rule** itself (after-tax 2000-window Δ > 0 is false), i.e. on the
pre-registered pipeline they would not even have reached Stage 2.

### 3.5 E5 — incidence and the 2017-window decomposition (report only)

On the 2017→2026 window the difference between each arm's and K's after-tax log NAV was attributed
to calendar years and to veto episodes (session runs of the real-time state):

| | `IC12rt/A` | `IC12rt/B` |
|---|---|---|
| total Δ log NAV over the window | −34.46 pp | −17.14 pp |
| episodes | 9 | 9 |
| 2019 / 2020 / 2021 | −1.04 / −20.24 / +5.32 | −0.41 / −10.97 / +2.91 |
| 2022 / 2023 / 2024 | −6.18 / −15.59 / −4.04 | −2.78 / −7.43 / −3.26 |
| 2025 / 2026 | +5.61 / +1.69 | +3.44 / +1.37 |
| worst episodes | 2020-09-23…11-18 (−11.0 pp, 40 s); 2022-02-02…08-03 (−10.5 pp, 125 s); 2023-03-15…04-26 (−6.8 pp, 29 s) | 2020-09-23…11-18 (−5.3 pp); 2022-02-02…08-03 (−4.6 pp); 2020-03-11…05-06 (−3.8 pp) |

The cost is not one accident: it is the **2020 rebound** (the veto held into the recovery after the
COVID crash), the **2022 bear** (the veto re-entered late and out of phase) and the **2023
recovery**. The 2020s are exactly where the real-time state is *less* on than the revised one
(17.24 % vs 20.25 % of sessions) yet the arm still loses more — the revisions did not merely make
the veto rarer, they moved it to the right days, which is the definition of a look-ahead.

## 4. Reproduction and checks (all green)

`python -m research.ic12_confirm_v1.screen --harness` → **PASS 27/27** in 43 s
(`HARNESS.json`):

* `research.fast_engine.parity --quick` → PASS (15.9 s).
* K's anchors through the engine: pre-tax m = 2 base 15.7649 % / calibrated 15.1785 %, max DD
  46.6698 / 46.7948 %, after tax $100k 10.7533 / 15.2354 / 23.9720 % — every cell within 4e-05 pp.
* R2 reference row: 15.4766 % base, 14.8961 % calibrated, max DD 46.7584 %, m = 1 15.7538 %.
* `IC12/A`, `IC12/B` reproduce `research/batch_screen/results/bank_v2/IC12__{A,B}.json`:
  Δ to the committed records ≤ 0.0011 pp on after-tax 2000/2010/2017, B1 and B2 medians; the
  stage-1 gate block is identical to the committed one (A: max-DD Δ −9.908889/−9.924543 pp,
  turnover ratio 1.007843; B: −6.959888/−6.958286, 1.003588). The results table above is therefore
  the framework's own pipeline, not a re-implementation.
* real-time state: the committed `nfci_rt_state.csv` rebuilds from `rt_state.build()` exactly; the
  pre-vintage segment equals the committed state on 10,533 weekdays; `IC12rt` on the committed
  state is bit-identical to `IC12.targets` for both arms; the real-time state differs on
  354/6,932 sessions.
* `fetch_vintages --verify`: 5 files, 0 mismatches against `DATA_MANIFEST.json`.

**Determinism.** `--run` twice (172.0 s and 159.0 s wall) wrote byte-identical `RESULTS.json`
(sha256 `e2ed676661e830647b325f98701e0eb95172c22a265b595b9edb74c886b4ab83`). Every number is
rounded to 6 dp and every container sorted before serialising.

**Cost.** `--run` = 172 s (one process) for the control plus four arms: stage 1 + its 5,000/1,000-path
clean bootstrap, the after-tax windows, the six OOS windows, the 21 ETFs, the 21 rolling starts, the
framework's fresh-seed bootstraps (seed 20261016, blocks 10/40), this study's E3 (seed 20261017,
blocks 10/20/40) and the pooled OOS bootstrap. No per-day or per-path Python loop was written; the
only study-local numpy is the target construction (the committed pipeline with a different state
series) and the pre/post-processing. No file of `research/fast_engine` or `research/batch_screen`
was edited, and no engine kernel was added, so no parity cell was needed beyond the ones above.

## 5. Caveats

1. **Selection.** The arms came from a post-hoc reading of bank_v2; the deflated Sharpe there was
   0.000 over 215 (now 223) trials. A negative result is what this design can carry; a positive one
   would still only have been a *candidate for owner decision*.
2. **Vintage coverage.** ALFRED's NFCI archive starts 2011-05-25, so the 2000 and 2010 windows and
   the 2001–2010 rolling starts are *mixed*: they carry the committed (revised) signal before that
   date. Only the 2017 window and the 2011–2021 starts are strictly real-time. The 2000-window
   after-tax cells that bank_v2 used as its Stage-1 decision are therefore **not** a clean real-time
   test either way; the pure cells are, and they are uniformly negative.
3. **The state fallback is the status quo signal**, not a stricter one. There is no vintage data
   before 2011-05-25 to be strict with, and inventing an extra lag would have changed a registered
   parameter; the mixed cells are reported with that limit and are never the binding gate.
4. **E4 is a mixed cell** (ETF windows start 2000–2004) — see §3.3.
5. **Bootstrap vs fixed window.** `IC12rt/B` passes all six E3 cells while failing all three fixed
   windows and all 21 starts. The gates require both, so this does not change the verdict, but a
   reader who trusts only the bootstrap would mis-read the arm.
6. **One instrument, one account.** Both studies run each window/instrument as its own account (the
   campaign's convention); no portfolio layer is modelled.
7. **The fetch was rate-limited.** The first ALFRED pull (6 parallel workers) drew HTTP 403 after
   ~570 vintages; the committed panel comes from the successful first attempt, and the fetcher is
   now sequential with a response cache and long backoff (`fetch_vintages.py`). ALFRED was
   unreachable again at the end of the session, so the panel could not be re-downloaded for an
   independent second copy; its internal consistency is checked (every vintage's last observation ≤
   its own vintage date, 800/800) and the per-vintage raw-response SHA-256 are recorded.

## 6. Amendments

**A1 — pooled OOS draw pairing (bug fix, `screen.py`, not a gate change).** The first draft of
`gather` subtracted the control's six-market pooled OOS vector from an arm's four-market vector.
Because `boot.oos_pooled` returns the per-market *concatenation*, the vectors are not comparable
across different market lists, and IC12 declares `M2` not applicable. Fixed before any result was
recorded: the arm and the control are now drawn in one call, so the G8 Δ is paired (the framework's
own `stages.oos_pooled_delta` behaviour).

**A2 — 2017-window decomposition indexing (bug fix, E5, report-only).** `_nav` spans sessions
`i0-1 … i1`, so the per-session log-return difference indexes sessions `[i0, i1]`; the first draft
used `[i0+1, i1+1]`. Fixed before any result was recorded; an `assert` now pins the alignment.

No gate, arm, seed, threshold or parameter was changed after any non-control arm was simulated.
