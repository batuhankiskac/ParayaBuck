# ParayaBuck Operations Runbook

## Güvenli varsayılanlar

Production hardening tamamlanıp paper chaos testleri onaylanana kadar:

- `paper: true`
- `timeframe: 5m`
- `higher_timeframe: 1h`
- `stock_feed: iex`
- `crypto_trading_enabled: false`
- `live_trading_enabled: false`
- `manage_external_positions: false`
- cycle başına en fazla bir yeni pozisyon

Crypto verisi ve virtual sinyaller izlenebilir; bu ayarlarda broker'a crypto emri gitmez.
`live_trading_enabled=false`, live hesap bağlantısı arming ile doğrulansa bile entry üretimini
ayrıca engeller.

## Startup kontrol listesi

1. `.env.example` ve tracked dosyalarda gerçek secret bulunmadığını doğrula.
2. Şüpheli veya daha önce commit edilmiş anahtarı Alpaca panelinden rotate et.
3. `requirements.lock` ile bağımlılıkları kur.
4. `ruff check .`, `ruff format --check .`, `mypy src` ve `pytest` çalıştır.
5. Config'in IEX için en fazla 30 equity symbol içerdiğini doğrula.
6. Açık external ve managed pozisyonları ayrı ayrı gözden geçir.
7. Startup reconciliation, warmup readiness, data gap, queue age ve TradingStream health
   durumlarının temiz olmasını bekle.
8. Önce shadow, ardından yalnızca equity paper rollout uygula.

## Market bazlı runtime ve sembol evreni

Runtime equity ve crypto için ayrı expected symbol, pending timestamp, readiness,
data-gap, stream health ve portfolio watermark tutar. `pb-stocks` arızası yalnız equity
entry'lerini; `pb-crypto` arızası yalnız crypto entry'lerini durdurur. Account, ledger,
decision worker veya TradingStream gibi ortak bileşenlerin arızası global circuit breaker
açar. Equity seansı kapalıyken stock worker'ın event üretmemesi crypto readiness'i
etkilemez.

`universe.equity.reference_symbols` stream limitine dahildir fakat
`trade_reference_symbols: false` iken işlem adayı değildir. IEX için referanslar, aktif
adaylar ve restart'ta ledger'dan pinlenen managed semboller dahil toplam en fazla 30
olmalıdır. Candidate fallback dosyası yüzlerce sembol içerebilir; yalnız scanner'ın
persist ettiği `active_equity_universe.json` WebSocket'e bağlanır. Gün içi hot-swap
yoktur.

Scanner çıktısı atomik replace ile yazılır ve selection/data timestamp, skor kırılımı,
elenme nedenleri, scanner sürümü ve config digest içerir. Eksik/bozuk çıktı son iyi
dosyayı değiştirmez; startup configured fallback trade sembollerini kullanır.

Seans öncesi, motor kapalıyken scanner'ı çalıştır:

```bash
python scripts/scan_equity_universe.py
```

Komut Alpaca'da yalnız asset ve historical-data okuması yapar. Başarılı olduğunda
candidate metriklerini ve sınırlı active evreni atomik yazar. Running motor bu dosyayı
hot-swap etmez; yeni seçim bir sonraki motor başlangıcında yüklenir.

## Crypto synthetic OCO

Alpaca crypto için yalnız simple market, limit ve stop-limit emirleri destekler; bracket
ve OCO varsayılmaz. Güvenli long yaşam döngüsü:

1. Idempotent client order ID ile market entry gönderilir.
2. Partial fill sonrası açık remainder iptal edilir ve kesin fill ledger'a yazılır.
3. `qty_available`, fee reserve ve asset increment ile kapatılabilir miktar bulunur.
4. Broker-side GTC stop-limit açılır; offset
   `crypto_stop_limit_offset_bps` ile belirlenir.
5. Target, canlı event yoksa 1m bar high/close üzerinden application-side izlenir.
6. Target veya stop failover atomik ledger claim ve symbol lock alır.
7. Stop iptali broker'dan terminal olarak doğrulanmadan exit gönderilmez.
8. Stop yarış sırasında fill olmuşsa target/emergency exit gönderilmez.
9. Exit miktarı yalnız ledger `remaining_owned_qty` ile sınırlıdır; hiçbir akış
   `close_position(symbol)` çağırmaz.

Stop seviyesi geçildiği halde stop-limit açık kalırsa state `triggered_unfilled` olur.
Fiyat `crypto_emergency_exit_bps` eşiğini aşarsa aynı coordinator stopu iptal edip exact
managed quantity market exit gönderir. Cancel/submit sonucu belirsizse ikinci kör emir
gönderilmez; `manual_intervention_required` ve crypto entry circuit breaker oluşur.

