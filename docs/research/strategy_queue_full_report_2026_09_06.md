# ARCHIVAL_SUPERSEDED_DO_NOT_USE_AS_CURRENT

This 2026-09-06 intake snapshot is historical context only. Read current status, counts, NEXT state and hashes from the frozen equity-secondary gate report, source matrix, manifest and bound normalized packages.

# Full 34-card research queue report — 2026-09-06

Retrieval/check date: 2026-09-07. Queue/report date remains 2026-09-06.
This report consolidates the exact pasted attachment and the three detailed
owner reports. It is research-only: the final H14 r4 contains 18 isolated
retrospective diagnostic cells, with no economic acceptance, current-V3 or
joint-portfolio run, and no production, configuration, registry, PAPER, or
LIVE state change. No new data was downloaded.

## Yönetici özeti

34 kartın tamamı (H01–H16, K01–K12, X01–X06) kapsandı. H14 için SPY-only
stratejisi 18 hücrede (iki dönem, üç kol, üç maliyet seviyesi) izole ve geriye
dönük tanı çalışmasını tamamladı. Bu sonuç mevcut Borsa V3 ile ortak portföy
testi değildir: günlük V3 NAV serisi saklanmadığı için katkı ve korelasyon
`NOT_COMPARABLE` durumundadır. V3, kripto ayarları ve üretim/PAPER/LIVE akışı
değiştirilmemiştir.

H14'ün 1x maliyetteki gözlemi, sabit dört-seans kontrolüne göre iki dönemde de
daha düşük düşüş gösterirken toplam getirisi daha düşüktür. Bu, farklı bir
piyasa korunması veya otomatik tahsis kanıtı değildir. Diğer 30 kartta gereken
yerel veri, PIT zamanı veya yerel motor yeteneği eksiktir; X04–X06 mevcut
long-only spot sözleşmesinin kapsamı dışındadır. Hiçbir kart için ekonomik
reddetme kararı verilmemiştir.

## Scope and result vocabulary

Kapsam tamamdır: H01–H16 (16 hisse), K01–K12 (12 kripto) ve X01–X06 (6
cross-asset), toplam 34 benzersiz kart. `DATA_BLOCKED`: gereken yerel veri yok.
`PIT_BLOCKED`: karar anında verinin mevcut olduğunu kanıtlayan geçmiş sürüm veya
zaman damgası yok. `CAPABILITY_BLOCKED`: yerel motor gereken özellik, yaşam
döngüsü, short veya türev işlemi desteklemiyor. `OUT_OF_SCOPE`: mekanizma
long-only equity/spot sözleşmesinin dışında. `PORTFOLIO_UNTESTED`: temel yol var
ama dondurulmuş hücreler çalıştırılmadı. `RETROSPECTIVE_DIAGNOSTIC_COMPLETE`:
izole hücreler makbuz kontrolleriyle tamamlandı. `PORTFOLIO_CONTRIBUTION_UNTESTED`:
mevcut portföyle ortak defter testi yok. `ECONOMICALLY_REJECTED` yalnızca
tamamlanmış testler için ayrılmıştır; hiçbir karta verilmedi.

## Current baseline and immutable inputs

The incumbent is `equity_residual_momentum_v3`, weekly, long-only, with a 0.55
allocation ceiling. Crypto is `crypto_low_variance_hysteresis_v1`, long-only
spot, 22 completed daily returns, HYST4 retention, top two equal weight, 96-hour
decisions, and a 0.05 ceiling. Shared limits remain gross <=0.60 and cash
>=0.40. Native fills are next tradable opens after completed-bar decisions.

Reusable evidence and exact pins:

* Equity v5 bars: `data/historical/equity_pit_symmetric_2016_2026_v5.csv`,
  SHA-256 `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`; v5 manifest declares SIP, split adjustment, 1d bars,
  and NYSE evaluation through 2026-07-30.
* Equity lifetimes: `data/historical/equity_entity_lifetimes_full_v5.csv`,
  SHA-256 `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`.
* Equity actions: `data/historical/equity_survivorship_full_corporate_actions_v5.csv`,
  SHA-256 `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`; split-adjusted prices and explicit dividend cash are
  separate accounting layers.
* H14 IWM provenance: captured raw bundle
  `data/research/external/m3_iwm_raw_2026_09_r4/`, bundle SHA `9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258`,
  normalized sidecar `data/research/generated/m3_data_gate_r4/iwm_normalized.csv`,
  SHA `8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2`.
