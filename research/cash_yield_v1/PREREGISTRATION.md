# cash_yield_v1 — does paying interest on the account's idle cash survive Turkish tax?

**Pre-registration.** Committed before any non-control arm was simulated on real data. Only
`screen.py --smoke` (synthetic random-walk prices, no market data) had been run. The gate set, the
arm set, every parameter, the seeds, the stages and the selection rule below are frozen; any later
change goes in "Amendments" section 10.

## 0. Why this study exists

Idea **I5** of `docs/research/idea_survey_v4.md` (rank 5), the last untested axis on the survey's
list. Production **K** leaves the account's cash idle: the 0.98 cap buffer and the `0.98 − w`
residue of every vol-targeted weight earn **zero**. The measured precedent is pre-tax only and
under the *old* control: `tbill_sweep_v1`'s report arm **A_ALL** (idle cash at `DTB3 − 0.5 pp`, no
trading) added **+0.68 pp** CAGR against production **E2** (15.35 % vs 14.67 %), its block-20
bootstrap median was **+0.857 pp** on **100 %** of paths, and it was expected to dominate the BIL
ETF form (+0.21 pp) outright. The ETF form of the same idea was then closed **after tax**:
`r2_tbill_combo_v1` measured the R2+BIL combination losing 0.21–0.37 pp against R2 in all three
windows with a clean-bootstrap median of −1.08 pp, because a fund's resizes realise taxable gains.
A **broker cash sweep is a different instrument with a different tax base**: interest income, never
bought or sold, no tracking error, no spread, no re-sizing band. Its after-tax treatment has never
been measured here, and the direction is genuinely open: interest is taxed **annually with no
Yİ-ÜFE indexation and no deferral**, which is exactly the compounding penalty `idea_survey_v4` §3
fact 4 quantifies.

So: **does crediting `DTB3 − 0.5 pp` (and `− 1.0 pp`) on the account's uninvested cash beat
K after Turkish income tax, without failing any pre-tax non-inferiority gate?**

## 1. Question and hypothesis

- **Hypothesis.** The interest stream is taxed as *menkul sermaye iradı* at the tariff's marginal
  rate — under K that marginal rate is the top bracket (35–40 %) because the strategy's own
  realised gains already fill the lower ones — so a `DTB3 − 0.5 pp` sweep on ~15 % average idle
  cash should keep roughly half to two-thirds of its gross return. Gross is certain (`tbill_sweep_v1`
  measured it); the after-tax sign is what is open.
- **Pre-registered expectation (honest ex-ante).** Pre-tax **+0.2 … +0.7 pp**; after tax
  **+0.05 … +0.30 pp**. The survey's own ranking put this idea last of five, and the survey's
  cross-cutting caveat 2 says the tax-only ideas are "proposed as *measurements* whose likely answer
  is 'no material effect'". A zero result is an acceptable, published outcome: it closes the axis.
- **The one decision the study is really making.** If the after-tax gain is real but small, the
  owner has to compare it with a bank/TSY alternative for the same cash and with the operational
  risk of an opt-in sweep. The report therefore states the gross, the tax and the net separately.

## 2. Data and provenance

Full detail in `DATA_MANIFEST.json` (paths, bytes, SHA-256, ranges, uses, derived evidence). In
short: **nothing is downloaded**. Every input is an existing committed research file, restored
unchanged from branch `research/band_h2_combo_v1`:

- `stress_robustness_v1_daily.csv` (QQQ/SPY/QLD bars) — the main window and both bootstraps.
- `stress_robustness_v1_dtb3.csv` (FRED DTB3, 1954-01-04 → 2026-09-28) — synthetic-QLD\*
  financing **and the new yield rate**. Main-window mean DTB3 **1.915 %/yr**; 2015-09 → 2021 mean
  **0.896 %**; 2022-01 → 2026-09 mean **3.977 %**; last observation **4.10 %**.
- `after_tax_tr_v1_usdtry_cbrt.csv` (CBRT USD/TRY buying, 2000-01-03 → 2026-10-09),
  `after_tax_tr_v1_yiufe.csv` (Yİ-ÜFE, 1999-01 → 2026-09).
