# Crypto portfolio expansion: finite batch research report

**Tarih:** 2026-09-05  
**Kapsam:** Mevcut crypto OHLCV ile, V3 mantığı ve mevcut tahsis/risk sözleşmesi korunarak tamamlayıcı alpha ailelerini karşılaştırmak. Bu belge araştırma girdisi ve kanıt raporudur; yeni backtest, strateji kodu, config/PAPER/LIVE değişikliği veya terfi kararı değildir.

## Çerçeve ve veri

Mevcut crypto botu `crypto_low_variance_hysteresis_v1` kullanır. Sinyal 1d, son 22 günlük gerçekleşmiş volatilite, 23 ardışık günlük bar, düşükten yükseğe sıralama, HYST4 (eldeki sembol rank <=4 ise tut), en fazla iki eşit ağırlıklı pozisyon ve 96 saat karar aralığıdır (`src/parayabuck/portfolio/crypto_low_variance_hysteresis.py:22-55`, `:65-119`). Dondurulmuş T1 çalışmasında bu mekanizma turnover'ı 8.72'den 4.00'e, maliyeti %53 azaltmış; full-window getiriyi %5.25'ten %10.54'e yükseltmişti (`docs/crypto_candidate_tournament_validation.md:20-50`). Bu rapordaki tamamlayıcılık iddiası, düşük varyans + HYST4'ün yerine geçmekten çok farklı getiri sürücülerinin ortak sermayeye katkısını ölçme hipotezidir.

Tek ortak veri kaynağı `data/historical/crypto_expanded_1h.csv` (2021-04–2026-07, 705,541 satır; 25 sembol veri izi, configured pool 22) ve PIT scanner/complete-D1 türetimidir (`docs/crypto_candidate_tournament_validation.md:3-10`). 1h boşlukları, sıfır hacim, venue-PIT listeleme eksikliği ve survivorship sonucu sınırlar. 95% idle zero-yield cash, %5 crypto account allocation, native next-hour execution ve ortak risk/maliyet kuralları korunmalıdır. “Aday” sonuçta ölçülmüş değil, aşağıdaki kanıtla araştırmaya değerdir.

## Aile karşılaştırması

| Aile | Sinyal ve yerel tarifi | Mevcut kanıt | Beklenen tamamlayıcılık | Öncelik |
|---|---|---|---|---|
| 1. Low-MAX22 (C3) | `MAX_i(P_i/P_{i-1}-1)` son 22 tamamlanmış D1 getirisi; ascending, 23 bar, HYST4/top2/96h/%5 | Full 3x -0.9612%, recent 3x -1.1538%; standalone REJECT (`crypto_max_candidate_2026_09.md:7-14`, `:78-99`) | Düşük varyanstan farklı uç pozitif sıçrama/lottery boyutu; fakat maliyet ve crash riski yüksek | Yalnız portföy testi |
| 2. Signed-risk22 (C4) | `sum(min(r,0)^2)/sum(r^2)`; 22 `completed dailyreturns`, ascending, HYST4/top2/96h/%5 | Full 3x +4.9203%, recent 3x -1.7351%; R1/R3/R4/R5 false, standalone REJECT fakat portfolio UNTESTED (`crypto_signed_risk_candidate_2026_09.md:5-19`, `:23-35`) | Aşağı-yön asimetrisini low-vol'dan ayırabilir; ortak drawdown dönemlerinde başarısız olabilir | Yüksek bilgi değeri |
| 3. Downside semivariance T3 | `sqrt(mean(min(r,0)^2))`, 22 completed D1, top2 equal, 96h | Full return %6.22, Sharpe .369, 3x sonucu zayıf; T0'a göre yalnız mütevazı kazanç (`crypto_candidate_tournament_validation.md:20-28`, `:32-50`) | Pozitif getiriyi cezalandırmadan kayıp tarafını hedefler; C4 ile yakın ama aynı değil | Portföy katkı ablation |
| 4. Cross-sectional momentum | Göreli 24h/7d/30d veya frozen 7d ranking, long-only spot; eski 1h aday | 1x -%6.54, multi-market -%8.8; aile kapalı (`docs/rejected_strategies.md:82-105`) | Trendli ve UP-UP dönemlerde low-vol'e farklı maruziyet sağlayabilir; eski standalone kaybı aileyi portföy testinden otomatik çıkarmaz | Ancak frozen portföy protokolüyle |
| 5. Time-series trend/momentum | Her varlığın kendi 30d getirisi >0 ve own 15d SMA; haftalık 168h | 1d 1x -%12.2, 2x -%13.9, 3x -%15.5, Sharpe -1.64, WF 1/5 (`docs/rejected_strategies.md:107-124`) | Geniş piyasa trendi sürerse low-vol park etmesinden ayrışabilir; yönsüz/yatay piyasada nakit kuralı gerekir | Düşük; yalnız portföy katkısı |
| 6. Cross-sectional reversal | En negatif 70-bar getiriyi seç, negatif olması şartı, top2 equal, haftalık 168h | 1x +%3.56, 2x -%0.24; WF 5/10, ETH %90.1 PnL; aile kapalı (`docs/rejected_strategies.md:126-142`) | Sert satış sonrası mean-reversion; momentum ve low-vol ile düşük korelasyon hipotezi | Orta, concentration gate şart |
| 7. High-proximity 364 | `close_t/max(close_{t-363..t})`, weekly 168h, top2 equal | 1x/2x/3x -%11.14/-12.90/-14.61, Sharpe -.385; 5/10 WF, pozitif PnL BTC bağımlı (`docs/crypto_bakeoff_validation.md:19-24`, `:43-60`, `:127-136`) | Anchoring/strength devamı varsayımı; low-vol ile mekanik olarak farklı ama yerel kanıt kötü | Öncelik yok; yalnız birleşik stres testi |
| 8. Low-disagreement volume5/prior45 | `mean(vol t-5..t-1, nonzero)/mean(vol t-50..t-6, nonzero)`, zero-volume gates, weekly/top2 | 1x +%5.51 fakat 2x -%1.53, WF 4/10, SOL %80.1 PnL, bull-regime bağımlı (`docs/crypto_bakeoff_validation.md:21-24`, `:43-60`, `:138-141`) | Hacim/attention kanalı low-vol'dan farklı; fakat maliyet ve tek sembol kırılganlığı yüksek | Orta-düşük |

