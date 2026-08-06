# ParayaBuck

ParayaBuck, Alpaca API üzerinden ABD hisseleri ve kripto varlıkları için çalışan olay
tabanlı bir işlem motoru ve yerel Web terminalidir. Stratejiler puanlanmış ve açıklanabilir
sinyaller üretir; risk yöneticisi ve broker sınırı onaylanmayan sinyallerin emre dönüşmesini
engeller.

## Güvenlik

- Varsayılan işlem modu Alpaca paper hesabıdır.
- Gerçek para modu hem Web UI üzerinden seçilmeli hem de uygulama
  `PARAYABUCK_ENABLE_LIVE=YES` ile başlatılmalıdır.
- Hisse girişleri broker üzerinde yaşayan bracket emirleri kullanır.
- Kripto girişleri broker-side stop-limit ve application-side synthetic OCO ile korunur;
  protection kurulamazsa yalnız botun managed miktarı kapatılır.
- Kararlar, reddedilen sinyaller, emir geçişleri ve hesap anlık görüntüleri
  `data/runtime/parayabuck.sqlite3` içinde tutulur.
- Web kontrol yüzeyi yalnızca loopback adreslerine bağlanabilir.

## Kurulum ve Web UI

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
parayabuck
```

Aynı uygulama doğrudan Python ile de başlatılabilir:

```bash
python -m parayabuck
```

Varsayılan adres `http://127.0.0.1:8000` olur. Alpaca anahtarları, paper/live modu,
risk yüzdeleri ve motor periyodu Web UI içindeki Ayarlar penceresinden yönetilir.
API bilgileri mevcutsa paper motoru uygulama açılırken otomatik başlar. Live mod hiçbir
zaman otomatik başlamaz ve her açılışta Web UI doğrulaması gerektirir.

Gerçek para modu:

```bash
PARAYABUCK_ENABLE_LIVE=YES parayabuck
```

## Yapılandırma

Tek yapılandırma kaynağı `config/settings.yaml` dosyasıdır:

- `app`: Web sunucusu ve log ayarları
- `alpaca`: paper modu ve veri feed seçimi
- `universe`: equity ve crypto için ayrı aday, fallback, pin ve aktif evren ayarları
- `engine`: periyot, sinyal eşiği, warmup, karar günlüğü ve yüzde bazlı risk limitleri

Ücretsiz Alpaca hesaplarında gerçek zamanlı hisse verisi için `stock_feed: iex`, uygun
veri aboneliğinde `stock_feed: sip` kullanılabilir.

IEX stream toplamı reference semboller dahil 30 equity ile sınırlıdır; crypto bu limite
dahil değildir. `data/universe/equity_candidates.json` geniş fallback aday kaynağıdır.
Deterministik scanner sonucu `data/runtime/active_equity_universe.json` altında atomik
olarak saklanır ve yalnız bu sınırlı aktif evren stream edilir. `SPY` varsayılan olarak
benchmark/reference'tır ve `trade_reference_symbols: false` iken işlem dışıdır.

`inverse_trade_symbols`, short satış açmadan düşüş yönlü pozisyon alınabilen onaylı
kaldıraçsız (`-1x`) ters ETF listesidir. Varsayılan liste `SH`, `PSQ` ve `DOG` ile
sınırlıdır; kaldıraçlı ters ETF'ler scanner tarafından reddedilir. Bu fonlar günlük ters
getiri hedeflediğinden uzun süreli benchmark takibi sapabilir; motor yalnız fonun kendi
yükseliş/range kurulumu ile benchmark düşüş rejimi aynı anda uygun olduğunda giriş arar.

Kripto evreni iki modda çalışır:

- `dynamic_selection_enabled: false`: `trade_symbols` listesi geriye uyumlu biçimde
  doğrudan warmup, stream ve işlem izin listesi olur.
- `dynamic_selection_enabled: true`: aynı liste güvenli aday havuzu ve fallback'tir.
  Saatlik 14 günlük veri üzerinden likidite, veri bütünlüğü, ATR, spread ve BTC
  korelasyonu puanlanır; kategori yoğunluğu uygulanarak en fazla 12 sembol seçilir.