- `ewma_band_v1_{gspc,index,tb3ms}.csv` — the six OOS windows; `range_vol_v2_*` (19) +
  `range_vol_v1_{spy,dia,iwm}` — the 21 quality-passing ETFs.
- `research/band_h2_combo_v1/RESULTS.json` — **provenance only** (the source of `K_ANCHORS.json`;
  read, never run). `K_ANCHORS.json` holds the control's 174 published cells, generated from it by
  a fixed path mapping, and is the `--harness` anchor file.

### 2.1 Yield parameters (fixed, no tuning)

| Parameter | Value | Why |
|---|---|---|
| Spread | **0.50 pp** (Z1, Z2), **1.00 pp** (Z3) | `tbill_sweep_v1`'s pre-registered sweep spread; Alpaca's own published rate is "up to 3.56 % APY" on 2026-09-22 against a DTB3 of 4.10 % on 2026-09-28, i.e. ≈ 0.5 pp below the bill rate. Z3 is the pessimistic plateau check. |
| Rate series | `DTB3`, ffilled over holidays, pre-1954 fallback TB3MS | the series every other stage's financing already uses; no new input |
| Accrual | daily, on the **session's ending uninvested cash balance**, at `max(0, rate[i-1] − spread) / 252` | Alpaca: "interest accrues daily based on the day's ending swept balance". Negative rates are clamped to zero (a sweep pays nothing, it does not charge). `rate[i-1]` is the same one-session lag `trv2.synthetic_2x` uses for financing and the same convention the archived report arm A_ALL used. |
| Crediting | on the **last session of each calendar month**, compounding into cash | Alpaca: "realized (credited) monthly on the last business day of the month … stays invested within the sweep program to compound into yield" |
| Pending accrual | **not** NAV until credited | the program books it at month-end; it can be neither traded nor used for a tax instalment |
| Day count | 252 sessions/yr | one session's accrual is `r/252`, so 252 sessions compound to `r` per year |
| Buffer | the whole uninvested cash box | in this engine the uninvested cash *is* `0.98 − w` plus the 0.98 cap buffer plus any tax money parked for March/July; all of it is swept in the model |

### 2.2 Turkish tax on the interest: what is modelled, and the legal uncertainty

Modelled (each arm's own mode, frozen):

- **Z1** — the year's TRY interest is declared **in full** as *menkul sermaye iradı* at the
  progressive tariff (2026 brackets deflated by the previous October's Yİ-ÜFE, the model's standing
  convention), **no** Yİ-ÜFE indexation, **no** declaration bar, **no** withholding credit, added to
  the same year's declared base as the realised capital gain, paid with the same March/July
  instalments. A capital loss (already floored at zero) cannot offset it (GVK 88).
- **Z2** — as Z1, but the year's TRY interest is compared with **22,000 × scale(year) TRY**: at or
  below it, nothing is declared; above it, the **whole** amount is. (The GİB guide's own words: the
  18,000 TL / 22,000 TL figure "is not an exemption" but the bar that decides whether the income is
  declared at all, and once passed "the whole amount" is declared.)
- **Z3** — Z1 at `DTB3 − 1.00 pp`.
- **Z1x** (report-only, never gated) — the interest is never taxed: the upper bound if a *mali
  müşavir* reads the bar as sheltering the amounts modelled here.

What public sources say (read-only web; quoted in `DATA_MANIFEST.json` → external_documentation):

1. **Is it taxable at all?** Yes. A Turkish tax resident is taxed on worldwide income; GİB's *Menkul
   Sermaye İradı Vergi Rehberi 2026* (§1) lists interest of every kind as MSI, and §3.2 names
   "yurt dışından elde edilen diğer menkul sermaye iratları" and "her türlü alacak faizleri" (with
   offshore bank interest) as the foreign-source items that must be declared. Confidence: **high**.
2. **Which bar applies?** §3.2's bar — **18,000 TL for 2025, 22,000 TL for 2026**, and it is a
   *bar*, not an exemption: at or below it nothing is declared, above it the whole amount is. The
   §3.3 bar of 330,000 TL (2026: 400,000 TL) is the one for *withheld* MSI and for the listed
   pre-2006 bond/eurobond/domestic-dividend items, so it does **not** apply to a foreign broker's
   cash interest. Confidence: **high on the text, medium on the aggregation rule** — the bar is
   measured on the total of the year's non-withheld, non-exempt MSI, so a foreign dividend and the
   interest share one bar.
3. **Withholding?** No Turkish withholding: *geçici 67* reaches only Turkish intermediaries. On the
   US side, portfolio/deposit interest paid to a non-resident alien is exempt from US withholding
   (IRC 871(h)/(i)) — and Alpaca's sweep is a **bank** sweep, so the payer is a US program bank.
   Confidence: **medium-high** (the repo's own `after_tax_tr_v1` already records this; no
   counterparty-specific confirmation is public). Consequence for the model: **no withholding
   credit** is granted to the interest.
4. **Is the 22,000 TL bar a fair model of the law?** No single arm is. Z1 ignores the bar entirely
   (strict conservative reading: foreign-source MSI with no Turkish withholding pays in full from
   the first lira); Z2 applies the bar as the law reads it for a taxpayer whose only non-withheld
   MSI is this interest. The truth for a real account is between them, and **Z1x is the upper
   bound**. The spread between Z1 and Z2 is therefore the study's own legal sensitivity, exactly as
   the survey intended.
5. **Basis and timing conventions that the model fixes** (each a modelling choice, not a law):
   the TRY value of the interest is taken at the **credit** session's CBRT buying rate (the FX gain
   on the USD principal held in cash is **not** assessed — a sweep balance is not disposed of, so
   there is no realisation event); the interest is assessed in the year it is credited; no expense
   deduction is taken; and the tariff is the same 2026 ladder the capital-gains model uses,
   deflated by the previous October's Yİ-ÜFE.

