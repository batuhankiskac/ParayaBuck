# gain_band_v2 — after-tax no-trade region, correction re-run (I3, control = production R2)

Pre-registered 2026-10-14, **before any non-control arm was simulated on real data on this
branch**. The only mode run before this commit is `--smoke` (synthetic random-walk prices, no
market data, no arm/ownership of the result). `--harness` and `--run` were not executed.

Campaign: idea **I3** of `research/idea_survey_v2` (`docs/research/idea_survey_v2.md` §4),
correction re-run of `gain_band_v1`. Control identity: production **R2** =
`equity_leveraged_trend_v1` 1.2.0 (= E2 + the half-weight early re-entry path). Working branch
`research/gain_band_v2`.

## 0. Diagnosis of `gain_band_v1` (why this re-run exists)

**`gain_band_v1`'s control label "C0" was bound to the pure-E2 target series, not to production
R2's.** The arms were driven by R2's series, so every "Δ vs R2" in that report that came from
those stages is really "Δ vs E2". File:line, in `research/gain_band_v1/screen.py`:

| Site | Line(s) | Effect |
|---|---|---|
| `arm_signals` | 305–313 (`if arm == "R2" … else` at 307–312) | any label other than `"R2"` — including `"C0"` — falls through to the pure-E2 target series (`factor = 1`, no early leg). |
| `main_inputs` | 604, 606 | `w_c0 = arm_signals(price, e2, vol_instr, "C0", comps)` = **E2** wants; `w_r2` = R2 wants. |
| `run()` | 1149, 1156 | `WANTS = {"C0": w_c0, A1/A2/A3: w_r2}` and the per-financing `W = {"C0": wc_, …}` → the control arm's target series is E2 for every run of the pre-tax main window, the R-USD row, the three after-tax windows and the m = 4 cost stress. |
| `oos_window` | 735, 740 | `w_c0, w_r2 = wants_of(...)`; `w_c0 if arm == "C0" else w_r2` → the six OOS windows' control is E2 (G7's Δ). |
| `instrument_window` | 784, 789 | same pattern → the 21-ETF control is E2 (gate F3's Δ). |
| `boot_oos_market` (gate G8) | 935, 937 | same pattern → the pooled OOS bootstrap's control is E2. |

**Not affected (already R2, by accident).** `boot_e1` drives *every* label from `w_r2`
(`legs = {"base": (so[0], sc[0], w_r2), …}` at line 851, used for all `arm in ARMS`), so gate
**G5** and the after-tax bootstraps **B1/B2** were already measured against production R2; the
pure-E2 series there is tracked separately as `anchor_e2` (used only for the engine-fidelity
anchor against `tax_harvest_v2`). The `--harness` reproduction checks confirmed the mislabel
rather than catching it: v1 asserted `("C0", REF_DEV, wc_)` — i.e. it pinned the label "C0" to
`e2_integration_v1`'s **E2** anchors (line 1092, `REF_DEV`, renamed `REF_E2` here) and the label
"R2" to the R2 anchors. So both facts were true simultaneously: the engine reproduces production
R2 exactly under the label "R2", and the *run* never used that label for its control.

**Symptom.** v1's control row reads pre-tax 14.67 % / 14.10 % CAGR, max DD 49.56 % — exactly E2
(`REF_DEV`: 0.146749 / 0.141043, 0.495643) — and after tax 9.53 / 14.50 / 22.69 %, exactly
`TAX_ANCHORS["C0"]` (E2). Production R2 is 15.48 % / 14.90 %, max DD 46.76 %, after tax
10.16 / 14.71 / 22.89 %. Arms A1–A3 (15.68 / 15.90 / 15.74 %) were R2 + the band and are
unaffected, so the v1 Δ columns are inflated by E2's own gap to R2 (≈ +0.79 pp CAGR pre-tax on
the main window, 0.63 pp after tax on the 2000 window). v1's verdict (FAIL on G5) was reached
through G5, which was against the correct control; the other 17 gate keys were not.