### 1 — Low-MAX22 (C3)

Mekanizma, kısa penceredeki en büyük tek günlük pozitif getiriyi “lottery-like” talep ve aşırı sıçrama riski için kesitsel proxy olarak kullanmaktır. MAX literatüründeki tipik bulgu yüksek geçmiş MAX'ın daha düşük ileri getirilerle ilişkili olmasıdır; [Bali, Cakici & Whitelaw, *Maxing Out*](https://doi.org/10.1016/j.jfineco.2011.03.014) ve MAX literatürü bu yönü destekler. Yerel C3 bunu kriptoda **düşük** MAX'ı seçerek ters yönde uygular; akademik hisse sonucu kriptoya doğrudan kanıt değildir.

Exact frozen rule ve ekonomik sonuçlar C3 belgesindedir. Full 1x referans %2.9635, incumbent %4.3493; full/recent 3x negatif ve recent drawdown incumbent'ın 1.5 katını aşar (`docs/research/crypto_max_candidate_2026_09.md:68-93`). Başarısız standalone gate, bu alpha'nın düşük varyans portföyünde marjinal korelasyon veya ortak drawdown azaltımı hakkında hiçbir şey söylemez. Portföy deneyi ancak C3/C4 HYST4 state'leri aynı engine ve ortak sermaye sınırlarıyla, candidate-vs-incumbent-vs-combination olarak ölçülürse anlamlıdır. Başarısızlık koşulları sıçrama sonrası devamlı trend, kuyruk kaybı, yüksek turnover ve C3'ün low-vol ile aynı sembollere yığılmasıdır. Gerekli veri mevcut OHLCV'dir; venue-PIT ve gerçek fill dağılımı halen eksiktir. Durum: **frozen, ekonomik kanıt var, standalone REJECT, portfolio UNTESTED**.

### 2 — Signed-risk22 (C4)