**This is not tax advice.** A written opinion from a Turkish *mali müşavir* is a precondition for
acting on Z2 (and on the 22,000 TL aggregation rule in particular), exactly as `idea_survey_v4` §4
I5 says.

### 2.3 Availability (public Alpaca documentation, read-only)

- **Program.** Alpaca's **High-Yield Cash** is the **FDIC Bank Sweep**: eligible uninvested cash is
  swept daily to participating program banks and earns a variable rate. Mechanically it is exactly
  what this study models: *"Interest accrues daily based on the day's ending swept balance"* and is
  *"realized (credited) monthly on the last business day of the month … stays invested within the
  sweep program to compound into yield"* (support FAQ, August 2026; docs.alpaca.markets, updated
  2026-07-07).
- **Eligibility.** Retail: *"Individual and business trading accounts"*, USD base currency,
  pre-funded, **opt-in** (Trading API blog, updated 2026-09-17). Broker-API structure: individually
  *fully disclosed* partners only, Individual Taxable and Custodial accounts, not omnibus, not JIT.
- **Non-US residents.** Alpaca supports live individual accounts for many non-US residents ("195+
  countries", Canada excluded) and its own non-US-resident guide (updated 2026-03-11) states that
  with a funded Trading API account *"you can also participate in our High-Yield Cash offering to
  earn passive income on uninvested cash"*. The 2024 launch of the *Global* High-Yield Cash API put
  ex-US customers explicitly in scope ("a principally protected store of value in USD … against
  local inflation and currency devaluation"). So: **public documentation says yes for a live,
  individual, non-US-resident Trading API account**, subject to the same opt-in and eligibility
  conditions and to Alpaca's country-level support list.
- **Rate.** "Up to 3.56 % APY" as of 2026-09-22 (variable, set by Alpaca; the Broker API's APR-tier
  object shows an account rate plus a correspondent margin), against DTB3 4.10 % — i.e. the
  pre-registered `DTB3 − 0.5 pp` is where the published rate actually sits today.
- **PAPER.** **No public statement that a paper account accrues the sweep.** The Paper Trading API
  is a simulation; nothing in the program's documentation mentions it. The PAPER bot therefore
  cannot *realise* this study's numbers; they describe what a **live** account would earn. This is
  reported, not gated, and it is the reason the idea is an owner/account decision rather than a
  strategy change.
- **Caveat on the rate's path.** The published rate is a *current* level, not a historical series.
  The study's rate history is DTB3 minus a constant spread — the only defensible back-cast — and
  Z3 (DTB3 − 1.00 pp) is the pessimistic end of it.

## 3. Control (C0 = K = production)

K exactly: `equity_leveraged_trend_v1` 1.2.0 = E2's 2-of-3 QQQ vote + R2's early re-entry leg
(0.5 × vt while E2 is off, entry: close > SMA50 ∧ vol20 < its 252-session median ∧ close > close
20 sessions ago; exit below SMA50) + A1u (gain-scaled downsize band 0.5 / 3 / 8 pp at `g_frac`
≤ 1 % / ≤ 5 % / > 5 %, USD FIFO) + H2 (a December downsize defers to January). Mechanics
unchanged: decide on the completed close, fill at the next open, `vt = min(1, 0.35/vol20)` on
synthetic QLD\* (daily-reset 2× on QQQ total return, 0.95 %/yr expense, financing DTB3 + 0.0 %
base / +0.75 % calibrated), total cap 0.98, 0.5 pp resize band, cost 4 bps × m × |Δw|, **idle cash
at zero**. Reference labels used only inside `--harness` (never arms, never gated): `R2`
(production R2, band off, no deferral), `E2` (the pure-E2 series), `A1u`/`H2` (the two devices
alone, for the smoke code-path identities), `E2y` (the pure-E2 series **with** the yield — the
A_ALL cross-check of the new code path).

**Mandatory control reproduction.** `--harness` must reproduce K's published cells cell-by-cell
against `K_ANCHORS.json` (174 leaves, tolerance 1e-4 = 0.01 pp): pre-tax main window m = 1/2/4
base/calibrated (**15.7649 / 15.1785 %** CAGR at m = 2, max DD **46.6698 / 46.7948 %**, turnover
**5.4561**), the R-USD row, the after-tax windows at all five capitals (at $100k **10.7533 /
15.2354 / 23.9720 %**, max DD 46.6698 / 44.6272 / 34.9774 %), the m = 4 cost stress, the six OOS
windows, the 21 instruments, E1/E2/E3, the seed-20261011 clean bootstrap (pre-tax and after-tax
medians, and K vs R2's +0.2856 / +0.1311 pp), and E5's counters. It must also reproduce production
R2's own anchors (15.4766 / 14.8961 %, after tax 10.160 / 14.706 / 22.886 %), the pure-E2 series,
`R2 engine == simulate_band` (≤ 1e-9), the B1/B2 control medians vs `tax_harvest_v2`, and the new
yield code path against **A_ALL** (pre-tax main window on the E2 target series: reference 15.35 %
CAGR / 45.7 % max DD, tolerance 0.05 pp because this study credits monthly where A_ALL compounded
daily; and the A_ALL bootstrap median +0.857 pp, tolerance 0.05 pp).

## 4. Arms (4 labels; every parameter fixed, no tuning)

| Arm | Rule |
|---|---|
| **K** (control) | production, idle cash earns 0. |
| **Z1** | K + idle cash earns `max(0, DTB3 − 0.50 pp)/yr`, accrued daily on the ending balance, credited monthly; interest declared in full as MSI at the progressive tariff, no indexation, no declaration bar, same March/July instalments. |
| **Z2** | as Z1, with the `22,000 × scale(year)` TRY declaration bar applied to the year's TRY interest (at or below: nothing declared; above: the whole amount). |
| **Z3** | as Z1 at `DTB3 − 1.00 pp`. |
| **Z1x** (report-only) | as Z1 with the interest never taxed. Reported in the after-tax windows at all five capitals and in the clean bootstrap; **never** in a gate, never in the selection. |

No other difference from K. No arm changes the signal, the sizing, the band, the deferral, the
costs or the cap. `m = 2` (with the m = 1 and m = 4 checks) for every pre-tax cell, as in the
parent campaigns.

## 5. Stages

- `pre` — pre-tax main window 2000-03-09 → 2026-09-29, base/calibrated × m = 1/2/4 + the R-USD row.
  (Idle cash earns its yield gross here, so Z1 and Z2 are identical pre-tax by construction and Z3
  differs by the spread — disclosed, not a result.)
- `AT` — after tax at $100k (gate capital) and $10k / $30k / $300k / $1M on the three windows
  (2000-03-09→2026-09-29, 2010-01-01→end, 2017-01-03→end), progressive 2026 tariff, liquidated, and
  the unliquidated view at $100k. Z1x runs here too (report-only).
- `B12` — clean bootstrap, seed **20261011**, block **20**, 1,000 after-tax paths (5,000 pre-tax
  paths), fixed 2015-09-01 → 2026-09-30 calendar with the realised USD/TRY and Yİ-ÜFE, market
  returns resampled; B1-recent (progressive) and B2-flat (20 % flat, losses carried forward) at
  $100k and $1M. The interest is taxed in B2 at the flat 20 % on its own (no bar, no loss offset).
- `cost` — the m = 4 cost stress at $100k on the three windows.
- `oos` — the six `ewma_band_v1` windows (^GSPC 1957-1999, M1-M5), pre-tax, calibrated, m = 2,
  R-USD rule; and the pooled M1-M5 block-20 bootstrap (2,000 paths each) as G8.
- `E1`/`E2`/`E3` — the parent campaigns' stages (after-tax 21 ETFs under R-INDEX at $100k;
  21 January rolling starts; fresh bootstraps seed 20261015 blocks 10/40). **Reported, not
  decisive.**
- `E5` — report only: decision traces, the extra capitals, the unliquidated view.

## 6. Gates (pre-registered; `ewma_band_v1`'s G1-G9 vs K, plus the after-tax pair)

Every gate compares the candidate against **K**, as the common protocol prescribes. G1/G2/G3/G5/G6
are checked under **both** financings; the rest use the base.

| Gate | Rule |
|---|---|
| **G1** | m = 2, base and calibrated: CAGR ≥ K, **or** Calmar ≥ 1.1 × K **and** CAGR ≥ K − 0.5 pp |
| **G2** | max DD ≤ K + 1.0 pp (base and calibrated) |
| **G3** | 2017+ CAGR ≥ K − 1.0 pp (base and calibrated) |
| **G4** | CAGR above K in ≥ 2 of the 3 sub-periods P1 2000-08 / P2 2009-16 / P3 2017-end (base) |
| **G5** | block-20 bootstrap (seed 20261011, 5,000 pre-tax paths): median (arm − K) ≥ 0 in both financings |
| **G6** | G1 re-checked at m = 1, base and calibrated |
| **G7** | six OOS windows: median Δ CAGR ≥ 0; in ≥ 5 of 6, Δ CAGR ≥ −0.5 pp **and** Δ DD ≤ +3 pp; Δ DD never > +8 pp |
| **G8** | pooled OOS bootstrap over M1-M5 (2,000 paths each): median Δ ≥ 0 |
| **G9** | turnover ≤ 2 × K |
| **AT** | after tax at $100k: arm − K ≥ 0 in **all three** windows |
| **B1B2** | clean bootstrap at $100k, B1-recent (progressive) **and** B2-flat: median (arm − K) ≥ 0 **and** share > 0 ≥ **55 %** |

**Selection rule (frozen).** Among Z1/Z2/Z3, adopt the arm with the highest **pre-tax base m = 2
CAGR** among those clearing **every** gate above (ties broken by the after-tax 2000-window CAGR).
If none clears, **K stands unchanged** and the study reports the failure — a "no material effect"
answer closes the axis and is a pre-registered acceptable outcome. `docs/research/idea_survey_v4.md`
§4 I5 pre-registers no selection rule of its own, so this one decides; the survey's arm list and
its three tax treatments are used verbatim.

**Interpretation note, fixed in advance.** Because Z1/Z2/Z3 leave the trade path (almost) untouched
— interest changes the cash balance and therefore, occasionally, a band decision — the *decision*
gates (G1–G9) are expected to pass on a small positive pre-tax delta; the binding gates are
therefore **AT** and **B1B2**, exactly as the survey predicted ("the direction is genuinely
ambiguous"). G9's ratio will sit at ≈ 1.00 (the yield adds no trades of its own).

**Reported but not decisive:** E1 (21 ETFs; its parent threshold median Δ > 0 and share > 0 ≥ 60 %
is printed), E2 (21 rolling starts), E3 (four fresh bootstraps). These sets exist to show whether a
pass, if any, is broad-based; they never enter the selection.

## 7. Differing conventions (disclosed)

1. **Pre-tax Z1 ≡ Z2.** The declaration bar is a tax rule, so the two arms are the same arm in the
   pre-tax stages; only Z3 differs there.
2. **Monthly crediting.** `tbill_sweep_v1`'s archived A_ALL compounded daily; this study credits
   monthly (Alpaca's documented practice). The difference is < 0.02 pp of CAGR, which is why the
   A_ALL cross-check carries a 0.05 pp tolerance instead of 0.01 pp. The K control's own anchors
   keep the 0.01 pp tolerance because K has no yield at all.
3. **The bootstrap's yield rate is resampled with the market.** In the clean bootstrap the market
   returns come from a resampled sequence, and the harness uses the *same* resampled DTB3 for the
   cash rate that it uses for the synthetic QLD\* financing (`rate=yy`). Tying the yield instead to
   the fixed 2015-2026 calendar would zero it out for the 2016-2021 ZIRP half of every path and
   would not be comparable across arms. Recorded here because it is a choice.
4. **B2's interest tax is flat 20 %, no bar, no loss offset.** B2 is a synthetic tax system; giving
   the interest its own 20 % keeps the capital-gain book's loss carry-forward untouched.
5. **The USD principal's FX is not assessed.** A sweep balance is never disposed of, so no
   *değer artışı kazancı* arises on the currency; only the interest is taxed.
6. **The pending accrual is outside NAV until the month-end credit.** It therefore cannot be traded
   or used to pay an instalment (the program holds it), which is the conservative reading.

## 8. Run protocol and pre-registered expectations

- `--smoke` on synthetic random-walk prices only (no market data): the parent's probes and
  identities (rule gain vs the engine's own sell gain in TRY and USD; `R2` engine ==
  `simulate_band`; H2 == `simulate_band_overlay` tail-10; K with an empty December window == A1u;
  rolling-vol median; finiteness; after-tax ≤ pre-tax; the band and the deferral fire), **plus the
  new ones**: the yield machinery is inert at a zero rate (bit-identical NAV to K), Z1x ≥ Z1 ≥ K
  and Z1 ≥ Z3 on the same synthetic path, progressive ≤ pre-tax for every Z arm, the interest tax
  is positive exactly when the interest is declared, the Z2 bar is a cliff (tiny interest ⇒
  Z2 == Z1x; large interest ⇒ Z2 == Z1), accrual-session count exact, and the pure-cash closed form
  `∏_months (1 + r/252 · n_sessions)` reproduced to 1e-9 while *daily* compounding is rejected.
- `--harness`: everything in section 3. `--run` refuses to proceed unless the harness passes.
- `--run` twice; the two `RESULTS.json` files must be byte-identical.
- Determinism: one engine, one seed set, no randomness outside the two bootstrap draws
  (seed 20261011 blocks 20 for B12; seed 20261015 blocks 10/40 for E3), no network, no clock.
- Runtime: expected ~15–25 min per `--run` (the clean bootstrap's 5,000 pre-tax × 2 financings ×
  4 arms plus the after-tax legs dominate).

## 9. Caveats and limits

1. **The rate is the biggest modelling assumption**, and it is not a fact about the past. DTB3
   minus a constant spread is the only defensible back-cast of a variable program rate, and the
   published "up to 3.56 % APY" exists for exactly one date. Z3 (DTB3 − 1.00 pp) is the pessimistic
   end; the *sign* of the after-tax answer is the finding, the magnitude is indicative.
2. **The declaration bar (Z2) is a rule the model applies to a category whose aggregation is not
   fully public** (foreign interest + foreign dividends share one bar). Z1 and Z2 bracket it, and
   Z1x is the upper bound.
3. **No withholding credit** is modelled on the interest (medium-high confidence that none exists,
   both sides); if Alpaca withholds on the sweep, every Z arm's after-tax number moves down and the
   ranking barely moves (all Z arms carry the same interest).
4. **The after-tax evidence has no true out-of-sample**: the TRY/Yİ-ÜFE calendar starts in 2000, so
   the OOS windows are pre-tax only; the counterweights are B1-recent, B2-flat and the E2/E3
   variations.
5. **The tax model is not tax advice** (section 2.2): FIFO, per-calendar-year netting without loss
   carry-forward, the 2026 tariff deflated by the previous October's Yİ-ÜFE, 10 % indexation floor,
   instalments funded by selling, liquidation at the window end, one CBRT buying-rate series.
6. **PAPER.** Nothing here changes the deployed bot, and a paper account may not accrue sweep
   interest at all (section 2.3). The deliverable is a measurement plus the availability finding.
7. **Cash-vs-sweep interaction.** The engine's idle cash already includes the 0.98 cap buffer and
   the money parked for March/July instalments; a real broker sweeps the settled balance only, and
   unsettled trades can differ. Not modelled; it is second-order because the buffer is large
   relative to settlement timing.

## 10. Amendments

**A1. `K_ANCHORS.json` regenerated with explicit leaf paths (before the first complete `--run`).**
The first version of the anchor file was written by a recursive flattener that collapsed the
prefixed sub-dicts (`after_tax_windows.*.K`, `e1.*.K`, …) to their section key, so `--harness`
compared only 24 of its 174 entries and reported `K control anchor block: 0/174 cells compared …
-> FAIL` (the 24 that did resolve matched exactly; the R2/E2 anchors, the `R2 == simulate_band`
identity and the A_ALL cross-check all passed in that same run). The file is now generated with
explicit dotted paths — **790 leaves** — and was verified against a real run of
`pre_tax_main`/`after_tax_windows`/`cost_stress`: **279 cells compared, 0 mismatches, max|diff|
5.0e-07** (the parent's `RESULTS.json` stores `round(x, 6)`, so 5e-7 is its own rounding). No arm,
parameter, stage, gate, seed, threshold or selection rule changed, and no `RESULTS.json` existed
before or after that failing run.

**A2. Pre-run state.** Before the pre-registration commit `ac9011a3`: `--smoke` on synthetic
random-walk prices only, plus one timing/anchoring probe that ran the *control* K alone on the main
window (it reproduced K's published 15.7649 % / 10.7533 % / 46.6698 %, which is the harness
requirement, not a result of this study). After that commit: `--harness` (one run, failed on A1
alone). No non-control arm had been simulated on real data before the commit.

**A3–A5. Three implementation fixes and one probe mode, all made before the first complete
`--run` and none touching an arm, parameter, stage, seed, threshold, gate or the selection rule.**
Each was a crash with a non-zero exit and **no** `RESULTS.json` written, and each was found by a
**code-path probe**: `CASH_YIELD_PROBE=1 screen.py --run` shrinks the bootstrap path counts
(60 pre-tax / 12 after-tax / 12 per E3 run / 20 per OOS market) and redirects `RESULTS.json` and
`SUMMARY.txt` to `/tmp`, so the whole pipeline — stages, `reference_checks`, `gates_cash` and the
summary writer — runs end to end in ~40 s without touching the study's outputs (the parent
campaign's A2 probe, same idea). The probe's own numbers are meaningless by construction (tiny
path counts) and were discarded.

1. `stage_e3` ran its arms as `ARMS + ("R2",)` while its `deltas` list carries the report arm
   `Z1x`, which was not among them (`KeyError: 'Z1x'`). Its arm list is now
   `ARMS_ALL + ("R2",)`, so the E3 cell set matches what the summary prints.
2. `gates_cash` built a headline `row` only for the candidates, but the summary's per-arm table
   prints the control too (`KeyError: 'K'`). The row builder is now `_arm_row(arm, …)`, called for
   the control as well (the control carries no gates — it is the reference).
3. The summary asked the after-tax window record for `years`, which it does not carry
   (`KeyError: 'years'`); it now uses the main window's year count.
4. The synthetic smoke is evaluated once per `--run` and stored in `RESULTS.json["smoke"]`
   (it was called twice, and the summary read a key `run()` does not set).

**A6. Disclosed deviation from the survey's spread wording.** `idea_survey_v4` writes Z1's rate as
"`DTB3 − 0.50 pp`/yr, accrued daily"; this study implements accrued **daily, credited monthly**,
because Alpaca's own documentation of the sweep it models says exactly that ("Interest accrues
daily based on the day's ending swept balance … realized (credited) monthly on the last business
day of the month"). The pre-tax difference from daily compounding is < 0.02 pp, and the A_ALL
cross-check confirms it: the pure-E2 series with this study's yield reproduces the archived arm's
published 15.35 % at **15.343 %** (diff −0.007 pp).
