# Literature review: information discreteness and continuous information

This review freezes a pre-outcome interpretation of the literature. It does
not use local candidate outcomes.

## Primary definition

Da, Gurun, and Warachka define information discreteness (ID) from signed daily
returns over the prior twelve months after skipping the most recent month. Let
`PRET` be the cumulative return over that 12-1 interval. Their published
measure is:

```text
ID = sign(PRET) * (% negative daily returns - % positive daily returns)
```

The positive, negative, and zero-return proportions share one denominator, so
`%positive + %negative + %zero = 1`. Zero-return days therefore remain in the
denominator but are neither positive nor negative. The original construction
uses signed daily returns (CRSP-style simple returns), not a log-return
substitute. The skip month is applied to the cumulative-return interval and to
the daily sign counts.

For positive `PRET`, lower (more negative) ID means more continuous information:
many positive sessions produce a negative `%negative - %positive`. Higher ID
means more discrete information: a few positive jumps alongside negative or
zero sessions push the measure upward. A positive winner with all 252 sessions
positive has ID `-1`. In this design `PRET` is an eligibility gate, not an
additional weight in the continuity ranking.

Primary source: [Da, Gurun & Warachka, “Frog in the Pan: Continuous Information and Momentum” (author PDF)](https://business.uq.edu.au/sites/default/files/events/files/mitch-warachka-paper.pdf), especially the definition, direction, skip-month discussion, and robustness sections. The bibliographic record is also available from [RePEc](https://ideas.repec.org/a/oup/rfinst/v27y2014i7p2171-2218..html).

## Study-by-study evidence

### Da, Zhi; Gurun, Umit G.; Warachka, Mitch (2014)

- **Sample and universe:** Main US equity panel, 1976–2007, with a longer historical return extension back to 1927; the paper studies monthly cross-sectional momentum portfolios.
- **Past-return definition:** Cumulative return over the prior 12 months excluding the most recent month (`12-1`).
- **Information-discreteness definition:** Signed daily simple-return counts, `sign(PRET) × (%negative - %positive)`, with zero days in the common denominator.
- **Formation horizon:** Twelve months of daily observations after a one-month skip; the paper also reports 6-1-6 style checks and time-weighted variants.
- **Skip convention:** The most recent month is skipped both to avoid short-term order-pressure reversal and to define the 12-1 signal.
- **Holding period:** The principal portfolio evidence uses six-month holding periods; persistence is examined beyond that horizon.
- **Long/short structure:** Cross-sectional winner/loser momentum portfolios and ID-sorted portfolios; the paper is not a long-only six-slot implementation.
- **Cost evidence:** The main contribution is return/mechanism evidence rather than a complete live cost engine. Turnover and a time-weighted ID are discussed as implementation considerations; transaction costs are not a substitute for the later project-specific cost test.
- **Momentum controls:** The paper separates continuous/discrete paths from conventional momentum and reports residual-ID evidence after controls including turnover, size, analyst coverage, book-to-market, idiosyncratic volatility, institutional ownership, and return consistency.
- **Mechanism:** Gradual information is underreacted to; discrete, attention-attracting information is incorporated faster. The continuous component persists materially longer.
- **ParayaBuck relevance:** This is the canonical formula and direction. It justifies a winner gate followed by an ascending raw-ID continuity sort, while making clear that the local six-name implementation is an implementation test, not an exact replication.

### Lim, Wang & Yao (2018 working paper; later journal version)

- **Sample and universe:** Nearly one hundred years of US individual-stock returns, 1927–2017, with size, horizon, and international/asset-class extensions in the study.
- **Past-return definition:** Time-series momentum defined from an asset’s own prior return sign; ID conditions the strength of continuation.
- **Information-discreteness definition:** The Da-Gurun-Warachka daily sign-count measure, with the most-continuous group represented by the low raw-ID direction.
- **Formation horizon:** Monthly time-series momentum formations over multiple lookbacks; the paper reports 12-1-style comparisons and cross-sectional double sorts.
- **Skip convention:** The reported momentum constructions include the standard recent-month exclusion where specified; exact variants differ by test and are not imported into this design beyond the canonical 12-1 choice.
- **Holding period:** Monthly/longer holding conventions across horizons.
- **Long/short structure:** Time-series long/short portfolios and independent/sequential ID sorts; not a long-only six-name account.
- **Cost evidence:** The study is primarily pre-cost return evidence; it does not replace the project’s current spread, slippage, and regulatory-fee test.
- **Momentum controls:** It explicitly studies the common component with conventional cross-sectional momentum and finds that dollar-neutral TSMOM can retain a distinct component, while non-dollar-neutral TSMOM is more closely subsumed.
- **Mechanism:** Continuity conditions time-series momentum and is consistent with gradual diffusion rather than a purely cross-sectional label.
- **ParayaBuck relevance:** Supports testing continuity as a separate, auditable path-quality alternative while requiring a strong displacement gate against V3.

Source: [Time-Series Momentum in Nearly 100 Years of Stock Returns](https://scispace.com/pdf/time-series-momentum-in-nearly-100-years-of-stock-returns-3170r26uxs.pdf).

### Lin, Ko, Chen & Chu (2016), “Information discreteness, price limits and earnings momentum”

- **Sample and universe:** Taiwan equities, 1989–2014.
- **Past-return definition:** Earnings-momentum setting rather than a direct US price-momentum replication.
- **Information-discreteness definition:** A continuity/discreteness measure adapted to the daily price path and price-limit environment.
- **Formation horizon:** The paper’s earnings-momentum event windows differ from the US 12-1 construction; no horizon is transplanted here.
- **Skip convention:** Event-study conventions vary; the paper does not establish a replacement for the canonical US one-month skip.
- **Holding period:** Post-earnings momentum horizons.
- **Long/short structure:** Portfolio/event comparisons, not a six-name long-only account.
- **Cost evidence:** Not a complete implementation-cost study.
- **Momentum controls:** Earnings momentum and price-limit/attention conditions are central controls.
- **Mechanism:** Price limits make information attention-grabbing; continuity changes the degree of earnings-momentum underreaction.
- **ParayaBuck relevance:** Provides out-of-market evidence that continuity can interact with information diffusion, while warning that market microstructure and event definitions are not interchangeable.

Source: [ScienceDirect article record](https://www.sciencedirect.com/science/article/pii/S0927538X16300099).

### Huang, Lee, Song & Xiang (2021/2022), “A Frog in Every Pan: Information Discreteness and the Lead-lag Returns Puzzle”

- **Sample and universe:** Linked-firm return relations; the study examines economically connected firms and multiple linkage definitions.
- **Past-return definition:** Lead-lag/co-momentum return relations rather than a direct replacement for 12-1 single-stock formation.
- **Information-discreteness definition:** Continuity versus discreteness of the information-bearing price path, extending the Frog-in-the-Pan mechanism to linked firms.
- **Formation horizon:** Study-specific lead-lag windows; not imported as a local parameter sweep.
- **Skip convention:** Not the canonical 12-1 single-stock signal; no substitute is inferred.
- **Holding period:** Lead-lag horizons around linked-firm information diffusion.
- **Long/short structure:** Relation portfolios and co-momentum tests, not six-name long-only implementation.
- **Cost evidence:** Mechanism paper rather than a full account-level cost audit.
- **Momentum controls:** Results are examined across shared analyst coverage and several linkages, helping distinguish diffusion from a single conventional-momentum label.
- **Mechanism:** Continuous information is underreacted to while discrete information is incorporated more quickly; investor attention/cognitive triggering is part of the explanation.
- **ParayaBuck relevance:** Strengthens the behavioral mechanism and the red-team warning that “continuity” must be tested against existing momentum controls rather than assumed independent.

Source: [HKU repository record](https://hub.hku.hk/handle/10722/308370).

### Doukas & McKnight (2005), “European Momentum Strategies, Information Diffusion, and Investor Conservatism”

- **Sample and universe:** Thirteen European markets, 1988–2001.
- **Past-return definition:** Conventional international momentum formation; not the canonical ID formula.
- **Information-discreteness definition:** Investor-conservatism/information-diffusion mechanism rather than daily sign-count ID.
- **Formation horizon:** European momentum horizons reported by the paper; no horizon is imported into this design.
- **Skip convention:** Study-specific momentum convention.
- **Holding period:** Study-specific European momentum holding periods.
- **Long/short structure:** International momentum portfolios.
- **Cost evidence:** Out-of-sample international evidence is useful context, but it is not a current cost-engine validation.
- **Momentum controls:** The mechanism is tested against conventional momentum interpretations.
- **Mechanism:** Gradual dissemination and investor conservatism can create continuation.
- **ParayaBuck relevance:** Supports the behavioral plausibility of information diffusion outside the US, but does not determine the local ID formula.

Source: [Wiley article record](https://onlinelibrary.wiley.com/doi/10.1111/j.1354-7798.2005.00286.x).

### Agyei-Ampomah (2007), “The Post-Cost Profitability of Momentum Trading Strategies: Further Evidence from the UK”

- **Sample and universe:** UK equities, 1988–2003, with evidence that size/liquidity matters.
- **Past-return definition:** Conventional momentum past-return rankings, not ID.
- **Information-discreteness definition:** Not an ID study.
- **Formation horizon:** Multiple conventional momentum horizons.
- **Skip convention:** Study-specific conventional momentum convention.
- **Holding period:** Short and longer momentum holding periods.
- **Long/short structure:** Long/short momentum portfolios.
- **Cost evidence:** Short-horizon profits can disappear after transaction costs, while longer-horizon evidence is more resilient, especially among relatively large/liquid stocks.
- **Momentum controls:** Conventional momentum and liquidity/size comparisons.
- **Mechanism:** Trading costs and liquidity determine whether a paper anomaly is implementable.
- **ParayaBuck relevance:** Supports the project’s pre-frozen turnover, spread, slippage, and liquidity gates and the use of the existing $20m median-dollar-volume scanner floor.

Source: [DOI record](https://doi.org/10.1111/j.1468-036x.2007.00383.x).

### McLean & Pontiff (2016), “Does Academic Research Destroy Stock Return Predictability?”

- **Sample and universe:** 97 published stock-return predictors across in-sample, out-of-sample, and post-publication periods.
- **Past-return definition:** Predictor-specific; not an ID formula.
- **Information-discreteness definition:** Not an ID study.
- **Formation horizon:** Predictor-specific.
- **Skip convention:** Predictor-specific.
- **Holding period:** Predictor-specific.
- **Long/short structure:** Predictor-specific long/short implementations.
- **Cost evidence:** Post-publication and out-of-sample returns generally decline; deterioration is more severe for predictors with high in-sample returns, low liquidity, or high idiosyncratic risk.
- **Momentum controls:** The paper is a broad publication-bias/decay audit rather than a momentum-control paper.
- **Mechanism:** Public discovery can arbitrage away predictability and alter trading behavior.
- **ParayaBuck relevance:** Justifies one frozen design experiment with no auto-promotion, not a promise that the literature survives unchanged in the current dataset.

Source: [Journal of Finance article record](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365).

## Additional implementation and criticism evidence

- [Patton & Weller, “What You See is Not What You Get: The Costs of Trading Anomalies”](https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2018/03/Weller-1.pdf) shows that microcap trading costs can materially change anomaly economics. The local design therefore keeps the existing liquidity and spread gates and makes cost robustness a future gate.
- [“The illusory nature of momentum profits”](https://www.sciencedirect.com/science/article/pii/S0304405X0300206X) is a direct warning that high momentum returns can be concentrated in expensive, illiquid names.
- [Medhat & Schmeling, “Short-term Momentum”](https://openaccess.city.ac.uk/id/eprint/31278/) finds that some short-term momentum evidence is strongest in large, liquid, covered stocks and can survive costs. This is a useful counterweight to the claim that every continuation effect is a microcap artifact, but it is not ID-specific.
- [Li, “Intraday Time-series Momentum and Information Discreteness in international markets”](https://wp.lancs.ac.uk/fofi2020/files/2020/04/FoFI-2020-092-Zeming-Li.pdf) is a working-paper/high-frequency extension across developed markets. It supports mechanism plausibility but is indirect evidence for this daily, long-only implementation.

## Design conclusion from the literature

The evidence supports one defensible, testable hypothesis: among positive
12-1 winners, the low-raw-ID (more continuous) path is a distinct behavioral
ordering hypothesis worth one tightly governed experiment. The literature does
not justify a new residual-return formula, an arbitrary blend with V3, a new
threshold, or a claim of automatic production value.