* Crypto OHLCV: `data/historical/crypto_expanded_1h.csv`, 705,541 rows,
  25 symbols, 2021-01-01 through 2026-07-31, SHA-256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`; frozen screen manifest
  `data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt03_frozen.json`.
* Corrected P364 evidence remains in
  [`high_proximity364_final_assessment_2026_09_05.md`](high_proximity364_final_assessment_2026_09_05.md),
  [`portfolio_expansion_batch_2026_09_05_report.md`](portfolio_expansion_batch_2026_09_05_report.md),
  and the corrected history-fixed manifest/results/audit. It remains a
  research candidate for passive shadow observation only; joint max-two
  shared-ledger evidence does not exist.

## All 34 cards

| ID | Mechanism and primary evidence | Verified local feasibility / family note | Status |
|---|---|---|---|
| H01 | Value; Asness, Moskowitz, Pedersen | No PIT book/market or identifier history; value may complement V3 but overlaps H03/H05 | `DATA_BLOCKED` |
| H02 | Gross profitability; Novy-Marx | No PIT accounting concepts or restatement policy; quality/value overlap | `DATA_BLOCKED` |
| H03 | Low accruals; Sloan | No PIT income/cash-flow/asset fields; quality-adjacent | `DATA_BLOCKED` |
| H04 | Low asset growth; Cooper, Gulen, Schill | No PIT balance sheets, M&A/spinoff treatment; investment/value overlap | `DATA_BLOCKED` |
| H05 | Net payout yield; Boudoukh et al. | No PIT buyback/issuance plus dividend action history in one decision-safe feed | `DATA_BLOCKED` |
| H06 | Public insider purchases; Cohen, Malloy, Pomorski; SEC Form 4 timing | No EDGAR acceptance-time archive, transaction taxonomy, or historical insider routine | `DATA_BLOCKED` |
| H07 | Analyst forecast revisions; Chan, Jegadeesh, Lakonishok | No forecast vintages, publication times, estimate horizons | `DATA_BLOCKED` |
| H08 | PEAD; Bernard and Thomas | No earnings release timestamp or pre-release consensus; event adapter absent | `DATA_BLOCKED` + `CAPABILITY_BLOCKED` |
| H09 | Customer/supplier information spillover; Cohen and Frazzini | No PIT relationship or event feed; likely information-momentum adjacency | `DATA_BLOCKED` |
| H10 | Filing-text change / Lazy Prices; Cohen, Malloy, Nguyen | No original filing vintages, publication timestamps, or text pipeline | `DATA_BLOCKED` |
| H11 | Fund-flow forced sales; Coval and Stafford | No PIT holdings/flow and rebalance-event archive; reversal-adjacent | `DATA_BLOCKED` |
| H12 | Index-change price pressure; Greenwood and Sammon | No announcement/application-time and PIT membership feed; effect may be weaker | `DATA_BLOCKED` |
| H13 | Overnight versus intraday; Lou, Polk, Skouras | No ETF intraday/quote history or opening/closing execution truth | `DATA_BLOCKED` + `CAPABILITY_BLOCKED` |
| H14 | Turn-of-month ETF exposure; McConnell and Xu | SPY-only candidate and fixed-calendar control completed the frozen 18-cell retrospective diagnostic; QQQ/IWM are context sidecars, not gates; no intraday truth or current-V3 joint NAV | `RETROSPECTIVE_DIAGNOSTIC_COMPLETE` + `PORTFOLIO_CONTRIBUTION_UNTESTED` |
| H15 | Cash merger arbitrage; Mitchell and Pulvino | No deal terms, amendment/close/break event archive or terminal lifecycle | `DATA_BLOCKED` + `CAPABILITY_BLOCKED` |
| H16 | Post-spinoff pricing; Amihud, Huh, Subrahmanyam | No spin-off events, successor mapping, distributions, or lifecycle handling | `DATA_BLOCKED` + `CAPABILITY_BLOCKED` |
| K01 | MVRV; Coin Metrics realized-cap methodology | No ledger movements, realized cap, chain cutoff, or PIT revisions; overlaps K02/K12 | `DATA_BLOCKED` |
| K02 | NVT; Coin Metrics adjusted-transfer methodology | No adjusted transfer value or chain-specific PIT; exchange volume is invalid proxy | `DATA_BLOCKED` |
| K03 | Economic network-use growth; Coin Metrics address/transfer methods | No active/economic addresses, fees, Sybil controls, or metric vintages | `DATA_BLOCKED` |
| K04 | Net issuance/burn dilution; Coin Metrics supply methods | No issuance, burn, current/expected supply, vesting, or migration history | `DATA_BLOCKED` |
| K05 | Preannounced token unlocks | No immutable announcement/vesting archive, publication time, or actual unlock confirmation | `DATA_BLOCKED` + `PIT_BLOCKED` |
| K06 | Spot crypto ETF net flows; Lim and SEC filing | No issuer flow history, creation/redemption timestamps, or ETF PIT mapping; short regime | `DATA_BLOCKED` |
| K07 | Exchange coin inflow/outflow; Glassnode PIT warning | No transfers, labels, label vintages, venue scope; overlaps K08 | `DATA_BLOCKED` + `PIT_BLOCKED` |
| K08 | Stablecoin exchange liquidity; Chi et al. | No token contract, mint/burn/bridge exclusions, labels, or PIT flow | `DATA_BLOCKED` + `PIT_BLOCKED` |
| K09 | Basis/funding/OI as spot signal; BIS and exchange schemas | No causal feature sidecar or available-at join. Spot-only output needs no derivative ledger | `DATA_BLOCKED` |
| K10 | Spot rebound after liquidation shock; Deribit/Kraken schemas | No liquidation events, OI, mark/index, side, quantity, or venue aggregation; spot-only test | `DATA_BLOCKED` |
| K11 | Signed order flow; Anastasopoulos et al. | Paper includes long-only analysis, but no local trades/quotes/depth/aggressor data or cost truth | `DATA_BLOCKED` + `CAPABILITY_BLOCKED` |
| K12 | SOPR loss realization; Glassnode/Coin Metrics methods | No UTXO/account history, entity clusters, or immutable PIT metric; overlaps K01/K10 | `DATA_BLOCKED` + `PIT_BLOCKED` |
| X01 | Bond ETF curve carry/roll-down; Koijen et al. | No yield curves, duration history, distributions, or ETF carry inputs; direct paper is not ETF proof | `DATA_BLOCKED` |
| X02 | Commodity ETF futures-curve carry; Koijen/CME/SEC | No dated futures curves, contract specs, rolls, collateral, or fund receipts | `DATA_BLOCKED` |
| X03 | Country ETF PIT value; Asness et al. | No country valuation vintages, FX, ETF accounting, or PIT membership; H01 family | `DATA_BLOCKED` |
| X04 | Equity pairs/stat arb; Gatev et al. | Requires short leg, borrow, recall, financing, pair margin, and two-leg fills | `OUT_OF_SCOPE` + `CAPABILITY_BLOCKED` |
| X05 | Crypto dated-futures cash-and-carry; BIS | Requires spot/futures legs, basis, expiry, margin, settlement, and derivative history | `OUT_OF_SCOPE` + `CAPABILITY_BLOCKED` + `DATA_BLOCKED` |
| X06 | Spot/perpetual funding carry; Binance/BIS | Requires perp short, funding, mark/index, margin, liquidation, and two-leg history | `OUT_OF_SCOPE` + `CAPABILITY_BLOCKED` + `DATA_BLOCKED` |

## H14 and existing V3 comparison boundary

H14's frozen protocol is [`h14_turn_of_month_protocol_2026_09_06_r2.md`](h14_turn_of_month_protocol_2026_09_06_r2.md).
Its required native matrix is 18 cells: six candidate, six passive/cash, and
six fixed-calendar control cells across full/recent windows and 1x/2x/3x costs.
The completed r4 receipt and independent audit are
[`h14_native_batch_receipt.json`](../../data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_receipt.json)
and [`h14_native_batch_independent_audit.json`](../../data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_independent_audit.json).
All 18 cells are internally `PASS`; this clears the retrospective diagnostic
gate, while current-V3 portfolio contribution remains untested. The detailed
result audit is [`strategy_queue_h14_result_audit_2026_09_06.md`](strategy_queue_h14_result_audit_2026_09_06.md).
The frozen r4 execution manifest is
[`h14_turn_of_month_protocol_2026_09_06_r4.json`](../../data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json).
The retained corporate-action endpoint is
`https://data.alpaca.markets/v1/corporate-actions`; ex-date entitlement follows
the provider's [`announcement-date semantics`](https://docs.alpaca.markets/us/reference/getcorporateannouncements).
The supplied SPY corporate-action ledger has known missing quarters in 2016-Q1
and 2018-Q2, both outside the tested `full_2022` and `recent_2024` windows. It
contains four actions per year for 2019–2025 and two through 2026-06-18; no
quarterly gap was detected inside the used windows. Raw provider page/HTTP/
per-symbol completion receipts are absent, so this is not a blanket
completeness or point-in-time provenance claim. Passive total returns are
conditional on the supplied ledger. The optional 2016 context window was not
run.
The diagnostic reconciles all 18 NAV/cash outputs; 12 candidate/control cells
have recorded entry/exit fills, while six passive cells use the native
end-of-sample terminal-close convention without a synthetic terminal fill.