C4 negatif getirilerin kare enerjisini toplam kare enerjiye böler. Küçük skor, son dönemde aşağı yönlü hareketin toplam hareket içindeki payının düşük olduğunu ifade eder; bu bir realized signed-jump ölçümü değildir. [Markowitz, *Portfolio Selection*](https://doi.org/10.1111/j.1540-6261.1952.tb01525.x) ve downside-risk geleneği portföy riskinin ortak hareketlere bağlı olduğunu destekler; C4'ün kendi başarısını kanıtlamaz.

Exact frozen rule: 22 tamamlanmış daily returns, ascending, tie-break symbol, HYST4/top2/96h/5%; C4 novelty rho incumbent ile -0.357, raw top-2 aynılık %5.59, T3 ile rho .164 ve aynılık %9.68 (`docs/research/crypto_signed_risk_candidate_2026_09.md:5-17`). Full 3x +%4.9203 incumbent +%2.2523 iken recent 3x -%1.7351 ve drawdown gate'i bozulur (`:23-35`). Bu ayrışma portföy katkısı için iyi adaylık sinyalidir, fakat recent zarar ve shared-capital risk ölçülmeden terfi gerekçesi değildir. Başarısızlık koşulları geniş tabanlı crypto selloff, aynı düşük-downside coinlerin low-vol ile çakışması, numerator/denominator sıfır veya eksik barlar ve cost-feedback'tir. Veri mevcut OHLCV ile hesaplanabilir; ek veri gerekmez. Durum: **portfolio testine en güçlü aday; standalone REJECT korunur**.

### 3 — Downside semivariance T3

T3, pozitif ve negatif getiriyi simetrik cezalandıran varyans yerine yalnızca negatif getirilerin RMS'sini sıralar. [Brown, Cohen, Tang & Yam, “Taylor’s law of fluctuation scaling for semivariances”](https://doi.org/10.1073/pnas.2108031118) semivariance ölçümünün istatistiksel özelliklerini açıklar; bu çalışma crypto getirisi veya Sortino üstünlüğü kanıtı değildir. Repo frozen formula'sı `sqrt(mean(downside_t²))`, 22 complete returns, top2 equal ve 96h'dir (`docs/crypto_candidate_tournament_validation.md:20-28`). Full 1x %6.22 ile T0 %5.25'i geçer, ancak T2/T4 gibi varyans ailesi içi değişimler genel olarak düşük bilgi getirisi vermiştir (`:32-50`).

Beklenen yardımcı rejim, pozitif trendi korurken asimetrik aşağı şokların arttığı dönemdir. Başarısızlık, sık küçük negatif dönüşlerin skoru yapısal olarak düşük göstermesi, aynı varlıkların C4/low-vol ile seçilmesi ve 2x/3x maliyetidir. Exact rule sabittir; yeni pencere/eşik uydurulmayacak. Mevcut OHLCV yeterli, fakat bağımsız aile sayılabilmesi için C4 ile overlap ve marjinal portföy katkısı ölçülmelidir. Durum: **known implementation, standalone weakly positive, portfolio candidate**.

### 4–5 — Momentum ailesi: cross-sectional ve time-series

Cross-sectional momentum akranlara göre geçmiş kazananı; time-series momentum ise varlığın kendi geçmiş getiri işaretini kullanır. [Fieberg, Liedtke, Metko & Zaremba, *Cryptocurrency factor momentum*](https://www.tandfonline.com/doi/full/10.1080/14697688.2023.2269999) 2014–2022 arasında 3,900’den fazla coin üzerinde geçmiş kazananların üstünlüğünü raporlar, fakat long-short/çok geniş evren sonuçlarını bu long-only 22-symbol Alpaca havuzuna taşımak doğru değildir. [Moskowitz, Ooi & Pedersen, *Time series momentum*](https://doi.org/10.1016/j.jfineco.2011.11.003) varlık-kendi geçmişi trend etkisini tanımlar. 2025 crypto çalışması momentumun özellikle UP-UP rejiminde yoğunlaştığını bulur; bu, koşulsuz çalıştırma yerine frozen rejim hipotezini destekler ([State transitions and momentum](https://doi.org/10.1016/j.frl.2025.108356)).

Repo sonuçları iki ailenin standalone olarak kapatıldığını gösterir: cross-sectional momentum 1x -%6.54 ve -%8.8; time-series 1d 1x -%12.2, 3x -%15.5, WF 1/5 (`docs/rejected_strategies.md:82-124`). Bunlar mevcut low-vol/HYST4'ün tamamlayıcı olamayacağını kanıtlamaz; ancak portfolio testinde yalnızca mevcut frozen tarifler, ortak OHLCV, aynı execution ve önceden belirlenmiş candidate weights kullanılmalıdır. Beklenen yardımcı rejim sürdürülen UP-UP trendidir; başarısızlık yatay/UP-DOWN geçişleri, işlem maliyeti ve 2022 tipi ayı piyasasıdır. Ek veri gerekmez. Durum: **standalone family closed; portfolio diversification test optional, no retuning**.

### 6 — Cross-sectional reversal

Reversal, aşırı kaybedenlerin geçici aşırı tepki sonrası toparlanacağı varsayımıdır; [De Bondt & Thaler, *Does the Stock Market Overreact?*](https://doi.org/10.1111/j.1540-6261.1985.tb05004.x) klasik kaybedenlerin ileri dönem üstünlüğünü raporlar. Repo exact rule 70-bar negatif trailing return, en negatif iki coin, weekly 168h; 1x +%3.56 fakat 2x negatif, WF çoğunluk değil ve ETH %90.1 PnL (`docs/rejected_strategies.md:126-142`).

Yardımcı rejim sert ama geçici coin-spesifik satış; momentum/low-vol ile farklı davranabilir. Başarısızlık kalıcı iflas/delisting, likidite kuruması, tek coin yoğunlaşması ve short olmayan long-only yapı nedeniyle düşüşün devamıdır. Veri fiyatlardan mevcut; ancak coin survivorship ve historical venue-PIT büyük sınırlamadır. Portfolio testinde ETH/SOL/BTC jackknife, 2x/3x cost ve common-capital marginal DD zorunludur. Durum: **portfolio-only exploratory**.

### 7 — High-proximity 364

Bu aile 364 günlük yüksek seviyeye yakınlığı anchoring/relative-strength göstergesi sayar. George & Hwang çizgisindeki 52-week-high mekanizmasının kaynak özeti [Wiley Financial Review](https://doi.org/10.1111/fire.12371) içindedir. Yerel exact rule ve sonuçlar: `close/max(last 364)`, weekly/top2; full net -%11.14, -%12.90, -%14.61 (1x/2x/3x), Sharpe -.385 (`docs/crypto_bakeoff_validation.md:19-24`, `:43-60`).

Trend devam ederse yardımcı olabileceği varsayılır; ancak testte hem BTC rejimlerinde negatif ve pozitif PnL BTC'ye bağımlıdır (`:90-100`, `:127-131`). OHLCV yeterli, yeni parametre üretmek gerekmez. Durum: **low priority; only if a predeclared portfolio covariance test is requested**.

### 8 — Low-disagreement volume5/prior45

Hacim oranı attention/disagreement şokunu ölçer; düşük taraf, son 5 gün hacminin önceki 45 güne göre düşük olduğu coinleri seçer. [Gervais, Kaniel & Mingelgrin, *The High-Volume Return Premium*](https://doi.org/10.1111/0022-1082.00349) hacim şoklarının sonraki getirilerle ilişkisini kurar; bu repo adayı düşük tarafı kullandığı için kaynak yalnız mekanizma dayanağıdır.

Exact rule, zero-volume gates ve sonuçlar `docs/crypto_bakeoff_validation.md:21-24`. 1x +%5.51 iken 2x -%1.53, WF 4/10; pozitif PnL'nin %80.1'i SOL'dan gelir ve bull-regime dışı edge negatiftir (`:43-60`, `:138-141`). Mevcut OHLCV ile ölçülebilir, fakat Alpaca volume biriminin ekonomik anlamı sınırlıdır. Başarısızlık düşük hacim = işlem yapılamazlık, zero-volume bias, SOL yoğunlaşması ve maliyet artışıdır. Durum: **portfolio-only, low priority**.

## Gerçekten yeni, fakat henüz executable olmayan iki uzantı

**Amihud illiquidity:** Günlük `ILLIQ = mean(|r_t| / dollar_volume_t)` ile düşük veya yüksek illiquidity sıralaması, mevcut OHLCV'deki close/volume ile hesaplanabilir. [Amihud, *Illiquidity and stock returns*](https://doi.org/10.1016/S1386-4181(01)00024-6) temel kaynaktır; crypto uygulamalarında bunun önemli sınırlaması küçük/illiquid coinlerde beklenen alpha'nın işlem maliyeti ve fiyat etkisi tarafından yenmesidir ([Cakici et al.](https://doi.org/10.1016/j.irfa.2024.103244)). Bu nedenle `high-illiquidity long` bir alpha değil, önce maliyet/stress ve tradability araştırmasıdır; `low-illiquidity` ise low-vol ile muhtemel duplicate olabilir. Frozen parameter üretmeden önce dollar-volume birimi, zero-volume handling, venue-PIT ve fill calibration kilitlenmelidir.

**Eş-bütünleşme / spot pairs:** Engle–Granger veya Johansen ile log-price spread ve error-correction; aşırı pozitif spread'de pahalı tarafı satıp ucuz tarafı almak klasik pairs trading'dir. Crypto cointegrated-pairs çalışmaları [Keilbar & Zhang](https://doi.org/10.1007/s42521-021-00027-5) ve [Tadi & Witzany](https://doi.org/10.1186/s40854-024-00702-7) bu mekanizmayı gösterir. Mevcut botun spot long-only tek bacaklı hedef portföyüyle doğrudan uyumlu değildir: gerçek eş-bütünleşme stratejisi iki bacak, short/borçlanma veya hedge, ortak execution ve spread stop/exit gerektirir. Spot-only long variant, “ucuz bacağı al ve pahalı bacağı satma” varsayımını taşıyamaz; bu yüzden yeni veri kadar işlem yeteneği de gerektirir. Mevcut 1h OHLCV yalnızca aday keşfi için yeterlidir; production-ready araştırma değildir.

## Veri/işlem boşlukları

Funding/basis carry için perpetual/futures mark/index price, funding rate, contract metadata, collateral, liquidation ve fee history yoktur; spot OHLCV ile proxy üretmek ekonomik olarak savunulamaz. Orderflow için trades, aggressor side, bid/ask depth, spread ve venue-level timestamps yoktur. Signed-jump için mevcut 24 hourly gözlem/gün in-fill ayrıştırmasına yetmez; repo açıkça 5-minute yaklaşık 78–288 gözlem gereksinimini, saatlik veri gürültü tabanını ve SOL/XRP boşluklarını kaydeder (`docs/crypto_bakeoff_validation.md:24-31`). Bu üç alan için paid data/download veya proxy ile doldurma yapılmayacak.

## Toplu çalışma için sonlu giriş ve karar kapıları

Toplu portföy araştırması sekiz aileyi ve ayrıca Amihud/cointegration feasibility fişlerini kapsamalı; yeni pencere, eşik, cadence veya optimizer sweep yapılmamalı. Her candidate için aynı PIT universe, aynı 1h execution, completed-D1 cutoff, 96h karar ve mevcut allocation/risk policy kullanılmalı. Ayrı engine resetleri, exact candidate provenance, fills/fees/turnover, net-return correlation, marginal drawdown/Sharpe, overlap ve top-symbol jackknife kaydedilmeli.

Zorunlu karşılaştırmalar: incumbent tek başına; her aday tek başına yalnız tanı amaçlı; incumbent + aday ortak %5 crypto sleeve; aynı sermayede candidate weights için iki alternatif yalnız sonuçlardan önce dondurulmuş olmalı. Sonuçlar rejimlere (BTC up/flat/down), tam ve recent pencerelere, 1x/2x/3x maliyete ve walk-forward fold'lara ayrılmalı. Bir aday standalone REJECT olsa bile yalnız portfolio contribution sonucu ile “tamamlayıcı” denebilir; negative standalone sonucu pozitif çeşitlendirme kanıtı değildir.

**Sonuç:** En yüksek bilgi kazanımı C4 signed-risk22 ve T3 downside'ın incumbent ile ortak sermayede denenmesidir; C3 MAX22 de düşük korelasyon ihtimali nedeniyle aynı batch'te tutulur. Reversal ve low-disagreement ikinci halka adaylarıdır; momentum, proximity ve illiquidity ancak cost/concentration gates ile sınırlı keşif olarak kalır. Time-series ve cross-sectional momentum literatürde anlamlı olsa da bu checkout'taki standalone başarısızlıklar nedeniyle yeni parametre araması kapalıdır. Eş-bütünleşme, funding/basis ve orderflow “OHLCV ile tamamlandı” sayılamaz; gerekli bacak/veri yeteneği kurulmadan portföy adayına dönüştürülmemelidir. Hiçbir sonuç Borsa V3 mantığını, mevcut tahsisi, PAPER veya LIVE durumunu değiştirmez.