`BTC/USD` ve `ETH/USD` pinned'dır. Managed açık pozisyonlar da restart sırasında
ledger'dan pinlenir; unmanaged external pozisyonlar `manage_external_positions: false`
iken yönetilmez. Stablecoin bazlı USD pariteleri aday olamaz. Aktif sonuç
`data/runtime/active_crypto_universe.json` dosyasına atomik yazılır. Bozuk, stale,
yanlış sürümlü veya config digest'i uyuşmayan dosya açılışı engellemez; pinned semboller
ve config sırasındaki adaylardan deterministik fallback kurulur.

Bot başlangıcındaki preflight, aktif dosyayı iki piyasa için de doğrular. Dosya geçerli
ve güncelse Alpaca çağrısı yapmadan kullanır; eksik, stale veya config ile uyumsuzsa
ilgili scanner'ı stream ve işlem motoru başlamadan otomatik çalıştırır. Geçici API
hatasında startup kapanmaz ve deterministik fallback kullanılır.

Manuel komut yalnız zorla yenileme ve operasyonel bakım içindir:

```bash
.venv/bin/python scripts/run_crypto_universe_scan.py
```

Scanner yalnız asset, historical bar ve latest quote okur; WebSocket açmaz ve emir
oluşturmaz. Başlangıç preflight'ında üretilen sonuç aynı başlangıçta uygulanır. Çalışan
runtime'a hot-swap yapılmaz.
Kripto evreninin büyümesi `max_crypto_exposure_pct`, `max_crypto_position_pct`,
`max_crypto_positions` veya cycle başına tek yeni pozisyon limitini değiştirmez.

Kripto paper desteği live trading'i açmaz. Güvenli varsayılanlar:

```yaml
alpaca:
  paper: true
engine:
  crypto_trading_enabled: false
  live_trading_enabled: false
  manage_external_positions: false
```

Paper chaos kapısı `python scripts/run_paper_chaos.py` ile çalıştırılır. Ayrıntılı
rollout ve müdahale adımları [operations runbook](docs/operations.md) içindedir.
Paper doğrulamasında önce scanner'ı çalıştırın, JSON'daki seçimi inceleyin, motoru
yeniden başlatın ve health ekranındaki crypto `symbol_count` ile stream aboneliklerini
karşılaştırın. Bu işlem `live_trading_enabled` değerini değiştirmez.

## Backtest

Tek giriş noktası: `scripts/run_backtest.py` (aynı CLI: `python -m parayabuck.backtest`
veya kurulu pakette `parayabuck-backtest`). Her çalıştırma konsola detaylı rapor
basar ve `reports/` dizinine `report.json` + `run_manifest.json` + CSV raporları yazar
(summary, trades, yearly/monthly returns, by-symbol, by-regime, score decile,
equity curve, drawdowns).
Manifest, koşunun parametrelerini, veri kapsamını, benchmark'ı, fiyat düzeltme
tabanını ve kod revizyonunu kaydeder; böylece her rapor yeniden üretilebilir.

```bash
# 1) Örnek veriyle hızlı test (offline, hiçbir şey indirmez; DEMO sembolü sentetiktir):
python scripts/run_backtest.py --generate-sample

# 2) Kendi CSV verinle:
python scripts/run_backtest.py data/historical/DEMO_5m_2024.csv --symbol DEMO --timeframe 5m

# 3) Alpaca'dan 10 yıl gerçek veri, borsa + kripto (.env anahtarları ile):
#    10 yıllık pencere deep profil (1d) kullanır.
python scripts/run_backtest.py --market both --years 10
python scripts/run_backtest.py --market crypto --years 7

# 4) Canlıda doğrulanmış 5m + 1h profil — canlı motorla aynı zaman ölçeği:
#    (pencere <= ~120 işlem günü; daha uzun pencerelerde --timeframe 1d gerekir)
python scripts/run_backtest.py --market equity --days 90 --profile live

# 5) Portföy strateji araştırması (veri indirme + baseline + nested validation):
python scripts/run_portfolio_research.py --download
python scripts/run_portfolio_research.py --baseline
python scripts/run_portfolio_research.py --nested
```

