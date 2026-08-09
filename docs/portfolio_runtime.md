# PortfolioRuntime

Tarih: 2026-08-08
Statü: **UYGULANDI — geçerli GREEN manifest olmadığı için fail-closed**

Runtime yazılım yolu equity ve crypto için tamamlandı. Bunun işlem yapabilmesi,
stratejinin onaylandığı anlamına gelmez: repository içinde şu anda GREEN approval
manifest yoktur ve yeni vNext deneyleri forward holdout olgunlaşana kadar askıdadır.

## Aktivasyon sözleşmesi

- `engine.approve_portfolio_strategy=true` artık hata üretir. Boolean yetkilendirme
  olarak kullanılamaz.
- Her sleeve için `approval_manifest_paths` ve ayrı
  `approval_manifest_checksums` gerekir.
- Manifest; GREEN durumu, expiry, source commit, strateji import yolu/modül hash'i,
  sabit parametreler, approved universe, dataset hash'leri ve validation/holdout
  kayıtlarını bağlar.
- Eksik, değiştirilmiş, expired, archived, farklı commit'e ait veya yanlış modül
  hash'li manifest runtime oluşturulmadan reddedilir.
- Paper credentials yalnız shadow/paper manifestlerini; live credentials yalnız
  shadow/canary/live manifestlerini kabul eder.
- Canary/live ayrıca HMAC imzalı, account ve manifest setine bağlı kısa ömürlü
  `LiveOperatorApproval`, config arming ve pozitif dolar cap gerektirir.
- Manifest evreni scanner veya restart sırasında genişletilmez. Bot dışı
  pozisyonlar account risk bütçesinden düşer fakat runtime tarafından satılmaz.

## Uygulanan bileşenler

- `PortfolioDecisionService`: yalnız tamamlanmış ve eksiksiz bar batch'inden
  `PortfolioContext` oluşturur.
- `TargetToOrderPlanner`: broker pozisyonu ve açık emirleri hedef delta ile
  uzlaştırır; satışları alışlardan önce sıralar; asset metadata increment/minimum
  kurallarını uygular.
- `PortfolioRiskOverlay`: günlük %0,5 kayıp, persist edilmiş high-water'dan %5
  drawdown, %60 gross, %50 equity, %10 crypto, varlık ve %40 cash limitleri.
- `PortfolioStateRepository`: session → decision → target/risk → order → fill
  zincirini idempotent SQLite kayıtlarıyla tutar; PnL, fee ve slippage ayrıdır.
- `PortfolioRuntime`: backtest ile aynı strategy scheduler semantiğini kullanır;
  shadow'da emir kaydeder fakat broker'a yazmaz, paper'da aynı planner çıktısını
  submit eder, canary'yi %10 ölçekler.
- Belirsiz submit yanıtı kör retry edilmez; `client_order_id` ile broker
  reconciliation yapılmadan order yeniden gönderilmez.

Runtime timeframe'i manifest stratejisinin timeframe'iyle aynı olmalıdır. İlk vNext
equity ve crypto stratejileri `1d` kullanır. Equity günlük barı normal seans kapandıktan
sonra; crypto günlük barı UTC rollover sonrasında final olur. Emir en erken sonraki
tradable olayda gerçekleşir.

## Mevcut güvenli sonuç

Yazılım uygulanmıştır fakat aktivasyon kanıtı yoktur. Bu nedenle `/api/status`
`research_only_no_approved_portfolio_strategy` döndürür ve `EngineRuntime.start()`
broker reconciliation öncesinde durur. Onay çıkmaması geçerli bir sonuçtur.
