# Untested research ideas for K after Turkish tax (idea survey v5)

> Evidence: tag `archive/research/idea-survey-v5-2026-10` (not on `main`).

> Evidence: this document is the whole artefact. **Survey only — no screen, no arm, no gate, no
> backtest, no file outside `docs/research/` was written, and no dataset was downloaded.** Every
> number below is quoted from `docs/research/*.md` or read out of an *existing* stored result file
> (`research/band_h2_combo_v1/RESULTS.json` and `research/tax_harvest_v2/RESULTS.json` from tag
> `archive/research/band-h2-combo-v1-2026-10` / `archive/research/tax-harvest-v2-2026-10`); which
> numbers are which is stated inline. Two Turkish tax sources were read online (§4.1) — text only,
> nothing saved.

Date: 2026-10-16. Branch `research/idea_survey_v5`. Nothing here is tax advice, and every arm
specification below is a proposal for a pre-registration, not a result.

## 1. Verdict

Survey v5 proposes five untested ideas for K **after Turkish tax**, ranked by expected after-tax
value per unit of research risk. The ranking is deliberately inverted relative to the last three
surveys: since v4, *every* rule-level axis on the previous lists has been measured
(`exposure_k_v1`, `early_quorum_v1`, `exit_timing_v2`, `cash_yield_v1`, `ladder_v1`, `deriv_vote_v1`,
`breadth_vote_v1`, `smooth_target_v1`), and all but one of them failed. What is left is (a) one
**premise** that the whole programme rests on and that has never been checked, (b) one **sizing
shape** that has never been varied, (c) one **deferral trigger** that a closed study explicitly
named as its natural next test, (d) one **tax-year budget** no arm has ever used, and (e) one
**signal class** the repo has never touched.

| # | Idea | Axis changed | Expected after-tax Δ vs K ($100k) | Confidence it is untested |
|---|---|---|---|---|
| **I1** | **Tax-unit and tax-regime layer**: GVK mük. 20/D eligibility, the tax-awareness bill under τ = 0 / flat-20 % / progressive, and income splitting across two taxpayers' accounts | the *taxpayer*, not the signal | **+0 … +5 pp** (0 if the current premise holds; the whole 5.0 pp drag if a household member qualifies for 20/D); splitting alone ≈ **+0.2 … +0.5 pp** | high (no study has ever varied the tax unit or the tax rate at which K's own components are chosen) |
| **I2** | **Exposure-shape replication**: replace `min(1, 0.35/σ)` with `min(1, c/σ^p)`, p ∈ {0.75, 1.25, 1.5, 2}, each calibrated to K's own **time-average** weight on a pre-2000 window | the *shape* of the sizing rule at fixed average exposure | −0.3 … +0.5 pp (pre-tax −0.4 … +0.6) | high (scout-verified: `p ≠ 1` and target-variance scaling appear nowhere; no arm in 78 reports ever held the average weight fixed) |
| **I3** | **State-keyed realization deferral** with a hard drift cap: defer a downsize when the *tax state* says so, not when the calendar does | the deferral trigger (state replaces the December window) | +0.1 … +0.5 pp | high (`monthly_defer_v1`'s "defer only when the indexed gain is positive" arm was **dropped**, never run; the state-keyed trigger has no study) |
| **I4** | **Annual tax budget**: measure the loss pool, then key realization on the year's bracket scale, the loss pool and (if confirmed) the annual exemption band | when *within/between tax years* a realization lands | +0.02 … +0.3 pp (measurement-first) | high (`idea_survey_v3` I5 / v4 I3: "no arm in the repo has ever realised a gain on purpose"; the pool has never been measured) |
| **I5** | **Valuation / ERP-conditioned exposure**: scale the on-state weight by the earnings yield or CAPE, anchors frozen on the pre-2000 sample | the *signal class* (valuation, not trend) | −0.5 … +0.4 pp | high (scout-verified: no study mentions CAPE, Shiller, earnings yield, P/E or ERP anywhere) |

**The one idea that dominates.** §4.1 is first not because it is clever but because GVK mük.
20/D — a **20-year income-tax exemption on foreign-source income and capital gains for
individuals who become Turkish residents from 1/1/2026** after three non-resident years
(Resmî Gazete *Tebliğ 333*, 4/7/2026) — would delete the ~5.0 pp/yr tax drag that I2–I5 are each
fighting for a fifth of a point over. It is one adviser question and, if the answer is yes, it
voids the other four ideas and inverts K's execution layer (K's band and H2 pay pre-tax return for
tax timing that would no longer be owed). It is listed as an *arm set*, not as a footnote, because
the study behind it — "which parts of K are tax-regime-dependent, and where are the switch
points?" — is genuinely new and is useful even when the answer is no.

## 2. Method and the control

Read in full for this survey: `INDEX.md`; `idea_survey_v2/v3/v4`; and the 2026-10-14/15/16 wave
(`k_stage_b_v1`, `band_h2_combo_v1`, `gain_band_v1/v2/v3`, `gain_band_integration_v1`,
`tax_harvest_v1/v2`, `monthly_defer_v1`, `exit_timing_v2`, `smooth_target_v1`, `cash_yield_v1`,
`exposure_k_v1`, `early_quorum_v1`, `ladder_v1`, `deriv_vote_v1`, `breadth_vote_v1`,
`intraday_stop_v1`, `vt_level_tax_v1`, `tax_asym_v1/v2/v3`, `tax_frontier_v1`, `tax_hedge_v1`,
`r2_tbill_combo_v1`, `tbill_sweep_v1`, `riskoff_after_tax_v1`, `riskoff_rotation_v1–v4`,
`leverage_frontier_v1`, `gold_riskoff_oos_v1`, `shortside_v1`, `instrument_cost_v1`,
`relstrength_v1`, `early_qqq_v1`, `seasonal_overlay_v1`, `seasonal_placebo_v1`, `exec_delay_v1`,
`moc_execution_v1`, `overnight_hold_v1`, `signal_ensemble_v1`, `trend_robustness_v2`,
`ewma_band_v1`, `vol_window_v1`, `range_vol_v1–v3`, `vix_filter_v1`, `credit_vote_v1`,
`tsm_tr_v1`, `crash_entry_v1`, `dip_overlay_v1`, `rule_variants_v1`, `after_tax_tr_v1`,
`fast_reentry_v1/v2`, `e2_confirmation_v1`, `vote_hysteresis_v1`). Four read-only sweeps covered
the rest, including the pre-prune register (`archive/research/pre-prune-2026-09-30`) for the
sleeve era; the "untested" claims below are backed by those sweeps' verbatim findings and by
targeted full-text searches (`CAPE|Shiller|earnings yield|P/E|ERP|real rate` →
`docs/research/WORKER_PROTOCOL.md:33` is the only hit, in the *allowed-input list*; `exponent|vol**2|
sigma^2` → no hit in any arm list).

