# gain_band_v1 — after-tax no-trade region: gain-scaled resize band + bracket budget (I3)

Pre-registered 2026-10-14, **before any non-control arm was simulated on real data**. The
only modes run before this commit were `--smoke` (synthetic random-walk prices, no market
data) and the timing benchmark reported under "Amendments" if it needed a fix.

Campaign: idea **I3** of `research/idea_survey_v2` (`docs/research/idea_survey_v2.md` §4).
Control identity: production **R2** = `equity_leveraged_trend_v1` 1.2.0 (= E2 + the
half-weight early re-entry path). Working branch `research/gain_band_v1`.

## 1. Question

R2 resizes to its vol target on the first close that breaches a **constant 0.5 pp** band,
and ~91 % of its realised Turkish taxable gain comes from those downward resizes under
**FIFO** lot matching (`after_tax_tr_v1`, `tax_harvest_v1`). Does replacing that constant
band, **on downward resizes only**, with a threshold that grows with (a) the **embedded
indexed taxable TRY gain of the lots the sale would consume** and (b) the **year's
accumulated realised TRY gain relative to the year's deflated bracket ceiling** improve
the **after-Turkish-tax** CAGR at $100k versus R2, without a materially worse drawdown?

Constant wider bands (2.5/5/10 pp) were tested pre-tax (`ewma_band_v1` B25/B50/B100: they
lose 0.07–0.60 pp CAGR) and after tax (`after_tax_tr_v1`: ±0.21 pp). This study tests
**state-dependent, asymmetric** bands — the Dammon–Spatt–Zhang prediction — and nothing
else: the signal, the sizing, the instruments and the fill model are production R2's.

## 2. Data and provenance

Nothing is downloaded. Every input is an existing committed research file, checked out of
this repository unchanged; `DATA_MANIFEST.json` records the source branch, size and
SHA-256 of each. Inputs and their uses:

| File | Use |
|---|---|
| `data/historical/stress_robustness_v1_daily.csv` | QQQ/SPY/QLD open, close, dividend bars (`trv2.load()`) |
| `data/historical/stress_robustness_v1_dtb3.csv` | DTB3 financing rate |
| `data/historical/after_tax_tr_v1_usdtry_cbrt.csv` | CBRT USD/TRY forex buying (tax conversion, indexation arithmetic) |
| `data/historical/after_tax_tr_v1_yiufe.csv` | TUIK Yİ-ÜFE monthly index (indexation, deflated brackets) |
| `data/historical/ewma_band_v1_gspc.csv` | gate G7 window ^GSPC 1957-01-02 → 1999-12-31 |
| `data/historical/ewma_band_v1_index.csv` | gate G7 windows M1–M5 (^IXIC/^GSPC/^N225/^GDAXI/^FTSE OHLC) |
| `data/historical/ewma_band_v1_tb3ms.csv` | TB3MS pre-1954 T-bill rate for M2 |
| `data/historical/range_vol_v2_*.csv` (19), `range_vol_v1_{spy,dia,iwm}.csv` | gate F3: 21 quality-passing equity ETFs (`EWC` excluded by `range_vol_v2`'s fixed quality rule: 5.6 % invalid/flat bars) |

The TRY/Yİ-ÜFE calendar starts 2000-01 (USD/TRY) and 1999-01 (Yİ-ÜFE); there is no
pre-2000 Turkish tax evidence, so the after-tax stage has no true out-of-sample and the
clean bootstraps B1/B2 are the substitutes (as in every after-tax study here).

## 3. Control (C0)

Production R2, bit for bit, through a single sequential engine that is a byte-faithful copy
of the after-tax engine used by `tax_harvest_v1/v2` and `r2_tbill_combo_v1`
(`research/gain_band_v1/tax_engine.py` = byte copy of `research/tax_harvest_v2/tax_engine.py`),
with the production execution layer:

- Signal: at least 2 of 3 votes on the QQQ **price** close — close > SMA200; 252-session
  price return > 0; close > the 126-session max/min midpoint.
- Sizing: `vt = min(1, 0.35 / vol20)` on the synthetic QLD\*, early path at `0.5 x vt`
  while the vote is off and QQQ close > SMA50, vol20 < its trailing 252-session median and
  close > close 20 sessions ago; exit the early path below SMA50; E2's vote overrides.
- Cap 0.98; decide at the completed close, fill at the **next open**; idle cash earns zero;
  cost = 4 bps x m x |Δw|; **0.5 pp** symmetric resize band; entries and exits to a target
  of zero always fill.
- Instrument: synthetic QLD\* = daily-reset 2x on QQQ total return, 0.95 %/yr expense,
  financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr.
- Main window 2000-03-09 → 2026-09-29 (6,679 sessions; session 253 onwards).

## 4. Arms (4: control + 3 candidates; every parameter fixed)

All arms share **R2's target series exactly** (`wants` are computed once; only the fill
decision differs). Only a **downward resize** (`tgt > 0` and `tgt < held weight`) may be
suppressed; entries, exits to zero, and up-resizes always keep the production 0.5 pp band
(`B_up = 0.005`).