## Live arming

1. Motoru durdur.
2. Process ortamında `PARAYABUCK_ENABLE_LIVE=YES` ayarla.
3. UI'da live talebi oluştur. Backend live Alpaca hesabını read-only sorgular ve maskeli
   hesap son ekini döndürür.
4. `LIVE <account suffix>` ifadesini gir.
5. Ayarı `restart_engine=false` ile kaydet.
6. Live motor başlangıcını ayrı başlatma işlemiyle yap.

Arming yalnızca mevcut server process belleğinde tutulur; doğrulanan account ID,
credential fingerprint ve config digest'e bağlıdır. Beş dakika sonra, credential/config
değişiminde, start denemesinde, stop veya restart sonrasında geçersizdir.
Varsayılan config'te `live_trading_enabled=false` olduğu için bu akış yalnızca live
readiness incelemesi yapar; entry açmaz.

## Circuit breaker ve müdahale

Aşağıdakiler yeni entry'leri durdurur:

- stale signal veya aşırı queue age
- account snapshot TTL aşımı
- reconciliation/protection hatası
- TradingStream/worker restart limiti
- belirsiz broker write sonucu
- post-fill risk ihlali
- kritik journal persistence hatası
- final market bar event'inin dolu kuyrukta korunamaması

Circuit breaker açıkken managed pozisyonların exit ve broker-side protection yönetimi
durdurulmamalıdır. `ambiguous_order_state`, `protection_failure` veya
`manual_intervention_required` durumunda:

1. Yeni emir göndermeyi deneme.
2. Client order ID ile broker order'ını sorgula.
3. Open orders, recent activities/fills ve position quantity'yi karşılaştır.
4. Ledger'daki `owned_qty` ile external miktarı ayır.
5. Yalnızca doğrulanmış managed miktar için kontrollü exit uygula.
6. Fill ve kalan broker quantity doğrulanmadan state'i `closed` yapma.

## Restart recovery

- SQLite WAL dosyalarını çalışan başka bir process varken kopyalama veya silme.
- Startup reconciliation'ın tamamlanmasını bekle.
- External pozisyonlara otomatik stop eklenmediğini doğrula.
- Her managed pozisyon için persisted original stop/target ve exact owned quantity'yi
  broker açık emirleriyle karşılaştır.
- Belirsizlik çözülmezse motoru halted bırak ve Alpaca panelinden manuel inceleme yap.

## Paper chaos testi

Live readiness öncesinde paper hesapta şu olayları kontrollü uygula:

- submit/cancel timeout
- market-data ve trade-update WebSocket kopması
- HTTP 429
- process restart
- partial fill + cancel
- protection submit failure
- triggered fakat dolmayan stop-limit
- disk/journal write failure
- aynı sembolde external + managed quantity

Başarı ölçütü kârlılık değil; stale/duplicate entry olmaması, external miktarın değişmemesi,
managed quantity/protection farkının sıfır olması ve restart sonrasında state'in yeniden
kurulmasıdır.

Offline fake-broker kapısı:

```bash
python scripts/run_paper_chaos.py
```

Bu araç Alpaca client oluşturmaz ve gerçek API anahtarı kullanmaz. Gerçek paper rollout
için ayrıca sırasıyla yalnız BTC/USD, BTC/USD+ETH/USD ve sekiz sembollü birkaç günlük
gözlem yapılmalıdır. Günlük zarar ve risk state session tarihi UTC'dir.

## Paper rollout kontrol listesi

1. Unit, lint, format ve type kontrollerini geçir.
2. Crypto kapalı equity paper regression çalıştır.
3. Yalnız BTC/USD ile entry → stop → target ve restart senaryolarını doğrula.
4. BTC/USD ve ETH/USD ile aynı-cycle global tek-entry kuralını doğrula.
5. Sekiz crypto sembolüyle birkaç gün 7/24 stream/data-gap gözlemi yap.
6. Submit/cancel timeout, 429, WebSocket disconnect, partial fill, stop failure,
   triggered-unfilled stop ve disk failure chaos senaryolarını uygula.
7. External+managed aynı sembolde broker miktarının değişmediğini doğrula.
8. Equity seansı açıkken iki marketin eşzamanlı paper çalışmasını gözle.
9. Her unresolved durumun açık circuit breaker/manual intervention ürettiğini doğrula.
10. Bunlar tamamlanana kadar `live_trading_enabled: false` bırak.