**Control = production K** = `equity_leveraged_trend_v1` 1.2.0 = E2 (2-of-3 QQQ vote) + R2 early
re-entry + A1u (gain-scaled downsize band 0.5 / 3 / 8 pp at a USD FIFO gain fraction ≤ 1 % / ≤ 5 % /
> 5 %) + H2 (a downsize whose *fill* session is one of the last 10 December sessions defers to
January). Reproduce before anything else — from `band_h2_combo_v1/RESULTS.json`, base financing,
m = 2, main window 2000-03-09 → 2026-09-29:

| Arm | pre-tax CAGR base / cal. | max DD base / cal. | turnover/yr | sells/yr | after tax $100k 2000 / 2010 / 2017 | tax paid ($100k, 2000 win.) | Yİ-ÜFE relief (TRY) |
|---|---:|---:|---:|---:|---:|---:|---:|
| C0 = R2 | 15.4766 / 14.8961 % | 46.7584 / 46.8797 % | 5.9915 | 29.56 | 10.160 / 14.706 / 22.886 % | 766,160 | 18.58 m |
| A1u | 15.7213 / 15.1192 % | 47.1877 / 47.3096 % | 5.4863 | 10.43 | 10.615 / 15.102 / 23.701 % | 819,008 | 21.84 m |
| H2 | 15.5171 / 14.9359 % | 46.2542 / 46.3786 % | 5.9482 | 28.65 | 10.303 / 14.840 / 23.161 % | 775,620 | 19.37 m |
| **K** | **15.7649 / 15.1785 %** | **46.6698 / 46.7948 %** | **5.4561** | **10.17** | **10.753 / 15.235 / 23.972 %** | **830,903** | **22.61 m** |

(pre-tax columns and turnover/sells are `pre_tax_main` at m = 2, base financing, from
`band_h2_combo_v1/RESULTS.json`; the after-tax columns are the same file's `after_tax_windows`
at `100k`; the two runs differ only in warm-up, e.g. C0's main-window CAGR is 15.4766 % here and
15.4732 % in `fast_reentry_v1`.)

K's clean bootstraps vs R2: B1-recent **+0.286 pp (75.0 % of paths)**, B2-flat **+0.131 pp
(64.0 %)**; the controls' own medians are B1 3.8577 % / B2 4.0376 % (`exit_timing_v2`). Common
mechanics for every arm below, so a pre-registration only has to state the delta: daily decision on
the completed close, fill at the next open, `vt = min(1, 0.35/σ20)` on the harness's synthetic 2×
QLD\*, cap 0.98, 0.5 pp resize band, 4 bps × m cost, idle cash at 0 %, A1u + H2 exactly as shipped,
the `fast_reentry_v2` 21-instrument set and the six `ewma_band_v1` OOS windows where the idea is
measurable there, and the corrected after-tax engine at $100k/$10k/$1M with the six windows, plus
B1-recent and B2-flat (seed 20261011, block 20). Nothing is tuned after seeing a result.

## 3. Where K's tax base is, and the four bounds every idea is measured against

From `band_h2_combo_v1` / `k_stage_b_v1` (per-reason fields not tabulated in any report) and §3 of
`idea_survey_v4`: under K the realised gain by reason is **88 % resize trims, 11 % terminal
liquidation, 1 % tax-funding sales**; the band does not shrink the base, it *moves it later and
makes it older* (relief 22.61 m vs R2's 18.58 m TRY; K's terminal-liquidation gain grew 4×, 0.86 →
3.41 m TRY, because the units that survive the band are the old, gain-rich ones). K's tax bill is
*larger* in absolute dollars than R2's; what improved is the ratio: a drag of **5.01 pp vs R2's
5.31 pp**. Indexation relief removes ≈46 % of gross gain and is nearly saturated.

**Bound A — what an account setting is worth (measured, adopted).** `cash_yield_v1`: idle cash at
`DTB3 − 0.50 pp` taxes *interest*, realises nothing, and adds **+0.478 / +0.234 / +0.429 pp** after
tax at $100k with max DD *down* 4.13 pp, B1/B2 +0.63 pp on 100 % of paths. No rule change, no signal
risk. This is the size of the largest *certain* remaining win, and it is already adopted.

**Bound B — what a passing tax-timing arm is worth (measured, not adopted).** `tax_harvest_v2`'s
H2_15 / H2_20 (the same rule with a 15- and 20-session December window) pass every pre-registered
gate in that campaign and are worth **+0.295 / +0.295 / +0.517 pp** and **+0.332 / +0.437 / +0.769 pp**
after tax at $100k; the campaign's FAIL came only from the *5-session* plateau neighbour
(−0.007 pp in one window), and H2_20's worst instrument Δ max DD is +1.87 pp against a +2 pp bar.
`monthly_defer_v1` (the same mechanism at all twelve Yİ-ÜFE month boundaries) is worth
+0.26 … +0.51 pp after tax but fails the pre-tax bootstrap because it holds an above-target weight
for up to a month — the closest arm misses by 0.05 pp.

**Bound C — the ceiling of the whole realization-timing family (arithmetic, unreachable).** K's
2000-window tax bill at $100k is 830,903 USD, of which ~11 % (≈91 k) is the terminal liquidation.
If *every* realization could be pushed to the window's liquidation, the terminal wealth would rise
from ≈1.507 m to ≈2.25 m USD — i.e. an after-tax CAGR ceiling of ≈12.3 % vs K's 10.75 %,
**≈ +1.6 pp/yr** [INFERENCE, from `cash_yield_v1`'s K terminal 1,506,985 USD and the 11 %
liquidation share]. That is the entire prize of the deferral family; the risk-required resizes and
exits make most of it unreachable, which is why every *measured* arm is +0.1 … +0.8 pp and not more.

