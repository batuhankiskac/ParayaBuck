# Crypto complementary-strategy queue review — 2026-09-06

This is a research-only review of the exact cards K01–K12 from the 2026-09-06 queue. It does not run an economic test, download data, change configuration or strategy registration, alter PAPER/LIVE, submit trades, or imply promotion. The review asks whether a card could add a distinct return driver to the incumbent crypto sleeve; it does not rank cards by standalone or all-period backtest return.

## Local evidence and hard boundaries

The current historical crypto source is `data/historical/crypto_expanded_1h.csv`: 705,541 rows, 25 symbols, and only `timestamp,symbol,open,high,low,close,volume` ([`docs/crypto_foundation_parity.md:101-123`](../../crypto_foundation_parity.md)). The three other `data/historical/crypto_18_*` files also contain OHLCV plus timeframe/market columns only. A repository-wide search found no local series for on-chain transfers, realized cap, supply issuance, address/entity labels, ETF creations/redemptions, exchange flows, stablecoin flows, funding, futures basis, open interest, liquidation events, trade direction, or order-book depth.

The configured research pool is 22 USD pairs. Three rows-only extras in the expanded file (`MKR/USD`, `NEAR/USD`, `TRX/USD`) are excluded. Historical availability is inferred from bars; historical broker tradability, delistings, address-label vintages, and venue coverage are unavailable. Young/partial symbols are therefore not silently backfilled. The incumbent contract is daily 22-return low variance, top-2 equal weight, 96-hour decisions, 5% crypto allocation, shared gross <=60% and cash >=40% ([`docs/crypto_foundation_parity.md:7-25`](../../crypto_foundation_parity.md); [`config/settings.yaml:159-193`](../../config/settings.yaml)).

All future tests must carry `event_time`, `publication_time`, `available_at`, revision/version, provider/venue, asset identity, and source hash. Decisions may consume only rows with `available_at <= decision_time`; current corrected history or later address relabels cannot be projected backward. Signal observations must be shifted past the finalization boundary. The existing screen’s explicit complete-24-hour-bar and no-forward-fill rule is the minimum local standard ([`docs/research/portfolio_expansion_crypto_screen_protocol_2026_09_05.md:14-21`](portfolio_expansion_crypto_screen_protocol_2026_09_05.md)).

### Early feasibility: K09, K07, K08

These three have the clearest possible complementarity story, but none is currently testable locally. K09 is a derivatives-observation signal only: it may produce a long/cash spot target from basis/funding/OI. Derivative execution, margin, collateral, funding settlement, and liquidation accounting are explicitly out of scope for this spot-only adaptation and are not blockers. The actual blocker is timestamp-safe historical feature ingestion and joining: the repository has no basis/funding/OI sidecar with instrument and venue identity, publication/availability timestamps, revision policy, or research adapter that causally joins observations to spot bars. K07 and K08 have the needed conceptual data in external provider methodologies, but exchange address labels are revised over time; a current label map is not a PIT history. They require immutable historical snapshots or provider PIT endpoints, plus a publication/availability lag.

The first data gate for all three is a small, frozen BTC/ETH pilot with raw observations and provenance, not a backtest. Required fields are listed in each card below. Until that gate passes, verdicts are `DATA_BLOCKED` or `CAPABILITY_BLOCKED`; no proxy from local OHLCV is acceptable.

## Primary source register

