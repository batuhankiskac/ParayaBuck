# Farklı piyasa koşulları için strateji adayları

Tarih: 5 Eylül 2026. İncelenen yerel kaynak: `main`, `ed11beba916202dcb4986853e848652af6e7a3ef`.

Bu belge bir aday taraması ve araştırma taslağıdır; dondurulmuş deney manifesti veya ekonomik sonuç değildir. Üç GPT-5.6 Luna alt ajanı mevcut stratejileri, hisse/ETF literatürünü ve kripto literatürünü ayrı ayrı araştırdı. Ana ajan önerileri güncel yerel raporlar ve birincil kaynaklarla karşılaştırdı. Yeni backtest, veri indirme, broker işlemi, strateji kaydı veya PAPER/LIVE değişikliği yapılmadı. Başlangıçta çalışma ağacında dört eski survivorship JSON değişikliği vardı; bunlar korunmuştur. Sunucudaki çalışan yapılandırma doğrulanmadı.

**ÖNEMLİ DÜZELTME — C1 önceliği askıya alındı.** İlk sürüm, arşivdeki [strategy foundation C1 çalışmasını](../../data/research/strategy_foundation_v1/report.md) ve aynı dokuz ETF üzerindeki B1 + 12 aylık kendi fiyat trend filtresini atlamıştı. Bu nedenle çoklu varlık savunmacı ETF tahsisini yeni bir birinci öncelik olarak sunmak doğru değildir. [C1 uygulanabilirlik fişi](candidate_feasibility_2026_09_05.md) sınıflandırmayı `C1 NO_NEW_ECONOMICS_DUPLICATE_FAMILY` olarak kaydeder. C1, gerçekten yeni bir ekonomik mekanizma ve veri/muhasebe geçişi kanıtlanana kadar beklemede tutulmalıdır. C2 de bu belgeyle kabul edilmiş sayılmaz; yalnızca bağımsız yenilik geçidinden sonra incelenebilir.

Botun sürekli açık olması mümkündür; her koşulda pozisyon açmak veya kâr üretmek ayrı iddialardır. Uygun aday bulunmayan koşullarda nakitte beklemek de geçerli bir sonuç olmalıdır.

**Mevcut durum ve tekrar edilmemesi gereken çalışmalar**

Yerel [ayarlar](/Users/batuhankiskac/Desktop/ParayaBuck/config/settings.yaml:137) hissede `equity_residual_momentum_v3`, kriptoda `crypto_low_variance_hysteresis_v1` seçiyor. Hisse stratejisi günlük veriden haftalık residual momentum; kripto stratejisi günlük veriden 22 günlük düşük oynaklık sıralaması ve 96 saatlik karar aralığı kullanıyor. Kripto stratejisinin temel hipotezi momentum değildir. Azami tahsisler sırasıyla %55 ve %5; hesap brüt sınırı %60, asgari nakit %40. Bunlar gerçekleşmiş pozisyon ağırlıkları değil, ayar sınırlarıdır.

- [M3 ekonomik raporu](/Users/batuhankiskac/Desktop/ParayaBuck/docs/equity_m3_economic_2026_09_r1.md:3): veri ve doğruluk geçişlerine rağmen `M3_ECONOMIC_GATE_FAIL`. Çok faktörlü residual momentum bu incelemede yeni aday sayılmadı.
- [M8/M10 takip raporu](/Users/batuhankiskac/Desktop/ParayaBuck/docs/equity_v3_followups_2026_08_report.md:29): hisse/sektör ETF kolunun kontrolle aktif getiri korelasyonu 0,693842; önceden belirlenen <0,60 bağımsızlık eşiğini geçememiş. PEAD ise gerekli olay verisi bulunmadığı için bloke edilmiş. Bunlar yeni keşif gibi sunulmamalı.
- [Reddedilen stratejiler](/Users/batuhankiskac/Desktop/ParayaBuck/docs/rejected_strategies.md:82): kripto kesitsel momentum, zaman serisi momentumu, 10 haftalık kaybedenleri alma, zirveye yakınlık ve anormal hacim aileleri elenmiş. ATR, RSI veya yeni pencere eklemek tek başına yeni ekonomik mekanizma değildir.
- [Hisse yol haritası](/Users/batuhankiskac/Desktop/ParayaBuck/docs/equity_v3_alpha_roadmap_2026_08_report.md:24) kısa vadeli reversal ve çeşitli momentum değişikliklerini de içeriyor. Aynı sonuçlara bakarak yeni eşik seçmek araştırma önceliği olmamalı.

