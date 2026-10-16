# vol_shape_v1 — I2 exposure-shape replication at matched average weight

Pre-registered 2026-10-16, **before any non-control arm of this study was simulated on real data
on this branch**. Working branch `research/vol_shape_v1`; code in `research/vol_shape_v1/`
(`screen.py`, `shapes.py`, `boot_shape.py`). Control: **production K** = `equity_leveraged_trend_v1`
1.2.0 = E2 (2-of-3 QQQ vote) + R2 early re-entry + A1u (USD gain-scaled downsize band 0.5 / 3 / 8 pp
at `g_frac` ≤ 1 % / ≤ 5 % / > 5 %) + H2 (a downsize whose fill session is one of the last 10
December sessions defers to January).

Idea: `docs/research/idea_survey_v5.md` §4.2 (I2), which this study instantiates exactly.

## 0. Question

K sizes the exposure as `vt(σ) = min(1, 0.35/σ20)`. The vol-target *level* (`exposure_k_v1`:
0.375/0.40/0.45 all break the DD budget) and the vol *estimator* (`vol_window_v1`,
`ewma_band_v1`, `range_vol_v1–3`) have been swept; the **exponent** has not, and no arm in the
record ever held the *time-average* exposure fixed. Question: **does the shape of the sizing rule
matter once the average exposure is matched to K's?**

Every arm keeps K's market decision (the E2 vote, the early-re-entry state machine, the 0.98 cap,
the 0.5 pp band, A1u and H2) and changes only the sizing schedule to `min(1, c_p/σ20^p)`.

## 1. Arms (5 labels; every parameter fixed, no tuning)

