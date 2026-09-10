# ARCHIVAL_SUPERSEDED_DO_NOT_USE_AS_CURRENT

This 2026-09-07 intake/ledger snapshot is historical context only. Read current status, counts, NEXT state and hashes from the frozen equity-secondary gate report, source matrix, manifest and bound normalized packages.

# 34-card acquisition and execution ledger — 2026-09-07

Bu ledger, 6 Eylül’deki 34 kartın veri toplama ve çalıştırılabilirlik durumunu
7 Eylül’deki yeni kamu veri edinimiyle günceller. `ACQUIRED` gerçek yerel
dosyanın alındığını, `PARTIAL` en az bir gerekli veri ailesinin geldiğini,
`PIT_PENDING` karar anı sürümünün henüz kanıtlanmadığını, `CAPABILITY_PENDING`
yerel motorun beklenen sözleşmeyi henüz desteklemediğini, `OUT_OF_SCOPE`
mevcut ürün sözleşmesinin dışında olduğunu ve `TEST_NOT_RUN` ekonomik koşunun
henüz yapılmadığını belirtir. İndirilen veri ekonomik alfa veya nedensellik
kanıtı değildir.

## Ortak edinim ve güvenlik sözleşmesi

- Yeni dosyalar `data/research/external/crossasset_public_2026_09_07/` altında
  ve makbuz `data/research_manifests/crossasset_public_acquisition_2026_09_07.json`
  içinde tutulur. Önceki cache ve artefaktlara dokunulmadı.
- Makbuz yalnız sağlayıcı, sorgu kapsamı, sayfalama/HTTP durumu, tarih aralığı
  ve SHA-256 içerir; authorization header, API key, cookie veya credential
  taşıyan URL parametresi içermez.
- Her kartın ekonomik testi için `event_time`, `publication_time`,
  `available_at`, revision/vintage, kaynak hash’i, yürütme maliyeti ve PIT
  evreni ayrı dondurulmalıdır. Geçmişe bugünkü düzeltilmiş değer taşınmaz.
- X04–X06 için alınan araştırma verisi short veya türev işleminin ürün/PAPER/LIVE
  yetkisi değildir. Gerçekçi collateral, borrow, margin, liquidation, funding,
  settlement ve iki bacak fill muhasebesi olmadan ekonomik sonuç üretilmez.

## Kart durumu