**Bound D — what the tax unit is worth (measured).** R2's after-tax CAGR at $10k / $100k / $1M is
**11.69 / 10.16 / 9.19 %** (2000 window), 17.03 / 14.71 / 13.62 (2010) and 26.04 / 22.89 / 21.72
(2017) — the progressive tariff plus the deflated brackets make the *same rule* worth 1.5 pp more
per year at a tenth of the capital. A household split of $100k into two $50k accounts is therefore
worth roughly a third to a half of that gap [INFERENCE, to be *measured* by I1's arms, not
assumed].

**Closed axes (do not re-propose).** Exposure/vol-target *level* (`exposure_k_v1`: 0.375/0.40/0.45
all break the DD budget by +2.4/+4.7/+9.1 pp and lose the bootstraps; `vt_level_tax_v1`;
`leverage_frontier_v1`); downward-only resizes (`tax_asym_v1–v3`); vote form and breadth
(`deriv_vote_v1`, `breadth_vote_v1`, `vote_hysteresis_v1`, `signal_ensemble_v1`, `e2_confirmation_v1`);
vol *estimator* (`vol_window_v1`, `ewma_band_v1`, `range_vol_v1–3`); early-leg size/trigger
(`ladder_v1`, `early_quorum_v1`, `fast_reentry_v2`); exit-leg tax timing (`exit_timing_v2`);
smoothing (`smooth_target_v1`); stops/intraday/dip/crash entry (`intraday_stop_v1`, `dip_overlay_v1`,
`crash_entry_v1`); off-state assets (`tbill_sweep_v1`, `r2_tbill_combo_v1`, `leverage_frontier_v1`,
`riskoff_rotation_v1–v4`, `riskoff_after_tax_v1`, `gold_riskoff_oos_v1`, `shortside_v1`);
instrument choice (`instrument_cost_v1`, `early_qqq_v1`, `relstrength_v1`); execution timing
(`moc_execution_v1`, `exec_delay_v1`, `overnight_hold_v1`); seasonality (`seasonal_overlay_v1`,
`seasonal_placebo_v1`); macro/credit/VIX filters (`credit_vote_v1`, `vix_filter_v1`).

---

## 4. Ranked ideas

### I1 — The tax-unit and tax-regime layer *(rank 1)*