The canonical V3 control receipt
`data/research/equity_v3_current_parity_control_2026_08/control_r2.json`
contains an equity-curve hash and aggregate metrics but no reusable daily NAV
series. The available archived `equity_curve.csv` files are explicitly V2,
and the M3 receipt's `active_wealth` is a relative diagnostic rather than a
standalone V3 NAV. Therefore an exact H14-to-current-V3 daily alignment is
currently `NOT_COMPARABLE`; do not calculate descriptive correlation or
regime/year diagnostics from mismatched V2 or relative curves. If a verified
current-V3 daily NAV artifact is later retained, align only exact SPY
decision/fill dates and report this as descriptive evidence, never as a V3
rerun or portfolio-improvement proof.

## Prioritized research foundations

The priority order follows verified feasibility and plausible information
distinctness, not owner A/B labels and not a claim that different inputs imply
low correlation:

1. Retain H14 as a completed isolated diagnostic and pursue a verified daily
   V3 NAV/shared-ledger contribution test if that artifact becomes available.
   The current H14 evidence is SPY-only, daily, and bounded by its frozen
   event controls.
2. Build a no-download, operator-supplied PIT feature gate for K09, K07, and
   K08. K09 is the best first spot-only leverage/crowding candidate; K07/K08
   are flow candidates with mutable-label and overlap risks. Keep each metric
   separate before any combination.