| ID | Kart | Edinim / test durumu | Şu an gerçek yerel kanıt | Çalıştırma önkoşulu |
|---|---|---|---|---|
| H01 | Value | PIT_PENDING; TEST_NOT_RUN | Equity SEC/PIT toplama akışı aktif; alan düzeyi makbuz bekliyor | Point-in-time finansallar, kimlik ve holdout |
| H02 | Gross profitability / quality | PIT_PENDING; TEST_NOT_RUN | Equity SEC/PIT toplama akışı aktif; alan düzeyi makbuz bekliyor | PIT kâr, varlık ve muhasebe eşlemesi |
| H03 | Accruals | PIT_PENDING; TEST_NOT_RUN | Equity SEC/PIT toplama akışı aktif; alan düzeyi makbuz bekliyor | PIT accrual/cash-flow alanları ve formül dondurma |
| H04 | Conservative investment / asset growth | PIT_PENDING; TEST_NOT_RUN | Equity SEC/PIT toplama akışı aktif; alan düzeyi makbuz bekliyor | İlan tarihi, birleşme ayrımı, yıllık finansallar |
| H05 | Net payout yield | PIT_PENDING; TEST_NOT_RUN | Equity SEC/PIT toplama akışı aktif; alan düzeyi makbuz bekliyor | Temettü, buyback, issuance ve market-cap PIT akışı |
| H06 | Insider purchases | PIT_PENDING; TEST_NOT_RUN | Equity SEC EDGAR edinimi aktif; acceptance-time eşlemesi bekliyor | Form 4 kabul zamanı, işlem tipi, rutinlik ve holdout |
| H07 | Analyst forecast revisions | DATA/PIT_PENDING; TEST_NOT_RUN | Yerel forecast-vintage dosyası yok | Forecast vintages, yayın zamanı ve estimate horizon |
| H08 | PEAD | DATA/PIT_PENDING; CAPABILITY_PENDING; TEST_NOT_RUN | Release/consensus PIT dosyası yok | Earnings release zamanı, pre-release consensus, olay adapter’ı |
| H09 | Customer/supplier spillover | DATA/PIT_PENDING; TEST_NOT_RUN | PIT ilişki/event feed yok | Tarihsel ticari bağlar ve available-at olay akışı |
| H10 | Filing-text change | DATA/PIT_PENDING; TEST_NOT_RUN | Orijinal filing-vintage/text pipeline yok | Filing sürümü, yayın zamanı ve metin hash’i |
| H11 | Fund-flow forced sales | DATA/PIT_PENDING; TEST_NOT_RUN | PIT fund holdings/flow arşivi yok | Yayınlanmış flow, holdings ve rebalance olayları |
| H12 | Index-change pressure | DATA/PIT_PENDING; TEST_NOT_RUN | Announcement/application PIT arşivi yok | İlan ve uygulama zamanı, üyelik snapshot’ı |
| H13 | Overnight/intraday | DATA/CAPABILITY_PENDING; TEST_NOT_RUN | V5 günlük split-adjusted bars var; intraday/auction truth yok | Intraday/quote history ve execution convention |
| H14 | Turn-of-month ETF exposure | RETROSPECTIVE_DIAGNOSTIC_COMPLETE; PORTFOLIO_CONTRIBUTION_UNTESTED | SPY r4 18-cell receipt/audit; current V3 daily NAV yok | V3 daily NAV/shared ledger; no promotion |
| H15 | Cash merger arbitrage | DATA/CAPABILITY_PENDING; TEST_NOT_RUN | Deal lifecycle archive yok | Terms, amendment, close/break and terminal handling |
| H16 | Post-spinoff pricing | DATA/CAPABILITY_PENDING; TEST_NOT_RUN | Spin-off event/successor archive yok | Event, distribution, successor mapping and lifecycle |
| K01 | MVRV | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Owner Coin Metrics MVRV/market-cap acquisition; receipt/path consolidation pending | Immutable metric vintage, chain cutoff, entity policy |
| K02 | NVT | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Owner Coin Metrics usage/transfer acquisition; adjusted transfer mapping pending | PIT adjusted transfer value and revision policy |
| K03 | Network economic use | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Owner usage/fees acquisition; active/economic address semantics pending | Sybil/spam rules, chain cutoff, metric vintage |
| K04 | Net issuance / dilution | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Owner issuance/supply acquisition; asset-specific supply policy pending | Mint/burn/circulating supply history and PIT snapshot |
| K05 | Token unlocks | DATA/PIT_PENDING; TEST_NOT_RUN | No immutable announcement/vesting receipt yet | Announcement vintage, vesting and actual unlock event |
| K06 | Spot ETF net flows | DATA/PIT_PENDING; TEST_NOT_RUN | Issuer flow publication archive not consolidated | Publication timestamp and creation/redemption history |
| K07 | Exchange inflow/outflow | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Owner flow download has current-flash/mutable status | Immutable historical snapshot, labels, direction and available-at |
| K08 | Stablecoin exchange liquidity | DATA/PIT_PENDING; TEST_NOT_RUN | No frozen contract/bridge/venue ledger | Token identity, mint/burn/bridge filters and PIT flow |
| K09 | Basis/funding/OI spot signal | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Binance public funding/perp sample plus owner broader funding; historical OI/PIT gate pending | No derivative accounting for spot output, but frozen feature sidecar and causal lag required |
| K10 | Liquidation-shock rebound | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Binance derivatives schemas/data probes; historical liquidation/OI completeness pending | Liquidation event, mark/index, side, quantity and venue aggregation |
| K11 | Signed order flow | DATA/CAPABILITY_PENDING; TEST_NOT_RUN | No local trades/quotes/depth/aggressor archive | Immutable microstructure feed, classifier and cost truth |
| K12 | SOPR loss realization | DATA/PIT_PENDING; TEST_NOT_RUN | No UTXO/account/entity-cluster PIT archive | Chain history, entity policy and immutable metric vintage |
| X01 | Bond ETF yield-curve carry/roll-down | PARTIAL; PIT_PENDING; TEST_NOT_RUN | FRED DGS2/5/10 1962/1976–2026-09-03; Treasury 2024 daily 1M–30Y; current IEF page/holdings | Historical IEF ETF bars, duration/distributions and vintage/available-at mapping |
| X02 | Commodity futures-curve carry | DATA_PARTIAL; LICENSE/PIT_PENDING; TEST_NOT_RUN | CME methodology/spec links; Yahoo 429 and Stooq challenge; no usable curve rows | Licensed/entitled dated settlement curve, contract specs, roll and DBC fund receipts |
| X03 | Country ETF PIT value | PARTIAL; PIT_PENDING; TEST_NOT_RUN | Local MCHI/EWJ/EWZ 2016-01-04..2026-07-31 bars + corporate actions; SEC MCHI N-PORT 2025-05-31 (565 rows); FRED USD/CNY/HKD | Historical PIT P/B/P/E/value fields, FX timing, ETF accounting and membership vintages |
| X04 | Equity pairs/stat arb | PARTIAL; DATA/CAPABILITY_PENDING; TEST_NOT_RUN | Local v5 SPY/QQQ bars; FINRA 2024-01-02 short-volume sample | Borrow/locate/recall/financing/margin costs, short fills and isolated research adapter |
| X05 | Crypto dated-futures cash-and-carry | PARTIAL; DATA/CAPABILITY_PENDING; TEST_NOT_RUN | Binance BTCUSDT spot/perp, BTCUSDT_240329 dated contract, index-price archive, current exchange metadata | Full dated curve/expiry/settlement, collateral/margin/fees and two-leg ledger |
| X06 | Spot-perpetual funding carry | PARTIAL; DATA/PIT/CAPABILITY_PENDING; TEST_NOT_RUN | Binance 745 spot + 745 perp hourly rows, 94 funding observations; OI historical query rejected `-1130` | Historical OI/liquidation/margin, mark/index, funding interval, costs and two-leg ledger |

## Executable next batches

1. **X01 diagnostic preflight:** join downloaded Treasury/FRED yield rows to a
   newly obtained historical IEF price/distribution/duration source. Until
   that source is PIT, the current packet supports curve mechanics and a data
   gate only, not an ETF economic run.
2. **X03 country data preflight:** use the existing v5 bars and CA rows plus
   SEC N-PORT report/acceptance dates as an accounting sample. Economic value
   ranking waits for historical valuation vintages; current N-PORT holdings do
   not become historical P/E or P/B.
3. **X05/X06 schema preflight:** use Binance Vision/REST bounded files to
   validate timestamp, contract, funding and index joins. The existing crypto
   owner’s wider acquisition should be reused; do not redownload its funding
   history. No short/derivative economics until the shared two-leg ledger and
   historical risk costs are available.
4. **X04 data gate:** local prices are usable for pair formation diagnostics;
   FINRA short volume is descriptive and cannot substitute for borrow cost.
   A borrow/recall/locate source is required before any return result.

Current executable status is therefore: X01 curve-data preflight, X03
accounting/PIT sample preflight, and X05/X06 timestamp/schema preflight can
proceed from real files; no X04–X06 economic result is authorized by these
artifacts, and no H/K candidate is accepted or promoted.