- Coin Metrics, [market and realized capitalization / MVRV methodology](https://docs.coinmetrics.io/asset-metrics/market/capact1yrusd), [valuation and NVT/SOPR methodology](https://docs.coinmetrics.io/asset-metrics/economics/nvtadj), [adjusted transfer value](https://docs.coinmetrics.io/asset-metrics/transactions/txtfrvaladjntv), [active addresses](https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/addresses/active-addresses), [current supply](https://docs.coinmetrics.io/asset-metrics/supply/ser), and [expected-supply methodology](https://coinmetrics.io/wp-content/uploads/2021/01/CMBI-Adjusted-Free-Float-Methodology-v1.0.pdf).
- Glassnode, [exchange netflow endpoint and mutable-label warning](https://docs.glassnode.com/basic-api/endpoints/transactions), [exchange-data transparency notice](https://docs.glassnode.com/further-information/exchange-data-transparency-notice), [point-in-time data](https://research.glassnode.com/introducing-point-in-time-data/), [entity-adjusted SOPR](https://docs.glassnode.com/basic-api/endpoints/indicators), and [UTC/resolution rules](https://docs.glassnode.com/data/general-information/timestamps-and-resolutions).
- Lim, [The Price Impact of Spot Bitcoin ETF Flows](https://papers.ssrn.com/sol3/Delivery.cfm/2382ce4d-6fcc-4280-ba52-bb638a8c580b-MECA.pdf?abstractid=6592830&mirid=1), and SEC [spot Bitcoin trust creation/redemption filing](https://www.sec.gov/Archives/edgar/data/2015034/000119312525076003/d943052d424b3.htm).
- Chi et al., [Return-forecasting and volatility-forecasting power of on-chain activities](https://arxiv.org/abs/2411.06327).
- Schmeling, Schrimpf and Todorov, BIS [Crypto Carry Working Paper 1087](https://www.bis.org/publications/working-paper-1087-crypto-carry) and [PDF](https://www.bis.org/publ/work1087.pdf).
- Deribit, [funding chart API](https://docs.deribit.com/api-reference/market-data/public-get_funding_chart_data), [funding-rate API](https://docs.deribit.com/api-reference/market-data/public-get_funding_rate_value), [ticker fields including OI/funding](https://docs.deribit.com/api-reference/market-data/public-ticker), and [liquidation procedure](https://support.deribit.com/hc/en-us/articles/25944769313309-Liquidations).
- Binance, [funding history and open-interest statistics](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Info); Kraken, [market analytics covering OI, liquidation volume, aggressor differential, CVD, basis and funding](https://docs.kraken.com/api/docs/futures-api/charts/market-analytics).
- Anastasopoulos et al., publisher page for [Order flow and cryptocurrency returns](https://doi.org/10.1016/j.finmar.2026.101047).

## Card reviews

### K01 — Chain valuation: MVRV

**Hypothesis and evidence.** MVRV is market capitalization divided by realized capitalization. Coin Metrics defines realized cap by valuing each native unit at its last on-chain movement price; this is a cost-basis proxy, not an observed trade-level investor cost basis. A within-asset historical deviation from its own realized-cap ratio could identify valuation regimes that are slower than the incumbent’s 22-day realized-volatility rank.

**Local data and missing fields.** Local OHLCV has neither ledger movements nor realized-cap values. Required fields are daily market cap/current supply, realized cap, chain height or ledger cutoff, asset identity, provider revision status, and `available_at`. Coin Metrics supports daily asset metrics, but provider access and an immutable PIT export are absent locally.

**Conservative frozen minimal spec.** Research-only, configured assets with complete provider history; daily completed observations; `MVRV_t = CapMrkt_t / CapReal_t`; one-period lag; long/cash only; no cross-asset threshold chosen after results. A later manifest must freeze the lookback, direction, and missing-data rule before economics. Do not use MVRV as a proxy for price or local OHLCV.

**Complementarity, provenance and gates.** Slow valuation may diversify short-horizon low variance, but high-MVRV stress can coincide with incumbent risk-off states. Gate realized-cap revision history, chain-specific definitions (UTXO/account/L2 exclusions), timestamp lag, symbol mapping, and no post-ex-post reclassification. `DATA_BLOCKED`; queue **P1** after K09/K07/K08 data feasibility. Economic evidence in the methodology is not long-only portfolio evidence.

### K02 — Usage valuation: NVT

**Hypothesis and evidence.** NVT is network value divided by adjusted transfer value. Coin Metrics describes adjusted transfer value as a noise-reduced on-chain transfer measure and warns that NVT is not directly comparable across assets because network usage differs structurally. A within-asset NVT or smoothed NVT regime could be a slower usage/value signal.

**Local data and missing fields.** No transfer ledger, adjusted-transfer heuristics, market cap, chain cutoff, or provider revision/PIT data exist locally. The OHLCV `volume` column is exchange-reported bar volume and cannot substitute for on-chain transfer value.

**Conservative frozen minimal spec.** Daily `NVTAdj = market value / adjusted transfer value`, one-period lag, within-asset history only, no cross-asset raw-level comparison, no post-result smoothing/threshold selection, long/cash only. Freeze asset coverage and the provider’s adjusted-transfer version before testing.

**Complementarity, provenance and gates.** NVT could diversify volatility selection if usage/value moves slowly, but it shares valuation and cycle exposure with K01 and can fail during exchange/internal-transfer or chain-structure changes. Gate adjusted-transfer version, self-churn/early-spend rules, chain coverage, `available_at`, and denominator zero handling. `DATA_BLOCKED`; queue **P1**, jointly with K01 but do not combine before separate evidence.

### K03 — Network economic-usage growth

**Hypothesis and evidence.** Growth in economically meaningful transfers, fees, or economically active addresses may capture adoption/use rather than price. Coin Metrics defines active addresses as unique ledger participants and explicitly warns that cheap address creation and transactions can make counts trivially forgeable. Adjusted transfer value removes specified artifacts but remains a provider heuristic.

**Local data and missing fields.** No active-address, economic-address, adjusted-transfer, fees, spam/Sybil, chain identity, or provider vintage series exist. Local bar volume is unsuitable because it measures venue trading, not network use.

**Conservative frozen minimal spec.** Select one metric family before results: 30-day growth in either adjusted transfer value or economically active addresses, within asset, daily completed and one-period lagged. No composite of fees, addresses and transfers until each component has separate provenance and a predeclared weighting. Long/cash only.

**Complementarity, provenance and gates.** Usage growth may be orthogonal to low variance and price trend, but address/contract migration and incentive programs can make apparent growth non-economic. Gate chain-specific definitions, spam/Sybil controls, migration handling, revisions, and publication lag. `DATA_BLOCKED`; queue **P1**.

### K04 — Net supply increase / dilution

**Hypothesis and evidence.** New issuance minus burns relative to current supply can represent dilution pressure. Coin Metrics’ current-supply definition is ledger-visible issued units; its expected-supply methodology uses known programmatic issuance and vesting, which is a forecast contract rather than an observed future fact.

**Local data and missing fields.** No issuance, burn, current-supply, token-generation, vesting, or chain ledger data exist locally. OHLCV volume and close do not identify supply changes.

**Conservative frozen minimal spec.** Use realized daily net supply change `(issued - burned) / prior current supply`, with an independently versioned optional expected-supply field. Do not mix realized and expected supply in one score. Daily one-period lag, within-asset normalization, long/cash only; freeze whether a positive value means cash before any result.

**Complementarity, provenance and gates.** Dilution may diversify price-risk selection and may matter most for high-emission assets, but issuance can be anticipated and therefore already priced. Gate chain ledger cutoff, burns, staking/reward treatment, L2/bridge supply, token migrations, and expected-supply revision history. `DATA_BLOCKED`; queue **P1**.

### K05 — Preannounced token unlocks

**Hypothesis and evidence.** A dated vesting/unlock event can create potential supply pressure. The mechanism requires an announcement known before the decision, the exact affected allocation, vesting condition, and later confirmation of the actual unlock. Generic supply methodologies cannot replace a token-specific primary announcement.

**Local data and missing fields.** No protocol/issuer announcement archive, announcement publication timestamps, vesting schedules, beneficiary identities, actual unlock transfers, or token migration map exists. There is no valid generic unlock series for the 22-pair pool.

**Conservative frozen minimal spec.** Event-only research: each event must have a canonical issuer/governance URL, immutable capture, `publication_time`, scheduled unlock time, units/percent of supply, beneficiary class, and actual on-chain confirmation. Use only events published before `decision_time`; do not infer a signal from a current unlock calendar. Direction, event window, and asset eligibility must be frozen in a manifest before economics.

**Complementarity, provenance and gates.** Event pressure could be distinct from low variance, but anticipatory positioning and heterogeneous unlock recipients make direction uncertain. Gate primary announcement, amendments, timezone, actual transfer, lock extensions, and address/entity mapping. `DATA_BLOCKED` and `PROVENANCE_BLOCKED`; queue **P2**.

### K06 — Spot crypto ETF net flows

**Hypothesis and evidence.** Net creations/redemptions of spot BTC/ETH ETFs can transmit institutional demand to spot. Lim’s preprint uses daily net flow data for five US spot Bitcoin ETFs over January 2024–April 2025 (313 trading days) and reports both same-day association and next-day prediction, while also finding that flow persistence can make temporary shocks look permanent. SEC filings establish that creations/redemptions exchange cash or bitcoin through authorized participants; they do not provide a ready historical flow feed.

**Local data and missing fields.** No ETF flow, creation basket, redemption, AUM, issuer publication timestamp, exchange holiday calendar, or ETF-to-underlying mapping exists. The available 2021–2026 OHLCV file does not solve the short post-2024 ETF sample or the publication-lag problem.

**Conservative frozen minimal spec.** BTC/ETH only, daily issuer-published net creations/redemptions, aggregate by underlying, consume only after publication, shift at least one decision interval, long/cash spot target, and freeze the ETF set and event window before testing. Do not use price or AUM changes as flow proxies; do not treat ETF share volume as net flow.

**Complementarity, provenance and gates.** ETF flow is plausibly orthogonal to low-volatility ranking and may be concentrated in BTC/ETH, but it can become a broad crypto beta signal and has a short regime history. Gate issuer/SEC source, correction policy, cash versus in-kind treatment, publication timestamp, non-US holidays, and a post-2024 untouched holdout. `DATA_BLOCKED`; queue **P1** after the K09/K07/K08 pilot.

### K07 — Coin inflows/outflows to exchanges

**Hypothesis and evidence.** Asset-specific net flow into labeled centralized-exchange addresses may proxy immediately available sell or buy liquidity. Glassnode defines exchange netflow as exchange inflows minus outflows, but states that exchange address labels and statistical methods are continually updated, making ordinary series mutable; its transparency notice recommends PIT metrics for immutable points. Chi et al. report intraday on-chain flow forecasting for BTC, ETH and USDT, but this is shortpaper evidence, not evidence that the current application can implement it.

**Local data and missing fields.** No chain transfers, labeled exchange addresses, label-vintage snapshots, venue identities, deposit/withdrawal timestamps, or asset-specific flows exist. The local `volume` field is not an exchange-wallet flow.

**Conservative frozen minimal spec.** First pilot BTC/ETH only with provider PIT flow series and a frozen exchange-label universe. Use net native-unit flow and USD-normalized flow as separate candidates, daily or hourly only if the provider supplies `available_at`; never mix revisions. One-period lag, long/cash spot, and no universal sign assumption: sign must be predeclared with the metric version before results.

**Complementarity, provenance and gates.** Exchange flow is event/liquidity-driven and could diversify slow low variance, but internal wallet shuffles, custodial changes and label repairs can create false signals. Gate PIT endpoint or immutable snapshots, label-set hash per timestamp, chain confirmations, exchange scope, internal-transfer exclusions, missingness, and publication latency. `DATA_BLOCKED` plus `PIT_BLOCKED`; queue **P0**.

### K08 — Stablecoin exchange liquidity

**Hypothesis and evidence.** Stablecoin net inflow to exchanges may represent deployable trading liquidity. Chi et al. find that USDT net inflow to cryptocurrency exchanges positively forecasts BTC and ETH returns, with the strongest reported effect at one hour. This is an empirical shortpaper result for a defined sample and data construction, not proof that minting, total stablecoin supply, or all stablecoin flows imply buying.

**Local data and missing fields.** No USDT/USDC chain transfers, exchange address labels, stablecoin contract mapping, mint/burn classification, venue coverage, or PIT flow series exist. Stablecoin issuance outside exchanges and bridge movements are not observable in OHLCV.

**Conservative frozen minimal spec.** BTC/ETH spot target; stablecoin net exchange flow only, with stablecoin contract and exchange label sets frozen; separate USDT and USDC series before any aggregate; completed interval and one-period lag; no minting-as-buying shortcut; long/cash only. The exact interval and direction must be frozen before economic results.

**Complementarity, provenance and gates.** This is a liquidity-flow mechanism likely more orthogonal to low variance than K01/K02, but it may be a crypto-wide beta proxy and can overlap K07. Gate token contract, mint/burn/bridge exclusions, exchange-label vintages, PIT timestamp, cross-venue coverage, and the one-hour finding’s data frequency/latency. `DATA_BLOCKED` plus `PIT_BLOCKED`; queue **P0**.

### K09 — Derivatives carry/leverage intensity as a spot signal

**Hypothesis and evidence.** BIS documents large, time-varying futures basis (“crypto carry”), high carry associated with leveraged demand and future crash risk. Exchange APIs expose funding, basis-related fields and open interest; Binance documents funding history and only limited recent open-interest history, while Kraken exposes basis, funding, OI and related analytics. These sources support measurement capability and a risk mechanism, not a derivative-execution recommendation.

**Local data and missing fields.** No futures/perpetual instrument IDs, spot/futures synchronized prices, basis, funding, OI, venue weights, mark/index prices, or timestamp-safe feature sidecar exist. The missing implementation capability is a causal feature-ingestion/research adapter: it must preserve venue/instrument identity, event/publication/available-at timestamps, revisions, stale intervals, and exact spot-bar joins. The application’s spot order path need not account for a two-leg carry trade, funding payments, collateral, or derivatives liquidation because those executions are outside the card’s scope.

**Conservative frozen minimal spec.** **Spot-only adaptation:** observe frozen cross-venue BTC/ETH basis/funding/OI; compute a predeclared, lagged spot long/cash state at the completed UTC decision time; submit spot orders only. No futures or perpetual orders, leverage, shorting, funding P&L, or cash-and-carry return attribution. Exact venue aggregation, horizon, sign and threshold must be frozen before data/results; do not label this `Crypto Carry` execution.

**Complementarity, provenance and gates.** Leverage/crowding can be orthogonal to incumbent low-volatility selection and may provide a risk-off cash state, but it can also correlate with broad crypto beta and crisis exits. Gate instrument roll/contract identity, mark/index timestamp, funding publication, OI unit, venue coverage, stale intervals, causal spot-bar joins, and observed spot fill cost. `DATA_BLOCKED` pending the feature sidecar and research adapter; do not label derivative execution capability as a blocker. Queue **P0**.

### K10 — Spot rebound after liquidation shock

**Hypothesis and evidence.** A large, verified liquidation event accompanied by OI reduction may mark forced selling and short-horizon exhaustion. Deribit documents incremental auto-liquidation and venue-specific behavior; Kraken’s official analytics schema includes liquidation volume and OI. These are mechanism/data definitions, not evidence that a fixed rebound window is profitable.

**Local data and missing fields.** No liquidation prints, side, quantity, venue, OI, mark/index price, trade-level timestamps, or cross-venue coverage exists. Hourly OHLCV cannot distinguish ordinary selling from forced liquidation or measure OI reduction.

**Conservative frozen minimal spec.** Event detector requires venue-tagged liquidation volume, OI delta, spot return/volume and confirmed event time; trigger only after the event is available; one fixed waiting interval and one fixed holding interval selected before results; spot long/cash only, no derivatives. No threshold or window may be selected from the rebound curve.

**Complementarity, provenance and gates.** Event reversal may diversify slow low variance but likely raises turnover, spread and gap risk. Gate venue event semantics (full versus incremental liquidation), duplicate events, OI units, cross-venue aggregation, latency, and worst-case fill during shock. `CAPABILITY_BLOCKED` and `DATA_BLOCKED`; queue **P2**.

### K11 — Aggressive buy/sell order flow

**Hypothesis and evidence.** Signed trade flow, aggressor imbalance, CVD or depth imbalance can capture immediate demand pressure. Anastasopoulos et al. report predictive power from international order flow for a broad crypto cross-section and explicitly evaluate long-only portfolios, but their data and model are not in this repository; publisher metadata says data are available on request. Kraken’s official analytics endpoint lists aggressor differential, CVD, order book, spreads, liquidity and slippage fields.

**Local data and missing fields.** No trade direction, aggressor side, tick data, quote updates, depth snapshots, cancellation data, venue IDs, or executable spread history exists. OHLCV volume cannot reconstruct signed order flow.

**Conservative frozen minimal spec.** Start with a single venue and BTC/ETH; immutable trades/quotes, deterministic aggressor classification, fixed aggregation interval, lagged signed volume or depth imbalance, long/cash spot only. Freeze venue, interval, classifier, missing-data rule and holding cadence before results. Do not use an ML ensemble or add returns/volume features until the single metric has its own evidence.

**Complementarity, provenance and gates.** Microstructure flow can be distinct from low variance but is the most likely card to lose its edge to spread, latency and turnover. Gate trade/quote synchronization, aggressor classification, crossed-book handling, venue survivorship, queue latency, participation and slippage. `CAPABILITY_BLOCKED` and `DATA_BLOCKED`; queue **P2**.

### K12 — On-chain loss realization: SOPR

**Hypothesis and evidence.** SOPR compares the value of spent outputs with their creation value; below one indicates aggregate realized loss under the metric definition. Glassnode provides raw and entity-adjusted SOPR, with entity-adjusted data discarding same-entity (“in-house”) transactions using proprietary clustering. Coin Metrics publishes a separate SOPR methodology. This is an on-chain realized-cost proxy, not a direct exchange sell tape.

**Local data and missing fields.** No UTXO/account ledger, output creation/spend prices, entity clusters, same-entity exclusions, or PIT SOPR series exists. Later entity relabeling would change historical values unless a PIT endpoint/snapshot is used.

**Conservative frozen minimal spec.** BTC first, then only assets with the same documented semantics; use entity-adjusted SOPR from an immutable PIT series, completed daily, one-period lag, long/cash only. Freeze whether the signal is a single SOPR level or a fixed deviation from one; do not use a current entity map on old history.

**Complementarity, provenance and gates.** Loss realization could diversify price-volatility ranking and may be useful around capitulation, but it is cycle-sensitive and related to K01 cost-basis measures and K10 reversal. Gate entity-cluster version, UTXO/account treatment, internal transfers, chain coverage, metric publication lag, and revision immutability. `DATA_BLOCKED` plus `PIT_BLOCKED`; queue **P1**.

## Queue decision and next gates

No card is a `PAPER_RESEARCH_CANDIDATE` from this review. The first queue is K09/K07/K08 because their mechanisms are plausibly complementary and external providers expose relevant schemas, but all three remain blocked until a no-download, operator-supplied or already-authorized immutable sample proves native context, timestamp availability and coverage. K01/K02/K03/K04/K06/K12 follow as slower data-gated fundamental/flow cards. K05, K10 and K11 remain later because event provenance or execution fidelity is materially harder.

For every card that clears data feasibility, freeze a manifest before any economic result: source URLs and hashes, asset/venue scope, native history start/end, publication and availability lag, revision/PIT policy, one-period signal shift, exact formula, fixed direction/threshold/window, missing-data behavior, and holdout boundary. Then run the same shared-account comparison: incumbent, candidate diagnostic, incumbent+candidate under the same 5% crypto budget, and a matched-exposure control. Report time-aligned net-return correlation, common drawdown days, incumbent weak periods, position overlap, marginal risk/return, turnover, 1x/2x/3x costs, and block-bootstrap/holdout evidence. Separate shortpaper findings from long-only implementability, provider snapshots from historical PIT, and technical data PASS from economic acceptance. No cash is assumed available for a new sleeve, and no allocation is split automatically.