**Aday listesi**

Hedef koşullar aşağıdaki tasarımların hipotezidir; ParayaBuck üzerinde doğrulanmış rejim performansı değildir. Öncelikler beklenen getiriye değil, bilgi kazanımına, mevcut stratejiden farkına ve uygulanabilirliğine göre verildi.

| Öncelik | Aday | Araştırılacak koşul | Mevcut bottan farkı | İlk engel |
|---|---|---|---|---|
| Beklemede | Çoklu varlık savunmacı ETF tahsisi (C1) | Hisse zayıflığı; farklı varlıkların ayrışması | Arşiv C1 ile aynı dokuz ETF ve kendi-fiyat trend ailesi | Gerçek yenilik ve ETF veri/muhasebe eşliği |
| 1 | Düşük betalı hisse seçimi | Yüksek beta hisselerin zorlandığı dalgalı dönemler | Momentum sıralaması yerine düşük piyasa duyarlılığı | Scanner kapsamı ve gerçekten ek katkı sağlama |
| 2 | Kalite + değer | Değerleme ayrışması ve uzun vadeli normalleşme | Fiyat türevi yerine yayımlanmış şirket finansalları | Geçmişte bilinen veriyle finansal özellik görünümü |
| 3 | Bilanço sonrası sürüklenme — PEAD | Şirket haberlerinin ayrıştığı dönemler | Tarihi belli kazanç sürprizi | Eski M10 veri engeli; güncel etkinlik belirsizliği |
| Gelecek yetenek | Gerçek iki bacaklı pairs trading | Göreli fiyatların geçici ayrıştığı yatay piyasalar | Piyasa yönünden ziyade iki varlığın farkı | Short/ödünç ve iki bacaklı yürütme |
| Gelecek yetenek | Kripto spot-vadeli taşıma getirisi | Vadeli priminin toplam maliyeti aştığı dönemler | Fiyat tahmini yerine vadeli-spot fiyat farkı | Türev, teminat ve tasfiye altyapısı |

**1. Çoklu varlık savunmacı ETF tahsisi**

Örnek araştırma evreni geniş hisse, altın, tahvil ve emtia ETF'lerini kapsar: SPY, EFA, GLD, TLT ve DBC gibi. Semboller yatırım önerisi veya doğrulanmış işlem listesi değildir. Aylık karar zamanında, yalnız tamamlanmış geçmiş verilerle tek bir orta vadeli trend ölçütü hesaplanır. Önceden belirlenen koşulu sağlayan varlıklara mevcut pozisyon sınırları içinde ağırlık verilir; uygun olmayan pay nakitte kalır. Eşik kaybında bir sonraki geçerli yürütmede çıkılır. Kesin pencere, eşik, ağırlık ve çıkış sözleşmesi sonuç görülmeden belirlenmelidir.

