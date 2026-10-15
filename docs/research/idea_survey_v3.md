# Untested research ideas for R2 after Turkish tax (idea survey v3)

> Evidence: tag `archive/research/idea-survey-v3-2026-10` (survey only; no backtests).

Date: 2026-10-15. Branch `research/idea_survey_v3`. This is a **survey, not a
backtest**: no screen was written, no arm was simulated, nothing in `src/`,
`config/`, `tests/`, `scripts/`, `benchmarks/` or `data/` was touched. All
numbers below are quoted from `docs/research/*.md` and from the cited papers;
every claim about what has *not* been tested is a statement about the repo's
record, verified by the reading described in §2.

## 1. Verdict

Five untested ideas, ranked by expected after-tax value per unit of risk, are
proposed in §5:

| # | Idea | Axis changed | Expected after-tax Δ vs R2 | Confidence it is untested |
|---|---|---|---|---|
| I1 | **Breadth / cross-index trend vote** | which series the votes read | +0.1 … +0.8 pp | high (no vote ever computed off QQQ) |
| I2 | **Derivative trend votes** (crossover / slope / rising midpoint) | vote functional form | +0.2 … +0.9 pp | high (regex-verified: no derivative rule anywhere) |
| I3 | **Partial-adjustment trading** (smooth the target-weight path) | trade path / turnover | +0.2 … +0.7 pp | high (no doc ever averages the target weight) |
| I4 | **Early-leg conviction ladder** | size of the early path | −0.1 … +0.4 pp (pre-tax +0.1 … +0.6) | medium-high (only *fixed* early weights tested) |
| I5 | **Loss-pool gain harvesting** (basis step-up against a same-year wasted loss) | tax structure | +0.02 … +0.30 pp | high (the mirror of the tested H1) |

Calibrate expectations against the last wave: R2 itself was the best arm found
in 60+ studies and is worth **+0.81 pp pre-tax, +0.63/+0.21/+0.20 pp after tax**
(`fast_reentry_v1`, `fast_reentry_v2`), with 95.3 % of its gain from three
rebound episodes. `gain_band_v2`'s A1 is the best after-tax arm on record
(+0.46/+0.44/+0.85 pp at $100k) and it still failed the pre-registered gates. A
candidate worth running this quarter has to be a **structural change to what or
when the bot holds**, not another timing overlay — §4 explains why the overlay
space is closed and why the four remaining open axes are the ones in §5.

## 2. Method

Read in full: `docs/research/INDEX.md` plus every report cited below (≈60
studies), and the code that fixes the boundaries —
`src/parayabuck/portfolio/equity_leveraged_trend.py` (votes, early path,
sizing) and the after-tax engine on `research/vt_level_tax_v1`
(`screen.py` lines 901–1597, copied as `tax_engine.py` into every later study).
Externally, only published literature was read (§5 cites the links); nothing
was downloaded except text.

Two exhaustive searches back the "untested" claims:

* **Derivative trend rules**: a repo-wide regex for
  `crossover|golden.?cross|sma_slope|slope_up|rising (midpoint|channel)|faster SMA|SMA 50 > SMA 200|sma_fast|ma_cross`
  over `docs/`, `src/`, `config/` returns **no matches**. Every trend vote ever
  tested is a price *level* compared with a level-derived threshold: `close vs
  SMA n` (n ∈ {40, 50, 60, 100, 150, 200, 250}), `252-session return > 0` (or a
  fixed 2 % hurdle), `close vs the 126-session close max/min midpoint`,
  `close vs close[-20]`, level ± 2 %/3 % exit buffers.
* **Target-path smoothing**: no doc ever averages, lags or partially adjusts
  the *target weight*. Only dead-band (`ewma_band_v1` B25/B50/B100,
  `gain_band_v1/v2`) and calendar batching (`monthly_defer_v1`,
  `tax_harvest_v2` H2) exist, and both are excluded by this brief.

## 3. The control and the after-tax facts that constrain every idea

**Control** = production R2 = `equity_leveraged_trend_v1` 1.2.0: hold QLD at
`min(1, 0.35/vol20)` capped at 0.98 while ≥2 of 3 votes hold on the QQQ close
(> SMA200; 252-session return > 0; > 126-session channel midpoint); while the
vote is off hold **half** that weight if QQQ > SMA50, vol20 < its 252-session
median and QQQ > its close 20 sessions ago, exiting below SMA50; decide at the
close, fill at the next open; 0.5 pp resize band. Reproduce before doing
anything: **15.48 % base / 14.90 % calibrated CAGR, max DD 46.76 %, turnover
5.99**, bootstrap Δ +0.683 pp (`fast_reentry_v1`; harness
`research/fast_reentry_v1/screen.py`, restore per protocol).

**After tax** (Turkish resident, progressive 2026 tariff, Yİ-ÜFE indexation,
FIFO, liquidation at the end, $100k; engine on `research/vt_level_tax_v1`):
R2 **10.16 / 14.71 / 22.89 %** on the 2000-03-09 / 2010-01-01 / 2017-01-03
windows; E2 9.53 / 14.50 / 22.69; clean bootstraps B1-recent **3.5155 %** and B2
**3.9678 %** median. R2's tax drag at $100k is **5.31 pp/yr** over
2000–2026 (15.47 → 10.16); the 2017 window costs ≈ 9 pp/yr (E2 31.24 → 22.14,
`after_tax_tr_v1`). **≈91 % of the realized taxable gain comes from the daily
vol-resize trims** (46.5 m of ≈50.9 m TRY); trend exits book net *losses*
(−2.6 m TRY over 26.5 y).