Portföy düzeyinde strateji araştırması (kalıcı ETF evreninde dual momentum,
1h kripto cross-sectional momentum, günlük time-series momentum) kilitli outer
holdout ve production gate ile yürütülür. Araştırma durumu:

- **Strateji durumu: RESEARCH ONLY — onaylı canlı strateji yok.** Runtime
  fail-closed'dur (`strategy_status=research_only_no_approved_portfolio_strategy`)
  ve UI aktif strateji göstermez.
- 2026-08-04 raporundaki metrikler doğruluk denetiminde geçersiz çıktı:
  [`docs/research_correctness_audit_2026-08-05.md`](docs/research_correctness_audit_2026-08-05.md)
  (holdout contamination, duplicate SPY, adjustment, DSR/PBO hesap hataları).
- Düzeltilmiş doğrulama (v1 rerun + v2 verisi): **RED — strateji arşivlendi**:
  [`docs/equity_dual_momentum_v2_results.md`](docs/equity_dual_momentum_v2_results.md).
- Eski tekil sembol alpha katmanının kaldırılması:
  [`docs/strategy_migration_2026-08-04.md`](docs/strategy_migration_2026-08-04.md).

Alpaca modunda `--profile` belirtilmezse pencere otomatik karar verir: pencere
`EQUITY_MAX_DAYS["5m"]` (120 işlem günü) içindeyse canlı profili (5m + 1h), daha
uzunsa deep profili (1d). Uzun hisse testlerinde veri corporate-action düzeltmeli
indirilir (`--adjustment splits`, varsayılan); böylece bölünme geçişleri yanlış
sinyal ve stop mesafesi üretmez.

Backtest aynı motor, strateji ve risk katmanını kullanır; yalnızca broker uygulaması
simülasyon brokerı ile değiştirilir. Alpaca'dan indirilen veri `data/cache/` altında
saklanır; sonraki çalıştırmalar önbellekten anında tamamlanır. Simülasyon brokerı
hacim katılımına dayalı dolum uygular: bir emir tek barda hacmin varsayılan %10'unu
geçemez (kısmi dolumlar bir sonraki barda devam eder) ve test sonunda açık
pozisyonlar kapanış fiyatından realize edilir — böylece toplam getiri ile işlem
istatistikleri aynı ekonomik kapsamı ölçer.

## AI Decision Logs

Normal uygulama logu bağlantı hatası, servis başlangıcı veya HTTP isteği gibi operasyonel
olayları anlatır. AI Decision Logs ise botun karar verirken gördüğü piyasa bağlamını,
kontrol ettiği strateji koşullarını, geçtiği veya takıldığı karar kapısını ve tahminin
sonradan nasıl sonuçlandığını saklar. Amaç, bir yapay zekânın yalnızca “emir verildi”
satırını değil, kararın tamamını yeniden değerlendirebilmesidir.

Bu sistem mevcut SQLite günlüğünün yerine geçmez. `data/runtime/parayabuck.sqlite3`
değişmez denetim izi olarak kalır; AI günlükleri ek ve açıklayıcı artifact'lardır.
Dosyalarda API anahtarı veya secret tutulmaz. Açıklamalar, sabit reason code'ları ve alan
adları İngilizcedir.

### Yapılandırma

```yaml
engine:
  ai_logging_enabled: true
  ai_log_path: data/runtime/ai_decisions
  prediction_horizon_bars: 48
```

- `ai_logging_enabled`: AI journal üretimini açar veya kapatır.
- `ai_log_path`: Bütün AI artifact'larının kök dizinidir.
- `prediction_horizon_bars`: Hedef veya stop görülmezse tahminin kaç bar sonra kapanış
  fiyatıyla sonuçlandırılacağını belirler.

Paper ve live günlükleri yeniden başlatmalar arasında aynı mod klasöründe devam eder:

```text
data/runtime/ai_decisions/
├── paper/
│   ├── manifest.json
│   ├── open_predictions.json
│   ├── strategy_stats.json
│   └── 2026-07-26/
│       ├── events.jsonl
│       ├── scan-digest.md
│       ├── outcomes.md
│       └── 153000_AAPL_<strategy>_a1b2c3d4.md
├── live/
│   └── ...
└── backtest/
    └── backtest-20260726T153000Z-a1b2c3d4/
        ├── manifest.json
        ├── open_predictions.json
        ├── strategy_stats.json
        └── 2024-01-02/
            └── ...
```

