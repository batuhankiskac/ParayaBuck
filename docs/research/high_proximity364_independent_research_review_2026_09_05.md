# Independent research review: high_proximity364 — 2026-09-05

## Verdict

The research problem is framed correctly as a complementarity question under fixed capital, common risk and execution constraints. The local signal is a frozen screen adaptation, not a proven independent alpha and not an automatic entry veto, ensemble, regime switch, or production candidate. The equity V3 logic and its 55% allocation ceiling must remain fixed; the existing gross 60%, cash 40% and crypto 5% limits leave no extra capital for this arm.

The mechanism report is strongest when it calls the feature a price-strength/high-water-mark proxy and lists crypto beta, size, liquidity, concentration, survivorship and cash artifacts as alternatives. Its literature claims are broadly supported, but the evidence does not establish the local 364-day crypto rule. The current attempt-03 screen artifact is verified complete: all 42 cells across three shards report `SCREEN_SHARD_RESULTS_READY_FOR_REVIEW`, the analysis reports `ANALYSIS_COMPLETE`, and the independent audit reports `PASS` with the recorded hashes. This is technical completion of a retrospective screen, not proof of independent alpha, shared-ledger integration, or production approval.

## Exact local rule versus literature

The local adapter computes `P364_t = close_t / max(close_{t-363}, ..., close_t)` on 364 completed daily prices, includes the current close in the denominator, ranks descending, and applies the shared screen adaptation: completed-D1 shift, UTC 96-hour decisions, HYST4 retention, top-2 equal targets, next-later-hour execution, actual fees and final mark without forced sale. This is a one-year trailing-peak proximity score. It is cross-sectional price strength with a high-water-mark anchor; it is not itself a return, a time-series sign rule, or volatility targeting.

George and Hwang, “The 52-Week High and Momentum Investing,” *The Journal of Finance* 59 (2004), 2145–2176, is the closest primary source. The publisher abstract says nearness to the 52-week high improves on past returns for forecasting future stock returns and that the forecast does not reverse in the long run ([Wiley article](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2004.00695.x)). That supports a continuation/anchoring hypothesis for the feature shape. It does not validate this implementation: the paper studies equities and a different portfolio formation, holding, universe, and long/short construction. “52-week high” is therefore a mechanism analogue, not an exact local recipe.

The report's crypto transfer is appropriately cautious when it distinguishes local validation from literature. Liu and Tsyvinski, “Risks and Returns of Cryptocurrency,” *Review of Financial Studies* 34 (2021), 2689–2727, report cryptocurrency-specific network factors, strong time-series momentum and attention predictors ([Oxford Academic](https://academic.oup.com/rfs/article-abstract/34/6/2689/5912024?login=false)). This supports possible continuation and attention channels, but it is not a 364-day cross-sectional high-water-mark test.

Liu, Tsyvinski and Wu, “Common Risk Factors in Cryptocurrency,” *The Journal of Finance* 77 (2022), 1133–1177, is the relevant exposure-control source ([Wiley article](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13119)). Its existence and bibliographic details are verified by the publisher; its factor framing supports decomposing any local result into market, size and momentum exposures before calling it a second signal. It does not prove incremental alpha for this bot.