Five engine facts bound the idea space:

1. **FIFO is mandatory** (oldest lot consumed). Lot-level choice is not
   available.
2. **Indexation**: indexed cost = `cost × Yİ-ÜFE(month before sale) /
   Yİ-ÜFE(month before purchase)`, applied **only when the rise ≥ 10 %**; CBRT
   USD **buying** rate for both conversion legs.
3. **Progressive tariff**, 2026 brackets 15/20/27/35/40 % over 190 k/400 k/1 M/
   5.3 M TRY, deflated each year by `Yİ-ÜFE(Oct Y−1)/Yİ-ÜFE(Oct 2025)`.
4. **No loss carry-forward** in the progressive regime: `assess()` floors each
   year's net TRY gain at zero (`g = max(0.0, year_gain)`), so a net-loss year's
   loss is discarded. (The B2 flat model *does* carry losses forward — the two
   bootstraps therefore price different tax systems.)
5. **No wash-sale rule is modelled** anywhere.

Selection gates (as `ewma_band_v1` defines them, unchanged): pre-tax G1–G9
(CAGR, max DD, 2017+, Calmar, block-20 bootstrap seed 20261011, tail
P(DD > 70 %), two OOS sets, turnover ≤ 2× R2); after tax, beat R2 at $100k in
all three windows **and** B1-recent and B2 median Δ ≥ 0 with share > 0 ≥ 55 %.

## 4. What is closed, and why only four axes are left

Closed by measurement, with the arm that closed it:

| Family | Closed by |
|---|---|
| Trend horizon / aggregation (SMA 100–250 ensemble, 2-of-3 vs proportional vs single-signal, hysteresis, confirmation days, exit buffers, min-hold, staged thirds) | `trend_robustness_v2`, `signal_ensemble_v1`, `vote_hysteresis_v1`, `rule_variants_v1`, `e2_confirmation_v1` |
| Total-return votes, T-bill or 2 % hurdles | `tsm_tr_v1` (fails G4; edge = 2016 only) |
| Vol estimator / window / band (EWMA 0.94, vol10/40/60, max(vol20,vol60), semi-deviation, Parkinson/GK/YZ, frozen ratio, 2.5/5/10 pp bands) | `vol_window_v1`, `ewma_band_v1`, `range_vol_v1–v3` |
| Vol target level, leverage grid | `vt_level_tax_v1`, `leverage_frontier_v1`, `tax_asym_v1–v3` |
| Idle/risk-off asset (BIL, BOND10/IEF, GOLD, PSQ/QID, sector/country ETFs) | `tbill_sweep_v1`, `leverage_frontier_v1`, `riskoff_rotation_v1–v4`, `riskoff_after_tax_v1`, `gold_riskoff_oos_v1`, `shortside_v1` |
| Macro / credit / VIX filters (BAA10Y, 10y−3m curve, VIX level/spike/term structure, VXN sizing) | `credit_vote_v1`, `vix_filter_v1` |
| Execution (MOC, t+1 close, t+2 open, split fills, overnight hold, intraday stops) | `moc_execution_v1`, `exec_delay_v1`, `overnight_hold_v1`, `intraday_stop_v1` |
| Seasonality / calendar (May–Oct half, seasonal vol target, cash in September, turn-of-month) | `seasonal_overlay_v1`, `seasonal_placebo_v1` |
| Instrument choice (TQQQ at 2/3, other 2× funds, early leg in QQQ/SPY/SSO, QLD↔SSO rotation) | `instrument_cost_v1`, `early_qqq_v1`, `relstrength_v1` |
| Tax timing (year-end loss harvest, December deferral, monthly deferral, gain-scaled band, inverse hedge, T-bill sleeve) | `tax_harvest_v1/v2`, `monthly_defer_v1`, `gain_band_v1/v2`, `tax_hedge_v1`, `r2_tbill_combo_v1` |

The recurring failure mode is stated in `VolMacroExecHistory`'s summary of that
record: **everything that lowered turnover also lowered return (bands, longer
vol windows, monthly cadence), and everything that raised return raised
drawdown or turnover past G2/G9.** That leaves exactly four axes with no
measurement behind them — the *series* the votes read (I1), the *functional
form* of the votes (I2), the *path* by which the target is traded (I3), the
*size* of the early path (I4) — plus one tax-structure mechanism the engine
implies but no arm has used (I5).

## 5. Ranked ideas