3. Establish the shared equity PIT accounting foundation for H01/H02/H03/H05,
   then H06/H11 once publication-time event feeds are available. Reuse the
   foundation, but preserve one frozen formula and candidate ID per mechanism.
4. Gate K01/K02/K04/K06/K12 and H07/H08/H09/H10 with exact publication,
   revision, identity, and holdout rules. K10 and K11 follow only after their
   event/trade-level data and cost semantics are available.
5. Treat H04/H12/H15/H16 and X01–X03 as later data/lifecycle work. Keep X04–X06
   outside the current long-only contract unless a separate short/derivative
   execution mission is explicitly defined.

These priorities identify acquisition and engineering gates, not claims that
the data cannot be obtained. The first three are actionable as follows:

| Foundation | Minimum fields and authoritative source | PIT/vintage gate | Access and engineering boundary |
|---|---|---|---|
| H14 daily calendar pilot | Split-adjusted SPY OHLCV `timestamp,open,high,low,close`; NYSE sessions; cash-dividend `ex_date,rate,raw_id`; Alpaca Market Data corporate actions and retained v5 files | Freeze source hashes and endpoint; preserve missing payable/record dates; no per-row `available_at` means use a conservative lag or retain a provenance stop | Existing local files; routine lossless adapter and receipt audit; no intraday auction truth or V3 rerun |
| K09/K07/K08 crypto feature gate | K09: venue/instrument basis or funding/OI plus mark/index; K07/K08: asset units, venue labels, transfer direction and token/contract identity; provider schemas from BIS, exchange APIs, Glassnode/Coin Metrics/Chi | Immutable snapshot or provider PIT endpoint with event/publication/`available_at`, revision policy, label-set hash, stale/missing rules | Likely credentialed or licensed provider access and a small operator-supplied sample; routine feature adapter after access; spot-only output, no derivative ledger |
| Equity PIT accounting foundation | Point-in-time market/book/accounting fields, identifier/lifetime history, publication/acceptance timestamps, restatement versions, dividends/buybacks; SEC/issuer filings or licensed fundamentals feed | Filing acceptance/publication time, vintage retention, restatement and survivorship policy, source hash | Usually licensed or credentialed data plus routine PIT normalization; no economic run until formulas and holdout are frozen |
| K11 signed-flow microstructure | Immutable trades, quotes, aggressor side/classifier inputs, depth, venue ID, spread and timestamps; venue market-data feed or licensed archive | Trade/quote availability and classifier version, venue survivorship, causal lag, fill-cost receipt | Credentialed/licensed high-volume history and a dedicated ingestion adapter; OHLCV cannot reconstruct signed flow |
| X01/X02/X03 cross-asset inputs | Bond yield/duration curves and ETF distributions; dated futures contract curves/rolls/collateral; country valuation, FX and ETF PIT membership; provider/exchange/SEC records | Contract-month and roll vintages, distribution dates, FX timestamp, ETF membership and publication lag | Usually multiple licensed/credentialed feeds plus adapters; also outside the current single long-only equity/spot contract for X04–X06 |

Every future candidate must freeze source hashes, asset identity, event and
publication times, `available_at`, revisions, formula, direction, threshold,
missing-data behavior, universe, costs, and holdout before economics. A true
complementarity result requires one shared ledger with common cash, fills,
fees, caps, position overlap, drawdown overlap, time-aligned net returns, and
matched controls. Fixed 50/50 curve mixes and half-cash curves remain
diagnostics; they do not prove joint allocation or max-two feasibility.

No automatic allocation, promotion, PAPER, LIVE, or production change follows
from this report.
