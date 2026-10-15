# Untested research ideas for K after Turkish tax (idea survey v4)

> Evidence: tag `archive/research/idea-survey-v4-2026-10` (survey only; no backtests).

Date: 2026-10-15. Branch `research/idea_survey_v4`. **This is a survey, not a backtest**:
no screen was written, no arm was simulated, no gate was evaluated, nothing outside
`docs/research/` was written, and no file was downloaded. Every number below is quoted
from `docs/research/*.md` or read out of an *existing* stored result file
(`research/band_h2_combo_v1/RESULTS.json` from tag `archive/research/band-h2-combo-v1-2026-10`,
`research/k_stage_b_v1/STAGE_B_RESULTS.json` from tag `archive/research/k-stage-b-v1-2026-10`);
which numbers are which is stated inline. The record's own caveat applies: none of this is
tax advice.

Control is production **K** = `equity_leveraged_trend_v1` 1.2.0 = E2 (2-of-3 QQQ vote) +
R2 early re-entry + **A1u** (gain-scaled downsize band 0.5 / 3 / 8 pp at an embedded USD FIFO
gain fraction ≤ 1 % / ≤ 5 % / > 5 %) + **H2** (a December downsize defers to January).
Reference numbers (from `band_h2_combo_v1.md`, control reproduces them exactly): pre-tax
**15.7649 % base / 15.1785 % calibrated**, max DD **46.670 % / 46.795 %**, turnover 5.4561/yr,
10.17 sells/yr; after tax at $100k liquidated **10.753 / 15.235 / 23.972 %** on the
2000-03-09 / 2010-01-01 / 2017-01-03 windows (C0 = R2: 10.160 / 14.706 / 22.886 %); clean
bootstraps B1-recent **+0.286 pp (75.0 %)** / B2-flat **+0.131 pp (64.0 %)** versus R2.

## 1. Verdict

| # | Idea | Axis changed | Expected after-tax Δ vs K ($100k) | Confidence it is untested |
|---|---|---|---|---|
| I1 | **After-tax exposure frontier under K** (vol-target re-trace, DD-matched) | steady-state exposure | +0.15 … +0.45 pp; **cost: +1 … +5 pp max DD** | high (only the *pre-tax* frontier was traced, under E2) |
| I2 | **Early-path trigger quorum** (2-of-3 instead of AND-of-3) | the early leg's entry condition | +0.20 … +0.60 pp (pre-tax +0.2 … +0.8) | high (only the early path's *size* and *thresholds* were tested) |
| I3 | **Annual tax-budget execution** (loss-pool pairing + bracket headroom = v3's I5, honestly assessed) | when a given realisation happens within/between tax years | +0.02 … +0.25 pp (pool-dependent) | high (only blind calendar deferral tested) |
| I4 | **Exit-leg tax timing** (defer/stage the large cuts the band cannot reach) | the tax event A1u/H2 exempt by construction | +0.02 … +0.15 pp | high (`exits to zero always fill` in both A1u and H2) |
| I5 | **Idle-cash yield under the tax code** (interest channel, not BIL) | the 2-15 % of NAV that earns 0 | +0.05 … +0.30 pp | high after tax (only the ETF sleeve was measured) |

The ranking is by expected after-tax value per unit of research risk, and it is
**deliberately front-loaded with exposure ideas**. §3 shows why: under K the taxed base is
*the strategy's own sells* (88 % of realised gain; the terminal liquidation is 11 %), the
indexation relief is nearly saturated, and the *effective* tax rate on realised gain is
unchanged from R2 (17.3 % vs 17.1 %). K's own after-tax edge decomposes as
**+0.288 pp pre-tax exposure + 0.30 pp lower drag** — i.e. the biggest lever in this system
has always been *what and how long you hold*, and the tax system only decides how much of
that extra return it takes. Only five axes are left with no measurement behind them (§4);
the top two are exposure ideas and the bottom three are tax/accounting refinements whose
honest ex-ante is +0.02 … +0.30 pp each, with a real chance of zero.

## 2. Method

Read in full: `docs/research/INDEX.md`; `idea_survey_v2.md`, `idea_survey_v3.md`;
`k_stage_b_v1.md`, `band_h2_combo_v1.md`, `gain_band_v1/v2/v3.md`,
`gain_band_integration_v1.md`; `tax_harvest_v1/v2.md`, `monthly_defer_v1.md`,
`smooth_target_v1.md`, `after_tax_tr_v1.md`, `vt_level_tax_v1.md`, `tax_asym_v1/v2/v3.md`,
`tax_frontier_v1.md`, `tax_hedge_v1.md`, `r2_tbill_combo_v1.md`, `intraday_stop_v1.md`;
`deriv_vote_v1.md`, `breadth_vote_v1.md`, `vote_hysteresis_v1.md`, `rule_variants_v1.md`,
`e2_confirmation_v1.md`, `tsm_tr_v1.md`, `signal_ensemble_v1.md`;
`leverage_frontier_v1.md`, `riskoff_after_tax_v1.md`, `riskoff_rotation_v1-v4.md`,
`instrument_cost_v1.md`, `early_qqq_v1.md`, `relstrength_v1.md`, `crash_entry_v1.md`,
`dip_overlay_v1.md`, `tbill_sweep_v1.md`, `gold_riskoff_oos_v1.md`, `shortside_v1.md`,
`vol_window_v1.md`, `ewma_band_v1.md`, `fast_reentry_v1/v2.md`. Three read-only sweeps
covered the remaining ~40 reports.

Two things were read *out of stored results* rather than reports, and they are the
quantitative core of §3:

* `research/k_stage_b_v1/STAGE_B_RESULTS.json` — K vs R2 vs A1u on the production engine,
  2017-01-03 → 2026-07-30, $100k, each arm's own fills through the corrected Turkish tax
  engine (`research/band_h2_combo_v1/tax_engine.py`, a byte copy of
  `archive/research/gain-band-v3-2026-10`'s). The per-reason fields
  (`realised_gain_try_by_reason`, `indexation_relief_try`, `tax_total_*`) were not
  tabulated in any report.
* `research/band_h2_combo_v1/RESULTS.json` — the unscaled $100k after-tax windows
  (relief, tax paid, postponements).

Externally, only published literature and a few practitioner pages were read (§4 cites the
links). Nothing was downloaded.

## 3. Where K's tax base actually is — and what that closes

The brief's premise is that after K "the remaining tax base is mostly vote EXITS (full round
trips) and end-of-window liquidation". **The repo's own production-engine measurement says
otherwise, and the difference changes the ranking.** Realised TRY gain by sale reason, K and
its control on the production engine's own fills (2017-01-03 → 2026-07-30, $100k, progressive,
each arm's realised gain *shares* are comparable even though the scaled quantities are not):

| Arm | sells/yr | resize share | liquidation share | tax-funding share | tax paid (USD) | Yİ-ÜFE relief |
|---|---:|---:|---:|---:|---:|---:|
| R2 | 33.0 | 96 % (26.27 m TRY) | 3 % (0.86 m) | 1 % | 222,197 | 10.44 m TRY |
| A1u | 8.8 | 87 % | 11 % | 1 % | 212,690 | 11.07 m |
| **K** | **8.7** | **88 % (26.01 m)** | **11 % (3.41 m)** | 1 % | **202,879** | **11.34 m** |

And unscaled, same $100k, where the levels *are* comparable across arms
(`band_h2_combo_v1/RESULTS.json`): 2000 window C0 relief **18.58 m TRY** / tax **766,160 USD**;
A1u **21.84 m** / 819,008; H2 19.37 m / 775,620; **K 22.61 m / 830,903**. 2017 window: C0
10.41 m / 360,297; **K 11.99 m / 371,487**.

Four facts follow, and they bound every idea in §4.

1. **The band does not shrink the base; it moves it later and makes it older.** K's realised
   resize gain is essentially R2's (17 % fewer sells, 26.01 m vs 26.27 m TRY in the scaled
   comparison; the *shares* are the load-bearing part), because A1u's tiers are 0.5 / 3 / 8 pp
   while a vote-off or an early-path toggle is a **20-50 pp** drift: the band *cannot* block
   the large cuts, and its own log confirms it — all 415 A1u band holds are gain-positive,
   127 of 133 runs sit at the 8 pp tier with `g_frac` 7.8-15.0 %. It blocks ~1,289 *small*
   gain-heavy trims (≈49/yr) over 26.5 years, which is where the ~19 sells/yr went.