| Arm | Sizing schedule |
|---|---|
| **C0 = K** | `p = 1`, `c = 0.35` — production, bit for bit. |
| **E1** | `p = 0.75` (flatter: fewer resizes, more exposure in turbulent regimes) |
| **E2** | `p = 1.25` |
| **E3** | `p = 1.5` |
| **E4** | `p = 2.0` (target-*variance* scaling; Moreira–Muir's form) |

> Naming: `E1`–`E4` are the **exponent** arms of `idea_survey_v5` §4.2. They are unrelated to the
> production "E2" *vote* / strategy name used elsewhere in this repo (and to `fast_reentry_v1`'s
> "E2" anchor arm). Every arm runs the fill rule `arm="K"` through the engine; only the
> target-weight array differs.

**Calibration (frozen here, before any arm run).** `c_p` is solved **once** on
**1985-10-01 → 2000-03-08** (3,648 ^NDX sessions, 3,628 with a defined σ20) so that
`nanmean(min(1, c_p/σ20^p))` equals `nanmean(min(1, 0.35/σ20))` on that window, `σ20` being the
annualized 20-session sample vol of the synthetic daily-reset 2× leg (`synthetic_2x` with zero
dividends — an index has none — base financing spread 0.0, 0.95 %/yr expense), i.e. the series K
sizes on. Bisection, monotone in `c`, tolerance 1e-15 relative. `p = 1` is `0.35` exactly by
identity (not solved). No α is searched; the window is excluded from every reported statistic.

| Arm | p | c_p | mean(vt) on the calibration window |
|---|---|---|---|
| C0 | 1.0 | 0.35 | 0.8396819604417771 |
| E1 | 0.75 | 0.42445881653676754 | 0.8396819604417769 |
| E2 | 1.25 | 0.29196422627125695 | 0.8396819604417767 |
| E3 | 1.5 | 0.24593188954326456 | 0.8396819604417776 |
| E4 | 2.0 | 0.17706308553599898 | 0.8396819604417775 |

`--harness` asserts these frozen constants against a fresh solve (1e-12).

## 2. Data and provenance

One download: `data/historical/vol_shape_v1_ndx.csv`, ^NDX daily OHLC 1985-10-01 → 2000-03-07
(3,648 rows, the last ^NDX session before 2000-03-08), Yahoo chart API,
`research/vol_shape_v1/fetch_ndx.py` (idempotent; the URL is printed). SHA-256 in
`DATA_MANIFEST.json`. Nothing else is downloaded: the main window, the TRY/Yİ-ÜFE calendars, the
six OOS windows and the bootstrap markets are the engine's existing committed files
(`fe_config`'s paths), unchanged.

## 3. Engine and parity (mandatory)

Everything runs on `research/fast_engine` (tag `archive/research/fast-engine-v1-2026-10`), restored
with `research/band_h2_combo_v1`, `research/trend_robustness_v2` (the pure-Python reference the
engine's parity suite imports) and the data files. No engine file is edited.

The arms are a **new combination, not a new mechanism**: the fill rule is `K` unchanged and the
target weights are the engine's documented extension point. The only place the engine feeds one
shared target series to every arm is `fe_boot._boot_chunk`, so `boot_shape.py` adds
`_boot_chunk_shape` (the same kernel with a per-arm target panel `(n_arms, W, T)`) and
`_boot_pt_shape` (its pre-tax twin for the pooled OOS bootstrap), plus the numpy panel builder
`chunk_panels` (a verbatim copy of `fe_boot.chunk_legs` with the shapes applied). Parity, asserted
in `--harness`:

1. `python -m research.fast_engine.parity --quick` → PASS.
2. K's published anchors through the engine (tolerance 0.01 pp), from
   `research/band_h2_combo_v1/RESULTS.json`: pre-tax main-window CAGR base 15.7649 % / cal
   15.1785 %, max DD 46.6698 % / 46.7948 %, turnover 5.456136 ; after tax at $100k 10.7533 % /
   15.2354 % / 23.9720 % ; clean bootstrap pre-tax medians K−C0 −0.0071 pp (base) / −0.0117 pp
   (calibrated), B1 +0.2856 pp (75.0 %), B2 +0.1311 pp (64.0 %).
3. **The shape option is inert at `(p = 1, c = 0.35)`**: the vt schedule is bit-identical to
   `fe_signals.vt_of` (^NDX and main window), the main-window target series is bit-identical to
   `fe_data.main_inputs`'s `w_r2` (both financing models), the panel builder reproduces
   `fe_boot.chunk_legs` bitwise, and **`boot_shape` with every arm on the identity shape
   reproduces `fe_boot.boot`'s `K` arm bit for bit** — both 5,000/1,000-path arrays and its own
   medians (the engine's `C0` arm is not comparable because this study's kernel runs K's fill rule
   for every arm).
4. The per-arm kernels equal `fe_sim.simulate` on the same paths (max |diff| 0 in `--smoke`), and
   the pooled-OOS kernel equals `fe_sim.simulate` on the same resampled market (max |diff| 0).
5. `fe_boot.draw_positions` equals an independent replay of the published draw recurrence.

Note on the discredited source material, quoted at the top of the study as the survey requires:
Moreira & Muir (JF 2017) motivate `p = 2`; Harvey et al. (JPM 2018) find the sizing *signal*
carries most of it; **Cederburg, O'Doherty, Wang & Yan (JFE 2020) find vol-managed portfolios
mostly fail out of sample across 103 strategies**. The honest prior is therefore "no edge", and
`idea_survey_v5` §4.2 expects −0.3 … +0.5 pp.

## 4. Shared mechanics

Production K exactly: decision on the completed close, next-open fill, 0.5 pp symmetric resize
band, entries and exits to zero always fill, `vt` on the synthetic daily-reset 2× leg
(`min(1, 0.35/σ20)`; the arm's own `min(1, c_p/σ20^p)`), total cap 0.98, cost 4 bps × m, idle cash
0 %, base financing (spread 0.0) and calibrated (0.75 %), synthetic QLD* expense 0.95 %/yr. `m = 2`
unless stated.

## 5. Stages

- **cal** — the frozen `c_p` table, and the ±10 % `c_p` plateau rows (report only, never selected).
- **main** — pre-tax main window 2000-03-09 → 2026-09-29, base and calibrated × `m = 1/2/4`, plus
  per-arm `mean_weight`, `mean |Δw|` and the exposure-normalised CAGR.
  **`mean_weight` is the arithmetic mean of the arm's base-financing target series over the
  measured window's decision sessions** `w[START-1 : T]` (the sessions the fills read), the same
  definition for every arm including K; `mean |Δw|` is the mean absolute target change over the
  same slice. The exposure-normalised CAGR is `(1+CAGR)**(w_K/w_arm) − 1` (diagnostic, not a gate).
- **AT** — after tax at $100k (gating) and $10k/$1M (report), progressive 2026 tariff deflated by
  the previous October's Yİ-ÜFE, liquidated at the window end, on the three standard windows.
- **cost** — `m = 4` after-tax stress at $100k (report).
- **B12** — clean bootstrap: `seed = 20261011`, `block = 20`, **5,000 pre-tax / 1,000 after-tax
  paths**, base and calibrated, `$100k` + `$1M`, B1-recent (progressive) and B2 (flat 20 % with
  loss carry-forward), the fixed 2015-09-01 → 2026-09-30 calendar with its realised USD/TRY and
  Yİ-ÜFE, QQQ market returns block-resampled.
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, `m = 2`,
  R-USD rule, own E2 + own early path + own synthetic 2× leg.
- **G8 boot** — the pooled out-of-sample bootstrap over M1–M5, 2,000 paths each, `seed = 20261011`,
  `block = 20`, `L = 2,772` sessions, pool `arange(start, len(days))` per market, one shared rng in
  market order, pre-tax, `m = 2`. Two disclosed conventions: (i) the simulation is production K's
  execution layer (H2's December window needs a calendar; `ewma_band_v1`'s pre-tax simulator had
  none), and (ii) each market's resampled paths run on a fixed calendar of that market's own last
  `L` sessions, the same construction as `fe_boot.BootWindow` (fixed calendar, resampled data).
- **dec** — report only: fill-decision differences vs K on the main pre-tax path, the share of
  sessions whose target differs by more than 0.5 pp, and sells per arm.

## 6. Gates (pre-registered)

Primary gate set = **`ewma_band_v1`'s G1–G9 with K as the control**, exactly as that study defines
them (the orchestrator protocol), plus the after-tax gates. "Both" means base and calibrated.

| Gate | Requirement |
|---|---|
| **G1** (m = 2, both) | CAGR ≥ K, **or** Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp. |
| **G2** (both) | pre-tax max DD ≤ K + 1 pp. |
| **G3** (both) | 2017+ CAGR ≥ K − 1 pp. |
| **G4** | CAGR above K in ≥ 2 of 3 sub-periods (P1 2000–08, P2 2009–16, P3 2017→end). |
| **G5** (both) | block-20 pre-tax bootstrap median of arm − K ≥ 0 (seed 20261011, 5,000 paths). |
| **G6** (both) | G1 at m = 1. |
| **G7** | six OOS windows: median Δ CAGR ≥ 0; ≥ 5 of 6 with Δ CAGR ≥ −0.5 pp and ≥ 5 of 6 with Δ DD ≤ +3 pp; Δ DD never > +8 pp. |
| **G8** | pooled OOS bootstrap (M1–M5, 10,000 paths): median Δ ≥ 0. |
| **G9** | turnover ≤ 2 × K. |

**After tax (all required).** (i) after-tax CAGR at $100k ≥ K in **all three** windows;
(ii) B1-recent (progressive) and B2 (flat) at $100k: median Δ ≥ 0 **and** share of paths with
Δ > 0 ≥ 55 %.

**Non-inferiority treatment for exposure-neutral arms (the survey's two-tier rule, declared now).**
The realised `mean_weight` drift vs K decides the *reading*, not the gate:
- `|drift| ≤ 1.0 pp` → **tier N**: the pre-tax reading is **non-inferiority** — G5 with a margin
  (median Δ ≥ −0.05 pp, both models) and G9 (≤ 2× K) — and the after-tax gates. All other gates are
  reported as information. The exposure-normalised CAGR is reported alongside.
- `|drift| > 1.0 pp` → **tier F**: the full G1–G9 apply and the exposure-normalised CAGR is
  reported alongside.

The tier rule can only *re-label* an arm, never adopt one: **adoption requires the strict G1–G9 and
the after-tax gate set**, because a relaxation that can only help a candidate is a data-mining
device. A tier-N arm that passes the non-inferiority reading but not the strict set is reported as
"exposure-neutral non-inferior, no gain".
§6.1 of `idea_survey_v3` pre-announced exactly this (median Δ ≥ −0.05 pp, turnover ≤ 2×) and
`idea_survey_v5` §4.2/§6.4 asks for it to be instantiated here.

**Support rule (pre-declared).** If the share of sessions whose *fill decision* differs from K's is
< 1 %, the arm's result is reported as "supported by < 1 % of sessions" and cannot be adopted
(`cash_yield_v1`'s precedent: 5 of 6,679 sessions produced +0.48 pp — small support is not
automatically disqualifying, but it must be disclosed).

**Decision rule.** `adopted = <arm>` iff strict G1–G9 ∧ after-tax gate set ∧ support ≥ 1 %;
otherwise `C0` (K). No arm is dropped and no parameter is changed after seeing data.

## 7. Multiple testing

Four exponent arms, each with **one** constant solved by a deterministic monotone rule (no search,
no threshold choice, the calibration window excluded from all statistics). The ±10 % `c_p` rows
are reported and never used to select. The posterior is that four correlated shape variants of the
same rule produce correlated deltas; a Bonferroni-style reading (a per-arm threshold at
0.05/4 ≈ 0.0125) makes a median-only pass insufficient, so the pre-registered support requirement
is that any candidate must show the **same sign** of the after-tax delta in all three windows *and*
both bootstraps *and* the pre-tax bootstrap, which is what the gate set already demands. Two of the
four arms (`E2` p = 1.25, `E4` p = 2.0) are the ones the literature motivates; `E1` (p = 0.75) and
`E3` (p = 1.5) are the interval neighbours that make a monotone reading possible. A
`c_p`-only reading of the plateau is pre-declared as noise.

## 8. Run protocol and pre-registered expectations

`--smoke` (synthetic prices only) → commit → `--harness` (parity + anchors + identity + option-off
parity) → `--run`, executed **twice** with byte-identical `RESULTS.json`.

Pre-registered expectations, stated before the run:

1. The identity checks and the option-off bootstrap parity pass; any mismatch is a bug.
2. `|mean_weight drift| ≤ 1.0 pp` for at least `E2`/`E3` (the calibration matches the mean of the
   *schedule*, and the 0.98 cap binds on both sides), but the 2000–2026 vol regime differs from
   1985–2000, so `E1`/`E4` may drift beyond 1 pp and fall into tier F. Both cases are pre-declared.
3. Pre-tax: within ±0.5 pp of K; `p < 1` should trade *less* and `p > 1` *more* (G9 is at 2×, so
   not binding in either direction).
4. After tax: A1u already blocks the gain-heavy trims, so the shape change's tax effect is
   second-order; the honest expectation is |Δ| ≲ 0.1 pp after tax at $100k — **inside the
   modelling uncertainty of §6.2 of the survey** — with a real chance that all four arms are
   noise-equivalent and the axis closes.
5. The study is decided pre-tax (the survey's own statement), and the likely outcome is "the shape
   does not matter at matched average weight".
6. If any arm passes every gate, the `±10 %` plateau rows and the p-ordering are reported for the
   reader, not used as evidence.

## 9. Caveats

One history per cell; no true after-tax out-of-sample (the TRY calendar starts 2000); the fixed
2015-09 → 2026-09 bootstrap calendar cannot price another FX/inflation regime; the liquidated
convention charges deferred tax at the window end; FIFO with per-year netting and no loss
carry-forward; the 2026 tariff deflated by the previous October's Yİ-ÜFE; instalments funded by
selling. The calibration window is 1985–2000 (^NDX price index, zero dividends — the index
convention every OOS stage of this campaign uses), which is a *different* market regime from the
measured window: the matched mean is matched on the pre-2000 window by construction, not on
2000–2026. G8's implementation differs from `ewma_band_v1`'s in the two disclosed ways of §5.
After-tax deltas below ~0.1 pp are inside modelling uncertainty. Not tax advice.

## 10. Amendments

**A1. Five code fixes in the reporting/aggregation layer, made after the first complete run.**
None touches an arm, a `c_p`, a stage, a threshold, a gate or the decision rule, and no numeric
cell of the arm results moved (the first run's printed arm cells — CAGR, max DD, turnover, OOS and
bootstrap deltas — are identical to the final ones):

1. **The realised-mean-weight and `mean |Δw|` slice indexed axis 0 of a 2-D array**, so both
   printed NaN and every arm was forced into tier F. Fixed to slice the time axis. Consequence: the
   tier label, which had been wrong, is now N for all four arms; the *adoption* gate is the strict
   G1–G9 set plus the after-tax set (declared in §6 before any run), so no gate outcome changed.
2. **Two harness checks did not gate the harness verdict** (`calibration_frozen`,
   `target_identity_main` had no top-level `pass`, so the aggregator skipped them, and the
   `vt_identity_ndx`/`target_identity_main` comparisons were computed against the base model's
   series in both financing models). Both were and are passing; they now gate.
3. **The verdict string** now names the failed gates instead of the misleading "no gain"; the
   tier-N label is a reading, never an adoption.
4. **`RESULTS.json` no longer records the parity run's wall clock** (nor its "in 12.0 s" tail
   line), so two `--run`s are byte-identical; the study's own wall clock is in `SUMMARY.txt` only.
5. A kernel-wrapper bug found by `--smoke` **before** the pre-registration commit, disclosed in the
   pre-commit note below: the panel builder emitted all five arms' panels while the kernel's output
   buffers were sized for the arms actually requested (heap corruption, non-deterministic crashes).
   Fixed by passing the arm list to `chunk_panels`; the pre-commit run of `--harness` includes the
   fix.

**Pre-commit honesty note.** Before the pre-registration commit, on this branch, the following ran:
(a) the ^NDX download (`fetch_ndx.py`); (b) the `c_p` solve, whose frozen values are in §1 — it
reads pre-2000 ^NDX only, simulates nothing and uses no 2000–2026 data; (c) `--smoke`, synthetic
random-walk prices only, which is what localized a heap-corruption bug in the study's own kernel
wrapper (the panel builder was emitting all five arms' panels while the kernel's output buffers
were sized for the arms actually asked of it); (d) `--harness` (control and identity only: the
engine's parity suite, K's published anchors, and the option-off bit-for-bit parity of the
bootstrap), which passed. **No non-control arm (E1–E4) was simulated on real data, no gate outcome
existed, and no arm's pre-tax/after-tax performance or bootstrap statistic had been computed** at
the time of this commit. The harness was run before the commit deliberately, to derisk it: it does
not touch a non-control arm.