**Hypothesis and mechanism.** §3's bound D is a *measurement of the tariff*, not of the strategy:
the same rule earns 11.69 % after tax at $10k and 9.19 % at $1M. Three consequences follow, none of
which any study has ever tested. (a) **The 20/D exemption.** GVK mük. 20/D (added by Law 7582 of
21/5/2026; implementation in *Gelir Vergisi Genel Tebliği Seri No: 333*, Resmî Gazete 4/7/2026,
no. 33300) exempts, for **20 years**, the *foreign-source* income and capital gains of individuals
who become Turkish residents on/after 1/1/2026 and had no Turkish residence or tax liability in the
prior three calendar years; the tebliğ lists "yurt dışındaki menkul kıymetlerin elden
çıkarılmasından doğan değer artış kazançları" as covered, exempt income is *not even declared*, and
an eligibility certificate must be obtained. If any household member qualifies — and the
eligibility test is about the *three years before* Turkish residence, so a returning spouse or
child can qualify while the owner does not — then essentially the whole of K's tax bill (a 5.0 pp/yr drag, §3) moves to a *different taxpayer*
for two decades, and the artefact the bot should trade is the *pre-tax* one. (b) **The tax-awareness bill.** K's A1u and
H2 buy tax deferral with pre-tax return (`idea_survey_v4` §3: K's edge over R2 is +0.29 pp pre-tax
*exposure* and +0.31 pp lower drag; the band holds a deliberately stale weight in
`monthly_defer_v1`'s language). That purchase is worth making only if the tax is owed at 35–40 %
with indexation. Nobody has measured the *price* at which the purchase stops paying. (c) **Income
splitting.** Two taxpayers = two progressive ladders (190 k / 400 k / 1.0 m / 5.3 m TRY at
15/20/27/35/40 % for 2026 — verified against a public 2026 table, and identical to the engine's own
`TARIFF_2026`), two loss pools, two December boundaries, two indexation calendars and two separate
FIFO books. Splitting a fixed capital across taxpayers is a *pure* tax-unit question with no signal
content, and it also creates the one legitimate version of the "two FIFO queues" idea that
`idea_survey_v2` I1 and v3 §7.3 had to reject (Alpaca has no lot-level sells — but a *second
account in another person's name* is two separate returns, not lot election).

**Literature and evidence strength.** Primary and unambiguous for (a): Resmî Gazete Tebliğ 333 and
the article text it reproduces; commentary confirms the scope question is open for foreign-bourse
capital gains ("*yurt dışı borsalarda işlem gören menkul kıymetlerin satışından elde edilen
kazançların da, istisna kapsamına girmesi mümkündür*" — musavirlerkulubu.com.tr, 16/10/2026) and
that a pre-exemption-period *değer artışı* liability does not disqualify. **Strong on the statute,
medium on the application to a US-brokerage ETF account.** For (c): the progressive-tariff
arithmetic is statutory, and the repo's own capital table already prices it; the academic frame is
the classic **tax-unit** literature (married-couple/income-splitting neutrality — e.g. Kleven,
Kreiner & Svarer, *Taxation of Couples*, and the marriage-penalty literature). For (b): AQR, *The
Enduring Appeal of Gain Deferral* Parts 1–3, and Krasner & Sosner, *Loss Harvesting or Gain
Deferral?*, JWM 27(1) 2024 — deferral is worth more the higher the rate and the longer the horizon.

**Why it is not a repeat.** No study in this repo has ever varied the *tax unit* or the *tax rate*
under which K's components are chosen: every arm in 78 reports is priced for one taxpayer under the
frozen 2026 progressive tariff. `cash_yield_v1` set the precedent for an "account setting" output
(it was adopted, not shipped as a rule) and `idea_survey_v2` §7.6 / v3 §6.5 mention 20/D only as an
owner-level caveat with no arm; the *regime-sensitivity* and *income-splitting* questions have never
been put to the engine. `idea_survey_v2` §7.4 rejected a *never-traded equity core*; that is a
different idea (an asset, not a taxpayer).

**Expected effect.** (a) 0 if the current premise holds; **+3 … +5 pp/yr after tax** for an eligible
taxpayer (the whole drag), since the pre-tax optimum then becomes the after-tax optimum.
(b) a *reported table*, not a delta: the tax rate at which A1u and H2 stop paying. (c) ≈ **+0.2 …
+0.5 pp/yr** from a two-way split at $100k, bounded above by bound D and to be measured, not
interpolated. It survives Turkish tax *by construction* — it is the tax.

**Data.** None new: the engine already takes `capital` as a scalar (`run_strategy(..., capital, ...)`)
and already has a no-tax `Regime` and the B2 `FlatAccount` (20 %, losses carried forward), so
τ = 0 / flat-20 % / progressive needs no new code. The 20/D check needs no data at all.

**Pre-registrable arms (4).** All parameters fixed; (a) is a *precondition*, not an arm.

| Arm | Rule |
|---|---|
| P | **Precondition (no simulation).** Written mali müşavir opinion on three questions: (i) 20/D eligibility of every household member, with the certificate's deadline (end of the year of Turkish residence); (ii) whether foreign-bourse *dealer* ETF capital gains fall inside 20/D's "değer artış kazancı"; (iii) whether income splitting across genuine separate owners is accepted (substance, gift reporting, no muvazaa). |
| R1 | **Regime sensitivity (report-only).** Re-run the four existing components (R2, A1u, H2, K) under τ = 0, flat-20 % (B2's account, applied to the arm's *own* path rather than to bootstrap resamples) and progressive-2026, at $10k/$100k/$1M on the six windows; report the *switch table* (the rate at which A1u's and H2's contribution changes sign). No new parameter. |
| R2 | **Two-way split.** Every account runs K at $100k/2; household terminal wealth = 2 × the arm's terminal, so the household CAGR *is* the per-account CAGR. Report vs K at $100k and vs K at $50k in one account. |
| R3 | **Three-way split** at $100k/3 (the same construction). |
| R4 | **Routed split.** Two accounts, total target = K's; each *downsize* is executed in the account whose consumed FIFO head has the lower estimated tax cost (engine's indexed-TRY arithmetic), allowed to leave the other account above its target by at most `ROUTE_CAP = 10 pp`; up-resizes fill in the account furthest below target; exits and tax-funding sales are not routed. Requires a two-book kernel option in a copy of the engine under `research/<NAME>/` plus an off/on parity cell. |

Fixed reporting: the regime table, the household/base case at all three capitals, turnover and
sells-per-owner, and an explicit statement that R2–R4 change *no* market decision when the two
accounts are identical (a bit-for-bit parity check: two unrouted identical accounts must reproduce
K's NAV exactly).

**Risks and failure modes.** (i) **The legal premise is the study** — if the adviser answers "not
eligible, and splitting is a muvazaa risk", I1 collapses to the report-only R1 table and should be
closed. (ii) The owner may have no second taxpayer; R1 then carries the whole idea. (iii) 20/D is
an *exemption*, not a refund: US withholding on QLD's dividends becomes a final cost with no
Turkish credit (`after_tax_tr_v1`: 20 % US withholding, GVK 123 credit only up to the Turkish tax)
— a tenth of a point, in the *opposite* direction. (iv) R4 is an anti-abuse-adjacent structure and
should be pre-registered as "run only after (iii) of the precondition is answered in writing".
(v) Data-mining is a non-issue here (no parameter is searched); the risk is legal, not statistical.

---

### I2 — Exposure-shape replication at matched average weight *(rank 2)*

**Hypothesis and mechanism.** K's exposure is `min(1, 0.35/σ20)` — the vol-target *level* was swept
to death (`exposure_k_v1`: every cell above 0.35 breaks the DD budget by +2.4 … +9.1 pp;
`vt_level_tax_v1`; `leverage_frontier_v1`), and the *estimator* was swept too
(`vol_window_v1`, `ewma_band_v1`, `range_vol_v1–3`). The **exponent** never was. `min(1, c/σ^p)`
with p > 1 (target-*variance* scaling) concentrates exposure in calm regimes and cuts it hard in
turbulent ones; p < 1 flattens the schedule and therefore trades *less*. Both are *shape* changes,
and the untested design is to hold the **time-average weight fixed**: every arm's `c_p` is
calibrated so that its mean target weight equals K's on a **pre-2000** window, which removes the
"adds average exposure" confound that closed the level axis and makes the DD gate a *test of the
shape* rather than a foregone conclusion. The window between v3's design intent and its execution
exists in the repo's own record: `idea_survey_v3.md:539-541` pre-announced a two-tier gate for
"exposure-neutral arms" (median Δ ≥ −0.05 pp pre-tax, turnover ≤ 2×), and no study ever instantiated
it — `smooth_target_v1` came closest and its own S3 "pays for it with exposure: mean target weight
67.31 % → 65.04 %".

**Literature and evidence strength.** Moreira & Muir, *Volatility-Managed Portfolios*, JF 72(4) 2017
([DOI](https://doi.org/10.1111/jofi.12513)) — the canonical result is the *variance*-scaled form,
which is exactly the p = 2 arm; Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & van Hemert, *The
Impact of Volatility Targeting*, JPM 2018
([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)) — the sizing signal, not the
scaling function, carries most of it. Counter-evidence, which must be quoted at the top of the
study: Cederburg, O'Doherty, Wang & Yan, *On the performance of volatility-managed portfolios*,
JFE 138(1) 2020
([DOI](https://doi.org/10.1016/j.jfineco.2020.04.015)) — across 103 strategies the vol-managed
portfolios mostly fail out of sample against buy-and-hold; the market factor is the partial
exception. **Evidence strength: strong for the mechanism in-sample, contested out of sample.**

**Why it is not a repeat.** Scout-verified: no study in the repo uses `min(1, c/vol^p)` with p ≠ 1
or target-variance scaling, and *no* arm in the record holds the time-average weight fixed by
construction (`smooth_target_v1`'s C0/S1/S2/S3 means differ; `monthly_defer_v1`, `gain_band_v1–v3`,
`ewma_band_v1` bands and `ladder_v1` all move average exposure and say so). The closest study is
`exposure_k_v1`, and the difference is precisely the confound it died of: it changed the *level* at
fixed shape, this changes the *shape* at fixed level.

**Expected effect.** Pre-tax **−0.4 … +0.6 pp** CAGR with max DD inside the G2 budget
(K + 1.0 pp) — by construction the shape change carries no average-exposure increase, though the
arm's realized mean may drift, which is why the drift is reported and bounded — and turnover within
G9. After tax the channel is asymmetric and must be stated in the pre-registration:
p > 1 cuts exposure exactly when the vol target would have trimmed it, so it should *reduce* the
number of taxable resizes (a tailwind) while p < 1 increases them (a headwind) — but A1u blocks the
gain-heavy trims on both sides, so the honest prior is that the tax effect is second-order and the
study is decided pre-tax.

**Data.** None new for the arms. The **calibration** needs a pre-2000 daily series for the same
market: `^NDX` (Yahoo chart API, from 1985-10-01), converted to the harness's synthetic 2× basis;
the repo already builds synthetic LETF legs this way (`relstrength_v1`, `range_vol_v2`).

**Pre-registrable arms (4).** `p = 1` is K. For each arm, `c_p` is solved **once, on
1985-10-01 → 2000-03-08**, so that the mean of `min(1, c_p/σ20^p)` equals the mean of K's own
`min(1, 0.35/σ20)` on the same window; the calibration window is excluded from every reported
statistic. No other parameter changes.

| Arm | Rule |
|---|---|
| E1 | `p = 0.75` (flatter; fewer resizes, more exposure in turbulent regimes) |
| E2 | `p = 1.25` |
| E3 | `p = 1.5` |
| E4 | `p = 2.0` (target-variance scaling; the Moreira–Muir form) |

Fixed reporting and gates: G1–G9 exactly as `ewma_band_v1` defines them, **plus** the realized
2000–2026 mean weight and the mean |Δw|; the two-tier rule is applied as pre-declared in
`idea_survey_v3` §6.1 — if the realized mean weight is within ±1.0 pp of K's the arm is judged on
pre-tax non-inferiority (median Δ ≥ −0.05 pp, turnover ≤ 2× K) and on the after-tax gates, and if
it drifts beyond ±1.0 pp the full G1 (CAGR ≥ K − 0.10 pp) and G2 (DD ≤ K + 1 pp) apply *and* the
exposure-normalised CAGR is reported alongside. After tax: K's own gates (three windows at $100k,
B1/B2 median Δ ≥ 0 and share > 0 ≥ 55 %).

**Risks and failure modes.** (i) The cap 0.98 binds most of the time for p ≤ 1 (the shared K/R2 *target* series' mean is
67.31 % of NAV — `smooth_target_v1`'s C0 row; R2 and K differ only in the execution layer, not in
the target), so the arms may differ from K on a small share of
sessions — the study must report the share of differing sessions and refuse to interpret a result
driven by < 1 % of them (the `cash_yield_v1` precedent: 5 of 6,679 sessions, +0.48 pp — small
support is *not* automatically disqualifying, but it must be disclosed). (ii) p = 2's crisis weight
can fall below 0.1; that is the point of the arm, and the tail gate `P(DD > 70 %)` is the honest
test. (iii) The calibration constant is a *single* pre-declared number per arm; a plateau check
(±10 % on `c_p`) is pre-registered as a *reported* robustness row, not as a selection device.
(iv) Cederburg et al. is the reason to expect nothing: state that expectation in the
pre-registration and keep the arm count at four.

---

### I3 — State-keyed realization deferral with a hard drift cap *(rank 3)*

**Hypothesis and mechanism.** K's deferral device, H2, is keyed on the *calendar* ("one of the last
10 December sessions"), and its generalization, `monthly_defer_v1`, is keyed on *every* Yİ-ÜFE
month boundary — and that study failed only on the pre-tax bootstrap because it held an above-target
weight for up to a month. Its own caveat names the missing arm: "*a version that deferred only when
the indexed gain is positive (dropped from the arm set) is the natural next test*"
(`monthly_defer_v1.md:234-236`), and the survey's A2 for it was dropped to keep the arm count at
four. The state exists inside the engine and is **not** what A1u uses: A1u's tiers read the raw
**USD** FIFO gain fraction, while the tax is a function of the **indexed-TRY** gain and of the
marginal bracket rate of the *fill year*. The idea is to defer when the *tax saving* of deferring
beats the *pre-declared* cost of a stale weight — a trigger with no calendar in it — and to bound
the stale weight with a hard drift cap. A drift cap is exactly what H2_20 lacked: H2_20's after-tax
+0.33/+0.44/+0.77 pp is the best *measured* tax number in the record, and its only blemish is a
+1.87 pp worst-instrument Δ max DD (bar +2 pp).

**Literature and evidence strength.** The mechanism is the repo's own, measured twice (H2; the
monthly version) and bounded above by H2_20. Externally: AQR's *Enduring Appeal of Gain Deferral*
Parts 1–3; Constantinides, *Capital market equilibrium with personal tax*, Econometrica 1983;
Dammon, Spatt & Zhang, *Capital Gains Taxes and Portfolio Rebalancing*, TIAA-CREF 2003 — the
embedded gain *is* the state variable of the optimal rebalancing rule. **Evidence strength: strong
for the channel, and the size is already bounded by a measured arm.**

**Why it is not a repeat.** The closed axis is *calendar* trim timing — H2's fixed window
(4 tails plateau-tested in `tax_harvest_v2`) and `monthly_defer_v1`'s twelve boundaries, both of
which defer on a date. This arm has no date: it defers iff the fill's *tax state* clears a fixed
threshold, so it will pick up H2's December deferrals (a sanity check the pre-registration can
require: it must reproduce at least the H2 deferrals whose indexed gain is positive) *and* the
month-boundary deferrals `monthly_defer_v1` had to buy with a stale weight. `exit_timing_v2`
(exit-leg timing, closed) and `smooth_target_v1` (path smoothing, closed) are different devices:
both change *which* fills happen or their size, this changes only *when* an already-determined fill
executes, and only when the tax says so.

**Expected effect.** After tax **+0.1 … +0.5 pp** at $100k — bounded above by H2_20's measured
+0.33/+0.44/+0.77 pp and below by zero (the regime may simply never fire, as the 5-session H2
plateau almost did). Pre-tax ≈ 0 to −0.05 pp, since the arm trades the same fills later and the
drift cap bounds the tracking error. It survives Turkish tax *because it is the tax state*: each
deferral adds one month of Yİ-ÜFE to the indexed basis and, when it crosses the year boundary, one
instalment year of deferral and one bracket year of spreading.

**Data.** None new (the engine's own lot book, CBRT USD/TRY and Yİ-ÜFE series). The trigger needs
the FIFO head's purchase month, its indexed cost and the year's marginal rate — all already computed
in `tax_engine.py`; the *screen* layer cannot see them, so the arm needs a kernel option in a copy of
the engine under `research/<NAME>/` with an off/on parity cell (off = bit-identical to K).

**Pre-registrable arms (4).** All caps and thresholds fixed in advance; every arm keeps A1u and the
0.5 pp band, never defers an exit to zero, an up-resize or a tax-funding sale, re-evaluates the
ordinary rule at every close, and never postpones the same fill twice.

| Arm | Rule |
|---|---|
| D1 | Defer a downsize iff the fill's consumed FIFO lots carry a **positive indexed-TRY gain**; max 21 sessions; all twelve month boundaries, December included. (The dropped `monthly_defer_v1` arm.) |
| D2 | D1 **plus a hard drift cap**: fill immediately, at the next open, whenever `held − target > 10 pp`. |
| D3 | Defer iff the fill's **estimated tax** (the fill year's marginal bracket rate × the consumed lots' indexed-TRY gain) exceeds `0.05 % × NAV`; max 21 sessions; the same 10 pp drift cap. |
| D4 | **H2 with a 20-session December window + the 10 pp drift cap** — the measured upper bound with the risk control H2_20 lacked. |

Fixed reporting and gates: pre-tax G1–G9 with the two-tier `idea_survey_v3` §6.1 rule applied
pre-declared (exposure-neutral arms on non-inferiority, others on G1/G2 from the first line); after
tax K's gates at $100k/$10k/$1M plus B1/B2; and, mandatorily, the deferred indexed gain in TRY, tax
paid, deferrals/yr by year, the distribution of `held − target` (max and p95), and the January-gap
episodes (the two loss episodes on the R2 path are 2015-12→2016-01 −0.699 pp and 2024-12→2025-01
−0.318 pp; 2018 is exactly 0.000 because R2 was already flat).

**Risks and failure modes.** (i) The most likely outcome is "the state never fires" (D1/D3) or
"the cap makes it H2 again" (D2/D4) — that is a *useful* answer and must be pre-declared as such.
(ii) The `monthly_defer_v1` failure mode is inherited: holding an above-target weight into a
mid-month decline; the cap is the defence, and G2/DD is the gate. (iii) The tax state is known at
the close *except* for the very first sessions of a month (the Yİ-ÜFE of month M−1 is published
early in M); the pre-registration must fix the convention (use M−2 for the first three sessions, or
treat the step as unavailable) and report the sensitivity, because the month convention is worth
0.2–0.6 pp on some arms (`idea_survey_v3` §6.3). (iv) Multiple-testing: three thresholds are
declared here and none is searched; the loop "one more threshold" is what closed the band wave, so
the pre-registration must state that D1–D4 are the *last* arms on this mechanism.

---

### I4 — Annual tax budget: loss pool, bracket scale, exemption band *(rank 4)*

**Hypothesis and mechanism.** Every arm in the record reacts to the tax *per sale*. None has ever
reacted to the *year*: the progressive engine has no loss carry-forward and no loss offset outside
its own calendar year, the brackets are deflated each year by the previous October's Yİ-ÜFE (so a
year's real bracket headroom is known *before the year starts*), and the exemption band (below)
resets annually. The direction of travel is therefore state-dependent in the year, and two of the
three devices need **no new trade at all**: (i) a pending downsize can be *assigned* to the adjacent
tax year with the lower marginal-rate state (the H2 mechanism, but keyed on the bracket state rather
than the calendar); (ii) a band-blocked downsize can be *released* early in a year whose bracket
scale is high (cheap) instead of deferred into a cheap year; (iii) only the loss-pool fill and the
exemption harvest *create* a sale, and those carry the legal question flagged below.

**Literature and evidence strength.** Weak-to-medium by design, and honestly bounded:
`idea_survey_v4` I3 pre-registered "required first measurement: Σ_years |min(0, year_gain)|, the
number of loss years, the fraction of each loss year already absorbed, and the year-by-year realised
gain against the deflated bracket ceilings" and said the honest ex-ante is "+0.02 … +0.25 pp, with a
real chance of zero". `tax_harvest_v1` H1's mirror result (loss *harvesting* fired 7 times in 26.5 y
and realised one loss) is the reason to expect the pool to be nearly empty. Literature: Berkin & Ye,
*Tax Management, Loss Harvesting and HIFO Accounting*, FAJ 59(4) 2003; Krasner & Sosner 2024;
Constantinides 1983; the practitioner form of exemption-band harvesting (Kitces, *Mechanics of the
0 % Long-Term Capital Gains Rate*). **Evidence strength: strong for the mechanism, explicitly weak
for the size.** The value of the study is closer to "close the axis with a measurement" than to
"find a gain", and it should be pre-registered that way.

**Why it is not a repeat.** `tax_harvest_v1` H1 harvested *losses* on a calendar date and failed
because an indexed-TRY lot is rarely at a loss; `gain_band_v1` A2's bracket budget was *inert*
because A1u had already blocked the large-gain sales (it only ever saw ≤1 %-gain sales — 190 events
in 26.5 y) — the budget must therefore be paired with a *release* rule, not with a wider band. No
arm has ever realised a gain on purpose, and no arm has ever used the *year's* bracket scale as a
state. `monthly_defer_v1` and H2 are month/December devices; this is the year device.

**Expected effect.** Pre-tax ≈ 0 to −0.01 pp (Y1/Y4 trade the same or a few more times; Y2/Y3 add
two trades per firing). After tax **+0.02 … +0.3 pp**, the width being the pool's unknown size. It
survives Turkish tax because the mechanism *is* the tax code: the same realised TRY, moved one year,
changes both the progressive rate and the number of instalment years, and a gain realised against a
same-year wasted loss is taxed at zero.

**Data.** None new. One legal question needs a source: whether an annual **değer artışı istisna
haddi** applies to foreign-securities capital gains for a Turkish resident — public 2026 tables show
an exemption amount (120–150 k TRY depending on the source; the amounts are revalued annually like
the brackets) and the after-tax engine applies none for capital gains (it models only the 22,000 TRY
investment-income threshold for *dividends*). That must be answered by the adviser before arm Y4 is
run; if it exists it is a free, annually-resetting band and Y4 becomes the strongest arm here.

**Pre-registrable arms (4; `Y0` is a pre-run gate, not an arm).** `Y0` is a *pre-run gate*: the pool
and bracket-occupancy
measurement must be produced and committed first, and the study stops (arms not run) if the measured
mean |pool| / NAV < 0.2 % — a pre-declared stopping rule, because an empty pool would make Y2/Y4
pure noise.

| Arm | Rule |
|---|---|
| Y0 | **Report-only, pre-run.** Per year: net realised TRY gain, `max(0, ·)`, the pool `−min(0, ·)` in TRY and as % of NAV, the count of loss years, the YTD marginal-rate occupancy against `190 k/400 k/1.0 m/5.3 m × scale(year)` TRY, and the top-bracket share of tax paid. Committed before any arm runs. |
| Y1 | **Year selection (no new trade).** A downsize whose fill would fall in the last 10 December or first 10 January sessions fills in whichever adjacent year has the lower YTD marginal-rate state; 10 pp drift cap; exits and up-resizes unchanged. |
| Y2 | **Loss-pool fill.** At the last session of a year with net realised TRY gain < 0, sell-and-rebuy the smallest quantity that brings the year to ≈ 0, capped at 25 % NAV; round trip inside the decision close, `g_frac` on the engine's indexed-TRY formula. **Runs only after a written adviser opinion on a same-instrument same-session round trip (muvazaa / economic substance).** |
| Y3 | **Bracket-scale release.** Release a band-blocked downsize (fill it now) when the *current* year's bracket scale `scale(y)` exceeds the next year's by more than 10 % (both known from the October Yİ-ÜFE); otherwise defer as usual. 10 pp drift cap. |
| Y4 | **Exemption-band harvest.** Only if the adviser confirms an annual değer artışı istisna haddi: at the last session of each year, sell-and-rebuy up to that year's exempt amount of indexed-TRY gain, capped at 25 % NAV. |

Fixed reporting and gates: K's gates as in I3, plus the pool table, sells/yr *excluding* the harvest
round trips (or G9 reads wrongly), and the `neg_gain_by_reason` table by year (an exit moved into a
gainless year destroys its loss offset).

**Risks and failure modes.** (i) The pool may be empty (H1's finding) — hence the Y0 stopping rule.
(ii) Y2/Y4's legal premise is **unverified**: no wash-sale or anti-abuse rule exists anywhere in the
model, which is a property of the *model*, not a legal fact. (iii) The step-up protects appreciation
that would otherwise be realised later at a possibly *lower* deflated-bracket rate, so the benefit is
a discounted rate difference, not a full one. (iv) In the liquidated convention the deferred tax is
charged at the window end, so Y4's benefit must be read on both views. (v) Data-mining: Y0 is the
gate, thresholds are fixed, and the study must be published even if every arm is inert.

---

### I5 — Valuation / ERP-conditioned exposure *(rank 5)*

**Hypothesis and mechanism.** K's only state variables are price-trend and volatility. The one signal
class with a documented long-horizon relation to *expected return* — valuation — appears nowhere in
78 reports (the only hit for `CAPE|Shiller|earnings yield|P/E|ERP` under `docs/` is
`WORKER_PROTOCOL.md:33`, in the list of *allowed inputs*; Shiller data is permitted and has never
been downloaded). The mechanism is the predictive regression: at high prices relative to earnings
(and relative to real bond yields, i.e. a low equity risk premium) subsequent long-horizon excess
returns are lower, so the *optimal* exposure to a fixed-beta instrument should be lower. Applied to
K, the natural form is a *scalar on the on-state weight*, with the anchors frozen on the pre-2000
sample, and — because a valuation tilt mechanically *cuts* exposure and would otherwise pass the
drawdown gate for free — a **matched-average-weight arm** that isolates timing from level.

**Literature and evidence strength.** Campbell & Shiller, *Valuation Ratios and the Long-Run Stock
Market Outlook*, JPM 1998 (strong in-sample, still the reference); Fama & French, *The Equity
Premium*, JF 2002 (dividend/earnings ratios predict); **Goyal & Welch, *A Comprehensive Look at the
Empirical Performance of Equity Premium Prediction*, RFS 2008 (the counterweight: the predictors
fail out of sample)**; Asness, *Fight the Fed Model*, JPM 2003 (on ERP/E-Y versus yields); Erb &
Harvey on the CAPE's limits. **Evidence strength: medium in-sample, weak out of sample — the honest
prior is "no edge".** The reason to run it anyway is that it is the *last untouched signal class*
for the on-state, it is cheap on the Numba engine, and it has 150 years of free data behind it
(Shiller's monthly series from 1871), i.e. a *pre-sample* the repo has never used as an OOS.

**Why it is not a repeat.** No study sizes or times exposure on any valuation quantity; the closest
neighbours are macro *votes* that failed (`credit_vote_v1` BAA10Y and the curve, `vix_filter_v1`),
and those are business-cycle/vol states, not valuation. Replacing the *level* of the vol target is
explicitly closed (`exposure_k_v1`); this is a *different signal multiplied into the on-state weight*
with the average exposure held fixed in its deciding arm.

**Expected effect.** Pre-tax **−0.5 … +0.4 pp** CAGR (the likely failure is de-risking through
2013–2021); after tax the tax channel *helps* a de-risking rule (fewer taxable resizes) but the
pre-tax loss dominates. Max DD almost certainly *lower* — which is why the verdict must rest on the
CAGR gate and the after-tax windows, and why the DD gate cannot be allowed to make a
return-reducing arm look attractive.

**Data.** Free and long: Shiller monthly CAPE / earnings / real rates 1871+ (`http://www.econ.yale.edu/~shiller/data.htm`,
the `ie_data.xls` series), ^NDX daily 1985-10+, ^GSPC daily 1950+, QQQ/QLD as the harness already
consumes them, and DTB3/real-rate series from FRED. Long OOS: the entire 1871–1999 pre-sample for
the *signal* (the arms themselves cannot run before the synthetic 2× leg exists, which is why the
pre-registration must include a signal-level pre-run check).

**Pre-registrable arms (4; `V0` is a pre-run gate, not an arm).** The signal is the **previous
month's** published value (1-month lag), anchors are frozen on the pre-2000 sample only, and no
threshold is searched.

| Arm | Rule |
|---|---|
| V0 | **Pre-run signal check (gate, not an arm).** On 1871–1949 only: regress the forward 10-year real S&P return on the earnings yield / CAPE; the arm set below runs only if the slope has the expected sign with |t| ≥ 2 in that sub-sample, and the same regression is reported for 1950–1999 and 2000–2026 as OOS evidence. |
| V1 | Scale the on-state target weight by `clip(EY_t / EY_ref, 0.70, 1.15)`, where `EY_ref` is the 1950-01→1999-12 **median** earnings yield (one frozen constant). |
| V2 | Two-state: weight × 0.75 while CAPE > its **1871–1999 90th percentile** (one frozen threshold), else × 1.0. |
| V3 | Causal percentile: weight × 0.75 while CAPE > the *expanding* median + 1σ of all data up to the previous month, × 1.10 while below the median − 1σ, else × 1.0 (no frozen anchor at all). |
| V4 | **V2 with the average weight matched to K's** over 1985-10 → 2000-03 (the level-free interpretation arm: the same two-state tilt, rescaled so its mean target weight equals K's). |

Fixed reporting and gates: K's gates as in I2 (including the two-tier rule and the realized mean
weight), plus the average exposure and the de-risked-session share. The verdict is taken on **V4**
(exposure-matched) and **V3** (causal, no frozen anchor); V1/V2 are reported as *level* variants and
cannot be counted as gains while their mean weight is below K's.

**Risks and failure modes.** (i) The signal is the most-mined in finance; the guards are the frozen
pre-2000 anchors, the causal expanding arm V3, and the V0 pre-run gate — if V0 fails, the study
stops before any arm. (ii) A valuation tilt is a *level* bet on a decade: 2013–2021 is in the test
window and is precisely where it loses. (iii) Monthly data with a 1-month lag makes reaction slow;
that is the honest form and must not be optimised into a faster signal. (iv) The matched-average
construction reduces the arm's power (it *is* mostly a timing test), which is the point.

---

## 5. Ideas considered and rejected (one line each)

1. **Any off-state asset variant** — bills/bonds/gold/inverse/defensive-sector rotas all fail
   (`riskoff_rotation_v1–v4`, `riskoff_after_tax_v1` L2 = +0.04 pp after tax at $100k but −0.32 pp at
   $1M, `gold_riskoff_oos_v1`, `leverage_frontier_v1`, `shortside_v1`, `tbill_sweep_v1`), the bill
   *ETF* loses after tax while only the interest-paying **cash sweep** survives (`r2_tbill_combo_v1`
   vs `cash_yield_v1`), and a *permanent never-trimmed* defensive sleeve is a variant of the
   pre-prune sleeve stack (`gold_v1b`, `dividend_growth_v1` passed Stage B, then the whole sleeve
   stack was removed 2026-09-25) and of the never-traded core that `idea_survey_v2` §7.4 rejected
   algebraically.
2. **Lot-level selection and the two-queue wrapper** — Alpaca exposes no lot-level sells and FIFO is
   assumed (`idea_survey_v2` I1, blocked for two waves), and the only implementable queue pair is
   QLD↔SSO, which `relstrength_v1` already priced at −1.2 … −4.1 pp; the *legal* form of two queues
   is I1's two-taxpayer split, which is a different idea.
3. **Loss harvesting and hedging** — `tax_harvest_v1` H1 fired 7× in 26.5 y because an indexed-TRY
   foreign-equity lot is rarely at a loss, and `tax_hedge_v1` showed inverse-ETF losses do not offset
   long-leg gains (−0.44 … −0.53 pp after tax).
4. **Calendar/seasonal and structural-date effects** — the whole seasonal family is
   placebo-rejected (`seasonal_overlay_v1` S3 killed by `seasonal_placebo_v1`; S1 −4.45 pp;
   turn-of-month S4 −6.84 pp), and the pre-FOMC drift, the only structural-date effect with a
   literature, was rejected in `idea_survey_v2` §7.1 and *disappeared after 2015* (Kurov, Wolfe &
   Gilbert, FRL 40, 2020) while adding ~8 round trips/yr.
5. **LIVE-account frictions as an *idea* (Alpaca fees, order type, FX conversion, dividend
   withholding)** — bounded and level-only: the research harness charges 4 bps/side against a
   production model of 2 bps slippage + 1 bps half-spread + SEC/TAF, and K's whole cost drag is
   `5.4561 × 4 bps ≈ 0.22 %/yr`, so a full 1 bp/side improvement is ≈ 5.5 bp/yr; the
   dividend-withholding/assessment gap (research taxes dividends inside total return; the production
   replay credits them untaxed; `after_tax_tr_v1` says 20 % US withholding with a GVK 123 credit
   capped at the Turkish tax) is ≈ 0.1 pp/yr and moves every arm's *level*, not their ranking.

## 6. Cross-cutting caveats

1. **The premise is the biggest parameter.** Everything in §4.2–§4.5 is priced for one taxpayer under
   the frozen 2026 progressive tariff with Yİ-ÜFE indexation, per-calendar-year netting and no
   carry-forward. I1 is the check on that premise, and the engine's own tariff reproduces a public
   2026 table (190 k / 400 k / 1.0 m / 5.3 m TRY at 15/20/27/35/40 %), which is why it is credible —
   but the *inputs* to the model (the month convention, the 10 % indexation floor, the absence of a
   capital-gains exemption band) carry the repo's own stated uncertainty of 0.2–0.6 pp.
2. **After-tax deltas below ~0.1 pp are inside modelling uncertainty**, and five of the last seven
   measured arms landed exactly there. Report the fixed calendar, a flat-rate row and a
   no-indexation row for every arm.
3. **The liquidated convention penalises deferral arms** (it charges the deferred tax at the window
   end) and **B2 understates indexation** (no currency, no inflation): the deciding pair is
   {liquidated, B1-recent}, with B2 as a lower bound.
4. **The gate set still rejects tax-only arms by construction** (G5 is a pre-tax bootstrap whose
   median must not be negative) and it accepts exposure-reducing arms trivially on drawdown. Both
   failure modes are pre-registered against in I2/I5 (two-tier non-inferiority; matched average
   exposure).
5. **Every idea here is small until I1 answers.** The four rule-level ideas are each worth a
   quarter-point of after-tax CAGR or less, and the honest expectation for two of them (I4, I5) is
   "no material effect, axis closed". The owner's decision-relevant output of this survey is the
   order of operations: **I1's precondition first, then I2 (cheap, no legal risk), then I3 (the best
   measured bound still on the table), then I4/I5 as axis-closing measurements.**
6. **Not tax advice.** Every rule cited here is a model; the 20/D, exemption-band and wash-sale
   questions are adviser questions and are stated as such.

## 7. Status

Survey only. No screen, harness, pre-registration, arm or gate was run; no file outside
`docs/research/` was written; no dataset was downloaded (two Turkish tax sources were read online,
text only). The five proposals in §4 each carry every parameter fixed so that a pre-registration can
be committed before any simulation, as the protocol requires.