The decision state is the **rule gain**: the net TRY gain of the units the sale would
consume, computed FIFO over the live lot book with the **engine's own indexation
arithmetic** (GVK mük. 81: if Yİ-ÜFE(month before sale) / Yİ-ÜFE(month before purchase) − 1
≥ 10 %, the cost basis is multiplied by that factor; the per-lot gain floors at 0) and the
**CBRT buying rate of the trade date**. Proceeds are the same units x fill price, FX at the
trade date, net of the trade's cost. `g_frac = rule gain / rule proceeds`.

| Arm | Rule |
|---|---|
| **C0** | production R2 (constant 0.5 pp band, no other change) |
| **A1** | **gain-scaled band**: a downward resize fills iff `|tgt − w| > B_down(g_frac)`, with `B_down` = **0.5 pp** if `g_frac ≤ 1 %`, **3 pp** if `1 % < g_frac ≤ 5 %`, **8 pp** if `g_frac > 5 %`. Up-resizes/entries/exits: 0.5 pp. No deferral state. |
| **A2** | A1 **+ bracket budget**: while `YTD net realised rule gain ≥ 1,000,000 x scale(year)` TRY — the year's **deflated 27 %-bracket ceiling** (`scale(year)` = Yİ-ÜFE(previous October)/Yİ-ÜFE(2025-10), the engine's own bracket deflation) — a discretionary downward resize is **postponed** (not executed; the ordinary rule is re-evaluated at every later close, so it executes in the next tax year at the latest). Hard override: fill immediately if `|tgt − w| > **15 pp**`, or on the decision close at which the E2 vote turns off, or if the target is ≤ 0. |
| **A3** | A2 with the hard override at **10 pp** (the plateau neighbour of A2's tail protection). |

`YTD net realised rule gain` accumulates the same rule gain (indexed TRY, FIFO) over every
sale the account makes in the calendar year — resizes, exits, and the tax-instalment
funding sales — including the sale being decided (it is added after the fill, so the
session's own sale does not gate itself).

### 4.1 Resolutions of the survey's ambiguities (I3 §4 of the survey)

1. **The survey specifies five arms (C0, A1–A4); the task caps the study at ≤ 4 arms.**
   A4 ("thresholds 1.5 %/7 %") is **dropped**. The plateau property is instead tested on
   the budget/override dimension the survey itself flags as the tail-protection decision:
   neighbours are A1↔A2 (budget on/off) and A2↔A3 (override 15 pp vs 10 pp).
2. **"The year's deflated 27 % ceiling"** is read as the TRY amount at which the model's
   marginal rate leaves the 27 % bracket for 35 %: `1,000,000 x scale(year)` TRY, using the
   engine's own deflation (`scale(year) = Yİ-ÜFE(year−1 Oct)/Yİ-ÜFE(2025-10)`).
3. **"Indexed taxable gain of the lots the sale would consume"** is implemented on the
   *live* lot book at the fill (the engine's FIFO book), evaluated at the fill price and
   the fill's own cost; the same value is used in the pre-tax and after-tax runs (see 4.2),
   so the arm's trade path does not depend on whether tax is levied.
4. **"discretionary downsizes are postponed to the next tax year"** is implemented as
   "not executed while the budget condition holds"; the ordinary rule is re-evaluated at
   every subsequent close, so the postponement ends with the calendar year (or on an
   override). This is the `tax_harvest_v2` H2 convention (a postponed fill is not retried).
5. **Arms in the price-only stages.** The six out-of-sample windows and the 21 ETFs have no
   TRY/Yİ-ÜFE calendar (^GSPC 1935–56, ^N225, ^GDAXI, ^FTSE, and ETF histories from 1996),
   and the bracket ceiling is a TRY amount that cannot be transplanted to a unit-NAV,
   USD-numbered history. Therefore **in the OOS and instrument stages** the rule is the
   **R-USD instantiation**: `fx ≡ 1`, no indexation (`index=False`), and the bracket budget
   is inert (a unit-NAV account never realises 1,000,000 "TRY" in a year), so **A2 ≡ A3 ≡ A1
   there**. Their *band* mechanism is identical to A1's and that is what those two stages
   measure; the budget's incremental effect is measured on the main window (pre-tax, G1–G6,
   G9) and in the after-tax stage (windows, B1, B2). To make the comparison explicit, the
   main window is **also** reported pre-tax under R-USD for every arm.
6. **B2 (flat 20 %)** taxes the account's non-indexed USD gain (its own definition, kept to
   stay comparable with the reference bootstrap anchors), while the arm's **decision** state
   uses the same indexed rule gain as B1. The B2 column is therefore a lower bound on the
   indexation channel, not a different rule.
7. **Account scale of the decision state (added in Amendment A1).** The bracket budget is a
   *nominal TRY* threshold, so it is capital-dependent by construction (the model's tariff
   brackets are fixed TRY amounts, exactly as in every after-tax study here). The pre-tax runs
   of the main window and of bootstrap E1 therefore use the gate capital **$100k**, the same
   account scale as the after-tax gate runs, so that the pre-tax and after-tax trade paths of
   an arm coincide (the pre-tax *return* statistics are scale-free: with no tax the NAV path
   scales linearly). The OOS/instrument stages keep a unit-NAV account, where the budget is
   inert by construction (item 5).

## 5. Mechanics, stages and cost

- Pre-tax main window: base and calibrated financing, m ∈ {1, 2}; m = 4 reported only
  (cost stress). The account is funded with $100k (see 4.1 item 7); the reported CAGR, max DD
  and turnover are the scale-free NAV-path statistics.
- Pre-tax R-USD row: base and calibrated, m = 2 (report-only).
- Six OOS windows (`ewma_band_v1`/`e2_confirmation_v1`): ^GSPC 1957-01-02 → 1999-12-31 and
  M1 ^IXIC 1975–1999, M2 ^GSPC 1935–1956, M3 ^N225, M4 ^GDAXI, M5 ^FTSE; calibrated
  financing, m = 2, each window with its own E2 + early path and its own synthetic 2x leg,
  R-USD rule (4.5).
- 21 quality-passing ETFs (`fast_reentry_v2`/`range_vol_v2` design): own E2 + early path,
  own synthetic 2x leg, calibrated, m = 2, R-USD rule.
- After tax: the corrected multi-instrument Turkish engine, base financing, m = 2, at
  $10k/$100k/$1M, liquidated at the window end (the primary convention); windows
  2000-03-09→2026-09-29, 2010-01-01→2026-09-29, 2017-01-03→2026-09-29. The gate capital is
  $100k. The unliquidated view is reported.
- Bootstrap **E1** (block 20, seed 20261011, 1,000 ten-year paths on the fixed
  2015-09-01 → 2026-09-30 calendar with its realised USD/TRY and Yİ-ÜFE; market returns —
  QQQ overnight/intraday/DTB3 — resampled, block-20, exactly the `tax_asym_v2` /
  `vt_level_tax_v1` / `tax_harvest_v2` construction): used for
  - **G5** (pre-tax, base and calibrated, **5,000 paths**), and
  - **B1** (progressive) and **B2** (flat 20 %) after-tax at $100k and $1M (**1,000 paths**,
    the first 1,000 draws of the same stream, so the control medians reproduce
    `tax_harvest_v2`'s anchors).
- Bootstrap **E2** (gate G8; `fast_reentry_v1` design): pooled block-20 bootstrap over
  M1–M5, 2,000 paths per market, 2,772-session paths, calibrated, m = 2, R-USD rule.

## 6. Gates (all must pass; no gate is relaxed after seeing data)

Pre-tax, against R2 (C0), `ewma_band_v1`'s G1–G9 verbatim:

- **G1** (m = 2, base and calibrated): CAGR ≥ C0, or Calmar ≥ 1.1 x C0 with
  CAGR ≥ C0 − 0.5 pp.
- **G2** (both): max DD ≤ C0 + 1 pp.
- **G3** (both): 2017+ CAGR ≥ C0 − 1 pp.
- **G4**: CAGR above C0 in ≥ 2 of the 3 sub-periods P1 2000–2008, P2 2009–2016, P3 2017 →.
- **G5** (both): bootstrap E1 pre-tax median of (arm − C0) ≥ 0.
- **G6** (both): G1 at m = 1.
- **G7**: the six OOS windows (calibrated, m = 2): median Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in
  ≥ 5 of 6; Δ max DD ≤ +3 pp in ≥ 5 of 6 and never > +8 pp.
- **G8**: pooled OOS bootstrap E2 median Δ ≥ 0.
- **G9**: turnover/yr ≤ 2 x C0.

After tax at $100k (progressive 2026 tariff, liquidated):

- **AT**: after-tax CAGR > R2 in **all three** windows.
- **B1**: E1/B1 median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 0.55.
- **B2**: E1/B2 median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 0.55.

Drawdown across instruments (the task's extra condition, `fast_reentry_v2`'s gate-I design):

- **F3**: the 21 quality-passing ETFs (calibrated, m = 2, R-USD) show Δ max DD ≤ +3 pp in
  **≥ 80 %** (i.e. ≥ 17 of 21) of instruments.

Plateau (the `ewma_band_v1` lesson — a single cell must not decide the verdict):

- **P**: the selected arm's pre-registered neighbours (A1↔A2, A2↔A3) **each** beat R2 after
  tax at $100k in **all three** windows.

## 7. Selection rule

Selected arm = the arm that passes **every** gate (G1–G9, AT, B1, B2, F3, P) and has the
highest **after-tax liquidated CAGR at $100k on the 2000-03-09 → 2026-09-29 window**. If no
arm passes every gate, the verdict is "no candidate" and nothing is proposed for
production. Reported but not gated: m = 4 cost stress, the unliquidated view, the
per-window Δ at $10k/$1M, per-year postponement counts, and the R-USD main-window row.

## 8. Run protocol

`--smoke` (synthetic only, no market data), then `--harness` (control reproduction: R2/C0
pre-tax at m = 1/2 base/calibrated vs `fast_reentry_v1`/`e2_integration_v1`; the after-tax
anchors at $100k on the three windows; the control's B1/B2 bootstrap medians vs
`tax_harvest_v2`; and the identity "C0 engine == `simulate_band`" plus "rule-gain probe ==
the engine's own sell gain"), then `--run`, which runs every arm on every stage and writes
`RESULTS.json` + `SUMMARY.txt`. `--run` is executed **twice** and the two `RESULTS.json`
files must be byte-identical.

## 9. Caveats and limits

- One history per cell; the after-tax side has no true out-of-sample (TRY/Yİ-ÜFE from 2000
  only). B1 holds the TRY calendar fixed at 2015-09 → 2026-09; B2 charges no inflation and
  so understates indexation-based deferral. The liquidated convention charges the deferred
  tax at the window end.
- The rule reads the *live* lot book: in the after-tax runs the FIFO book is the tax
  engine's own (indexation, trade-date FX, per-lot floor). Pre-tax and after-tax runs share
  the decision state, but tax-instalment funding sales can perturb the book slightly.
- FIFO, per-year netting without loss carry-forward, the 2026 tariff deflated by the
  previous October's Yİ-ÜFE, and the 10 % indexation floor are the model's assumptions
  (`after_tax_tr_v1` confidence levels), not tax advice.
- Multiple testing: 4 arms, 3 gain tiers, 2 override levels — the plateau gate P and the
  six-window/five-per-window structure of G7 are the guards; no cell will be optimised
  after the fact.

## 10. Amendments

**A1 (before the pre-registration commit, recorded for transparency).** While testing the
first working version of `screen.py` I ran a *sanity probe* of the pre-tax main window for all
four arms on real data (base financing, m = 2, unit capital) before noticing that the bracket
budget, being a nominal TRY threshold, is inert at a unit-NAV account. The probe returned
C0 14.675 % / DD 49.56 %, A1 = A2 = A3 15.683 % / DD 47.46 % (band blocks 1,293, budget
blocks 0). That probe is an unregistered look at candidate-arm results before the pre-
registration commit and is disclosed here; no arm definition, parameter, gate, threshold or
selection rule was changed as a result of it, and the full `--run` is the pre-registered one.
The one change made is item 4.1(7) below, a *mechanics* fix (pre-tax runs at the gate capital
$100k) required for the pre-tax and after-tax trade paths of an arm to be the same strategy;
it changes no gate and no arm parameter, and it is what makes the budget active on the
pre-tax path at all (at unit capital it fired 0 times).

**A2.** `--smoke` uses no market data and now proves, in addition to the identity checks, that
the bracket budget is *reachable*: at a large account scale (fx = 1, flat Yİ-ÜFE, $1e9) arm A2
postpones 20 downsizes that the band would have filled, while A1 (no budget) postpones none.
The smoke was strengthened before the full run; it changes no gated quantity.
