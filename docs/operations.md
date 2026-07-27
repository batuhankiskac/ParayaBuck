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

## Live arming

1. Motoru durdur.
2. Process ortamında `PARAYABUCK_ENABLE_LIVE=YES` ayarla.
3. UI'da live talebi oluştur. Backend live Alpaca hesabını read-only sorgular ve maskeli
   hesap son ekini döndürür.
4. `LIVE <account suffix>` ifadesini gir.
5. Ayarı `restart_engine=false` ile kaydet.
6. Live motor başlangıcını ayrı başlatma işlemiyle yap.

Arming yalnızca mevcut server process belleğinde tutulur ve restart sonrasında geçersizdir.
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