2. **The effective tax rate on realised gain did not improve.** Tax paid ÷ (pre-tax terminal −
   capital) is 17.3 % (K) versus 17.1 % (R2) on the 2000 window. K's tax bill is *larger* in
   absolute USD at the same $100k. What improved is the *ratio*: pre-tax 15.7649 % → 10.753 %
   is a drag of **5.01 pp** versus R2's **5.31 pp**. Decomposing K − C0 = +0.593 pp:
   **+0.288 pp is pre-tax (the extra exposure the band holds) and +0.305 pp is drag**
   (relief up 4.0 m TRY, announcements later, one December bill moved a full instalment year).
3. **Indexation relief is nearly saturated.** It removes 22.6 m of ~49 m TRY of gross gain
   under K (≈46 %), and adding another month of Yİ-ÜFE to a 5-to-20-year-old lot changes the
   relief by ~1 % of the relief. This is why the whole deferral family measures +0.02 … +0.5 pp
   and not more (H2: +0.14/+0.13/+0.27 pp; monthly deferral +0.26…+0.51 pp; the band's own
   drag channel 0.21 pp), and it is why v3's "pure tax-timing on trims is spent" is correct.
4. **With a compounding account, early payment dominates any bracket effect.** A TRY of tax
   paid in 2005 compounds into ≈16× the post-tax cost by 2026 at K's own pre-tax rate; a TRY
   paid at the terminal liquidation costs 1×. That is the arithmetic behind AQR's deferral
   result (`AQR, The Enduring Appeal of Gain Deferral, Part 3`: an 80 %-unrealised-gain
   portfolio needs a **16-year** horizon before switching to a +1.0 pp/yr better strategy
   breaks even) and the reason ideas in the *acceleration* direction start with a negative
   prior. And the same arithmetic caps the *size* of any deferral idea: the base is fixed by
   the terminal liquidation, so a rule can only change *when* it is taxed.

The corollary is the survey's single most useful statement: **the well is not "tax" any more,
it is "tax-cheap exposure".** Buys realise nothing, an investment that is never sold is taxed
once (at the window end), and the early leg's lots are days-to-months old, so its sales fall
in the band's 0.5 pp tier at `g_frac` ≤ 1 %. The ideas below are ranked accordingly.