Fieberg, Liedtke, Metko and Zaremba, “Cryptocurrency factor momentum,” *Quantitative Finance* 23 (2023), 1853–1869, is verified in the journal index ([Taylor & Francis journal record](https://ideas.repec.org/s/taf/quantf2.html)). The study is broad factor-momentum research over a much wider crypto universe and cannot be relabeled as evidence for the configured 22-symbol, long-only, top-2, 5% sleeve.

Han, Kang and Ryu, “Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market: A Comprehensive Analysis under Realistic Assumptions” (2024 working paper), explicitly accounts for transaction costs and interim daily price fluctuations. Its abstract reports strong time-series momentum, weak cross-sectional momentum, concentration among large winners, and frequent loser rebounds ([authors' paper](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)). This is useful negative-transfer evidence: a high-proximity winner basket may primarily select large winners or crypto beta, and loser/reversal behavior can make apparent diversification regime-dependent.

Begušić and Kostanjčar, “Momentum and liquidity in cryptocurrencies” (2019), study momentum-liquidity sorts and report stronger momentum among liquid cryptocurrencies plus liquid-winner and illiquid-loser long-only strategies ([arXiv](https://arxiv.org/abs/1904.00890)). This supports a liquidity interaction hypothesis, not an independent high-proximity effect. It makes liquidity and tradability controls mandatory.

## Findings

1. **Mechanism classification — supported, confidence high.** Descending `P364` selects coins currently closest to their trailing 364-day high. The defensible label is slow cross-sectional price strength/high-water-mark proximity. The expected continuation, attention, anchoring and information-diffusion channels are hypotheses.

2. **Independent alpha — unproven, confidence high.** The cited equity and crypto studies do not match the local horizon, universe, long-only top-2 construction, 96-hour cadence, 5% sleeve, or execution contract. A curve improvement cannot be called independent alpha until market, size, momentum, liquidity, BTC/ETH and concentration exposures are controlled.

3. **Complementarity question — correctly posed, evidence pending.** A candidate can still be useful if it changes holdings or incumbent-weak-period exposure under one shared ledger. Standalone return, low correlation, or high proximity alone cannot choose between a second allocation, an entry filter, or an ensemble architecture. The user has not selected an architecture; the reports should keep that decision open. The completed attempt-03 screen does not resolve this joint economic question because its audit explicitly identifies mixtures as curve approximations without a joint ledger, fills, netting, or shared risk proof.

4. **Regime claims — hypothesis only.** “Persistent trend,” “incumbent weakness,” and “market-wide high” are useful predeclared partitions, not post-hoc conclusions. Every partition must be reported, including failures and cash exposure. No all-regimes claim is supported by the literature or the current artifact status.

5. **Volatility targeting — separate concept.** Volatility management changes exposure sizing based on realized risk; `P364` ranks price location. They may coexist in a future coordinator, but the proximity score must not be described as volatility control or as proof that a volatility overlay improves V3.

6. **Current evidence status — screen complete; joint conclusion pending.** The report's proposed gates are appropriate: dated eligibility, cash/headroom, BTC/ETH and top-symbol leave-one-out, actual fees, common ledger, terminal marks and fixed-capital controls. The remaining limitation is economic interpretation and transferability, not a technical failure of attempt-03. No new backtest or integration is authorized by this review.

7. **Observed opportunity is narrower than configured universe.** The exposure audit records BTC/USD as the only traded symbol for high_proximity364 in every audited full/recent cost cell. At the full-window first active mark, six symbols had a locally reconstructed 364-day consecutive history; at the recent-window first active mark, only BTC/USD did. BTC activity ends at `2025-07-03T04:00:00+00:00`, after which the ledger is inactive. Because the frozen payload has no per-decision score, rank, PIT eligibility snapshot or cash reason, this establishes the observed path but cannot establish that ranking caused BTC selection or that later cash was a regime forecast. The candidate may have little or no cross-sectional information when the observed eligible pool is one symbol, and the apparent cash period may be a data-readiness/native-history limitation.

## Primary-source register

| Source | Year | Direct primary/publisher URL | What it supports | Transfer limit |
|---|---:|---|---|---|
| George & Hwang, *The 52-Week High and Momentum Investing* | 2004 | https://doi.org/10.1111/j.1540-6261.2004.00695.x | 52-week-high nearness and stock momentum continuation | Equity, different portfolio design |
| Liu & Tsyvinski, *Risks and Returns of Cryptocurrency* | 2021 | https://doi.org/10.1093/rfs/hhaa113 | Crypto time-series momentum and attention/network factors | Not local cross-sectional P364 |
| Liu, Tsyvinski & Wu, *Common Risk Factors in Cryptocurrency* | 2022 | https://doi.org/10.1111/jofi.13119 | Market/size/momentum exposure controls | Does not prove incremental local alpha |
| Fieberg, Liedtke, Metko & Zaremba, *Cryptocurrency factor momentum* | 2023 | https://doi.org/10.1080/14697688.2023.2269999 | Broad crypto factor-momentum evidence | Wider universe and different construction |
| Han, Kang & Ryu, *Time-Series and Cross-Sectional Momentum...* | 2024 | https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf | Cost/liquidation-aware caution; winner concentration and loser rebounds | Working paper; not local rule |
| Begušić & Kostanjčar, *Momentum and liquidity in cryptocurrencies* | 2019 | https://arxiv.org/abs/1904.00890 | Liquidity interaction with crypto momentum | Different sorts and portfolio |

## Disposition

Keep the mechanism report's core framing and its explicit alternatives. Record attempt-03 as technically complete and independently audited (`ANALYSIS_COMPLETE`, audit `PASS`, 42/42 cells), while keeping high-proximity364 as a complementary research hypothesis rather than an independent-alpha conclusion. Treat BTC-only selection and the post-2025-07-03 cash period as observed ledger facts with unobserved causes; do not call them regime evidence until a bounded trace records eligibility, scores, ranks, retained symbols and cash reasons. The shared-ledger, exposure, concentration and execution gates remain open. Do not revive closed thresholds, claim period dominance, alter V3, or choose a veto/ensemble architecture from this literature review.