Common mechanics for every arm below, so a pre-registration only has to state
the delta: daily decision on the completed close, fill at the next open,
`vol_target 0.35` computed on the **held** instrument (keep the harness's
existing convention: `fast_reentry_v1`'s screen, 2× synthetic), 0.98 cap,
0.5 pp resize band, 4 bps × m cost, R2's early path unchanged unless stated,
the same five OOS windows and 21-instrument set as `fast_reentry_v2`, and the
same after-tax engine at $100k with B1-recent and B2. Each idea lists ≤4 arms
with every parameter fixed; nothing is to be tuned after seeing a result.

---

### I1 — Breadth / cross-index trend vote  *(rank 1)*

**Hypothesis and mechanism.** A single index's 2-of-3 vote is taken off by
index-idiosyncratic noise (a single index crossing one of three levels), and
each false exit is a full round trip: sell QLD (usually into a vol spike, so the
realized loss can only offset *same-year* gains — there is no carry-forward),
then buy it back higher, which starts a fresh FIFO lot at a high basis so the
recovery appreciates from a taxable starting point. If the *exit* is confirmed
by breadth (several economically different
equity indices turning down together) instead of by one index's own level
crossings, the bot takes fewer false trips, holds its lots through noise, and
realises fewer TRY gains per year. Breadth entering the vote rather than
leaving it is the one piece of a diversification argument that a long-only
single-instrument strategy can actually use: the *signal* is diversified even
though the *position* cannot be.

**Literature.** Moskowitz, Ooi & Pedersen, *Time Series Momentum*, JFE 104(2)
2012 ([PDF](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf),
[SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463)): TSM is
significant **for a diversified portfolio across 58 markets** and "performs
best during extreme markets", while single-market results are noisier. Hurst,
Ooi & Pedersen, *A Century of Evidence on Trend-Following Investing*, JPM 2017
([PDF](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf)):
same conclusion over 1880–2016 and 67 markets. Faber, *A Quantitative Approach
to Tactical Asset Allocation* (2007/2013), multi-asset trend.
**Evidence strength: strong for diversified trend *portfolios*; medium for a
breadth *filter* on one long-only position**, because the mapping from
"diversify the bet" to "confirm the exit" is an extrapolation, not a tested
result. The repo's own vote agreement rates (SMA–TSM 86.7 %, TSM–DON 81.7 %,
all three 80.3 %) show how much of the vote is redundant *within* one index —
breadth has to add information that a second vote on the same index does not.

**Why it is not a repeat.** Closest tested: `trend_robustness_v2` M1/M2, which
**splits the position by index** (half QLD iff QQQ > SMA200, half SSO iff
SPY > SMA200) — SPY's trend never touches the QLD on/off decision — and
`relstrength_v1` Q1–Q4, where the second index only chooses *which instrument*
to hold. `credit_vote_v1` K2/K4 added a *macro* (BAA10Y/curve) vote. **No
study, in any doc, has ever computed an E2 vote on a non-QQQ series or used
breadth as the QLD gate** (`TrendSignalHistory`: every E2 vote in every doc and
test is on the signal instrument's own closes).

**Expected effect.** Pre-tax −0.5 … +0.6 pp CAGR, ΔDD −2 … +1 pp, sells/yr
−20 … −45 %. After tax **+0.1 … +0.8 pp** (three windows; B1/B2 medians
+0.0 … +0.3 pp). It survives Turkish tax for one reason: the tax base is
*realizations*, not returns — 91 % of it trims and exits. Fewer confirmed-exit
episodes mean fewer lots consumed and fewer same-year losses that the
progressive model throws away. It is the only idea here that plausibly improves
*both* pre-tax and after-tax outcomes at once.

**Data.** Free and complete. Votes need only closes: QQQ/^NDX (1999-03/1985),
SPY/^GSPC (1993-01/1950), IWM/^RUT (2000-05/1987), EFA (2001-08), plus OOS
indices ^IXIC (1971), ^FTSE (1984), ^GDAXI (1959), ^N225 (1965) from the Yahoo
chart API; DTB3 and the financing series from FRED CSV. All of these already
exist in `data/historical/` on the harness branches. Long OOS is *better* than
for the production rule, because index history starts decades before QQQ.

**Pre-registrable arms (4).** Panel = {QQQ, SPY, IWM} (IWM = ^RUT before
2000-05-26); each panel member gets the production votes (SMA 200, 252-session
return > 0, 126-session close-midpoint) on its own closes.

* **B1 — foreign votes.** The three votes are read on **SPY** instead of QQQ;
  majority 2-of-3; QLD stays the instrument; early path unchanged (still QQQ
  conditions).
* **B2 — breadth-confirmed exit.** On-state = QQQ's 2-of-3 as today. The
  position goes off only when QQQ's majority is broken **and** ≥2 of the 3
  panel indices have their own majority broken. Re-entry unchanged.
* **B3 — breadth-gated state.** On iff QQQ's majority **and** ≥2 of 3 panel
  members' majorities hold.
* **B4 — 5-vote majority.** Votes = {QQQ SMA200, QQQ TSM252, QQQ DON126,
  SPY TSM252, IWM TSM252}; hold on ≥3 of 5.

**Risks / failure modes.** (i) Breadth is ~0.9 correlated with QQQ, so the arm
can be pure lag: the 2000–02 and 2008 exits arrive later and only *deepen* DD
(`trend_robustness_v2` M1 cut 2008 but paid 1.2 pp/yr). (ii) The 21-instrument
gate is hostile — `relstrength_v1` lost 1.2–4.1 pp on 4/4 arms, and
`fast_reentry_v2`'s instrument gate is what nearly killed R2 (persistence gate
failed, marginal DD neighbour). (iii) Adding IWM/EFA adds exits in
2000–02 (fine) and false exits in 2011/2015/2018 (not fine). (iv) G4 (2017+)
is the single most likely gate to fail, since breadth confirmation delays every
re-entry.