## 4. Ranked ideas

Common mechanics for every arm: daily decision on the completed close, fill at the next open,
`vol_target 0.35` on the held instrument, cap 0.98, 0.5 pp resize band, 4 bps × m, R2's early
path, A1u + H2 exactly as shipped, the `fast_reentry_v2` 21-instrument / OOS sets where the
idea is measurable there, the corrected after-tax engine at $100k with the six windows,
B1-recent and B2-flat (seed 20261011, block 20). Nothing is tuned after seeing a result.

---

### I1 — The after-tax exposure frontier under K *(rank 1)*

**Hypothesis and mechanism.** K is, in effect, an upward exposure ratchet: it blocks the
gain-heavy downsizes the vol target demands and never blocks an up-size, and its own
attribution proves it — `k_stage_b_v1` decomposes A1u's $40,285 NAV gain as **$34,152 of
extra-exposure P&L** + $1,168 saved costs + $222 dividends + $4,743 intraday marking, with
five of 133 band runs carrying +$33.3 k of it. The daily attribution is explicit:
*not selling* in a high-vol rally (2023-03 +$15.8 k, 2025-06 +$9.3 k, 2026-04 +$9.1 k) is where
the return comes from, and the tax cost of that extra exposure is deferred, not avoided
(fact 1 above). K holds *more* exposure than R2 at *lower* max DD (46.670 % vs 46.758 %), so
the DD budget that constrains every arm (`max DD ≤ K + 1 pp`) has not been spent. The
question the owner actually faces is therefore: **what is the price, in drawdown, of the
after-tax-optimal exposure once K's tax logic is in place?** — and that frontier has never
been traced on the after-tax objective with K as the control.