Her backtest benzersiz bir `run_id` klasörü kullanır; farklı veri setleri veya parametreler
birbirine karışmaz. `scripts/run_backtest.py` sonucu üretilen `ai_log_dir` yolunu da
terminal çıktısına ekler. Tarih klasörleri UTC gününe göre açılır. Otomatik silme veya
sıkıştırma yapılmaz.

### Dosyaların anlamı

`manifest.json`, şema sürümünü, çalışma modunu, `run_id` değerini, timeframe'i, tahmin
ufkunu ve diğer dosyaların kısa açıklamasını taşır. Bir AI incelemesinde önce bu dosya
okunmalıdır.

`scan-digest.md`, motorun kabul ettiği her kapalı bar için bir satır içerir. Satırda
sembol, timeframe, kapanış, rejim ve açık pozisyon durumu ile her stratejinin `FIRED`
veya `skipped` sonucu bulunur. Sinyal oluşmadığında ilk belirleyici elenme nedeni burada
görülür:

```text
15:30:00 | AAPL 5m | close=192.14 | regime=range (adx=16.2, weak_directional_trend) |
position=none | <strategy-a>: skipped (reason) | <strategy-b>: skipped (reason)

Not: eski sinyal stratejileri emekli edilmiştir; emeklilik gerekçesi
docs/strategy_migration_2026-08-04.md içindedir.
```

`events.jsonl`, satır başına tek JSON nesnesi içeren append-only makine günlüğüdür.
Dosyanın tamamı belleğe yüklenmeden `event_type`, `strategy`, `symbol` veya `verdict`
üzerinden taranabilir. Olay türleri:

- `scan`: Bar, rejim, pozisyon ve bütün strateji değerlendirmeleri.
- `decision`: Oluşan sinyal, piyasa bağlamı, tahmin, kapı izi, risk diagnostics ve ilk
  broker sonucu.
- `account`: Karar döngüsündeki cash, equity, PnL, drawdown ve pozisyon snapshot'ı.
- `fill`: İlk karar anından sonra gelen backtest/asenkron giriş veya çıkış fill'i.
- `outcome`: Tahminin hedef, stop veya timeout ile kesinleşen sonucu.

Her olayda `schema_version`, `event_type`, `run_id` ve UTC ISO-8601 `timestamp` alanları
vardır. Bir `decision` olayının kısaltılmış örneği:

```json
{"schema_version":1,"event_type":"decision","run_id":"paper-...","timestamp":"2026-07-26T15:30:00+00:00","signal_id":"a1b2...","symbol":"AAPL","timeframe":"5m","strategy":"<strategy>","direction":"long","score":78.4,"confidence":0.784,"prediction":{"entry":192.14,"stop":189.83,"target":197.22,"risk_reward":2.2,"horizon_bars":48},"gate_trail":[{"gate":"strategy","status":"passed","reason":"Trend pullback resumed with acceptable momentum and EMA alignment."},{"gate":"minimum_score","status":"passed","reason":"score 78.40 meets 65.00"},{"gate":"ranking","status":"passed","reason":"rank 1 of 2; quality=135.206"},{"gate":"allocation","status":"passed","reason":"portfolio capacity was available and no position was held"},{"gate":"risk","status":"passed","reason":"approved"},{"gate":"broker","status":"passed","reason":"broker_response=accepted"}],"execution":{"traded":true}}
```

`open_predictions.json`, henüz hedefe, stopa veya zaman ufkuna ulaşmamış sinyallerin
yeniden başlatılabilir durumudur. Her yeni barda `bars_elapsed`, MFE, MAE ve hedef/stopa
en yakın mesafe güncellenir. Uygulama yeniden başlatılırsa takip buradan devam eder.
Dosya bozulmuşsa silinmez; `.corrupt-<UTC timestamp>` uzantısıyla karantinaya alınır.

`outcomes.md`, o UTC gününde sonuçlanan tahminlerin kısa indeksidir. Hangi karar
dosyasına gidileceğini, kaç barda çözüldüğünü, getiri ve verdict'i gösterir.