---

### I2 — Derivative trend votes (crossover / slope / rising midpoint)  *(rank 2)*

**Hypothesis and mechanism.** A level vote flips whenever the *price* touches a
threshold, so it flips repeatedly in a flat market. A derivative vote
(`SMA50 > SMA200`, OLS slope of log price > 0, channel midpoint higher than it
was 21 sessions ago) flips only when the *trend is turning*, which is rarer.
Rarer flips ⇒ fewer resizes ⇒ fewer FIFO lots consumed ⇒ a smaller progressive
tax base, at a small cost in lag. This is the cleanest available way to buy
tax relief with signal *form* rather than with a dead zone, and the repo has
never tried it.

**Literature.** Brock, Lakonishok & LeBaron, *Simple Technical Trading Rules
and the Stochastic Properties of Stock Returns*, JF 1992
([DOI](https://doi.org/10.1111/j.1540-6261.1992.tb04681.x)) — MA rules
predicted DJIA returns 1897–1986; Sullivan, Timmermann & White,
*Data-Snooping, Technical Trading Rule Performance, and the Bootstrap*, JF 1999
([link](https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00163)) — under
White's Reality Check the best-rule profits are largely a data-mining artefact;
Bajgrowicz & Scaillet, *Technical trading revisited*, JFE 106(3) 2012
([abstract](https://ideas.repec.org/a/eee/jfinec/v106y2012i3p473-491.html)) —
with FDR control, "even in-sample, the performance is completely offset by the
introduction of low transaction costs". Zakamulin, *The real-life performance
of market timing with moving average and time-series momentum rules*, JAM 2014
([link](https://link.springer.com/article/10.1057/jam.2014.25)) and
*Market Timing with Moving Averages* (Springer 2017) — MA timing is a weighted
momentum rule and its reported superiority is largely in-sample. Marshall,
Nguyen & Visaltanachoti, *Time series momentum and moving average trading
rules*, Quantitative Finance 17(3) 2017
([link](https://www.tandfonline.com/doi/abs/10.1080/14697688.2016.1205209)) —
the two families are close cousins. Baltas & Kosowski, *Demystifying
Time-Series Momentum Strategies* / *Improving TSM Strategies*
([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2140091),
[CME](https://www.cmegroup.com/content/dam/cmegroup/education/files/improving-time-series-momentum-strategies.pdf))
— **"momentum trading signals generated by fitting a linear trend on the asset
price path maximise the out-of-sample performance by reducing portfolio
turnover by about two thirds, hence dominating other momentum trading signals"**.
**Evidence strength: strong for the turnover claim (which is what this survey
needs), contested for any alpha claim** — cite the negative results at the top
of the report and expect reviewers to.

**Why it is not a repeat.** Every vote in the record is a level comparison
(regex-verified, §2). Closest tested: `vote_hysteresis_v1` (asymmetric vote
*counts* “3/≤1”, “≥2/0”, 3-session confirmation — level rules with different
thresholds; Y1–Y4 all fail) and `rule_variants_v1` A2/A3 (exit buffers = level ±
2 %/3 %). Neither changes the *kind* of condition. `signal_ensemble_v1`'s
inputs are still `close > SMA`, `return > 0`, `close > midpoint`.

**Expected effect.** Pre-tax −0.4 … +0.5 pp (the downside case is real: a
crossover exits 2000 and 2008 later), sells/yr −30 … −60 %, after tax
**+0.2 … +0.9 pp**. Tax survival is direct: trims are where the tax is, and
this arm's entire measurable effect is fewer trims. The busiest trim months are
the high-vol ones, where the indexed basis is worst — removing those trades is
worth more than their count suggests.

**Data.** Free closes only (Yahoo chart API), same set as I1. Long OOS:
^IXIC 1971–99, ^FTSE 1984+, ^GDAXI 1959+, ^N225 1965+.

**Pre-registrable arms (4).**

* **C1 — golden cross.** Vote (a) becomes `SMA50 > SMA200`; votes (b), (c)
  unchanged; majority 2-of-3.
* **C2 — all derivative.** Votes = {`SMA50 > SMA200`, OLS slope of log closes
  over 252 sessions > 0, 126-session midpoint higher than 21 sessions ago};
  2-of-3.
* **C3 — mixed quorum.** Votes = {`close > SMA200`, `SMA50 > SMA200`, 252-session
  slope > 0, rising 126-session midpoint}; hold on ≥2 of 4.
* **C4 — risk-adjusted trend hurdle.** Vote (b) becomes
  `252-session return / (252-session realized vol) > 0.25` (a fixed, pre-declared
  "trend of at least a quarter of its own noise"); votes (a), (c) level as today;
  2-of-3. Rationale: Barroso & Santa-Clara, *Momentum has its moments*, JFE 2015
  ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2041429)) and
  Daniel & Moskowitz, *Momentum Crashes*, JFE 2016
  ([NBER](https://www.nber.org/papers/w20439)) — the payoff to trend/momentum is
  state-dependent in vol, so the *hurdle* should scale with noise. Note that the
  sign of a t-statistic equals the sign of the return, so C4 is only
  non-identical to production because the threshold is 0.25, not 0 — that
  threshold is a free parameter and must be declared fixed before the run.

**Risks / failure modes.** (i) Data-snooping: `SMA50 > SMA200` is the most-mined
rule in finance, and Bajgrowicz–Scaillet is the direct counter-evidence; the
honest prior is that C1's pre-tax edge is ≤ 0. (ii) Lag ⇒ deeper crash exits:
G2/G6 and the intraday-stop precedent (`intraday_stop_v1` T8: DD 51.67 %,
fails G1/G2) show how easily a late exit costs DD. (iii) Turnover reduction may
be less than hoped because the *trims* are driven by vol20, not by the vote:
only the vote-driven exits shrink; keep I3 in mind as the complementary arm.
(iv) G5 for C4, whose threshold is arbitrary and therefore in-sample-ish.

---

### I3 — Partial-adjustment trading (smooth the target-weight path)  *(rank 3)*

**Hypothesis and mechanism.** Turkish tax is charged on *realizations*, and
realizations scale with the total traded volume Σ|Δw|, not with the number of
decision points. Trading to a smoothed version of the target
(`w̄*_t = mean(w*_{t−4..t})`, or a fixed partial-adjustment step toward the
target) strictly shrinks Σ|Δw| while still reaching the target eventually, so
it cuts the taxed volume with no permanent under-hedging — unlike a dead band,
which never closes the last part of the gap. Exits to flat are exempt, so crash
protection is unchanged.

**Literature.** Baltas & Kosowski (above) — regression/smoothed trading rules
cut turnover by about two thirds with no Sharpe loss; Marshall et al. 2017
(bands/MA of the signal); Harvey, Hoyle, Korgaonkar, Rattray, Sargaison & van
Hemert, *The Impact of Volatility Targeting*, JPM 2018
([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)) — the
vol-targeting *signal* carries the effect, while rebalancing frequency/timing
matters less than practitioners assume; Moreira & Muir, *Volatility-Managed
Portfolios*, JF 72(4) 2017
([DOI](https://doi.org/10.1111/jofi.12513)) for the sizing rule itself.
**Evidence strength: medium** — good evidence that smoothing cuts turnover
cheaply at 0-few-bps cost, no evidence that it creates return.

**Why it is not a repeat.** **No doc ever averages or lags the target weight**
(verified). The closest tested mechanisms are estimate smoothing
(`ewma_band_v1` W1 EWMA λ 0.94 on the *variance* — the closest miss in the
record: +0.04 pp, DD 49.6 → 47.4 %, fails G3 and G5 by 0.13/0.07 pp),
asymmetric estimators (`vol_window_v1` VMAX = max(vol20, vol60), fails G1–G8),
dead bands (`ewma_band_v1` B25/B50/B100; `gain_band_v1/v2` — excluded by this
brief), and calendar batching (`monthly_defer_v1`, `tax_harvest_v2` H2 —
excluded). A smoothing lag is none of these: it is not a dead zone, not
gain-scaled, not calendar-conditioned, and it does reach the target.

**Expected effect.** Pre-tax −0.15 … +0.05 pp, sells/yr −30 … −50 %, ΔDD
−0.5 … +1.5 pp, after tax **+0.2 … +0.7 pp**. Tax survival is the clearest of
the five: less volume traded ⇒ fewer lots consumed ⇒ smaller annual TRY gain at
the same marginals; the effect is mechanical, not an exposure bet.

**Data.** None new.

**Pre-registrable arms (4).** Let `w*` be R2's target weight today.

* **S1 — 5-session average.** Trade to `mean(w*_{t−4..t})`; **exempt**: when the
  vote is off and the early path is inactive, go flat immediately at the next
  open.
* **S2 — 10-session average.** Same, K = 10.
* **S3 — partial adjustment.** `w ← w + 0.2·(w* − w)` each session, same flat
  exemption.
* **S4 — smooth the on-state only.** K = 5 on the on-state vol sizing; the
  early-leg weight `0.5·w` is applied unsmoothed.

All four keep the 0.5 pp band applied *after* smoothing (compare the smoothed
target, not `w*`, with the held weight).

**Risks / failure modes.** (i) **The economics are already measured and they are
negative pre-tax**: the dead-band arms of similar effective size cut sells/yr
from 27.4 to 13.7/9.3/5.6 and *lost* 0.10/0.20/0.50 pp CAGR because a stale
below-target weight delays re-levering into rallies (`ewma_band_v1` B25/B50/
B100, all fail G1). Smoothing has the same asymmetry, so expect G1 and G5 to be
binding and the 2017+ window (numerous fast rebounds) to be the worst.
(ii) An arm with a pre-tax Δ ≈ 0 will fail G5 *by construction* — see §6.1.
(iii) Smaller, more frequent sells could raise the *count* of tax events even
as the volume falls; the engine's per-lot indexed basis makes this
non-obvious, so the arm must report realized TRY gain, sells/yr and tax paid,
not just CAGR.

---

### I4 — Early-leg conviction ladder  *(rank 4)*

**Hypothesis and mechanism.** R2's edge is the early re-entry path, and the
path's size is a *constant* half weight (164 active sessions over 26.5 y,
+0.81 pp, 95.3 % of the gain from three episodes). A recovery that is strong on
a volatility-normalized measure deserves more than half the production weight;
a marginal one barely deserves the position. Laddering `g ∈ {0.5, 0.75, 1.0}`
on the recovery metric makes the path's exposure proportional to the evidence,
which is the same idea as Moreira–Muir's inverse-vol sizing, applied to the
early path's own signal rather than to the market's vol.

**Literature.** Moreira & Muir 2017 (scale exposure by the state variable that
predicts returns, here vol-normalized trend strength); Hurst–Ooi–Pedersen 2017
(trend strength and horizon robustness); Baltas & Kosowski (volatility scaling
is where much of TSM's realized Sharpe comes from). Internal evidence is the
stronger input: `fast_reentry_v1`'s **R1 = the same path at full weight earned
16.17 % vs R2's 15.48 % with a *lower* in-sample max DD (44.6 % vs 46.8 %)** and
failed only the OOS ΔDD gate (+4.60 pp ^IXIC, +5.13 pp ^FTSE against a +3 pp
limit). That bounds the ladder's upside at roughly the R1–R2 gap (+0.7 pp) and
its downside at R1's OOS DD. **Evidence strength: medium-low externally;
medium internally (a measured upper bound exists).**

**Why it is not a repeat.** `fast_reentry_v2` tested six *one-parameter*
neighbours of R2 — SMA 40/60, vol-median window 126/504, early weight **1/3 and
2/3** — all *fixed* weights (all six were return-positive; the arm failed only
because the SMA-40 neighbour breached the DD gate) and `fast_reentry_v1` tested
R4 (crash-gated entry). A **state-dependent** early weight has never been
tested. `rule_variants_v1` D5 ("leverage by trend strength") is the closest
conceptually and it is *not* this arm: D5 tapered the **on-state** weight using
a QLD/SMA200 distance band, lost 4.4 pp with 121 switches and 20.7×/yr
turnover. The ladder here touches only the early path, uses a vol-normalized
metric, and is three discrete steps.

**Expected effect.** Pre-tax +0.1 … +0.6 pp, ΔDD −1 … +3 pp, sells/yr
+0 … +10 %, after tax **−0.1 … +0.4 pp**. Tax survival is the *weakest* of the
five: the arm adds exposure rather than removing realizations, so it only wins
after tax if the extra pre-tax return exceeds the extra tax at the 27–35 %
marginal rate it pushes into. State that plainly in the pre-registration, and
let the after-tax windows be the binding test.

**Data.** None new (closes; `vol20` on the held instrument).

**Pre-registrable arms (4).** Metric `m = (close/SMA50 − 1) / (vol20/√252)`,
evaluated on the decision close while the early path is active. Tercile cuts
fixed at `m < 0.5`, `0.5 ≤ m < 1.5`, `m ≥ 1.5`.

* **E1 — three-step ladder.** `g = 0.5 / 0.75 / 1.0` on those terciles.
* **E2 — two-step ladder.** `g = 0.5` for `m < 1.0`, else `1.0`.
* **E3 — continuous.** `g = clip(m/2, 0.5, 1.0)`.
* **E4 — momentum metric.** Same three steps, but keyed on
  `m20 = 20-session return / (vol20·√20)`, with the same 0.5/1.5 cuts.

Exits are unchanged in all four (below SMA50 → flat at the next open), and the
early path still ends the moment E2's vote turns back on. Add a plateau check:
swapping the cuts to (0.25/1.0) and (1.0/2.0) must not flip the sign of the
result, otherwise the terciles are curve-fitted.

**Risks / failure modes.** (i) The path is a three-episode bet (2001-10 +8.58 pp,
2023-01 +6.75 pp, 2002-11 +3.45 pp), so a ladder is being fitted to three
events; leave-one-episode-out must be reported (`fast_reentry_v2` shows R2
survives removing any one of them: +0.45/+0.52/+0.65 pp). (ii) OOS DD is R1's
documented failure mode; the +3 pp limit is likely to bind. (iii) Stepping the
ladder trades inside the early path, raising the sell count and possibly the
realized gain — the after-tax test is not a formality here. (iv) If E1–E4 all
land between R2 and R1 on CAGR and above R2 on DD, the honest reading is "the
early weight is a lever, not a signal", and the answer is to leave 0.5 alone.

---

### I5 — Loss-pool gain harvesting (basis step-up against a same-year wasted loss)  *(rank 5)*

**Hypothesis and mechanism.** The progressive engine computes tax on
`max(0, year_gain)`: a year whose realized net TRY gain is negative gets no
deduction *and no carry-forward* — the loss is destroyed. But a loss is still
usable *inside its own year*: any gain realized in that calendar year is netted
against it tax-free. So once year-to-date realized net TRY gain is negative,
deliberately realizing gains **up to that amount** (sell and immediately re-buy
the same quantity, at the same close, with no wash-sale rule in the model)
converts a destroyed loss into a **permanent cost-basis step-up**: the new lot
is written at today's price × today's CBRT rate, and every future realization
of that quantity is taxed on a smaller gain. Unlike the tested deferral family,
this does not move a sale between years; it *creates* a sale that the tax code
prices at zero.

**Literature.** The tax-aware literature is strong for the *loss* direction —
Constantinides, *Capital market equilibrium with personal tax*, Econometrica
1983; Berkin & Ye, *Tax Management, Loss Harvesting, and HIFO Accounting*, FAJ
59(4) 2003
([link](https://www.tandfonline.com/doi/abs/10.2469/faj.v59.n4.2548));
Chaudhuri, Burnham & Lo, *An Empirical Evaluation of Tax-Loss-Harvesting
Alpha*, FAJ 76(3) 2020
([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3351382)). The
US "0 % bracket gain harvesting" is the practitioner analogue. **Evidence
strength: the *mechanism* is textbook; the *size* is unmeasured in any regime
without loss carry-forward**, and the Turkish asymmetry (indexation plus
double-digit depreciation means foreign lots are rarely at an indexed
*Turkish* loss — `tax_harvest_v1` H1 fired 7 times in 26.5 y and only once
realized a loss) means the pool may be small or empty. Measure it first:
report `Σ_years |min(0, year_gain)|`, the number of loss years, and the size of
the pool relative to NAV before running any arm.

**Why it is not a repeat.** `tax_harvest_v1` H1/H1b harvest *losses* at
year-end and failed **because it lowered the basis of lots that were already at
an indexed gain** (−0.87 … −2.26 pp after tax); this arm does the exact
opposite and only when an offsetting pool exists. `tax_harvest_v2` H2,
`monthly_defer_v1` and `gain_band_v1/v2` all change *when or whether a resize
happens* — the deferral/band family this brief excludes. `idea_survey_v2` I4
targeted the same wasted-loss channel but through a **beta-matched substitute
(SSO/QQQ)** swap (never run; legal risk); L1–L4 keep the *same instrument* and
trade only against a loss that already exists.

**Expected effect.** Pre-tax ≈ −0.01 pp (spread); after tax **+0.02 … +0.30 pp**
(the range is wide because it is bounded by the pool, which must be measured);
2 extra trades per firing. Tax survival: it *is* the tax lever — but note (iii)
below, which caps it at a *discounted* rate difference, not a full rate.

**Data.** None new. (The engine's CBRT USD/TRY, Yİ-ÜFE and instrument series
are already in `data/historical/` in the `vt_level_tax_v1` manifest.)

**Pre-registrable arms (4).** Definition of the pool: at each decision close,
`pool_t = −min(0, YTD realized net TRY gain)`; the *indexed* gain of selling
`q` units now is computed with the engine's own formula.

* **L1 — year-end single fire.** On the last session of each year, if
  `pool > 0` and the FIFO-front lot is at an indexed TRY gain, sell-and-rebuy
  the smallest quantity that brings the year's net to ≈ 0, capped at 25 % of
  NAV.
* **L2 — first-crossing fire.** Same trade, triggered at the first session
  after `pool` turns positive, once per year, 25 % NAV cap.
* **L3 — L2 with a 10 % NAV cap** (turnover-bounded variant).
* **L4 — L1 with no cap** (report-only upper bound; not gated, so the size of
  the channel is visible).

Fill convention: round trip inside the decision close (the model has no
wash-sale rule); a variant that waits one session and buys at the next open is
the conservative alternative if the adviser objects, and it is what production
would actually do.

**Risks / failure modes.** (i) **"No wash-sale rule" is a property of the
*model*, not a verified legal fact** — the model has never contained one
(`tax_harvest_v1` caveats, `idea_survey_v2` §1.2 item 5). A written mali
müşavir opinion is a precondition, same as for the blocked lot-selection idea.
(ii) The pool may be empty: in a TRY base inflated by depreciation, almost no
year is net-negative, which is why H1's mirror failed. (iii) The step-up
protects appreciation that would otherwise be realized *later*, possibly at a
*lower* marginal rate (brackets deflate with Yİ-ÜFE), so the benefit is a
discounted rate difference. (iv) G9 (turnover ≤ 2× R2) is not at risk from two
trades a year, but the *sells/yr* report must exclude these or the gate reads
wrongly. (v) A large recycle into a single day's price creates a one-day
execution exposure (mitigated by the 25 % cap).

## 6. Cross-cutting caveats

1. **The gate set rejects tax-only arms by construction.** G5 is a pre-tax
   block-20 bootstrap whose median must not be negative. `gain_band_v2`'s A1 is
   the proof: it wins after tax in every window (+0.46/+0.44/+0.85 pp at $100k),
   passes B1 (+0.23 pp, 75.5 %) and B2 (+0.09 pp, 61.9 %), and fails **only**
   G5, with a median of +0.0002 pp (base) / −0.004 pp (calibrated) — i.e.
   symmetric noise. Any arm in this survey whose effect is measured in
   realizations (I3, I5) will land in the same place unless the pre-registration
   declares, *in advance*, a two-tier rule: exposure-neutral arms are gated
   pre-tax on non-inferiority (e.g. median Δ ≥ −0.05 pp and turnover ≤ 2× R2)
   and on the after-tax gates. Decide this before seeing any result.
2. **Production sizes on the held ETF's own realized volatility**
   (`src/parayabuck/portfolio/equity_leveraged_trend.py:257-260`,
   `context.closes(self.hold_symbol)`), while the research harness builds a
   synthetic 2× from QQQ and the protocol describes `min(1, 0.35/vol20)`
   without naming the series. Every arm must keep whatever convention the
   control harness uses, so the tested change is the only difference; if an arm
   is later promoted, re-run it with QLD's real bars (`instrument_cost_v1` did
   this for TQQQ and the pre/post gap was material).
3. **After-tax deltas below ~0.1 pp are inside modelling uncertainty.** The
   Yİ-ÜFE month convention ("month before purchase/sale") is adviser practice
   with low-medium confidence, and the alternative convention was measured at
   0.2–0.6 pp for E2. The brackets deflate by `Yİ-ÜFE(Oct Y−1)`, which is an
   assumption about a not-yet-published index for the current year.
4. **Turnover savings are worth almost nothing pre-tax at 4 bps × m.** The
   band arms bought ≈0.03–0.08 pp/yr of cost and gave back 0.10–0.50 pp of
   stale-target drag. The *only* reason to cut trades is the tax — so every
   turnover arm must be judged on realized TRY gains and tax paid, not on
   commission.
5. **Turkish tax law moved in 2026** (Law 7582 of 21/5/2026 added GVK mükerrer
   20/D; a foreign-asset regularisation window runs 4/6/2026 → 31/7/2027 —
   [GİB](https://cdn.gib.gov.tr/api/gibportal-file/file/getFile?objectKey=DUYURU%2FUNIVERSAL%2F2026%2F7582_bazi_varliklarin_ekonomiye_kazandirilmasi.pdf)).
   The engine encodes the 2026 rule set and reproduces the published controls,
   but the owner should confirm with the adviser that the two premises I5 rests
   on — per-calendar-year netting with no carry-forward, and Yİ-ÜFE indexation
   with the 10 % threshold — are still the law before spending research time on
   it.
6. **Expect small numbers.** Since 2026-09, four waves of after-tax arms have
   produced one shipped rule (R2, +0.2 … +0.6 pp after tax) and one candidate
   (H2 December deferral, +0.13 … +0.27 pp), while the largest measured after-tax
   effects (+0.5 … +1.8 pp from `gain_band_v2` A2/A3) came from holding *more*
   exposure, not from tax. An idea that claims more than ~+1 pp after tax
   should be treated as an exposure bet until proven otherwise.

## 7. Ideas considered and rejected

1. **Macro / credit / labour "economic trend" de-risk veto** (AQR *Economic
   Trend* 2023, *A Half Century of Macro Momentum* 2017; free long series:
   BAMLH0A0HYM2 1996+, ICSA 1967+, UNRATE 1948+, NFCI 1971+) — rejected: the
   closest test, `credit_vote_v1` K1 (hold only while BAA10Y ≤ its 200-session
   SMA), cost **5.2 pp of CAGR** (9.45 % vs 14.67 %) and −2.56 pp on the
   bootstrap, and every late-firing filter in the repo (X1–X3, K1–K4) loses.
   The series are free and long — the idea is not data-blocked, it is
   evidence-blocked.
2. **Weekly or monthly decision cadence** — already rejected in
   `idea_survey_v2` §7.2, and the repo bounds the pre-tax cost of every
   cadence-like arm at 0.07–0.60 pp (bands, `monthly_defer_v1`); it also
   blunts the fast early path that is R2's entire edge.
3. **Tax-lot selection (HIFO / lowest-indexed-gain / specific-lot)** —
   `idea_survey_v2` I1 (never run, blocked): Alpaca has no lot-level sells and
   FIFO is mandatory in the model; sub-variant "hold the position in two
   same-index wrappers to get two FIFO queues" fails on the same blocker plus
   `instrument_cost_v1`'s finding that no second 2× Nasdaq-100 fund qualifies.
4. **Beta-matched substitute loss capture (sell QLD into SSO/QQQ, swap back)** —
   `idea_survey_v2` I4, blocked on legal/anti-abuse risk, not re-proposed here.
5. **Conviction- or strength-scaled *on-state* leverage** (t-statistic sizing,
   trend-strength bands) — `rule_variants_v1` D5 (leverage by trend strength)
   lost 4.4 pp with 20.7×/yr turnover, and `signal_ensemble_v1` EP (exposure
   proportional to the vote count) missed G4 and G8 by 0.007 pp; the vol
   scaling that does work is already production.

Also considered and set aside, one line each: **conditional vol targeting**
("adjust only in the extremes", Bongaerts–Kang–van Dijk, FAJ 2020 —
[link](https://www.tandfonline.com/doi/abs/10.1080/0015198X.2020.1790853)) as a
*vol-target* arm rather than a vote arm, because the dead-band arms already
bound its payoff at < 0 pp; a **managed-futures / trend-ETF sleeve** (DBMF
2019+, KMLM 2020+) because there is no free 2000–2026 series and no pre-2000
OOS; **options or futures overlays** because the account type excludes them;
**intraday-range channel votes** as too close to a parameter change of an
already-tested vote.

## 8. Status

Survey only — no arm was run, no gate was evaluated, no file outside
`docs/research/` was written. The five arms in §5 are proposals for
pre-registration, each with its parameters fixed so that the pre-registration
can be committed before any simulation, as the protocol requires.