**Literature.** Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & van Hemert, *The Impact of
Volatility Targeting*, JPM 2018
([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)) and Moreira & Muir,
*Volatility-Managed Portfolios*, JF 72(4) 2017
([DOI](https://doi.org/10.1111/jofi.12513)) — the vol-target *level* is the first-order choice;
AQR *Enduring Appeal of Gain Deferral*, Part 3 (above) — the break-even tool for "hold the
lower-return, deferred position" versus "own the higher-return one". Evidence strength:
**strong for the mechanism, weak for the sign.** The repo has already measured the same grid
pre-tax and it failed on drawdown and tail risk (`leverage_frontier_v1`: every cell that
clears +1 pp CAGR fails `P(DD>70 % > C0 + 5 pp)`; 3x/0.35 → tail 20.4 % vs a 17.4 % limit)
and after tax under a *different* control (`vt_level_tax_v1`: 0.40 wins on the realised path,
**+0.61 / +0.96 / +1.72 pp**, but its clean bootstrap medians are **−0.088 / −0.153 pp**).

**Why it is not a repeat.** The two nearest studies are both *pre-K*. `vt_level_tax_v1`'s
control is production **E2** (14.67 %/49.56 %, *no* early path, *no* band) and it judged the
arms with pre-tax gates it had no way to change; `leverage_frontier_v1` has **no tax model at
all** and used a plain SMA200 signal with synthetic L× leverage, i.e. cells the account cannot
hold. Under K the *tax* arithmetic of a higher target changes in a specific, measurable way:
a higher target's extra trims are exactly the gain-heavy ones A1u defers (A1u blocks ≈49
resizes/yr, the high-`g_frac` ones; the surviving ~10 sells/yr still book ≈88 % of the
realised gain), K adds
relief (22.61 m vs 18.58 m TRY) and cuts the drag (5.01 pp vs 5.31 pp), so the marginal tax
cost per unit of exposure has fallen while the pre-tax DD penalty is unchanged. That is
precisely `vt_level_tax_v1`'s failing channel (the clean bootstrap), and it has not been
re-measured.

**Expected effect.** Pre-tax +0.15 … +0.60 pp CAGR at **+1 … +5 pp max DD** (the DD column
scales steeply: 0.40 was +0.50 pp CAGR for +4.9 pp DD under E2). After tax **+0.15 … +0.45 pp**
if a cell fits inside the DD budget, because the extra return is mostly deferred exposure and
the extra tax is deferred with it. Survival under Turkish tax is *conditional on the DD
budget*, not on the tax code — the study's honest headline may well be "the after-tax optimum
is beyond a drawdown the owner accepts".

**Data.** None new.

**Pre-registrable arms (4).** All under K; `vt` is the only change.

| Arm | Rule |
|---|---|
| C0 | K exactly (must reproduce 15.7649/15.1785 %, DD 46.670/46.795 %, 10.753/15.235/23.972 %). |
| F1 | `vt = 0.375` (a 1/14 increase), everything else K. |
| F2 | `vt = 0.40` — reproduces `vt_level_tax_v1`'s cell under the new control. |
| F3 | `vt = 0.45` — the top of the measured grid; expected to fail DD and reported as the frontier's endpoint. |

Fixed reporting: pre-tax CAGR/DD (base and calibrated), G1-G9 as `ewma_band_v1` defines them,
the after-tax windows at $100k/$1M, B1/B2, and **the after-tax CAGR-versus-max-DD frontier**.
Pre-registered selection rule: *select the highest after-tax CAGR among F1-F3 whose pre-tax
max DD ≤ K + 1.0 pp and whose G1-G9 all pass; if none, close the exposure axis with the
frontier table and do not re-run it.*

**Risks / failure modes.** (i) The DD gate probably binds at every cell — which is the
*answer*, not a bug, but it makes this a "price the risk" study. (ii) K's pre-tax gain, like
A1u's, is five episodes; a 2023-03-shaped rally cannot be assumed twice. (iii) `G9` turnover
is safe (a higher target trades slightly *less*: 58.1 → 43.0 resizes/yr at 0.40 under E2), but
the *trade size* is larger, so the per-episode tax lump grows and `G5`'s bootstrap is the
second-likely binding gate. (iv) The after-tax windows are single paths; B1/B2 decide.

---

### I2 — Early-path trigger quorum (2-of-3 instead of AND-of-3) *(rank 2)*

**Hypothesis and mechanism.** R2's early leg — half the production weight while the vote is
off, if QQQ > SMA50 **and** `vol20` below its 252-session median **and** QQQ > its close 20
sessions ago — is the entire edge (95.3 % of R2's gain from three episodes; **+0.81 pp CAGR
and −2.80 pp max DD** versus E2, 164 active sessions over 26.5 years). It requires **all three**
conditions, and no study has ever loosened the conjunction: `fast_reentry_v2` varied the SMA
(40/50/60), the vol-median window (126/252/504) and the early *weight* (1/3, 1/2, 2/3) —
all six one-parameter neighbours beat C0 on CAGR (+0.36 … +1.08 pp). A quorum of two would
make the leg active far more often. **The tax argument is what makes this the tax-cheapest
exposure in the system**: the early leg is built by *buys* (nothing is realised), and its
*lots* are days-to-months old, so the sales that end an early-leg episode consume young,
low-gain units which fall in A1u's 0.5 pp tier (`g_frac` ≤ 1 %) — the tier that fills
immediately. Compare the alternative exposure levers, whose realisations are large and late.

**Literature.** Faber, *A Quantitative Approach to Tactical Asset Allocation* (2007, 2013)
and Hurst, Ooi & Pedersen, *A Century of Evidence on Trend-Following Investing*, JPM 2017
([PDF](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf))
— the defensive leg of a trend rule pays for itself mainly in the rebounds; Moreira & Muir
2017 — sizing on the *state* that predicts returns. Evidence strength: **strong internally
(the leg's value is measured), medium externally**; the honest risk is that a quorum, like
every other looser trigger in the repo, re-enters into false starts.

**Why it is not a repeat.** Closest tested: (i) `fast_reentry_v2`'s six neighbours — all are
*single-parameter* changes to an *AND*; (ii) `vote_hysteresis_v1` Y1-Y4 and
`e2_confirmation_v1` — hysteresis/quorum on the **main** E2 vote (Y1-Y3 lose 0.05-1.7 pp,
Y4 fails 2017+, E2 closed by K5); (iii) `ladder_v1` (in flight) changes the early leg's
*size* on the *unmodified* trigger — orthogonal to changing which conditions gate it.
`rule_variants_v1` D5 (leverage by trend strength) is a *main-position* size rule and loses
4.4 pp with 20.7×/yr turnover.

**Expected effect.** Pre-tax +0.20 … +0.80 pp CAGR, max DD **+1 … +4 pp** (the leg is already
−2.8 pp on DD; more sessions of it will give some of that back). After tax **+0.20 … +0.60 pp**
— better than the pre-tax ROM×0.7 rule of thumb, because the extra realisations are the
cheapest in the system and their lots are young. It survives Turkish tax for a *structural*
reason, not a timing one: the exposure is acquired by purchase and its sales sit in the tier
A1u lets through, so the marginal traded unit is barely taxed.

**Data.** None new.

**Pre-registrable arms (3).**

| Arm | Rule |
|---|---|
| C0 | K exactly. |
| Q1 | Early leg (0.5 × vt, same exit below SMA50) active while **≥ 2 of 3** early conditions hold; re-entry and exit both use the 2-of-3 majority. |
| Q2 | Drop only the 20-session momentum condition: `QQQ > SMA50 ∧ vol20 < median252(vol20)`. |
| Q3 | Q1 plus hysteresis: enter at 2-of-3, exit only when **≥ 2 conditions fail** (symmetric 2-of-3 with the same 3 conditions). |

Fixed: everything else K; the early weight stays 0.5; the exit condition stays SMA50 for Q1/Q2;
`vt`, cap, band and H2 unchanged.

**Risks / failure modes.** (i) The 2-of-3 quorum's extra sessions are *marginal* recoveries —
the same class that made `vote_hysteresis_v1` Y1-Y3 lose 0.05-1.7 pp; expect the plateau check
(Q2 vs Q3 vs `fast_reentry_v2`'s SMA-40 neighbour) to be the honest test. (ii) DD: the
`fast_reentry_integration_v1` precedent is a `+1.77 pp` DD neighbour against a `+1 pp` limit.
(iii) Three-episode concentration: leave-one-episode-out must be reported (2001-10 +8.58 pp,
2023-01 +6.75 pp, 2002-11 +3.45 pp). (iv) The 21-instrument gate is hostile to *added*
exposure (only 15/21 positive for R2, and DD within +3 pp on 17/21).

---

### I3 — Annual tax-budget execution: loss-pool pairing + bracket headroom *(rank 3)*

**Hypothesis and mechanism.** Two features of the Turkish model are unused: (a) `assess()`
floors each year's net gain at zero with **no carry-forward**, so a net-loss year's loss is
destroyed, but a loss *is* usable inside its own year; (b) the tariff is progressive
(15/20/27/35/40 % for 2026, brackets revalued each year by the previous October's Yİ-ÜFE) and
brackets are *fixed in real TRY*, while the account compounds — so late in a 26-year window
every realisation sits in the 35-40 % band. The idea is to make each calendar year's realised
gain *deliberate*: (i) realise gains up to a same-year loss pool (tax-free, and it steps the
basis up, removing that appreciation from the terminal 40 % bullet); (ii) fill the unused part
of the year's bracket in a lean year instead of leaving it for a later, more expensive year;
(iii) the *no-new-trade* variant — move the year in which a **already pending** downsize
fills, keyed on the year's accumulated realised gain, instead of the calendar-blind H2 rule.

**Literature.** Constantinides, *Capital market equilibrium with personal tax*,
Econometrica 1983; Dammon, Spatt & Zhang, *Capital Gains Taxes and Portfolio Rebalancing*,
TIAA-CREF Institute 2003 ([PDF](https://personal.utdallas.edu/~hxz054000/RD%20no[1].%20754.pdf))
— gains should be deferred and losses realised, and the optimal rebalancing rule's state
variable *is* the embedded gain; Berkin & Ye, *Tax Management, Loss Harvesting and HIFO
Accounting*, FAJ 59(4) 2003; Krasner & Sosner, *Loss Harvesting or Gain Deferral?*, JWM 27(1)
2024 ([PDF](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQRJWMSum24LossHarvestingorGainDeferral.pdf));
the practitioner form of the 0 %-bracket step-up: Kitces, *Mechanics of the 0 % Long-Term
Capital Gains Rate*
([link](https://www.kitces.com/blog/understanding-the-mechanics-of-the-0-long-term-capital-gains-tax-rate-how-to-harvest-capital-gains-for-a-free-step-up-in-basis/)).
Evidence strength: **strong for the mechanism, and explicitly *against* the aggressive
variant** — AQR Part 3 shows a deferred position needs a 16-year horizon to give up a +1.0 pp
return advantage, i.e. decomposition (ii) starts from a negative prior unless the rate gap is
≥ ~20 pp and the deferral would have been short.

**Why it is not a repeat.** Closest tested, and each misses on a different axis:
`tax_harvest_v1` H1 harvested **losses** (and failed, because an indexed TRY lot is rarely at
a loss: 7 firings in 26.5 y, one loss); H2 and `monthly_defer_v1` defer **blindly by calendar**
(December, month boundaries) with no state and no pool (`monthly_defer_v1` A3 missed the
pre-tax bootstrap by 0.05 pp); `gain_band_v1`'s A2 carried a **bracket budget** but only in the
*defer* direction ("YTD rule gain ≥ 1,000,000 × scale(year) TRY → widen the band to 15 pp",
and it only ever saw ≤ 1 %-gain sales, 190 events over 26.5 y); `gain_band_v1`'s A1 is
gain-scaled, not year-scaled. **No arm in the repo has ever realised a gain on purpose.**

**Expected effect.** Pre-tax ≈ 0 (fact (iii) adds a handful of trades; the band's stale-weight
drag is the only cost). After tax **+0.02 … +0.25 pp**, the wide range being the point: it is
*bounded by a pool that has never been measured*. Required first measurement, before any arm
runs: `Σ_years |min(0, year_gain)|`, the number of loss years, the fraction of each loss year
already absorbed by same-year gains, and the year-by-year realised gain against the deflated
bracket ceilings. Expect (a) to be nearly empty (H1's finding) and (ii) to be worth little
(AQR Part 3), so the honest ex-ante is "a small effect with a real chance of zero".

**Data.** None new (the engine's own lot book and the CBRT/Yİ-ÜFE series).

**Pre-registrable arms (4).**

| Arm | Rule |
|---|---|
| C0 | K exactly. |
| Y1 | **Loss-pool fill** (v3's I5): at the last session of each year, if YTD realised net TRY gain < 0, sell-and-rebuy the smallest quantity that brings the year to ≈ 0, capped at 25 % of NAV; round trip inside the decision close, `g_frac` computed on the engine's indexed-TRY formula. |
| Y2 | **Bracket-headroom fill**: at the last session of each year, if YTD realised net TRY gain < 0.25 × the year's top bracket ceiling (scaled), sell-and-rebuy up to that ceiling, capped at 25 % of NAV. |
| Y3 | **No-new-trade year reallocation**: a pending downsize whose fill session falls in the last 10 December or the first 10 January sessions fills in whichever of the two adjacent tax years has the *lower* YTD realised gain (the year is chosen by state, not by the calendar); exits unchanged. |
| Y4 | Y1 + Y3 (report-only upper bound on the channel). |

**Risks / failure modes.** (i) **The legal premise, stated plainly.** Every arm except Y3
rests on a same-instrument sell-and-rebuy inside one session. The tax engine has **never
contained a wash-sale or anti-abuse rule** (`tax_harvest_v1`, `idea_survey_v2` §1.2 item 5,
`idea_survey_v3` §6.5), so the model prices a transaction whose treatment in Turkish law is
*unverified*: US practice is that gain harvesting has no wash-sale analogue, but an
"economic-substance"/muvazaa objection to a same-day round trip engineered only to reset the
basis is not excluded by anything in this repo. A written mali müşavir opinion is a
precondition for Y1/Y2/Y4, exactly as it was for lot selection; **Y3 is the only arm with no
legal question at all**, which is why it is in the arm set despite being the least novel.
(ii) The pool may be empty, and where it is not, the offset is worth the *marginal* rate, not
the average. (iii) The bracket in a lean year is small in real terms (190 k TRY ≈ $4.6 k at
the 2025 anchor; the top bracket is 5.3 m TRY ≈ $130 k), so Y2's room is narrow at $100k and
effectively nil at $1M. (iv) The mirror of H1's failure: the taxable gain is the *nominal TRY*
gain net of indexation, so a sale-and-rebuy inside one month gets *no* indexation step and
the rebuy resets a lot that would otherwise have been old and relief-rich — the same mechanic
that made `tax_harvest_v1` H1 lose −0.7 … −2.3 pp.
(v) `sells/yr` must be reported net of these trades or G9's turnover ratio reads wrongly.

---

### I4 — Exit-leg tax timing: the cuts the band cannot reach *(rank 4)*

**Hypothesis and mechanism.** A1u defers a downsize between two positive weights and **never**
an exit ("entries, exits to zero and up-resizes keep 0.5 pp"); H2 defers a December downsize
and again "never an exit, entry, up-resize or risk reduction". But an exit is the largest
single realisation the system makes: it clears the **whole** FIFO book in one fill, and after
K that book is ~3× older than R2's (33 → 8.7 sells/yr) which is visible in the numbers —
K's terminal liquidation gain grew 4× (0.86 m → 3.41 m TRY) and its relief grew 10.44 →
11.34 m TRY, i.e. the units surviving under K are precisely the old, gain-rich ones. A
vote-off, and the early path's SMA50 exit, are both 20-50 pp drifts, so the band cannot touch
them. Two tax-only devices remain: bring the *year* of the exit's fill into the neighbour year
(H2's mechanism, applied to the event H2 exempts, worth a full extra instalment year because
a December realisation is payable the following March/July and a January one a year after
that), and *stage* it (sell only to the early weight when the consumed lots are gain-rich).

**Literature.** Same deferral literature as I3 (AQR Part 1-3; Dammon-Spatt-Zhang 2003 on the
lock-in effect); no external work studies an *exit* rule under a realisation-based,
loss-offset-only regime — this is a repo-specific mechanic, which is why it is ranked below
the two exposure ideas.

**Why it is not a repeat.** `intraday_stop_v1`, `exec_delay_v1`, `moc_execution_v1` move the
*fill convention* of all trades; `vote_hysteresis_v1` Y4 delays every exit by 3 sessions
(blindly, under E2, no tax state, and it fails 2017+); `deriv_vote_v1` and `breadth_vote_v1`
change *which series or functional form* decides the exit, not *when the resulting sale
fills*; `smooth_target_v1` averaged the target *continuously* (S1 won after tax, +0.35/+0.27/
+0.36 pp, but failed pre-tax non-inferiority −0.109 pp, the OOS window −0.42 pp and B2 at
53 %). No arm in the record has ever conditioned *whether* or *in which year* a full exit
fills on the tax state, and both shipped devices exempt exits by construction.

**Expected effect.** Pre-tax: the deferral arm ≈ 0 (a 10-session hold), the staging arm
**negative** (it is a beta bet against the exit signal: `tax_asym_v1/v2/v3`'s A2 held a stale
high weight for tax reasons and cost +4.41 pp of max DD). After tax **+0.02 … +0.15 pp** —
and the honest reason it is not higher is arithmetic, not modelling: exits are 1-3 per year,
so only ~4 % of them (10/252 sessions) can be moved across a year boundary. It survives
Turkish tax in principle (the mechanism is exactly H2's, which passed every gate for
+0.14/+0.13/+0.27 pp on 71 fills) but its *size* is set by how rarely an exit lands at the
year end.

**Data.** None new.

**Pre-registrable arms (3).**

| Arm | Rule |
|---|---|
| C0 | K exactly. |
| X1 | A fill that would be an **exit to zero** (or an early-path exit) in the last 10 December sessions is postponed to the first January decision; the target is re-evaluated at every close; never postponed twice. |
| X2 | A fill that would be an exit to zero **and** whose consumed FIFO lots have a USD gain fraction > 5 % sells only down to the current early-leg weight (`0.5 × vt`), with a hard cap of 21 sessions and an immediate full exit if the early conditions fail. |
| X3 | X1 + X2, X1's window tested first. |

**Risks / failure modes.** (i) January-gap risk, inherited from H2 unchanged in kind (its two
measured episodes on the R2 path: 2015-12→2016-01 **−0.70 pp**, 2024-12→2025-01 **−0.32 pp**);
the k_stage_b replay's three December episodes were all negative pre-tax (−$2,624, −$725,
−$2,375). (ii) X2 holds a position the vote says to exit — the DD gate and B1/B2 decide it.
(iii) **Deferring an exit can destroy a same-year loss offset.** Under R2 exits booked net
*losses* (−2.6 m TRY over 26.5 y versus 46.5 m of trim gain, `after_tax_tr_v1`); an exit moved
into a year with no gains makes that loss worthless. Every arm must report `neg_gain_by_reason`
by year, or the result will be read wrongly. (iv) Exit *sizes* differ by arm, so `turnover` is
not the right gate column — `sells/yr` and realised TRY gain are.

---

### I5 — Idle-cash yield under the tax code, not under BIL *(rank 5)*

**Hypothesis and mechanism.** The strategy's cash is idle 15 % of sessions
(`k_stage_b_v1`: in-market share 85.37 %) and, more importantly, `0.98 − w` of NAV is idle in
every session where the vol target binds below the cap; production earns **zero** on it. The
measured version of this idea used an ETF sleeve (BIL, `tbill_sweep_v1` S2_10: 14.88 % vs
14.67 % pre-tax, DD 46.0 % vs 49.6 %, bootstrap +0.18 pp, every pre-tax gate passed; the
simpler S1 variant is the one that failed its 2×-cost gate and shipped off in
`paper_config_v2`) and was
closed **after tax** (`r2_tbill_combo_v1`: $100k 9.90 / 14.34 / 22.68 % versus R2's
10.16 / 14.71 / 22.89 %; clean-bootstrap median **−1.08 pp**, the sleeve winning 1.5 % of
paths) because "the bill sleeve's realised gains are taxed early". A broker cash interest /
sweep channel is a **different instrument with a different tax base**: it produces interest
income, not capital-gains realisations, it is never bought or sold, and it has no tracking
error, no spread and no re-balancing band. Its after-tax treatment has never been measured
here — and the direction is genuinely ambiguous, because interest is taxed *annually with no
indexation and no deferral*, which is exactly the compounding penalty §3 fact 4 quantifies.

**Literature.** Nothing academic is needed for the mechanism; the relevant evidence is the
repo's own two studies above (pre-tax positive, after-tax negative for the ETF form) plus the
tax asymmetry: TR taxes foreign interest income progressively while capital gains are
indexation-protected and deferrable. Evidence strength: **medium (gross positive is certain;
after-tax sign is not).**

**Why it is not a repeat.** The closed study priced a *fund*: its fix was realisations, its
fees, and a 10 pp re-sizing band. This arm prices a *yield*, i.e. it answers "is the tax on
interest smaller than the tax on the sleeve's capital gains, and is the gross worth it?" —
a question whose answer was assumed rather than measured.

**Expected effect.** Pre-tax **+0.20 … +0.70 pp** (the repo's own estimate for a sweep at
DTB3 − 0.5 pp is +0.68 pp with no code; the BIL sleeve measured +0.21 pp). After tax
**+0.05 … +0.30 pp**: the interest tax at 15-40 % costs a fraction of the gross, and unlike
the sleeve there is no realisation to push a *capital-gains* year into a higher bracket.
It survives Turkish tax *better than the sleeve* precisely because it does not realise
anything — the channel that killed `r2_tbill_combo_v1` does not exist here.

**Data.** None new: DTB3 is already in the manifests; the sweep rate must be pre-registered as
a fixed spread to DTB3 (the engine has no broker-interest model, which is the study's one
piece of new code).

**Pre-registrable arms (3).**

| Arm | Rule |
|---|---|
| C0 | K exactly (idle cash earns 0). |
| Z1 | Idle cash earns `DTB3 − 0.50 pp`/yr, accrued daily, taxed as `menkul sermaye iradı` at the progressive tariff with no indexation and no exemption (the conservative reading), paid with the same March/July instalments. |
| Z2 | Same, with the 22,000 × scale(year) TRY exemption applied to interest (the `DIV_THRESHOLD_2026` analogue). |
| Z3 | Z1 with `DTB3 − 1.00 pp` (the pessimistic-spread plateau check). |

**Risks / failure modes.** (i) The tax treatment of broker interest for a TR-resident retail
account is an adviser question (withholding at source? declaration? the 22 k exemption?) — the
spread between Z1 and Z2 is the study's own sensitivity to it. (ii) DTB3 was ~0 % for
2009-2015, so the historical average gross is small; the *current* rate regime is what makes
it interesting, and the bootstrap is what decides. (iii) It is an account/execution change,
not a trading rule: if the broker offers no sweep, the idea dies on availability, and the
whole study is a day's work that should be pre-registered as such. (iv) The unliquidated
view gains more than the liquidated one (interest is paid out, capital gains are not).

## 5. Ideas considered and rejected

1. **Any further trim-deferral variant** — extending H2's window (tail 15/20 measured at
   +0.30/+0.30/+0.52 pp with the worst-ETF DD cost rising to +1.87 pp against a +2 pp bar),
   `monthly_defer_v1`'s 12 month-boundaries (A3 missed the pre-tax bootstrap by 0.05 pp), a
   Yİ-ÜFE-vs-CBRT-conditioned version, or `smooth_target_v1`'s target averaging: all act on
   the ~10 sells/yr that already exist, the band has removed the 49/yr that carried the small
   gain-heavy trims, indexation relief is near-saturated (fact 3), and the brief excludes
   them.
2. **Re-testing `tax_asym` A2 ("resize down only when target < 50 % of held") under K** —
   subsumed: A1u already blocks ≈1,289 down-sizes over 26.5 y, K differs from A1u on 19
   sessions in 26.5 years, and the corrected bootstrap closed A2 (B1 +0.096 pp/51.9 %,
   B2 −0.121 pp/48.1 %, max DD +4.41 pp).
3. **Exit-signal improvements (derivative votes, breadth/cross-index or global breadth, 2-of-4
   quorums, confirmation days, hysteresis)** — they all failed *pre-tax* (`deriv_vote_v1` C3:
   OOS median −2.14 pp, 2017+ fails; `breadth_vote_v1` B4: fails G3/G4/G7, after tax −1.00 pp
   since 2017; `vote_hysteresis_v1` Y1-Y3: −0.05 … −1.7 pp CAGR), and K cannot repair a
   pre-tax timing loss. Bonus: removing a false *exit* also removes its same-year **loss
   offset** (−2.6 m TRY over 26.5 y), so "fewer false exits" can *raise* the Turkish tax.
4. **Extra-trading overlays (intraday stops, dip buying, crash entry, inverse hedges)** — a
   stop or an exit **always fills** under A1u/H2, so K does not change their tax arithmetic;
   they failed pre-tax anyway (`intraday_stop_v1` T8: 2.9 stops/yr, DD 51.67 %; `tax_hedge_v1`
   −0.44/−0.45/−0.53 pp after tax, hedge losses do not offset long-leg gains).
5. **Leverage via instrument or structure (TQQQ at 2/3, 3x funds, 2.5x synthetics, margin,
   futures, TR-domiciled wrappers, same-index two-queue wrappers)** — `instrument_cost_v1`
   closed the implementable one (TQQQ: −0.77 pp 2017+, −0.48 pp after tax; no other 2x
   Nasdaq-100 fund qualifies); synthetic cells are outside the account's constraints
   (`leverage_frontier_v1`'s tail gate); and the realised gain of a resize is approximately
   *invariant* to the fund's leverage ratio (a Δβ trade sells Δβ/L of NAV at ≈L× the gain
   fraction), so higher leverage buys no tax relief; two-queue lot splitting needs lot-level
   sells Alpaca does not offer, and a TR wrapper is outside the Alpaca harness.
6. **Not an idea, in scope, but out of the bot's control:** the 2026 regularisation window
   (Law 7582, 4/6/2026 → 31/7/2027) and any step-up-at-death planning are owner-level
   legal/estate decisions on a real account; the PAPER bot cannot execute them, and they are
   not modelled anywhere in the engine.

## 6. Cross-cutting caveats

1. **The gate set still rejects tax-only arms by construction.** G5 is a pre-tax bootstrap
   whose median must not be negative; `gain_band_v2`'s A1 and `smooth_target_v1`'s S1 both
   won after tax in every window and failed only on a pre-tax/bootstrap cell of 3-6 bp. I1/I2
   are exposed to the *opposite* risk (a real pre-tax gain with a DD cost), so their
   pre-registration must state the interaction: **the DD budget is the currency, and the
   after-tax frontier is the price list.**
2. **After-tax deltas below ~0.1 pp are inside modelling uncertainty** (the Yİ-ÜFE month
   convention is worth 0.2-0.6 pp on some arms; the bracket deflation for the current year is
   an assumption). I4 and the Y-arm subset of I3 sit inside that band by construction — they
   are proposed as *measurements* whose likely answer is "no material effect", and the honest
   reason to run them is to close the axis rather than to expect a gain.
3. **The after-tax evidence base has no true out-of-sample** (the CBRT/Yİ-ÜFE calendar starts
   in 2000), so every idea here is tested on one inflation/FX regime plus B1-recent (a fixed
   2015-09 → 2026-09 calendar) and B2-flat (which carries losses forward and so prices a
   *different* tax system). Any study in this list should pre-register a **regime stress**: a
   synthetic calendar in which PPI growth and TRY depreciation are decoupled (the case that
   makes H2/K's deferral premium vanish or invert), because the deferral premium is a
   first-order function of that gap and it has never been varied.
4. **A known fidelity gap to close before any of these ships:** the production replay credits
   QLD's dividends but does not assess them as dividend income
   (`k_stage_b_v1`, "Not modelled, in every arm equally"), while the research engine does
   assess them with the 22,000 TRY threshold and a withholding credit. If the owner's real
   account is withheld against, the *level* of every after-tax number moves; the *rankings*
   in §4 are unlikely to (all arms carry similar average exposure), but the divisor is not
   currently nailed down.
5. **The measured bounds that any proposal must beat.** Since 2026-09, four waves of
   after-tax work produced one shipped rule (R2, +0.2 … +0.6 pp) and one candidate (H2,
   +0.13 … +0.27 pp); the largest after-tax effects on record (+0.5 … +1.8 pp,
   `gain_band_v2` A2/A3) came from holding *more* exposure. An idea claiming > 1 pp after tax
   is an exposure bet until proven otherwise.
6. **The regime**: PAPER only. Nothing here touches `src/`, `config/`, `tests/`, `scripts/`,
   `benchmarks/`, `data/` or the deployed K.

## 7. Status

Survey only. No screen, harness, pre-registration, arm or gate was run; no file outside
`docs/research/` was written; nothing was downloaded (three web pages and the literature
listed above were read only). The five proposals in §4 each carry every parameter fixed so
that a pre-registration can be committed before any simulation, as the protocol requires.