`strategy_stats.json`, strateji başına aşağıdaki toplu sayaçları taşır:

- `resolved`, `correct`, `wrong` ve `accuracy`.
- `target_hit`, `stop_hit`, `timeout_correct`, `timeout_wrong`.
- `traded_resolved` ve `virtual_resolved`.
- Yön düzeltilmiş `total_return_pct` ve `avg_return_pct`.

### Bireysel karar dosyası

Her gerçek sinyal için ayrı Markdown dosyası açılır. Sinyal vermeyen taramalar ayrı
dosya üretmez; bunlar `scan-digest.md` ve `scan` JSONL olayında kalır. Karar dosyasının
bölümleri:

1. **Market context** — OHLCV kapanışı, motor timeframe'i, rejim güveni ve kanıtları,
   üst timeframe ile benchmark rejimi.
2. **Strategy evaluation** — her koşulun `PASS`/`FAIL` durumu, actual değer, beklenen
   eşik ve doğal dil açıklaması. Ayrıca hesaplanan EMA, RSI, ATR, Bollinger, göreli
   hacim ve puan bileşenleri.
3. **Prediction** — yön, giriş, invalidation/stop, hedef, risk/ödül ve beklenen bar ufku.
4. **Gate trail** — minimum skor, ranking, allocation, risk ve broker kapılarının
   sıralı sonucu. Seçilmeyen adayın neden kaybettiği ve risk sizing'deki bağlayıcı limit
   burada görünür.
5. **Order/fill lifecycle** — client order ID, miktar, fiyatlar, broker state'i ve mevcut
   fill; daha sonra gelen backtest fill'leri aynı dosyaya eklenir.
6. **Prediction vs. Actual** — tahmin çözüldüğünde otomatik eklenen beklenen/gerçekleşen
   karşılaştırması.

Örnek sonuç bölümü:

```markdown
## Prediction vs. Actual

- **Verdict: WRONG (stop_hit)**
- Predicted: LONG from 192.14, target 197.22, stop 189.83, within 48 bars.
- Actual: resolved at 189.83 after 17 bars; direction-adjusted return -1.202%.
- Path: MFE +0.614%, MAE -1.350%.
- Execution class: traded.
- Reading: Price hit the invalidation level; the setup failed.
```

### Tahmin nasıl “gerçekleşen” ile karşılaştırılır?

Sinyalin oluştuğu bar değerlendirmeye katılmaz. Sonraki aynı sembol ve timeframe
barlarında, long için en yüksek fiyat olumlu ve en düşük fiyat olumsuz hareket olarak
izlenir; short için işaretler ters çevrilir.

- Hedef stop öncesinde görülürse `target_hit / correct`.
- Stop hedef öncesinde görülürse `stop_hit / wrong`.
- OHLC barı hem hedefi hem stopu kapsıyorsa intrabar sıra bilinmediğinden muhafazakâr
  olarak stop önce kabul edilir.
- 48 bar boyunca ikisi de görülmezse son kapanış girişle karşılaştırılır. Tahmin yönünde
  pozitif getiri `timeout_correct`, sıfır veya negatif getiri `timeout_wrong` olur.

`MFE` (Maximum Favorable Excursion), tahmin açıkken girişten itibaren görülen en iyi yön
düzeltilmiş yüzdedir. `MAE` (Maximum Adverse Excursion) en kötü yüzdedir. Bunlar yalnızca
son fiyatı değil, tahmin süresince yaşanan yolu gösterir.

`traded`, sinyalin risk ve broker kapılarını geçerek kabul edilen bir emre dönüştüğünü;
`virtual`, sinyal oluşmasına rağmen puan, sıralama, kapasite, risk veya broker kapısında
elendiğini belirtir. Virtual tahminler de izlenir. Böylece örneğin risk filtresinin
engellediği sinyallerin sonradan gerçekten zararlı mı yoksa kaçırılmış fırsat mı olduğu
ölçülebilir. Tahmin sonucu OHLC piyasa hareketidir; gerçek broker state/fill bilgisi
`execution` ve `fill` kayıtlarında ayrıca tutulur.

Kısaltılmış bir outcome JSONL örneği:

```json
{"schema_version":1,"event_type":"outcome","signal_id":"a1b2...","symbol":"AAPL","strategy":"<strategy>","outcome":"stop_hit","verdict":"wrong","actual_price":189.83,"return_pct":-1.202,"bars_elapsed":17,"mfe_pct":0.614,"mae_pct":-1.35,"traded":true}
```

### AI ile inceleme sırası

Bir geliştirme veya zarar analizi için AI'ya şu sırayla veri verilmesi önerilir:

1. İlgili mod/run altındaki `manifest.json`.
2. Genel performans için `strategy_stats.json`.
3. İncelenen günlerin `outcomes.md` ve `scan-digest.md` dosyaları.
4. Kazanan, kaybeden ve virtual örneklerin bireysel karar Markdown dosyaları.
5. Toplu filtreleme veya özel hesap gerektiğinde `events.jsonl`.

Örnek analiz soruları:

- “En çok stop olan stratejinin başarısız kararlarında ortak rejim, RSI ve göreli hacim
  özelliklerini karşılaştır.”
- “Risk filtresinde elenen virtual sinyaller ile traded sinyallerin accuracy ve
  avg_return_pct değerlerini karşılaştır.”
- “Yüksek MFE gördüğü halde stop olan işlemlerde stop mesafesi veya hedef oranı ortak bir
  sorun gösteriyor mu?”
- “Ranking nedeniyle elenen adaylar seçilen adaylardan daha iyi sonuçlandı mı?”

Journal yazma hatası trading işlemini durdurmaz; standart uygulama loguna traceback
yazılır ve sonraki döngüde tekrar denenir. JSON state dosyaları geçici dosyaya yazılıp
atomik olarak değiştirilir. JSONL ve günlük Markdown dosyaları append-only tutulur.

## Geliştirme

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Backtest için örnek veri `python scripts/run_backtest.py --generate-sample` ile
üretilip aynı komut içinde çalıştırılır (sentetik OHLCV, `data/historical/`).

## Güvenlik ve live geçişi

- Gerçek Alpaca anahtarları yalnızca Git tarafından izlenmeyen `.env` dosyasında
  tutulmalıdır. `.env.example` sadece placeholder içerir ve uygulama tarafından hiçbir
  zaman credential hedefi olarak kullanılmaz.
- Bir anahtar daha önce `.env.example` veya başka bir tracked dosyaya yazıldıysa,
  dosyadan silmek yeterli değildir: Alpaca panelinden anahtarı hemen rotate edin ve Git
  geçmişini ayrıca temizleyin. Startup secret taraması böyle bir değer gördüğünde motoru
  bloke eder.
- Live mod için hem process ortamında `PARAYABUCK_ENABLE_LIVE=YES` bulunmalı hem de Web
  UI'da gösterilen maskeli hesap son eki `LIVE <son-ek>` ifadesiyle backend üzerinde
  doğrulanmalıdır. Arming doğrulanan account ID, credential fingerprint ve config digest'e
  bağlıdır; beş dakikada, credential/config değişiminde, start denemesinde, stop veya restart
  sırasında geçersiz olur. Ayar kaydı live motoru otomatik başlatmaz.
- Kontrol sayfası üçüncü taraf JavaScript çalıştırmaz. WebSocket bağlantıları control token'ı
  URL'ye koymak yerine 30 saniyelik, tek kullanımlık kanal ticket'ı alır.
- Varsayılan rollout yalnızca `5m + 1h`, IEX, equity paper, cycle başına tek yeni pozisyon
  ve `manage_external_positions=false` politikasıdır. Crypto execution test matrisi
  tamamlanana kadar kapalıdır.

Belirsiz emir sonucu, reconciliation/protection hatası, stale account snapshot veya veri
kuyruğu gecikmesi yeni entry circuit breaker'ını açar. Açık pozisyonların exit/protection
yönetimi bu giriş engelinden ayrıdır.

Execution lifecycle broker türünden bağımsız coordinator üzerinden ilerler. TradingStream
`filled_qty` değerleri kümülatif kabul edilir; ledger yalnızca pozitif delta'yı uygular.
Entry ve exit miktarları ayrı tutulur, child bracket fill'i parent managed lotu azaltır ve
kalan sahiplik sıfıra geldiğinde lot kapanır.