**What this re-run changes.** Nothing about the strategy, the arms, the parameters, the data,
the mechanics, the gates or the selection rule: the arms stay **R2 + the band**, the control
becomes **production R2** (R2's target series with the production 0.5 pp band and no arm rule),
and `--harness` gains an explicit assertion that the *control label the run uses* reproduces the
R2 anchors (pre-tax 15.48 / 14.90 %, max DD 46.76 %, after tax 10.16 / 14.71 / 22.89 % at $100k).

## 1. Question

R2 resizes to its vol target on the first close that breaches a **constant 0.5 pp** band, and
~91 % of its realised Turkish taxable gain comes from those downward resizes under **FIFO** lot
matching (`after_tax_tr_v1`, `tax_harvest_v1`). Does replacing that constant band, **on downward
resizes only**, with a threshold that grows with (a) the **embedded indexed taxable TRY gain of
the lots the sale would consume** and (b) the **year's accumulated realised TRY gain relative to
the year's deflated bracket ceiling** improve the **after-Turkish-tax** CAGR at $100k versus R2,
without a materially worse drawdown? (Unchanged from `gain_band_v1`.)

## 2. Data and provenance

Nothing is downloaded. Every input is an existing committed research file, checked out of this
repository unchanged; `DATA_MANIFEST.json` records the source branch, size and SHA-256 of each of
the 29 files (byte-identical to `gain_band_v1`'s inputs; all re-verified in this worktree). Uses:
`stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars, DTB3), `after_tax_tr_v1_{usdtry_cbrt,
yiufe}.csv` (TRY conversion and indexation), `ewma_band_v1_{gspc,index,tb3ms}.csv` (gate G7
windows), `range_vol_v2_*.csv` (19) + `range_vol_v1_{spy,dia,iwm}.csv` (gate F3's 21 ETFs; `EWC`
excluded by `range_vol_v2`'s fixed quality rule).

The TRY/Yİ-ÜFE calendar starts 2000-01 (USD/TRY) and 1999-01 (Yİ-ÜFE); there is no pre-2000
Turkish tax evidence, so the after-tax stage has no true out-of-sample and the clean bootstraps
B1/B2 are the substitutes (as in every after-tax study here).

## 3. Control (C0)

**Production R2, bit for bit**: the production target series (E2 vote + the half-weight early
re-entry leg, sized `vt = min(1, 0.35/vol20)`, cap 0.98) with the production execution layer
(decide on the completed close, fill at the next open, symmetric 0.5 pp band, entries and exits
to zero always fill, idle cash zero, cost 4 bps × m × |Δw|, synthetic QLD\* = daily-reset 2x on
QQQ total return, 0.95 %/yr expense, financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr),
through the single sequential engine `research/gain_band_v2/tax_engine.py` (byte copy of
`research/tax_harvest_v2/tax_engine.py` = the corrected `vt_level_tax_v1` multi-instrument
engine). Main window 2000-03-09 → 2026-09-29 (6,679 sessions; session 253 onwards).

**Anchors asserted in `--harness` (the task's requirement).** The control label the run uses
(`CONTROL = "C0"`) must reproduce, within `TOL = 1e-4`:

| Quantity | Reference | Value |
|---|---|---|
| pre-tax CAGR, base, m = 2 / m = 1 | `fast_reentry_v1/RESULTS.json` main_window | 15.48 % / 15.75 % |
| pre-tax CAGR, calibrated, m = 2 / m = 1 | idem | 14.90 % / 15.17 % |
| pre-tax max DD, base / calibrated, m = 2 | idem | 46.76 % / 46.88 % |
| after-tax liquidated CAGR at $100k, three windows | `vt_level_tax_v1` + `r2_tbill_combo_v1` | 10.16 / 14.71 / 22.89 % |
| after-tax max DD at $100k, three windows | idem | 46.76 / 43.78 / 36.24 % |
| B1-recent / B2-flat bootstrap median after tax at $100k | `tax_harvest_v2` | 3.5155 % / 3.9678 % |

The harness reports them as the `r2_anchors` block (pass/fail) and fails the whole harness if any
is outside tolerance; it additionally asserts that the control's NAV path **differs** from the
pure-E2 anchor series (the v1 bug, in reverse). The pure-E2 series is still reproduced under the
label `E2` against `e2_integration_v1` (`REF_E2`) so the engine's fidelity to that reference is
not lost.

## 4. Arms (4: control + 3 candidates; every parameter fixed, identical to `gain_band_v1`)

All arms share **R2's target series exactly** (computed once; only the fill decision differs).
Only a **downward resize** (`tgt > 0` and `tgt < held weight`) may be suppressed — including the
transfer between R2's full weight and its half-weight early-re-entry leg, which is a downward
resize between two positive targets and is therefore subject to the arm's band. Entries, exits to
zero and up-resizes always keep the production 0.5 pp band (`B_up = 0.005`).

| Arm | Rule |
|---|---|
| **C0** | production R2 (constant 0.5 pp band, no other change) |
| **A1** | **gain-scaled band**: a downward resize fills iff `|tgt − w| > B_down(g_frac)`, `B_down` = **0.5 pp** if `g_frac ≤ 1 %`, **3 pp** if `1 % < g_frac ≤ 5 %`, **8 pp** if `g_frac > 5 %`. Up-resizes/entries/exits: 0.5 pp. No deferral state. |
| **A2** | A1 **+ bracket budget**: while `YTD net realised rule gain ≥ 1,000,000 × scale(year)` TRY (the year's deflated 27 %-bracket ceiling, `scale(year)` = Yİ-ÜFE(previous October)/Yİ-ÜFE(2025-10)), a discretionary downward resize is **postponed** (the ordinary rule is re-evaluated at every later close, so it executes in the next tax year at the latest). Hard override: fill if `|tgt − w| > **15 pp**`, or on the decision close at which the E2 vote turns off, or if the target is ≤ 0. |
| **A3** | A2 with the hard override at **10 pp**. |

The decision state is the **rule gain**: the net TRY gain of the units the sale would consume,
computed FIFO over the live lot book with the engine's own indexation arithmetic (GVK mük. 81:
if Yİ-ÜFE(month before sale)/Yİ-ÜFE(month before purchase) − 1 ≥ 10 %, multiply the cost basis by
that factor; per-lot gain floors at 0) and the CBRT buying rate of the trade date; proceeds are
the same units × fill price, FX at the trade date, net of the trade's cost; `g_frac = rule gain /
rule proceeds`. `YTD` accumulates the same rule gain over every sale the account makes in the
calendar year (including the tax-instalment funding sales), added after the fill.

**Instantiation rule (unchanged).** R-INDEX (real USD/TRY + Yİ-ÜFE) where a TRY calendar exists
(main window, after-tax windows, both bootstraps); **R-USD** (`fx ≡ 1`, no indexation, budget
inert) for the six OOS windows and the 21 ETFs, so `A2 ≡ A3 ≡ A1` there; the pre-tax main window
is also reported under R-USD. The pre-tax runs of the main window and of bootstrap E1 use the
$100k gate capital so that the bracket budget is active at the same account scale as the
after-tax gate runs (pre-tax *return* statistics are scale-free).

## 5. Mechanics, stages and cost

Identical to `gain_band_v1` §5: pre-tax main window (base/calibrated × m = 1/2, m = 4 reported;
$100k account), the R-USD row, six OOS windows (`^GSPC` 1957-01-02 → 1999-12-31 and M1 `^IXIC`
1975–1999, M2 `^GSPC` 1935–1956, M3 `^N225`, M4 `^GDAXI`, M5 `^FTSE`; calibrated, m = 2, R-USD),
the 21 quality-passing ETFs (R-USD, calibrated, m = 2), and after tax at $10k/$100k/$1M
(liquidated at the window end; base financing, m = 2; gate capital $100k) with an m = 4 stress.
Bootstrap **E1** (block 20, seed 20261011, fixed 2015-09-01 → 2026-09-30 calendar with its
realised USD/TRY and Yİ-ÜFE, QQQ market returns resampled): 5,000 pre-tax paths (G5), 1,000
after-tax paths (B1 progressive, B2 flat 20 %) at $100k and $1M. Bootstrap **E2** (G8): pooled
block-20 over M1–M5, 2,000 paths per market, 2,772-session paths.

## 6. Gates (unchanged; all must pass; none is relaxed after seeing data)

Pre-tax against the control (= production R2), `ewma_band_v1`'s G1–G9 verbatim:

- **G1** (m = 2, base and calibrated): CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 − 0.5 pp.
- **G2** (both): max DD ≤ C0 + 1 pp.
- **G3** (both): 2017+ CAGR ≥ C0 − 1 pp.
- **G4**: CAGR above C0 in ≥ 2 of P1 2000–2008, P2 2009–2016, P3 2017 →.
- **G5** (both): bootstrap E1 pre-tax median of (arm − C0) ≥ 0.
- **G6** (both): G1 at m = 1.
- **G7**: six OOS windows (calibrated, m = 2): median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in ≥ 5 of 6;
  Δ max DD ≤ +3 pp in ≥ 5 of 6 and never > +8 pp.
- **G8**: pooled OOS bootstrap E2 median Δ ≥ 0.
- **G9**: turnover/yr ≤ 2 × C0.

After tax at $100k (progressive 2026 tariff, liquidated): **AT** after-tax CAGR > R2 in all three
windows; **B1**/**B2** E1 medians Δ ≥ 0 and share of paths with Δ > 0 ≥ 0.55.

Across instruments: **F3** Δ max DD ≤ +3 pp on ≥ 80 % (≥ 17 of 21) of the ETFs.
Plateau: **P** the selected arm's pre-registered neighbours (A1↔A2, A2↔A3) each pass AT.

## 7. Selection rule

Selected arm = the arm that passes **every** gate (G1–G9, AT, B1, B2, F3, P) and has the highest
after-tax liquidated CAGR at $100k on the 2000-03-09 → 2026-09-29 window. If no arm passes every
gate, the verdict is "no candidate" and nothing is proposed for production. Reported but not
gated: m = 4, the unliquidated view, per-window Δ at $10k/$1M, per-year postponement counts, the
R-USD main-window row.

## 8. Run protocol and pre-registered expectations

`--smoke` (synthetic only; also asserts that the control label "C0" is the R2 target series and
that the E2 anchor series differs from it), then `--harness` (control reproduction: the R2
anchors of §3 on the control label, the E2 anchors under the label `E2`, the B1/B2 medians, and
the identity "control engine == `simulate_band`"), then `--run` (every arm on every stage, writes
`RESULTS.json` + `SUMMARY.txt`), executed **twice** with byte-identical `RESULTS.json`.

Pre-registered expectations, stated before the run so that they cannot be re-interpreted after:

1. **The arms' numbers must be numerically identical to `gain_band_v1`'s arm rows** (the arms'
   code path is untouched; only the control changed). Any difference is a bug in this re-run.
2. The control's rows change from v1's E2 values to the R2 anchors of §3.
3. Therefore the Δ columns shrink by the E2→R2 gap. Where the arm's pre-tax edge over E2 was
   smaller than R2's own edge over E2, the gate that depends on that comparison flips to FAIL.
   G5, B1 and B2 (already measured against R2 in v1) should reproduce v1's values **exactly**.

## 9. Caveats and limits

As `gain_band_v1` §9, unchanged: one history per cell; no true after-tax out-of-sample; B1 holds
the TRY calendar fixed at 2015-09 → 2026-09 and B2 charges no inflation; the liquidated
convention charges deferred tax at the window end; the rule reads the live lot book (FIFO,
indexation, trade-date FX); FIFO, per-year netting without loss carry-forward, the 2026 tariff
deflated by the previous October's Yİ-ÜFE and the 10 % indexation floor are the model's
assumptions, not tax advice; four arms with three gain tiers and two override levels were fixed
in advance, with the plateau gate and the six-window structure of G7 as the guards.

## 10. Amendments

None at the time of this commit.
