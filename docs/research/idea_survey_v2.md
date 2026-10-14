# Idea survey v2 (2026-10-14): five untested ways to improve R2 after Turkish tax

> Evidence for this report (`research/idea_survey_v2/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/idea-survey-v2-2026-10`.

**This is an idea survey, not a backtest.** No screen was written, no non-control arm
was run. Every number quoted below is from an existing report in `docs/research/`
(cited inline) or from the literature (linked). Everything proposed here is
**untested** in this repository.

**Verdict.** Ranked by expected after-tax value per unit of research risk:

| # | Idea | Expected after-tax Δ vs R2 ($100k) | Cost of being wrong |
|---|---|---|---|
| **I1** | Tax-lot selection (sell the lowest indexed-taxable-gain lots, not FIFO) | **+0.3 … +1.5 pp/yr** | zero pre-tax; gated on a legal/broker question |
| **I2** | Defer every downward vol resize across the Yİ-ÜFE **month** boundary (H2 generalised to 12×/yr) | **+0.3 … +0.8 pp/yr** | small (tracking error, month-boundary gap risk) |
| **I3** | After-tax no-trade region: band width scaled by embedded taxable gain and the year's bracket budget | **+0.1 … +0.6 pp/yr** | medium (band arms have failed before) |
| **I4** | Same-year loss capture by swapping into a beta-matched substitute (keeps exposure, banks the loss) | **+0.1 … +0.6 pp/yr** | medium (index-basis risk; anti-abuse risk) |
| **I5** | Deliver the half-weight early-re-entry leg in QQQ instead of half-QLD (financing/expense at equal beta) | **+0.2 … +0.5 pp/yr** | low (one extra ETF, tax-neutral) |

The first two are the only ones where the mechanism is already *proven in this repo's
own tax engine* (I2) or is a pure accounting choice with zero pre-tax cost (I1).
I3–I5 are smaller, cheaper to test, and independent of I1/I2.

---

## 1. Scope and method

- Inputs read: `docs/research/INDEX.md` and all 54 current reports, summarised through
  five read-only scouts (tax; signal/vol/leverage; risk-off/instrument; execution/filters;
  pre-prune archive register `archive/research/pre-prune-2026-09-30`). Literature: AQR,
  *Journal of Wealth Management*, *Financial Analysts Journal*, *RFS/JFE*, JFQA, SSRN,
  practitioner guides (links in each idea).
- Not done (by task definition): no pre-registration of these ideas, no harness, no
  `screen.py`, no simulation, no gates run. Each idea below carries an arm spec that a
  later study can pre-register verbatim.
- Account facts taken as fixed: one instrument today (QLD, 2× Nasdaq-100, 0.95%/yr,
  daily reset), plus cash; a few liquid US ETFs may be added if justified; **no options,
  no futures, no margin, no shorting**; Alpaca order types market/limit/stop/MOC/MOO;
  one decision per day at the close, fill at the next open; 0.5 pp resize band; total
  cap 0.98.

### 1.1 The production rule (R2 = `equity_leveraged_trend_v1` 1.2.0)

Hold QLD at `w = min(1, 0.35 / vol20)` capped at 0.98 while ≥ 2 of 3 votes hold on the
QQQ close (close > SMA200; 252-session return > 0; close > 126-session channel midpoint).
While the vote is off, hold `0.5 w` if QQQ > SMA50, vol20 < its 252-session median and
close > close 20 sessions ago; exit the early position below SMA50. Reference numbers
(main window 2000-03-09 → 2026-09-29, m = 2, base financing): **15.4732% pre-tax CAGR**,
max DD **46.76%**, turnover **5.99/yr**, ~62 trades and ~30 sells per year
(`fast_reentry_v1/v2`, `tax_harvest_v2`). After tax at $100k, liquidated:
**10.16 / 14.71 / 22.89%** on the 2000 / 2010 / 2017 windows; E2 (its control):
14.67% / 14.10% pre-tax, DD 49.56%, 9.53 / 14.50 / 22.69% after tax.

### 1.2 The Turkish tax facts that decide the ranking

From `after_tax_tr_v1`, `vt_level_tax_v1`, `tax_harvest_v1/v2`, `r2_tbill_combo_v1`,
`tax_asym_v3`, `instrument_cost_v1`, `riskoff_after_tax_v1`:

1. **Gains are taxed in TRY** at the CBRT buying rate of the *trade date*; TRY
   depreciation is taxed even when the USD trade lost money.
2. **Yİ-ÜFE indexation** (GVK mük. 81) rescales the TRY cost basis when Yİ-ÜFE rose
   ≥ 10% between the month before purchase and the month before sale. It removes
   10–16 m TL of gain at $100k and keeps 2000–2013 tax near zero. **The indexation
   factor is monthly** — it steps once per calendar month.
3. **Progressive tariff** (2026: 15/20/27/35/40% up to 190k/400k/1.0m/5.3m TL), with
   brackets deflated to each year by the previous October's Yİ-ÜFE.
4. **Within-year netting only**: a net loss cannot be carried forward or offset other
   income. Trend-exit losses (−2.6 m TRY over 26.5 years) therefore offset trim gains
   **only in the year they occur**.
5. **FIFO** lot matching, applied by analogy (confidence low–medium); **HIFO/specific-lot
   identification is explicitly "not modelled — ask an adviser"**; no wash-sale or
   anti-abuse rule is modelled anywhere.
6. **~91% of R2's realised taxable gain comes from the daily vol-resize trims**
   (46.5 m of ~50.9 m TRY): each trim sells the *oldest* lots, i.e. the largest gains.
   Under FIFO every trim is the most expensive possible trim.
7. **Tax drag ≈ 5.1 pp/yr at $100k over 2000–2026 and 9.1 pp/yr since 2017.**
8. **The measured bound on "just trade less" is small.** Constant resize bands of
   5/10 pp cut trades 3–5× yet move after-tax CAGR by only −0.19 … +0.21 pp
   (`after_tax_tr_v1`); EWMA vol, vote hysteresis, wider bands and no-resize all lose
   pre-tax (`ewma_band_v1`, `vol_window_v1`, `vote_hysteresis_v1`).
   What *did* work was not "trade less" but "**trade later**": H2 (postpone a December
   downward resize into January) gains +0.142 / +0.128 / +0.266 pp and its longer
   windows are monotone in the deferral length — tail 20: **+0.332 / +0.437 / +0.769 pp**,
   at a small drawdown cost (`tax_harvest_v2`).
9. **A second instrument must clear ≈ 0.6–0.8 pp of pre-tax edge to add anything after
   tax** (BIL sweep +0.21 pp pre-tax → −0.09 after; L2 +0.82 → +0.04), because a new leg
   creates its own realisations (`r2_tbill_combo_v1`, `riskoff_after_tax_v1`).
10. **Primary convention is "liquidated"**: every lot is sold and taxed at the window end,
    so deferral earns only (a) indexation, (b) bracket spreading, (c) time value. The
    clean after-tax bootstraps are B1-recent (real 2015-09→2026-09 TRY/inflation calendar,
    market returns resampled) and B2 (flat 20%, losses carried forward). **Any deferral
    idea must be judged on the liquidated view and B1**; B2 charges no inflation and so
    structurally understates indexation-based deferral.

### 1.3 Why the ranking looks like this

The repo has spent ~60 studies on *what to hold and when to be in or out*. Almost
everything else was mined out: trend variants, vol estimators (10/20/40/60, EWMA, range,
semi-deviation, TimesFM), sizing grids (0.30–0.50, no target), leverage (1–3×),
bands, hysteresis, credit/curve/VIX/VXN filters, dip buying, crash entries, seasonality,
execution timing (close, next close, t+2, MOC), overnight, inverse funds, relative
strength, gold/bond/T-bill risk-off, crypto, instrument substitution. The remaining
high-value surface is not the signal — it is the **realisation engine**: which lot, on
which day, in which tax year. R2's tax cost is a *scheduling and accounting* problem
(91% of it is trims), and the current scheduler and accountant are the naive ones
(trim on the first close that breaches 0.5 pp; sell the oldest lot).

---

## 2. I1 — Tax-lot selection: sell the lowest indexed-taxable-gain lots

**Hypothesis and mechanism.** R2 decides *how many units* to sell at each close; the
*identity* of the lots sold is a free accounting choice. FIFO forces every trim to
realise the oldest, most appreciated lots. Choosing instead the lots with the smallest
*indexed taxable TRY gain per unit sold* ("LGF": `proceeds·FX_sale − basis·FX_buy·Idx`,
ascending) defers gain, maximises the Yİ-ÜFE credit actually used, keeps the appreciated
lots compounding, and spreads realised gain over more tax years (lower marginal bracket,
more indexation months). LGF is **not** the same as the industry-standard HIFO: under
Turkish indexation "highest basis" ≠ "lowest taxable gain", because the oldest lots carry
the largest indexation credit. The engine must rank by the indexed number, not the basis.

**Literature and evidence strength.**
- Dammon, Spatt & Zhang, *Capital Gains Taxes and Portfolio Rebalancing* (TIAA-CREF
  Institute, 2003, [PDF](https://personal.utdallas.edu/~hxz054000/RD%20no[1].%20754.pdf);
  underlying [RFS 2001](https://www.jstor.org/stable/2696767)): the optimal policy is to
  realise losses and defer gains; the incentive to trade declines with the size of the
  embedded gain; with a large gain the investor is "locked in". **Theory, strong.**
- Berkin & Ye, *Tax Management, Loss Harvesting, and HIFO Accounting*, FAJ 59(4) 2003 —
  the canonical HIFO result. **Practitioner-standard, strong.**
- Krasner & Sosner, *Loss Harvesting or Gain Deferral?*, JWM 27(1) 2024
  ([PDF](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQRJWMSum24LossHarvestingorGainDeferral.pdf)):
  across tax-aware strategies the tax benefit comes **from deferring gain realisation,
  not from harvesting more losses**. **Empirical simulation, strong.**
- AQR, *The Enduring Appeal of Gain Deferral* parts
  [1](https://www.aqr.com/Insights/Research/Tax-Aware-Investing/The-Enduring-Appeal-of-Gain-Deferral-Part-1),
  [2](https://www.aqr.com/Insights/Research/Tax-Aware-Investing/The-Enduring-Appeal-of-Gain-Deferral-Part-2):
  post-liquidation wealth is higher when gains are deferred, at any horizon. **Strong.**
- Mechanism of specific-lot election: [IRS Pub. 550](https://www.irs.gov/publications/p550),
  [Investopedia](https://www.investopedia.com/terms/s/specific-share-identification.asp).
  **Practical, US-centric.**

**Why it is not a repeat.** No study in the repo has ever changed the lot rule. FIFO is
*assumed* in `after_tax_tr_v1` ("selling the highest-cost lots first on trims (HIFO) would
target the same tax directly. It is not modelled, because FIFO is the practice assumed
here"), and all later tax studies inherited that assumption. The closest work is
`tax_harvest_v1` H1, which *harvests* lots (a trading decision, and it failed); I1 does not
trade differently at all — it only chooses which lots a given trade consumes.

**Expected effect size and tax survival.** The prize scales with the 91% trim share. In a
steady uptrend, trims fire every few days, so the lots a LGF rule would sell are only days
or weeks old and their *indexed taxable gain* is near zero, versus a FIFO trim that sells
multi-year lots with the largest gains. Halving or better the trim gain is plausible on
the realised path; the liquidated convention then recaptures the rest, leaving
indexation + bracket spreading + one extra year of compounding. Honest range
**+0.3 … +1.5 pp/yr after tax at $100k**; the *unliquidated* view gains more (and
the no-indexation sensitivity must be reported, since indexation is the amplifier).
Pre-tax impact is exactly zero on the NAV path except for the tax-funding sales
(0.6 m of 50.9 m TRY), so there is no pre-tax gate to fail.

**Data needed / availability.** None new: CBRT USD/TRY, Yİ-ÜFE monthly and the bracket
chain already exist in `archive/research/vt-level-tax-v1-2026-10`
(`data/historical/after_tax_tr_v1_usdtry_cbrt.csv`, `..._yiufe.csv`), against the R2
harness on `research/fast_reentry_v1`. Availability: 2000-01→2026-10 (CBRT bulletins,
6,676 rows) and Yİ-ÜFE from 2003 with a pre-2003 OECD/FRED PPI proxy. **There is no OOS
before 2000 for the Turkish tax side** — the honest OOS substitutes are B1-recent
(fixed 2015–26 calendar), B1-early (2005/2006–2016) and B2 (flat 20%).

**Pre-registrable arms (all parameters fixed).**

| Arm | Rule |
|---|---|
| C0 | R2, FIFO (must reproduce 15.4732% pre-tax and 10.16 / 14.71 / 22.89% after tax) |
| A1 | **LGF**: at every sale, consume lots in ascending order of `proceeds·FX_sale − basis·FX_buy·Idx_ratio` per unit; the sold quantity is unchanged from R2 |
| A2 | **HIFO** (highest TRY cost per unit, indexation ignored) — the field standard |
| A3 | **Average cost** (moving-average basis, DSZ's tractable proxy) — the fallback if specific-lot election is not available |

Gates: pre-tax identical (delta ≤ 0.01 pp on CAGR/DD, since only the lot book changes);
after tax Δ > 0 vs C0 in **all three** windows at $100k, plus B1-recent and B2 with
median Δ ≥ 0 and share of paths > 0 ≥ **55%**; report the unliquidated view, flat-27% and
no-indexation columns; G9 turnover unchanged (no new trades). A precondition gate: a
written opinion from a Turkish mali müşavir on lot election for a foreign broker, and a
broker that accepts it.

**Risks / failure modes.** (i) **Feasibility is the whole risk**: the repo's FIFO
assumption is low–medium confidence and Alpaca does not expose lot-level sells — selling
specific lots has been an open feature request since 2020
([forum](https://forum.alpaca.markets/t/selling-specific-lots-of-shares/956),
[GitHub #213](https://github.com/alpacahq/Alpaca-API/issues/213)); the amendable record
may instead be the Turkish return's per-lot schedule, which needs an adviser. (ii) A
general anti-abuse/wash-sale-like rule could void the election (none is modelled).
(iii) The liquidated view charges the deferred tax anyway, so the measured gain may come
out smaller than intuition. (iv) LGF creates many small, recently-acquired lots, which
mildly increases paperwork and the funding-sale problem (Alpaca reports wash-sale-adjusted
lots for US purposes regardless — [Alpaca wash sale](https://alpaca.markets/support/wash-sale)).

---

## 3. I2 — Defer every downward resize across the Yİ-ÜFE month boundary

**Hypothesis and mechanism.** The indexation factor is
`Yİ-ÜFE(month before sale) / Yİ-ÜFE(month before purchase)`. Deferring a *downward*
resize from the last sessions of month M to the first sessions of month M+1 adds one month
of PPI to the numerator applied to the **whole indexed cost basis of every lot sold**,
mechanically shrinking the taxable TRY gain, and (when M is December) also moves the
realisation into the next tax year, which spreads it across brackets. H2 proved the
mechanism for the one calendar boundary this repo tested (December→January, tails of
5/10/15/20 sessions): +0.006 / +0.142 / +0.295 / +0.332 pp on the 2000 window, monotone in
the deferral length, with the worst-ETF drawdown cost rising from +0.22 pp (tail 5) to
+1.87 pp (tail 20) against a +2 pp bar (`tax_harvest_v2`). The **12 month-boundaries per
year** are all free opportunities of the same kind, and no study has used them.

**Literature and evidence strength.** Indexation/deferral arithmetic is repo-internal
`after_tax_tr_v1` + GVK mük. 81 (confidence medium: no özelge for foreign ETFs, month
convention disputed). General deferral value: AQR parts 1–3 above (**strong**); systematic
deferral of gains as the dominant tax lever, Krasner & Sosner 2024 (**strong**). There is
no external literature on Turkish indexation timing — this idea is **repo-specific and
judgemental**, which is why it is ranked second rather than first.

**Why it is not a repeat.** Closest study: `tax_harvest_v1/v2` H2. Difference: H2 defers
resizes **only in the last 10 December sessions**, for the year-boundary reason. A1 defers
**every** downward resize across the next month boundary (12 openings/yr, deferral length
1–21 sessions, mean ≈ 10), i.e. it tests the *monthly indexation step* rather than the
calendar year. H2 is kept as an arm to show the generalisation is not worse than the
specific case.

**Expected effect size and tax survival.** Same family as H2, more events. If the effect
is driven by the indexation step (≈ 1 month of PPI on the indexed basis; 2–4% of basis in
recent TR years, ~1% of sale proceeds at a 40–60% basis ratio), a 12×-per-year version
should land **+0.3 … +0.8 pp/yr** after tax, bounded by the H2_20 ceiling and by the
pre-tax cost of tracking the vol target with a stale weight. Pure timing: no new trades,
same instruments, tax only moves later.

**Data needed / availability.** None new: the daily calendar plus the Yİ-ÜFE monthly
series already on the `vt_level_tax_v1` lineage (2003-01→ CBRT *Üretici Fiyatları*,
pre-2003 OECD/FRED Turkish manufacturing PPI proxy), USD/TRY from 2000-01. Pre-2000 OOS
does not exist for the tax side (same limit as every after-tax study here): B1-recent,
B1-early, B2.

**Pre-registrable arms (fixed).**

| Arm | Rule |
|---|---|
| C0 | R2 (0.5 pp band, next-open fills) |
| A1 | a downward vol resize whose fill would fall in month M is postponed to the **first session of M+1** (max 21 sessions) and filled at that open; **exits and up-resizes are never postponed** |
| A2 | A1, but only when the deferred sale would net an indexed TRY gain > 0 |
| A3 | H2-equivalent (defer only the December remainder) — anchor, not a candidate |
| A4 | A1 plus a hard override: execute immediately if \|target − held\| > 10 pp, or on the session the E2 vote turns off |

Gates: pre-tax G1–G9 vs R2 (m = 2 both financing models, seed 20261011, block-20
5,000-path bootstrap, six OOS windows), G9 turnover ≤ 2× R2; after tax: Δ > 0 vs R2 in
all three windows at $100k **and** B1-recent and B2 median Δ ≥ 0 with share > 0 ≥ 55%;
plus the `tax_harvest_v2` stress set (21 ETFs, 2× costs, January-gap years).

**Risks / failure modes.** (i) **Month-boundary gap risk** — H2's worst cell is a real
January 2016 gap (−0.70 pp); deferring across 12 boundaries multiplies that exposure, and
the 2001/2008/2020 sell-offs started mid-month. A4 exists for that. (ii) Deferring a
downsize into a fast decline leaves the position above its vol target exactly when risk
is highest — the DD column is the gate that decides this idea. (iii) The effect could be
a property of the tested windows (this repo has been bitten before: `tax_asym_v2`'s
+1.05 pp became −0.26 pp on the corrected control). (iv) If the indexation floor (10%)
is not met for recent lots, waiting buys nothing.

---

## 4. I3 — After-tax no-trade region: gain-scaled band + bracket budget

**Hypothesis and mechanism.** Replace the constant 0.5 pp band with a threshold that
grows with (a) the **embedded indexed taxable gain of the lots the sale would consume**
(DSZ: the incentive to re-diversify falls with the size of the gain) and (b) the **year's
accumulated realised gain relative to the deflated bracket ceilings** (the marginal rate
is 15% at the bottom and 40% above 5.3m TL; a TRY realised late in a high-gain year is
more expensive than the same TRY realised in a lean year). Buys/up-resizes keep the 0.5 pp
band because they realise nothing, so the no-trade region is **asymmetric** — the exact
qualitative prediction of DSZ and the opposite of the symmetric bands this repo tested.

**Literature and evidence strength.** Dammon, Spatt & Zhang 2003 (above) — the size of
the embedded gain *is* the state variable of the rebalancing rule. **Theory, strong.**
Constant-threshold practice (`pkctrading` [threshold-band
note](https://pkctrading.com/blog/tax-aware-rebalancing), Investopedia tax lots) —
**practitioner, weak but consistent**. Constantinides 1983 (NBER w1176,
[PDF](https://www.nber.org/system/files/working_papers/w1176/w1176.pdf)) — the tax-timing
option; realise losses, defer gains.

**Why it is not a repeat.** Closest studies: `ewma_band_v1` B25/B50/B100 (constant 2.5/5/
10 pp bands, pre-tax) and `after_tax_tr_v1`'s band table (same bands, after tax). Both use
a **constant** threshold; I3 makes the threshold a function of the lot's indexed gain and
the year's bracket position, and applies it to downsizes only. `tax_asym_v1/v2/v3`'s A2
("resize down only when target < 50% of held weight") is the closest *asymmetric* idea and
it is closed — but A2's trigger is a fixed fraction of the held weight, with no gain state
and no bracket state, and it failed on resampled paths, not on the realised one.

**Expected effect size and tax survival.** Bounded above by the measured band effect
(+0.21 pp) and below by nothing (it can lose). The state dependence is the whole bet:
the trims that hurt most are those that sell multi-year, largely-indexed lots, and those
are exactly the ones a gain-scaled band suppresses. Range **+0.1 … +0.6 pp/yr**, plus the
DD risk of holding a stale weight. Pre-tax cost is the tracking error of the wider band,
which must stay under ~0.3 pp or the idea is dominated by simply not doing it.

**Data needed / availability.** None new: the deflated bracket chain, Yİ-ÜFE and USD/TRY
are already computed in the `vt_level_tax_v1` engine; the lot book is internal.

**Pre-registrable arms (fixed).**

| Arm | Rule |
|---|---|
| C0 | R2 (0.5 pp symmetric band) |
| A1 | gain-scaled band: `B_down` = 0.5 pp if the sale's indexed taxable gain ≤ 1% of sold value, 3 pp if ≤ 5%, 8 pp if > 5%; `B_up` = 0.5 pp always |
| A2 | A1 + bracket budget: while YTD net realised TRY gain ≥ the year's deflated 27% ceiling, discretionary downsizes are postponed to the next tax year; hard override at \|target − held\| > 15 pp or on the vote turning off |
| A3 | A2 with the hard override at 10 pp (tail-protection plateau neighbour) |
| A4 | A2 with thresholds 1.5% / 7% (threshold plateau neighbour) |

Gates: as I2 (pre-tax G1–G9, G9 ≤ 2× R2 turnover; after tax all three windows + B1 + B2,
median Δ ≥ 0, share > 0 ≥ 55%); plus the plateau rule — A2 counts only if A3/A4 behave,
because the earlier band wave showed how easily a single cell decides the verdict.

**Risks / failure modes.** (i) The band widens precisely when the position is most
appreciated, so exposure drifts above target through uptrends — the DD and 2017+ gates
will show it. (ii) Every constant band tested so far lost pre-tax; the state dependence
must be worth more than that loss. (iii) The bracket budget defers into January, i.e.
inherits H2's gap exposure. (iv) Multiple-testing risk is high (threshold grid × years).

---

## 5. I4 — Same-year loss capture with a beta-matched substitute

**Hypothesis and mechanism.** Turkish losses are worth something **only in the calendar
year they are realised** (netting only, no carry-forward), and most years have taxable
trim gains to offset. R2 already realises losses on trend exits (−2.6 m TRY over 26.5
years), but only *after* the vote flips. The untested step is to realise an embedded loss
*while keeping the exposure*: sell the position at a loss and immediately buy a
beta-matched substitute (SSO, 2× S&P 500, at the same weight; or QQQ at ~1× for half the
beta), bank the loss against the year's trim gains, and swap back when the substitute's
indexed gain turns positive or after a fixed minimum hold. There is no wash-sale rule in
the modelled Turkish code. This is direct indexing applied to a single position with two
ETFs.

**Literature and evidence strength.** Chaudhuri, Burnham & Lo, *An Empirical Evaluation of
Tax-Loss-Harvesting Alpha*, FAJ 76(3) 2020
([PDF](https://dspace.mit.edu/server/api/core/bitstreams/e46342c4-9307-4235-88bf-636bf9b0b00e/content)):
HIFO-based loss harvesting yields **1.10%/yr** tax alpha on US large caps 1926–2018,
**0.85%/yr** under a wash-sale constraint, **0.51%/yr** when long and short rates are
equalised at 35%; highly variable (0.57–2.29% across sub-periods), largest in volatile,
low-return regimes. **Empirical, strong — but US rates and a broad portfolio.** Counter
weight: Krasner & Sosner 2024 and AQR's deferral series argue deferral, not harvesting,
is where the money is, and that harvesting resets basis. Dammon/Spatt/Zhang's endnote is
explicit that "wash sale rules are not very restrictive in practice given the existence of
close substitute securities".

**Why it is not a repeat.** Closest: `tax_hedge_v1` (buying SQQQ/QID *instead of* trimming
winners — 168 sells/yr, fails every window) and `tax_harvest_v1` H1 (year-end
sell-and-rebuy of the same book — fails because indexed lots are rarely at a loss and the
reset destroys the indexation credit). I4 is neither: it keeps the *same sign and size* of
exposure, it triggers on an *existing embedded loss* rather than a calendar date, and it
never resets the whole book.

**Expected effect size and tax survival.** The loss pool is smaller than a 500-stock
direct-indexing portfolio, and the substitute adds index-basis risk plus ~8 bps per round
trip. Bounded well below the Chaudhuri 1.1%: **+0.1 … +0.6 pp/yr**. It survives Turkish
tax exactly because the offset is *within* the year (no carry-forward is the reason the
timing matters) and the spread is 27–40% now versus a lower or deferred rate later.

**Data needed / availability.** SSO daily total returns (2006-06→present), QQQ
(1999-03→present) and QLD (2006-06→present) — all free via the Yahoo chart API; before
2006-06 SSO must be synthesised as daily-reset 2× SPX − 0.89% − financing, exactly as
`trend_robustness_v2`/`relstrength_v1` already do for other indices. New files go to
`data/historical/idea_survey_v2_*.csv` with source URL, range and SHA-256 in
`research/idea_survey_v2/DATA_MANIFEST.json` if the idea is ever run. Long OOS: none for
the tax side (same 2000-01 floor), plus B1/B2.

**Pre-registrable arms (fixed).**

| Arm | Rule |
|---|---|
| C0 | R2 in QLD only |
| A1 | on any session where the aggregate indexed taxable gain of the held QLD book < **−2%** of its value **and** YTD net realised TRY gain > 0: sell the book, buy SSO at the same weight at the next open; hold SSO until its aggregate indexed gain > 0 or **31 sessions** pass, then swap back to QLD at the next open |
| A2 | A1 with a **−5%** trigger (threshold plateau neighbour) |
| A3 | A1 + a relative-index guard: act only while the 63-session NDX − SPX return gap is < 3 pp (bounds the basis risk of a QLD↔SSO swap) |
| A4 | A1 restricted to the early-re-entry leg only (the leg that is most often underwater) |

Gates: as I2, plus G9 turnover ≤ 2× R2 (this arm trades more), plus a legal precondition
(no anti-abuse rule catching a same-day substitute purchase for a Turkish resident) and
the 2000–2002 window reported separately, because that is where NDX and SPX diverge most.

**Risks / failure modes.** (i) **No second eligible 2× Nasdaq-100 ETF exists**
(`instrument_cost_v1`): QLDX 404s, QQQU/QQUP/EQQL/QQXL too young, QQQX is a closed-end
fund, RYVYX a mutual fund — so the swap is always NDX↔SPX, an index-basis bet during
exactly the drawdowns that trigger it (2000–02: NDX −83% vs SPX −49%). (ii) The loss
resets basis, so the future gain is larger: it is a rate/timing arbitrage, not free money,
and it is worth nothing in a year with no realised gains. (iii) The "no wash-sale rule"
assumption is unverified for a Turkish resident's foreign-broker trades. (iv) Extra
turnover charges pre-tax cost at 4 bps × m, m = 2.

---

## 6. I5 — Deliver the half-weight early leg in QQQ, not half-QLD

**Hypothesis and mechanism.** The early-re-entry leg holds `0.5·w` of QLD, i.e. `0.5·w`
of capital plus `0.5·w` of embedded borrowing (DTB3 + spread, modelled as +0.75%/yr) plus
0.95%/yr expense, to obtain approximately `w` units of Nasdaq-100 beta. Holding
`min(0.98, w)` of **QQQ** obtains the same daily beta with no embedded borrow and a 0.20%/yr
expense, and the account's idle cash earns 0% anyway, so the extra capital is free in this
accounting. While the leg is on, the saving is
`0.5·(DTB3 + 0.75%) + 0.5·(0.95% − 0.20%) ≈ 2.0–2.8 pp/yr` of NAV in a 3–4% bill
environment; at the leg's duty cycle (the early path is active on only part of the
sessions — 164 active sessions are reported in the 126-session replay checks of
`fast_reentry_integration_v1`) that is roughly **+0.2 … +0.5 pp/yr**, and the whole saving
accrues on the *unlevered* half of the position, i.e. it is tax-neutral: the leg is
entered and exited on the same sessions, so the realisation pattern is unchanged.

**Literature and evidence strength.** No academic paper needed: this is the arithmetic of
daily-reset ETFs (SEC DERA,
[Distribution of Leveraged ETF Returns](https://www.sec.gov/files/DERA_LETF_Economics_Note_Nov2019.pdf);
Avellaneda & Zhang,
[path dependence](https://math.nyu.edu/inmemoriam/avellaneda//LETFSlides.pdf)) applied to
this repo's own financing model (`leverage_frontier_v1` calibrated = DTB3 + 0.75%×(L−1);
`instrument_cost_v1` measured real QLD at −0.55 pp vs base and +0.44 pp vs calibrated).
**Evidence: mechanical, high confidence on the sign, medium on the size.**

**Why it is not a repeat.** Closest: `instrument_cost_v1` tested the *opposite* direction —
**TQQQ at 2/3 weight** instead of QLD, which *adds* inside-fund borrowing
(0.333·DTB3 = 0.53 pp/yr, 0.83 pp since 2017) and lost 0.48 pp after tax in 2017+; and
`relstrength_v1` rotated QLD↔SSO on relative strength, a *signal* change. Nobody has
tested replacing a **sub-1.0 beta target** with the unlevered fund, where the saving is
strictly positive.

**Expected effect size and tax survival.** +0.2 … +0.5 pp/yr, nearly all of it pre-tax
(survives tax trivially — a pre-tax gain with no extra realisations). The tax offset to
watch: QQQ yields ~0.55% vs QLD's ~0.23% distribution, and in the model QLD distributions
are treated as capital gain while QQQ dividends are 20% US-withheld and taxable in Turkey
above the 22,000 TL threshold — a drag of ~0.1 pp/yr on the leg's notional, an order
smaller than the saving and already inside `instrument_cost_v1`'s error band.

**Data needed / availability.** QQQ daily total returns 1999-03→present (free, Yahoo chart
API); real QLD 2006-06→present for the cross-check arm. Both fit the existing harness's
synthetic-or-real fund switch used by `instrument_cost_v1`.

**Pre-registrable arms (fixed).**

| Arm | Rule |
|---|---|
| C0 | R2 in QLD only (early leg = 0.5·w QLD) |
| A1 | early leg = `min(0.98, w)` of **QQQ**; every other rule (SMA50, vol20 < 252-session median, 20-session momentum, exit below SMA50, E2 override, 0.5 pp band) unchanged |
| A2 | A1 + residual QLD so the beta is exact when `w > 0.98` (hold 0.98 QQQ + (w−0.98)/2 QLD) |
| A3 | A1 with **SSO** instead of QQQ (instrument sensitivity, and the S&P-vs-Nasdaq basis cost of the cheaper-but-different replacement) |
| A4 | A1 evaluated on **real** QLD/QQQ prices 2011-01→2026-09 as a real-fund confirmation of the synthetic result |

Gates: full pre-tax G1–G9 (this arm changes the pre-tax path, so it must clear the
pre-tax gates to count), G9 turnover ≤ 2× R2; then the after-tax three windows and B1/B2
(median Δ ≥ 0, share > 0 ≥ 55%) to prove the pre-tax gain is not eaten by the dividend
treatment.

**Risks / failure modes.** (i) **Reset convexity**: `0.5·QLD ≠ 1.0·QQQ` over multi-day
holds — QLD's daily reset gives more than 2× in a trending recovery, so the substitution
gives up part of the early path's tail upside; the 2023-01-20 episode (which supplies most
of R2's gain) is the test case. (ii) Two instruments = two lot books, and the swap back to
QLD at each leg's end is an extra realisation. (iii) The `min(0.98, w)` cap makes the beta
2% short in calm markets. (iv) 0.5 pp band and the cap must be re-checked for a second
instrument (cap 0.98 still binds).

---

## 7. Ideas considered and rejected

1. **Pre-FOMC announcement-window exposure** (be long into scheduled FOMC announcements).
   Lucca & Moench's drift
   ([NY Fed SR 512](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr512.pdf))
   averaged +49 bps in the 24 h before announcements 1994–2011, but it **disappeared after
   2015** — Kurov, Wolfe & Gilbert, *The disappearing pre-FOMC announcement drift*, FRL 40
   (2020) ([PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7525326/)): post-ZLB mean +0.09%
   with VIX as the driver; and it would add ~8 round trips/yr of realisations.
2. **Weekly/monthly decision cadence** (5× fewer decisions ⇒ fewer realisations).
   Zakamulin & Giner, *Optimal Trading Frequency for Trend-Following Strategies* (2025,
   [Springer](https://link.springer.com/chapter/10.1007/978-3-031-90907-8_14)): frequency choice
   has *limited* impact on performance, so the pre-tax cost would be small — but this repo's own
   band arms already bound the after-tax gain from fewer resizes at ±0.2 pp while costing
   0.07–0.60 pp pre-tax, and cadence would also blunt the early-re-entry path that is R2's
   entire gain.
3. **Option-implied uncertainty sizing (VVIX / SKEW / vol-of-vol).** Baltussen, van Bekkum
   & van der Grient, *Unknown Unknowns*, JFQA 53(4) 2018
   ([Cambridge](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/unknown-unknowns-uncertainty-about-risk-and-stock-returns/6E0E98349D20C1DCF67F3A0452361B80)):
   high uncertainty about risk predicts low returns. But `vix_filter_v1` killed every
   option-implied filter in the same family (level, term structure, VXN sizing all lose
   0.7–3.2 pp), such filters fire *late* (in the crash, after the trend has already exited),
   and VVIX history starts 2007 with no pre-2000 SKEW analogue in the harness.
4. **Static "tax-deferred core" (a never-traded QQQ/VOO sleeve financed by deleveraging R2).**
   `after_tax_tr_v1` already prices both endpoints: QQQ buy-and-hold beats E2 after tax by
   1.27 pp in 2010–2026 at $100k but loses by 2.79 pp over 2000–2026, and a never-traded core
   is a permanent return drag rather than an improvement to R2; deferred tax is only saved
   until the window's liquidation.
5. **Intraday stop-loss plus higher leverage.** Kaminski & Lo's stop-loss result does not
   survive this repo's evidence: the panic-exit family (`vix_filter_v1` VIX > 30,
   `crash_entry_v1`, `dip_overlay_v1` P3) all lose, the harness has no intraday fill model
   (decide-at-close/fill-next-open is the contract), and stops add realisations to a
   strategy whose tax problem is already 91% trims.

---

## 8. Method notes, limits, amendments

- **Ranking is a judgement**, not a result: I1/I2 are ranked first because the mechanism is
  already established in this repo's tax engine (I2) or is a pure accounting choice with no
  pre-tax cost (I1); I3–I5 are cheaper but smaller.
- **The after-tax stage has no true out-of-sample**: the TRY/Yİ-ÜFE calendar starts in 2000
  and every clean bootstrap holds the calendar fixed (B1-recent 2015–2026, B1-early
  2005/2006–2016, B2 flat with no currency). This is the binding limit on all five ideas —
  none of them can be validated in a TRY regime that has not happened. Report the fixed
  calendar, the flat-rate rows and the no-indexation row for each arm.
- **Two legal questions gate I1 and I4**: whether specific-lot identification is accepted
  for a foreign-broker account of a Turkish resident (FIFO is currently assumed by analogy,
  confidence low–medium) and whether any anti-abuse provision catches a same-day substitute
  purchase. Both need a mali müşavir before any implementation, and the simulated numbers
  should be filed in the *unliquidated* view too, since that is what a "never sell the last
  lot" outcome looks like.
- **The liquidated convention systematically penalises deferral ideas** (it charges the
  deferred tax at the window end) and **B2 systematically understates indexation** (no
  currency, no inflation). I1/I2/I3 must therefore be read on the liquidated + B1 pair, with
  B2 as a lower bound.
- **Not tax advice.** Every rule cited here is the model in `after_tax_tr_v1` (§Tax rules
  modelled), which states its own confidence levels and requires an adviser.
- **Amendments.** None — no data was consumed for these ideas, so nothing was changed after
  seeing results. If a later study runs any arm above, record its deviations here.

**Evidence.** No code, results or data were produced: this document is the whole artefact.
It was committed on branch `research/idea_survey_v2`.
