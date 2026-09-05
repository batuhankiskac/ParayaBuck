# C4 signed-risk candidate — 2026-09

**Durum:** Dondurulmuş standalone ekonomik değerlendirme tamamlandı; portföy katkısı değerlendirmesi yapılmadı. Bu kayıt tek başına kârlılık, PAPER veya LIVE kararı değildir.

C4, her sembolün son **22 tamamlanmış günlük basit getirisinde** şu skoru hesaplar:

`sum(min(r, 0)^2) / sum(r^2)`

23 ardışık tamamlanmış D1 fiyatı gerekir. Nonfinite fiyat/getiri, tarih boşluğu veya sıfır payda sembolü o kararda uygunsuz yapar. Skorlar artan sırada, sembol bağ kırıcıyla sıralanır. Bu, yüksek frekanslı realized signed-jump ölçüsünün basitleştirilmiş adı değildir; o literatür C4 uygulamasına doğrudan kanıt sayılmaz.

Incumbent düşük varyans stratejisinin HYST4 retention rank 4, top-2 eşit ağırlık, 96 saat cadence, `%5` toplam crypto allocation, native risk ve cost sözleşmesi değiştirilmez; C4’te değişen tek alpha skorudur. Native engine ham 1h veriyi kullanır, tamamlanmış D1 sinyal geçmişini causal cutoff ile kurar ve kararı sonraki uygun saat barı açılışında fill eder. Pencere sonunda zorunlu satış yoktur; pozisyonlar native final mark ile kaydedilir. Idle cash faizsizdir.

Primary ekonomik pencere `2021-04-01T00:00:00Z..2026-07-31T23:59:59Z`, recent pencere `2024-01-01T00:00:00Z..2026-07-31T23:59:59Z`’dir. Her hücre yeni engine instance ile çalışır: üç arm (`signed_risk`, incumbent `low_variance`, 20-tamamlanmış-D1 median(close×volume) azalan sıralı `dollar_volume`), iki pencere ve 1x/2x/3x maliyet olmak üzere 18 hücre.

Ekonomi öncesi novelty denetimi 487 paired snapshot (465 geçerli korelasyon, 22 undefined) üzerinde açık kaldı: incumbent ile median rho `-0.357`, raw top-2 aynılık `%5.59`; T3 downside comparator ile rho `0.164`, aynılık `%9.68`. Bu istatistikler C4’ün ekonomik olarak başarılı olduğunu göstermez.

Ekonomik sınıflandırma önceden sabittir: R1, C4 net return’ünün full ve recent 3x’te pozitif olması; R2, full 1x Sharpe üstünlüğü veya tanımlı drawdown/CAGR koşulu; R3, her iki pencerede 3x drawdown’ın incumbent’ın 1.5 katını aşmaması; R4, her iki pencerede 2x net return’ün negatif olmaması; R5, her iki pencerede 3x net return’ün dollar-volume comparator’dan düşük olmamasıdır. Herhangi bir başarısız gate `REJECT`, tümü başarılıysa yalnız `RETAIN_FOR_RESEARCH` verir; otomatik PAPER/LIVE terfisi yoktur.

Bu R1–R5 sınıflandırması C4’ü tek başına ve aynı sermaye pencerelerinde değerlendirir. C4’ün mevcut equity/crypto portföyüne eklenmesiyle oluşacak birleşik getiri, net getiri korelasyonu, marjinal drawdown veya Sharpe katkısı, ortak sermaye/risk limitleri ve önceden dondurulmuş nedensel rejim seçicisi bu çalışmada test edilmedi. Bu nedenle standalone `REJECT`, C4’ün portföy çeşitlendiricisi olarak elenmesi sonucunu vermez; böyle bir karar için ayrı bir portföy katkısı protokolü ve kontrolü gerekir. Markowitz’in portföy seçim çerçevesi de riskin varlıkların birlikte hareketine bağlı olduğunu vurgular: [Markowitz, “Portfolio Selection” (1952)](https://doi.org/10.1111/j.1540-6261.1952.tb01525.x). Bu kaynak C4 için performans veya çeşitlendirme kanıtı değildir.

Veri, configured fixed crypto pool ve bar-availability/scanner proxy’sidir; gerçek venue-PIT listing completeness iddiası yapılmaz. 1h boşlukları, sıfır volume saatleri, survivorship ve spot likidite maliyetleri sonucu sınırlayabilir. Sonuçlar yalnız bu frozen implementation’a aittir ve genel olarak düşük asimetri veya düşük volatilite kripto stratejilerine genellenmez.

## Denetlenmiş ekonomik sonuç

Canonical attempt `attempt_01_canonical` 18/18 hücreyi tamamladı. Bağımsız audit exact cell matrix, actual fills/fees, cash-equity ledger, terminal final-mark ve turnover parity kontrollerini geçti; MAX kontrolüyle 0 mismatch bulundu. 15 economic test ve 10 novelty test geçti. Raw canonical output: `data/research/crypto_signed_risk_economic_2026_09/attempt_01_canonical/economic_results.json`; audit artifact: `economic_results.audit.json`.

Son sınıflandırma **REJECT**: R1 `false`, R2 `true`, R3 `false`, R4 `false`, R5 `false`. R1, R4 ve R5 recent pencerede başarısızdır. R3 full pencerede geçer (`C4 DD 8.9559% <= 1.5 × incumbent DD 7.2528%`), recent pencerede kalır (`8.7267% > 1.5 × 3.9618%`). Bu, iki drawdown penceresinin de başarısız olduğu anlamına gelmez.

3x cumulative account-window return full’de C4 `%4.9203`, incumbent `%2.2523`, dollar-volume `%3.3939`; recent’te C4 `-%1.7351`, incumbent `%0.8664`, dollar-volume `%0.8398`’dir. C4 full 1x return `%12.2815`, recent 1x `%1.1491`; full/recent 1x drawdown sırasıyla `%8.3403` ve `%7.0941`’dir. Bunlar cumulative window/account sonuçlarıdır; yıllıklaştırılmış getiri değildir. `%5` toplam allocation, native next-hour execution, faizsiz cash ve forced-sale olmadan final mark sözleşmesi korunmuştur.

Bu sonuç yalnız frozen C4 implementation’ına aittir. Configured pool ve bar-availability/scanner proxy gerçek venue-PIT kanıtı değildir; PAPER/LIVE terfisi yoktur. Kaynak, config, protocol, manifest ve raw output değiştirilmemiştir.

### Kapsam sınırı

Birleşik portföy katkısı, shared-capital risk davranışı, net-return correlation, marginal drawdown/Sharpe ve causal regime selector için sonuç yoktur; bunlar standalone R1–R5 `REJECT` kararından ayrı, bekleyen araştırma sorularıdır.
