# ARCHIVAL_SUPERSEDED_DO_NOT_USE_AS_CURRENT

This 2026-09-06 intake snapshot is historical context only. Read current status, counts, NEXT state and hashes from the frozen equity-secondary gate report, source matrix, manifest and bound normalized packages.

# Independent audit of the 34-card strategy queue — 2026-09-06

Retrieval/check date: 2026-09-07. The report date remains 2026-09-06 to match
the frozen queue and owner reports. This is a read-only audit; no data was
downloaded, no strategy was run, and no product, configuration, registry,
PAPER, or LIVE state changed.

## Coverage and status vocabulary

The three owner reports contain exactly 16 H cards, 12 K cards, and 6 X cards:
34 unique IDs with no missing or duplicate heading. The detailed inputs are
[`strategy_queue_equity_review_2026_09_06.md`](strategy_queue_equity_review_2026_09_06.md),
[`strategy_queue_crypto_review_2026_09_06.md`](strategy_queue_crypto_review_2026_09_06.md),
and [`strategy_queue_crossasset_review_2026_09_06.md`](strategy_queue_crossasset_review_2026_09_06.md).

Coverage inventory: H01, H02, H03, H04, H05, H06, H07, H08, H09, H10, H11,
H12, H13, H14, H15, H16; K01, K02, K03, K04, K05, K06, K07, K08, K09, K10,
K11, K12; X01, X02, X03, X04, X05, X06.

The audit uses these distinct statuses:

* `DATA_BLOCKED`: the required local feature or event data is absent.
* `PIT_BLOCKED`: a current or mutable series cannot establish what was known
  at the decision time.
* `CAPABILITY_BLOCKED`: the existing native contract lacks the required event,
  feature, lifecycle, short, or derivative execution seam.
* `OUT_OF_SCOPE`: the card's required trading mechanism is outside this
  long-only spot/equity research contract. It is not an economic rejection.
* `PORTFOLIO_UNTESTED`: enough basic price/calendar data exists for a frozen
  diagnostic, but the required native economic cells have not run.
* `ECONOMICALLY_REJECTED`: reserved for a completed, predeclared economic
  comparison. None of the 34 cards has this status.

## Local evidence checked

The only local crypto history is
`data/historical/crypto_expanded_1h.csv`: 705,541 OHLCV rows, 25 symbols, and
fields `timestamp,symbol,open,high,low,close,volume`, through
2026-07-31. The configured research pool is 22 USD pairs; MKR, NEAR, and TRX
rows in the expanded file are not silently promoted. No local on-chain,
funding, basis, open-interest, liquidation, signed-trade, depth, ETF-flow, or
address-label PIT series was found.

The equity path is the v5 canonical daily data and corporate-action ledger:
`data/historical/equity_pit_symmetric_2016_2026_v5.csv`,
`data/historical/equity_entity_lifetimes_full_v5.csv`, and
`data/historical/equity_survivorship_full_corporate_actions_v5.csv`, with
manifests pinning SIP, split adjustment, NYSE sessions, and the 2026-07-30
evaluation endpoint. H14 also has the captured IWM SIP raw bundle and its
normalized sidecar. There is no local PIT fundamentals, Form 4, analyst
forecast vintage, filing-text, customer/supplier, fund-flow, index-event,
merger, or spin-off event archive.

The native incumbent remains long-only spot crypto with 22 daily returns,
top-two equal weighting, HYST4 retention, and 96-hour decisions. Equity V3 is
long-only weekly residual momentum with a 0.55 allocation ceiling. The common
contract has gross <=0.60, cash >=0.40, next-tradable-open fills, and explicit
cost stress. See [`strategy_queue_baseline_contract_2026_09_06.md`](strategy_queue_baseline_contract_2026_09_06.md).

## K-card audit

The owner report's primary sources directly support the metric definitions or
the published mechanism. They do not establish local profitability. The main
family overlaps are K01/K02/K12 (valuation and realized-cost measures),
K04/K05 (supply pressure), K07/K08 (exchange liquidity flows), and K09/K10
(leverage or forced-flow regime signals). Those overlaps require separate
candidate IDs and separate evidence before any ensemble is considered.