[Faber'in taktik varlık tahsisi çalışması](https://mebfaber.com/white-papers/) bu aileye tarihsel dayanak sağlar. Buradaki öneri çalışmanın birebir replikasyonu değildir. M8'den farkı, yalnız ABD hisse/sektör ETF'leri arasında dolaşmak yerine hisse dışı varlıkları kapsamasıdır. Sinyal hâlâ trend tabanlıdır; yeni bağımsız alfa iddiası yapılmıyor.

Yerel [ETF manifesti](/Users/batuhankiskac/Desktop/ParayaBuck/data/research/equity_etf_foundation_1d_v1/manifest.json) dokuz varlık için 2016–2026 panelini tanımlıyor ve her sembol için 2.666 bar, `adjustment=all` ile hash bildiriyor; tam veri dosyası bu düzeltmede okunmadı. Manifest SIP/IEX birleşimi ve temettü dahil düzeltilmiş fiyatlar bildiriyor; bunları normal corporate-action nakit akışlarıyla birlikte kullanmak çift sayım yaratabilir. Önce bu muhasebe sözleşmesi doğrulanmalı. SPY şu anda gözlem referansı; bu adayda işlem rolü ayrıca tanımlanmalı. Para piyasası ETF'si eklenirse mevcut %40 nakit tamponunun yerine otomatik sayılamaz.

Temel başarısızlık koşulları yatay piyasada sık yön değişimi ve hisse/tahvilin birlikte düştüğü şoklardır. Altın, tahvil veya emtia her düşüşte koruma sağlamaz. Kontroller: aynı ETF evreninin basit sabit tahsisi, mevcut V3 ve aynı risk/maruziyet düzeyine indirilmiş V3. Yalnız daha az hisse tutmaktan gelen düşüş azalması yeni alfa diye raporlanmamalı.

**2. Düşük betalı hisse seçimi**

Likidite koşullarını sağlayan hisseler, SPY'ye geçmiş duyarlılıklarıyla sıralanır; aylık olarak düşük beta grubundan sektör yoğunlaşması sınırlı bir sepet seçilir. Pozitif momentum koşulu eklemek ilk adayın kapsamına alınmaz. Çıkış aylık sıralama, veri uygunluğu veya mevcut risk kurallarıyla yapılır. Bu, V3 içindeki ters oynaklıkla ağırlıklandırmadan farklıdır: değişen unsur seçilen hisselerdir.

[Understanding Defensive Equity](https://www.nber.org/papers/w20591), düşük beta/oynaklık davranışının değerleme ve kârlılık özellikleriyle örtüşebildiğini gösteriyor. Bu nedenle kalite + değer adayından da bağımsız olduğu varsayılmamalı. Akademik long-short veya kaldıraçlı BAB sonuçları, burada önerilen kaldıraçsız uzun pozisyon sepetine taşınamaz.

Fiyat verisi ihtiyacı görece düşüktür. Ancak mevcut scanner'ın ATR alt sınırı sakin hisseleri dışlayabilir; görülebilen ve işlem yapılabilen evrenin aday için yeterliliği ölçülmeli. Finans, kamu hizmetleri gibi sektörlerde yığılma ve yüksek beta rallilerinde geride kalma başlıca risklerdir. Kabul sorusu, aynı riske ölçeklenmiş V3'e göre net portföy katkısı olup olmadığıdır; yalnız düşük oynaklık yeterli değildir.

**3. Yavaş kalite + değer seçimi**

Üç aylık karar taslağında şirketler, o anda yayımlanmış finansal tablolardan hesaplanan brüt kâr/varlıklar ve bir değerleme ölçüsüyle sıralanır. İlk deneyde tek kalite, tek değer ölçüsü seçilir; çok sayıda rasyodan sonradan kazanan bileşim çıkarılmaz. Likit, sektör dağılımı sınırlı üst grup alınır; dönemsel yenileme veya veri/risk geçersizliğinde çıkılır.

[Novy-Marx'ın kârlılık primi araştırması](https://www.nber.org/papers/w15940) kalite ve değer birleşimi için temel kaynaktır. Bu adayın güçlü tarafı residual momentumdan farklı bilgi kullanmasıdır. Sektör yoğunlaşması, ucuz görünen sorunlu şirketler ve uzun süre devam eden büyüme hissesi üstünlüğü başarısızlık koşullarıdır. Düşüş piyasasına karşı güvence sağlamaz.

[SEC EDGAR API'leri](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) şirket bildirimleri ve XBRL finansalları sunuyor; ücretli sağlayıcı otomatik olarak şart değildir. Ancak ücretsiz erişim hazır point-in-time veri anlamına gelmez. Bildirimin piyasaya ulaşma zamanı, ilk sürüm/restatement ayrımı, CIK-ticker geçmişi ve muhasebe tanımları gerekir. Bugünün düzeltilmiş finansal değerleri geçmiş karar tarihine yazılamaz. Düşük işlem sıklığı maliyeti sınırlar; veri mühendisliği maliyetini ortadan kaldırmaz.

**4. PEAD: olumlu bilanço sürprizi sonrası devam hareketi**

Bu aday yeni değildir; M10'un veri nedeniyle çalıştırılamayan araştırma hattıdır. Gerçek açıklama zamanında bilinen EPS ile açıklama öncesi beklenti karşılaştırılır. Önceden tanımlanan olumlu sürprizde, açıklamadan sonraki ilk izin verilen işlem zamanında alım düşünülür; sabit olay sonrası süre veya risk geçersizliğinde çıkılır. Çeyrek bitiş tarihinden ya da açıklama öncesi fiyattan işlem varsayılamaz.

[Bernard ve Thomas'ın çalışması](https://www.jstor.org/stable/2491062) tarihsel başlangıç noktasıdır. Bunun yanında [Christensen, Timmermann ve Veliyev'in 2026 makalesi](https://arxiv.org/abs/2601.08962), incelediği açıklama sonrası stratejinin 2016 sonrası sonuçlarını etkin fiyat oluşumuyla uyumlu buluyor. Bu farklı örneklem/tasarım bütün PEAD biçimlerini çürütmez; eski anomalinin bugün aynı şekilde sürdüğünü varsaymayı da desteklemez.

Gerekli EPS, beklenti sürümleri, gerçek açıklama saati ve olay görünümü mevcut M10 raporunda eksik. SEC bildirimleri tek başına geçmiş analist konsensüsünü sağlamaz. Beklenti yerine geçmiş kazançlardan sürpriz türetmek ayrı bir adaydır. Açılış boşluğu, kalabalık işlem ve piyasa düşüşü riskleri nedeniyle önceliği kalite + değerden düşüktür.

**5. Gerçek pairs trading: gelecekte ayrı yetenek**

Ekonomik olarak ilişkili iki hissenin geçmiş fiyat farkı modellenir; olağandışı ayrışmada ucuz bacak alınır, pahalı bacak short edilir. Yakınsamada, ilişkinin bozulmasında veya sabit azami sürede iki bacak kapatılır. [Gatev, Goetzmann ve Rouwenhorst](https://www.nber.org/papers/w7032) tarihsel dayanak sağlar; eski örneklem bugünkü borçlanma ve işlem maliyetlerini doğrulamaz.

Mevcut [TargetPortfolio sözleşmesi](/Users/batuhankiskac/Desktop/ParayaBuck/src/parayabuck/portfolio/models.py:55) negatif ağırlıkları kabul etmiyor. Ödünç bulunabilirliği ve maliyeti, short geri çağırma, iki bacağın farklı anda dolması ve ortak risk muhasebesi gerekir. Yalnız ucuz bacağı almak aynı strateji değildir. Ayarlarda SH/PSQ/DOG ters ETF isimlerinin bulunması, tek hisse çiftindeki short bacağını veya nötrlüğü sağlamaz. Yatay piyasa için ilginç, bugünkü ürün için doğrudan uygulanabilir değil.

**6. Kripto spot-vadeli taşıma getirisi: gelecekte ayrı yetenek**

En basit taslak, aynı varlıkta spot alım ile vadeli satışını eşleştirir; vadeli primi bütün ücret, spread, finansman ve teminat maliyetlerini yeterli payla aşıyorsa işlem değerlendirilir. Vadede/yakınsamada veya önceden belirlenmiş risk koşulunda çıkılır. Perpetual funding stratejisi bununla özdeş değildir; değişken ödeme akışı için ayrı sözleşme gerekir.

[BIS Crypto Carry](https://www.bis.org/publications/working-paper-1087-crypto-carry), mekanizmayı ve teminat/tasfiye risklerini inceliyor. Fiyat yönünden farklı getiri kaynağıdır; risksiz arbitraj değildir. [Alpaca spot belgeleri](https://docs.alpaca.markets/us/docs/crypto-trading) kriptoyu short veya kaldıraçla işlemeyi desteklemiyor. Bu aday mevcut spot yolunda uygulanamaz; türev erişimi, teminat, fonlama geçmişi, borsa riski ve iki bacaklı yürütme için ayrı çalışma gerektirir.

**Kripto OHLCV aramasının sonucu**

Bu taramada mevcut kapanmış ailelerden yeterince ayrışan, normal OHLCV ile hemen araştırma önceliğine alınacak güçlü bir yeni kripto adayı belirlenemedi. Bu, böyle bir strateji olmadığına dair kanıt değildir. Sık işlem yapan şok-reversal ve sıkışma-breakout önerileri maliyet ve önceki retlerle karşılaştırılınca geri plana alındı.

[Alpaca'nın yayımladığı ilk kademe tarifesi](https://docs.alpaca.markets/us/docs/crypto-fees) maker için taraf başına %0,15, taker için %0,25. Dolayısıyla yaklaşık alış-satış ücret yükü %0,30–%0,50; spread ve fiyat kayması buna eklenir. Gerçek hesap kademesi sorgulanmadı. Limit emir vermek maker dolumu garantilemez. Yeni kripto kaynağı aranacaksa zaman damgalı olay/on-chain verisi veya türev taşıma getirisi ayrı veri ve yetenek araştırması olarak ele alınmalı.

**Nasıl seçilmeli?**

1. Önce C1'in ETF veri/muhasebe uygulanabilirliği; mevcut fiyat verisiyle C2'nin scanner kapsamı. Kalite + değer için paralel bir finansal veri envanteri yapılabilir. Bunlar önerilen sonraki işlerdir, bu oturumda çalıştırılmadı.
2. Her deneyden önce aday kimliği, tek başlangıç spesifikasyonu, karşılaştırmalar, dönemler, maliyetler ve ret ölçütleri dondurulmalı. Bu belgedeki fikirler henüz dondurulmuş parametreler değildir.
3. Geçmişte araştırmada tekrar tekrar görülmüş 2016–2026 dilimleri yeni ve dokunulmamış holdout diye adlandırılmamalı. Kronolojik dış örneklem raporu ile gerçekten görülmemiş ileri dönem ayrılmalı; bütün denemeler sayılmalı. [Deflated Sharpe Ratio çalışması](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) seçim yanlılığına karşı bu kayıt ihtiyacını temellendirir.
4. Rejimler işlem anında bilinen geçmiş verilerle tanımlanmalı. Sonradan görülen tepe/diplerden kusursuz strateji seçici kurulamaz. Önce adaylar tek başına ve basit sabit birleşimde değerlendirilmeli; otomatik rejim seçici ayrıca test edilmelidir.
5. Sadece toplam getiri değil; dönem ve rejim sonuçları, maliyet sonrası getiri, işlem sıklığı, düşüş kuyruğu, en büyük katkı yapan varlığa bağımlılık ve mevcut portföye marjinal katkı ölçülmeli. 1x/2x/3x maliyetler yanında geç/eksik dolum da sınanmalı. Küçük örneklem başarı sayılmamalı.
6. Gerçekleşen son ağırlıklar ve ortak nakit/pozisyon limitleriyle karşılaştırılmalı. Altı araştırma adayını altı eşzamanlı runtime stratejisi saymak yanlış olur: mevcut mimari her piyasa için bir strateji seçiyor. Aynı piyasada çoklu strateji, ayrı birleşim ve sahiplik sözleşmesi gerektirir.
7. [P1 maliyet gözlem raporu](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/execution_cost_transferability_p1_paper_observation_2026_09.md:3) hâlâ `UNKNOWN_COST_TRANSFERABILITY` bildiriyor. Ayrıca [mimari raporu](/Users/batuhankiskac/Desktop/ParayaBuck/docs/strategy_architecture.md:167) üretim giriş durdurmaları ile backtest shadow davranışını ayırıyor. Yeni adayın farklı rejimde işlem yapabilirliği bu yürütme/risk farkları açıklanmadan doğrulanmış sayılamaz.

Araştırmanın başarı ölçütü daha sık işlem değil, mevcut risk sınırları içinde ölçülebilir ek katkıdır. Bu taramanın sonucu yalnız aday önceliğidir; hiçbir aday için ekonomik başarı veya terfi kararı verilmedi.