| ID | Source supports | Local/PIT finding | Audited verdict and correction |
|---|---|---|---|
| K01 | Coin Metrics MVRV/realized-cap methodology | No realized cap, ledger cutoff, or revision history | `DATA_BLOCKED`; slow valuation is a hypothesis, not long-only proof |
| K02 | Coin Metrics adjusted-transfer/NVT methodology | No adjusted transfer value or chain-specific PIT series; exchange volume is not a substitute | `DATA_BLOCKED`; related to K01, test separately first |
| K03 | Coin Metrics active-address and transfer definitions | No active/economic addresses, fees, spam controls, or chain vintages | `DATA_BLOCKED`; provider heuristics require version and lag gates |
| K04 | Coin Metrics current/expected-supply methodology | No issuance, burns, supply, vesting, or migration history | `DATA_BLOCKED`; realized and expected supply must not be mixed |
| K05 | Requires token/protocol announcements; no generic paper validates this implementation | No immutable announcement archive, publication time, vesting or actual-unlock confirmation | `DATA_BLOCKED` + `PIT_BLOCKED`; provenance is the primary blocker |
| K06 | Lim’s ETF-flow study and SEC creation/redemption filing support flow mechanism | No issuer flow history, publication times, ETF set, or cash/in-kind PIT archive | `DATA_BLOCKED`; short post-2024 sample and publication lag must remain explicit |
| K07 | Glassnode exchange-netflow definition and PIT-label warning | No transfers, exchange labels, label vintages, or venue scope | `DATA_BLOCKED` + `PIT_BLOCKED`; current labels cannot be projected backward |
| K08 | Chi et al. report stablecoin exchange-flow forecasting; provider methodology defines flow fields | No stablecoin contracts, mint/burn/bridge exclusions, labels, or PIT flows | `DATA_BLOCKED` + `PIT_BLOCKED`; overlaps K07 but is a separate flow type |
| K09 | BIS crypto-carry paper and official exchange funding/OI schemas support a crowding mechanism | No basis/funding/OI sidecar or causal `available_at` to spot-bar join | `DATA_BLOCKED` only for the proposed spot-only adaptation. **Correction:** derivative ledger, margin, funding P&L, and liquidation accounting are explicitly out of scope and are not blockers. |
| K10 | Deribit liquidation procedure and Kraken analytics support venue-specific event fields | No liquidation events, OI, mark/index, side, quantity, or venue aggregation | `DATA_BLOCKED`; a spot rebound needs no derivative execution. A feature adapter may later be a capability gate, but do not claim derivative-accounting necessity. |
| K11 | Anastasopoulos et al. directly report order-flow forecasting and a long-only portfolio analysis ([publisher](https://doi.org/10.1016/j.finmar.2026.101047)) | No signed trades, aggressor side, quotes, depth, venue IDs, or executable spread history | `DATA_BLOCKED` + `CAPABILITY_BLOCKED` for local trade/quote ingestion and causal execution. **Correction:** the paper's long-only result is external evidence, not local acceptance or a long-only profit claim. |
| K12 | Glassnode entity-adjusted SOPR and Coin Metrics SOPR methodologies | No UTXO/account data, entity clusters, or immutable PIT metric | `DATA_BLOCKED` + `PIT_BLOCKED`; related to K01 and K10, not interchangeable |

### K-card source and novelty corrections

K09 must remain a spot-observation candidate: its proposed output is a
long/cash spot target, so the absence of a derivative ledger is a declared
scope boundary rather than a feasibility failure. The actual gate is a
venue/instrument-identified feature sidecar with event time, publication time,
`available_at`, revision policy, stale handling, and a causal spot join.

K11 is the strongest source correction. The publisher page does describe
long-only portfolio analysis, while also presenting long-short results. The
repository may cite that as external long-only evidence, but it cannot claim
the reported numbers apply to the local 22-pair universe, 1h-to-daily
aggregation, native fill model, or costs. K10 similarly needs liquidation
observations as a feature; it does not need short positions or derivative P&L
to test a spot-only rebound hypothesis.

## H-card and X-card coverage audit

The H owner report covers H01–H16 and correctly keeps all fundamental/event
cards data-gated. H01/H02/H03/H05 should share a PIT accounting foundation,
but their formulas remain separate candidates. H06 and H11 need publication
and ownership-flow timing. H07–H10 need forecast, release, relationship, or
filing vintages. H12/H15/H16 require event lifecycle and successor identity.
H13 additionally needs intraday or session-level execution evidence.

H14 is the only H card with a usable local price/calendar path. It is
`PORTFOLIO_UNTESTED`, pending the exact 18 native cells: six candidate cells,
six passive/cash control cells, and six fixed-calendar exposure control cells
(full/recent windows × 1x/2x/3x costs). The SPY v5 bars, IWM captured bundle,
split basis, dividend ex-date/rate mapping, and next-open semantics are
preflight inputs; they do not constitute returns. The final 2026-07-30 signal
has no in-window next execution bar and must remain unfilled. The H14 protocol
is [`h14_turn_of_month_protocol_2026_09_06_r2.md`](h14_turn_of_month_protocol_2026_09_06_r2.md).

X01–X03 are valid hypotheses with direct academic support, but local inputs do
not include historical bond curves, commodity contract-month curves/roll
receipts, or country-ETF valuation/FX PIT vintages. Verdict: `DATA_BLOCKED`.
Their ETF versions must not be presented as the direct futures or country
factor evidence.

X04 requires long/short pairs, borrow, recalls, financing, pair margin, and
two-leg fills. X05 requires spot plus dated-futures short, basis, expiry,
margin, settlement, and synchronized execution. X06 requires spot plus
perpetual short, funding payments, mark/index, margin, liquidation, and
two-leg execution. Verdicts are `OUT_OF_SCOPE` + `CAPABILITY_BLOCKED`, with
`DATA_BLOCKED` also applying to X05/X06's missing derivative histories. A
long-only or spot-only proxy receives a new ID and cannot inherit X04–X06's
mechanism claim.

## Consolidated corrections and disposition

1. No K or X card has an economic rejection. All apparent published returns
   are mechanism evidence or external portfolio evidence and remain separate
   from local economics.
2. K09 is data-gated, not derivative-accounting-gated, when kept as the
   authorized spot-only signal. K10 has the same distinction for liquidation
   observations versus derivative execution.
3. K11's direct source includes a long-only analysis, but local feasibility
   still requires signed-trade/quote data, PIT venue identity, and cost-aware
   fills. No source result is copied into the local report as a return.
4. H14 is pending 18 native cells, not cleared. The daily path is feasible;
   no canonical result exists until the preflight and cells complete.
5. X04–X06 are scope/capability boundaries, not “poor performers.”
6. Family similarity must be measured with time-aligned net returns, common
   drawdown days, position overlap, and marginal risk after a shared-ledger
   test. Raw correlation or a standalone curve mix does not prove
   complementarity. Corrected P364 remains research-only; a shared P364 plus
   incumbent max-two allocator is unproven and was not built here.

## Primary source register

The owner reports link the primary evidence used for the audit. The direct
crypto sources are [Coin Metrics MVRV](https://docs.coinmetrics.io/asset-metrics/market/capact1yrusd),
[NVT](https://docs.coinmetrics.io/asset-metrics/economics/nvtadj),
[adjusted transfer value](https://docs.coinmetrics.io/asset-metrics/transactions/txtfrvaladjntv),
[active addresses](https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/addresses/active-addresses),
and [supply](https://docs.coinmetrics.io/asset-metrics/supply/ser);
[Glassnode exchange transactions](https://docs.glassnode.com/basic-api/endpoints/transactions),
[Glassnode PIT data](https://research.glassnode.com/introducing-point-in-time-data/),
and [Glassnode indicators/SOPR](https://docs.glassnode.com/basic-api/endpoints/indicators);
[Lim's ETF-flow study](https://papers.ssrn.com/sol3/Delivery.cfm/2382ce4d-6fcc-4280-ba52-bb638a8c580b-MECA.pdf?abstractid=6592830&mirid=1),
the [SEC spot-trust filing](https://www.sec.gov/Archives/edgar/data/2015034/000119312525076003/d943052d424b3.htm),
and [Chi et al.](https://arxiv.org/abs/2411.06327); [BIS Crypto Carry](https://www.bis.org/publications/working-paper-1087-crypto-carry),
[Deribit funding/market data](https://docs.deribit.com/api-reference/market-data/public-get_funding_chart_data),
[Deribit liquidation procedure](https://support.deribit.com/hc/en-us/articles/25944769313309-Liquidations),
[Binance funding/OI data](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Info),
[Kraken market analytics](https://docs.kraken.com/api/docs/futures-api/charts/market-analytics),
and [Anastasopoulos et al.](https://doi.org/10.1016/j.finmar.2026.101047).
These sources define metrics, data caveats, or published study designs. None
supplies local PIT history or validates this repository's execution and
portfolio contract.

No economic run, source download, or product change was performed for this
audit.
