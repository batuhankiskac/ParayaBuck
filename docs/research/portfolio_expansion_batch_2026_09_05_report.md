# Portfolio expansion batch — master research report

**Date:** 2026-09-05. **Status:** final research synthesis; attempt-03 runtime and independent audit PASS. No production, PAPER/LIVE, V3 change, allocation change, or auto-promotion is authorized by this document. The frozen seven-arm screen completed 42/42 constituents and 36 fixed initial-NAV blends; its evidence is retrospective and remains separate from the archived standalone studies and the diagnostic curve blend.

## Türkçe yönetici özeti

Bu tek batch, mevcut equity V3 sinyal mantığını ve `0.55` tavanını değiştirmeden, crypto için `0.05`, toplam brüt için `0.60` ve minimum nakit için `0.40` sözleşmesini koruyarak 16 araştırma başlığını toplar. Başlıklar 16 çalıştırılabilir veya kanıtlanmış strateji değildir: sekiz crypto, sekiz equity/cross-asset hipotezidir. Yeni ekran, incumbent HYST4/top-2/96h politikasına uyarlanmış yedi skor ailesinin 2022-01-01..2026-07-31 tam ve 2024-01-01..2026-07-31 recent pencerelerinde 1x/2x/3x maliyetlerle 42 hücresini tamamladı. Eski arşiv standalone kanıtı, 2022 başlayan yeni ekran ve 2021-04-01 başlayan düzeltilmiş C3/C4 tanı çıktıları aynı tablo gibi okunmamalıdır.

En güçlü araştırma önceliği, maliyet ve yoğunlaşma kontrolleriyle `high_proximity364`, ardından ortak sermayede `downside22` ve daha düşük korelasyon gerekçesiyle `reversal70` için gerçek shared-ledger katkı testidir. Bağımsız denetimin high-proximity 3x karşılaştırmasında full baseline TR/DD `2.038%/5.224%`, blend `4.440%/2.959%`, half-cash kontrol `1.019%/2.614%`; recent baseline `0.866%/3.962%`, blend `1.651%/2.002%`, half-cash `0.433%/2.026%` bulundu. Full blend DD'si half-cash'ten kötü, recent fark yaklaşık `0.024` yüzde puanı kadar küçüktür; koşulsuz hedge iddiası yoktur. Reversal korelasyonu yaklaşık 0.901–0.905 ile en düşük olsa da bu tek başına en iyi aday demek değildir. `downside22` yüksek korelasyonlu ve çoğunlukla daha yüksek DD'lidir. Bunlar 95% nakit ve 5% crypto bütçeli izole ekranın cumulative account-return/curve tanılarıdır; yıllıklaştırılmış full-V3 bot performansı veya V3+aday ortak hesap sonucu değildir. Standalone all-period üstünlüğü terfi kanıtı değildir; beklenen rejimler yalnız hipotezdir, sonradan etiketlenmiş aylara göre oracle switching yapılmaz.

Kalite, value ve PEAD revision-safe PIT fundamentals/events olmadan `DATA_BLOCKED`; overnight split/corporate-action ve open-timestamp kanıtı olmadan bloke; pairs/short, carry ve orderflow gerekli veri ve execution yeteneği olmadan `CAPABILITY_BLOCKED` kalır. Volatility targeting yeni alpha değil risk overlay'idir. Yeni sermaye, funding veya mevcut equity V3 tavanını artırma önerisi yoktur.

## Tamamlanmış ekran ve kanıt katmanları

* **Arşiv standalone kanıtı:** C3 MAX22 ve C4 signed-risk22 gibi eski ayrı ekonomik matrisler kendi ilan edilmiş pencerelerinde değerlendirilmiştir; standalone `REJECT` sonucu portföy katkısını tek başına reddetmez veya doğrulamaz.
* **Yeni SCORE-adaptation ekranı:** [42 hücrelik sonuç raporu](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_screen_results_2026_09_05.md) yedi uyarlanmış skor ailesini iki pencere ve üç maliyet seviyesinde tamamlar. Bu, eski haftalık tariflerin birebir yeniden çalıştırılması değildir.
* **Initial-capital blend tanısı:** 36 satır, incumbent ile sabit 50/50 NAV eğrisi ortalamasıdır. En düşük günlük net-PnL korelasyonu reversal70'te, en belirgin DD iyileşmesi high-proximity364'tedir; sonuçlar shared-ledger portföy kanıtı değildir. [Düzeltilmiş tanı denetimi](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_diagnostic_audit_2026_09_05.md) eski 12 karışımın cadence/endpoint ayrımını kapatır.
* **Kavramsal/data-blocked adaylar:** PIT, kurumsal olay, short/borrow, futures funding, orderflow veya iki-bacaklı execution gerektiren başlıklar veri/yetenek kapısı geçmeden ekonomik sonuç sayılamaz.

Ekran tam/recent başlangıçlarını açıkça `2022-01-01` ve `2024-01-01` kullanır. Düzeltilmiş eski C3/C4 diagnostic tam pencere başlangıcı `2021-04-01`'dir; bu iki pencere seti birleştirilemez.

## Fixed baseline and capital contract

The equity identity remains `equity_residual_momentum_v3`, with configured allocation ceiling 0.55. The crypto incumbent remains `crypto_low_variance_hysteresis_v1`, with 0.05 maximum allocation. Account gross is capped at 0.60, minimum cash is 0.40, crypto has a two-position limit, and the existing risk overlay, cadence, execution, fees and terminal policy remain fixed. These are ceilings, not guaranteed realized exposure. Evidence: [batch protocol](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_batch_2026_09_05_protocol.md:5).

A new sleeve cannot add capital or enlarge a cap. A true portfolio test needs one shared ledger; standalone curve blending is only a cheap diagnostic. The native product path runs one strategy per product (`src/parayabuck/backtest/product.py:208-307`), so joint crypto sleeves require a research-only dispatcher and explicit shared-risk tests.

## Crypto candidate inventory

### C3 — low-MAX22

**Mechanism:** `MAX_i(P_i/P_{i-1}-1)` across the last 22 completed D1 returns, ascending, 23-price history, HYST4/top-2/96h and 5% crypto sleeve. **Hypothesis:** exposure to short-lived positive jumps may differ from low variance. **Evidence/status:** standalone 3x full and recent returns were negative; C3 is standalone `REJECT`, while portfolio contribution remains untested ([candidate report](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/crypto_max_candidate_2026_09.md:68)). **Data:** existing 1h OHLCV and causal completed-D1 projection; venue-PIT completeness remains unavailable.

### C4 — signed downside-risk22

**Mechanism:** `sum(min(r,0)^2)/sum(r^2)` over 22 completed daily returns, ascending, same HYST4/top-2/96h/risk contract. **Hypothesis:** asymmetric downside exposure may diversify low variance. **Evidence/status:** standalone economic matrix completed 18/18 and was `REJECT` (`R1/R3/R4/R5=false`, `R2=true`), so portfolio contribution is untested ([candidate report](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/crypto_signed_risk_candidate_2026_09.md:23)). **Data:** feasible from existing OHLCV, with configured-pool and scanner-proxy limitations.

### C5 — downside semivariance T3

**Mechanism:** `sqrt(mean(min(r,0)^2))`, 22 completed returns, top-2 equal, 96h. **Hypothesis:** target loss magnitude without using the C4 total downside-energy ratio. **Evidence/status:** historical tournament evidence is weakly positive but not a portfolio result; overlap and marginal contribution are untested ([validation](/Users/batuhankiskac/Desktop/ParayaBuck/docs/crypto_candidate_tournament_validation.md:20)). **Data:** OHLCV feasible; exact candidate provenance and common-capital replay required.

### C6 — crypto cross-sectional momentum

**Mechanism:** rank recent relative returns and hold long-only winners. **Hypothesis:** persistent UP-UP trends may differ from low variance. **Evidence/status:** archived standalone momentum candidates lost money and are closed for retuning; a fixed portfolio-only replay would answer a different contribution question ([crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md:35)). **Data:** OHLCV feasible, but cost and concentration gates are mandatory.

### C7 — crypto time-series momentum

**Mechanism:** each asset’s own lagged return/trend state controls long-only eligibility. **Hypothesis:** persistent broad trends. **Evidence/status:** prior 1d variant was strongly negative with weak walk-forward evidence; no new parameter sweep is proposed ([rejected strategies](/Users/batuhankiskac/Desktop/ParayaBuck/docs/rejected_strategies.md:107)). **Data:** OHLCV feasible; portfolio-only test would retain the frozen rule.

### C8 — crypto cross-sectional reversal

**Mechanism:** select the most negative 70-bar trailing returns, requiring negative return, weekly top-2. **Hypothesis:** temporary coin-specific selloffs mean-revert while low variance parks elsewhere. **Evidence/status:** 1x was positive but 2x negative, with concentration in ETH; portfolio-only exploratory status ([crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md:47)). **Data:** OHLCV feasible; jackknife and concentration reporting required.

### C9 — high-proximity364

**Mechanism:** `close/max(close[-364:])`, weekly top-2. **Hypothesis:** strength continuation in persistent trends. **Evidence/status:** prior 1x/2x/3x results were negative and BTC-dependent ([crypto validation](/Users/batuhankiskac/Desktop/ParayaBuck/docs/crypto_bakeoff_validation.md:19)). **Data:** OHLCV feasible but low priority; no retuning.

### C10 — low-disagreement volume5/45

**Mechanism:** recent five-day nonzero volume divided by prior 45-day nonzero volume, with zero-volume gates. **Hypothesis:** attention/volume regime differs from return-volatility ranks. **Evidence/status:** 1x positive but 2x negative, walk-forward mixed and SOL-concentrated ([crypto validation](/Users/batuhankiskac/Desktop/ParayaBuck/docs/crypto_bakeoff_validation.md:21)). **Data:** OHLCV feasible; volume units and tradability remain limitations.

## Equity and cross-asset candidate inventory

### E1 — C2 low-beta equity

**Mechanism:** 252 aligned prior stock/SPY returns, ascending OLS beta, monthly capped long-only selection; no momentum gate. **Hypothesis:** reduce synchronized equity drawdowns. **Evidence/status:** standalone C2 was `REJECT`; portfolio contribution was not tested. **Data:** canonical PIT equity data is feasible only where 253-price history, lifetimes, corporate actions and T1 membership are complete. Source: [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md:30).

### E2 — overnight/intraday decomposition

**Mechanism:** one frozen statistic from `O_t/C_(t-1)-1` or `C_t/O_t-1`, with fixed cadence and next-open execution. **Hypothesis:** session-boundary effects may diversify residual momentum. **Evidence/status:** repository has gap diagnostics, not an executable alpha result; split/dividend, holiday, open-auction and timestamp causality gates remain. **Data:** OHLC exists, but adjusted signal/execution roles need proof ([equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md:38)).

### E3 — quality

**Mechanism:** one point-in-time profitability or safety measure, monthly/quarterly long-only ranking. **Hypothesis:** steadier firms may help in weak markets. **Evidence/status:** no revision-safe PIT fundamentals, filing availability, identifier lifetime and survivorship-complete receipt; `DATA_BLOCKED`. **Data:** current price data is insufficient ([equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md:44)).

### E4 — value

**Mechanism:** one frozen PIT book-to-market or earnings-to-price ratio, slow capped ranking. **Hypothesis:** valuation dispersion mean-reverts after growth leadership. **Evidence/status:** no valid PIT fundamentals or restatement history; `DATA_BLOCKED`. **Data:** blocked until vintage, filing timestamp, delisting and identifier gates exist ([equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md:50)).

### E5 — PEAD

**Mechanism:** timestamped earnings surprise against the immediately prior consensus vintage, next tradable bar and fixed event horizon. **Hypothesis:** idiosyncratic information may complement residual momentum. **Evidence/status:** release timestamps, estimate vintages and consensus are absent; `DATA_BLOCKED`. **Data:** filing date or quarter label is not a valid substitute ([equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md:56)).

### E6 — cross-asset ETF trend

**Mechanism:** one proposed fixed time-series rule over the archived ETF set with cash on failures. **Hypothesis:** covariance changes across persistent equity, bond, commodity and gold trends. **Evidence/status:** the nine-ETF archive is legacy reference material, not an exact current executable recipe; no current V3 curve exists. Adjustment plus explicit corporate-action cash flows and shared-account accounting remain gates. **Data:** bars exist but executable status is `UNTESTED` ([batch protocol](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_batch_2026_09_05_protocol.md:53)).

### E7 — volatility-managed V3 sleeve

**Mechanism:** lagged realized-vol scaling on the unchanged V3 sleeve, under the same cap and risk overlay. **Hypothesis:** reduce high-volatility drawdowns at the cost of slower recovery. **Evidence/status:** prices are feasible, but this is a new coordinator/overlay and has no local portfolio result. It must not alter V3 signal logic or configured allocation. **Data:** OHLCV feasible; shared cash and exposure accounting required.

### E8 — pairs/statistical arbitrage

**Mechanism:** two-leg spread, long cheap/short rich, convergence/stop/age exit. **Hypothesis:** relative value in sideways markets. **Evidence/status:** current long-only spot contract lacks borrow, shorting, two-leg fills and spread-risk accounting; `CAPABILITY_BLOCKED`. **Data:** OHLCV alone cannot make this executable ([crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md:67)).

The 16 headings above are retained as 16 exact research entries. Related mechanisms may share a broad theme, but each entry remains separately specified and separately gated.

The seven-arm screen is a separate, frozen **screen adaptation** with a deliberate 2022 full-window start for the 364-price arm. Its reversal interval and low-disagreement nonzero-count rules are explicit adaptations recorded in the screen protocol; they are not claims that the legacy archive used identical executable semantics. Attempt 03 completed `42/42` constituents and `36` fixed initial-NAV blends; the results and shard receipts are independently auditable. Completion establishes a retrospective screen, not promotion, shared-account validation, or an economic ranking that overrides cost and contribution gates.

## Sixteen-entry evidence index

| Entry | Document | Source verified | Data / execution status |
|---|---|---|---|
| C3 Low-MAX22 | [crypto candidate](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/crypto_max_candidate_2026_09.md) | [Bali, Cakici & Whitelaw](https://doi.org/10.1016/j.jfineco.2011.03.014) | OHLCV feasible; venue-PIT incomplete; portfolio-only |
| C4 Signed-risk22 | [crypto candidate](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/crypto_signed_risk_candidate_2026_09.md) | [Markowitz](https://doi.org/10.1111/j.1540-6261.1952.tb01525.x) | OHLCV feasible; standalone REJECT; portfolio UNTESTED |
| C5 Downside semivariance T3 | [crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md) | [Brown et al.](https://doi.org/10.1073/pnas.2108031118) | OHLCV feasible; common-capital replay UNTESTED |
| C6 Cross-sectional momentum | [crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md) | [Fieberg et al.](https://doi.org/10.1080/14697688.2023.2269999) | OHLCV feasible; retuning closed; portfolio-only |
| C7 Time-series momentum | [crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md) | [Moskowitz, Ooi & Pedersen](https://doi.org/10.1016/j.jfineco.2011.11.003) | OHLCV feasible; prior standalone REJECT; no sweep |
| C8 Cross-sectional reversal | [crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md) | [De Bondt & Thaler](https://doi.org/10.1111/j.1540-6261.1985.tb05004.x) | OHLCV feasible; concentration gate; portfolio UNTESTED |
| C9 High-proximity364 | [crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md) | [Wiley 52-week-high study](https://doi.org/10.1111/fire.12371) | OHLCV feasible; negative local result; low priority |
| C10 Low-disagreement volume5/45 | [crypto batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_batch_2026_09_05.md) | [Gervais, Kaniel & Mingelgrin](https://doi.org/10.1111/0022-1082.00349) | OHLCV feasible; volume/tradability limits; portfolio UNTESTED |
| E1 C2 low-beta equity | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Frazzini & Pedersen](https://doi.org/10.1016/j.jfineco.2013.10.005) | PIT membership/lifetime gates; standalone REJECT; portfolio UNTESTED |
| E2 Overnight/intraday | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Lou, Polk & Skouras](https://doi.org/10.1016/j.jfineco.2019.03.011) | OHLC exists; adjustment/open execution unproven; UNTESTED |
| E3 Quality | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Novy-Marx](https://www.nber.org/papers/w15940) | PIT fundamentals blocked; no price proxy permitted |
| E4 Value | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Asness, Frazzini & Pedersen](https://doi.org/10.1007/s11142-018-9470-2) | PIT fundamentals blocked; no executable result |
| E5 PEAD | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Bernard & Thomas](https://www.jstor.org/stable/2491062) | Event timestamps/vintages blocked; no executable result |
| E6 Cross-asset ETF trend | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Moskowitz, Ooi & Pedersen](https://doi.org/10.1016/j.jfineco.2011.11.003) | Bars exist; adjustment/shared-account gates; UNTESTED |
| E7 Volatility-managed V3 | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Moreira & Muir](https://doi.org/10.1111/jofi.12513) | Overlay hypothesis; no local result; shared cash required |
| E8 Pairs/statistical arbitrage | [equity batch](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_equity_crossasset_batch_2026_09_05.md) | [Gatev, Goetzmann & Rouwenhorst](https://www.nber.org/papers/w7032) | Spot long-only capability blocked; two-leg execution absent |

## On altı başlık için birleşik öncelik tablosu

| Başlık | Mekanizma | Yardımcı rejim hipotezi | Mevcut kanıt | Veri/işlem/kapsam engeli | Birleşik portföy önceliği |
|---|---|---|---|---|---|
| C3 low-MAX22 | 22 günlük sıçrama maksimumu, ascending top-2 | Kısa pozitif sıçramalar low-vol'dan ayrışabilir | Arşiv standalone negatif; ekran adaptasyonu tamamlandı | OHLCV var; PIT/ortak ledger yok | Orta; maliyet stres testi |
| C4 signed-risk22 | 22 günlük downside-energy oranı | Asimetrik düşüş riski ayrışabilir | Arşiv standalone REJECT; ekran tamamlandı | OHLCV var; shared-ledger katkısı yok | Orta; dTR ve DD için kontrol |
| C5 downside semivariance T3 | Negatif getirilerin RMS'i | Kayıp büyüklüğü low-vol'dan farklı olabilir | Eski kanıt zayıf pozitif; portfolio sonucu yok | OHLCV var; provenance ve ortak sermaye gerekli | Yüksek; C4'e alternatif |
| C6 cross-sectional momentum | Yakın dönem kazananlarını long-only seçme | Sürdürülen UP-UP trend | Arşiv standalone zayıf/kapalı | Maliyet ve yoğunlaşma kapıları | Orta-düşük; retuning yok |
| C7 time-series momentum | Varlık içi gecikmeli trend yönü | Geniş trend devamı | Önceki 1d varyantı negatif | OHLCV yeterli; eski sonuçlar zayıf | Düşük; frozen replay |
| C8 cross-sectional reversal | 70-bar negatif kaybedenleri seçme | Geçici coin-spesifik satış sonrası dönüş | 1x pozitif, 2x negatif; ETH yoğun | OHLCV; jackknife ve 2x/3x gerekir | Yüksek; ekran korelasyonu düşük |
| C9 high-proximity364 | Kapanış / son 364 gün zirvesi | Kalıcı trendlerde güç devamı | Ekran blendinde en iyi DD farkı; standalone eski sonuç negatif | BTC bağımlılığı ve shared ledger | Yüksek; cost/concentration gate |
| C10 low-disagreement volume5/45 | Yakın/önceki hacim oranı | Dikkat/hacim rejimi ayrışması | 1x pozitif, 2x negatif; SOL yoğun | Hacim birimi/tradability ve ledger | Düşük; yalnız stres testi |
| E1 low-beta equity | SPY'ye 252 günlük OLS beta sıralaması | Eşzamanlı equity DD azaltma | Standalone REJECT; katkı yok | PIT üyelik/lifetime/corporate-action | Orta; 0.55 tavanı içinde |
| E2 overnight/intraday | Açılış-gece ve gün içi getiriyi ayırma | Seans kuvvetleri birbirini dengeleyebilir | Gap tanısı var, alpha sonucu yok | Adjusted open, tatil, auction ve timestamp | Orta; önce data gate |
| E3 quality | Tek PIT kârlılık/güvenlik ölçüsü | Zayıf piyasalarda daha dayanıklı firmalar | Sonuç yok; `DATA_BLOCKED` | Revision-safe PIT fundamentals | Bloke; veri kurulunca |
| E4 value | Tek PIT book/market veya E/P oranı | Değerleme yayılımı normalleşmesi | Sonuç yok; `DATA_BLOCKED` | Vintage, restatement, delisting | Bloke; veri kurulunca |
| E5 PEAD | Release öncesi consensus vintage'a göre sürpriz | İdiosinkratik haber devamı | Sonuç yok; `DATA_BLOCKED` | Release timestamp ve consensus yok | Bloke; veri kurulunca |
| E6 cross-asset ETF trend | Sabit cross-asset trend, başarısızda nakit | Varlıklar arası covariance değişimi | Arşiv tarifesi legacy; güncel eğri yok | Adjustment ve shared-account muhasebesi | Orta; accounting gate |
| E7 volatility-managed V3 | Değişmeyen V3'ü gecikmeli vol ile ölçekleme | Yüksek vol'de DD azaltma | Yalnız literatür hipotezi | Yeni coordinator; V3 mantığı/tavanı sabit | Orta; alpha değil overlay |
| E8 pairs/stat-arb | İki bacak spread, ucuz al/pahalı sat | Yatay piyasada yakınsama | Kavramsal; mevcut kontrat bloke | Short/borrow, iki bacak fill ve risk | Bloke; capability kurulunca |

## Existing curve diagnostic

The corrected derived diagnostic [report](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_diagnostic_2026_09_05.md) uses existing native C3/C4 economic outputs only. It evaluates low-variance + MAX and low-variance + signed-risk normalized NAV averages at fixed 50/50 target weights, two windows and 1x/2x/3x costs: 12 cells. These C3/C4 economic studies use their declared 2021 full window; they are distinct from the seven-arm screen, whose frozen full window begins in 2022. Native TR/CAGR/hourly DD/hourly Sharpe use initial cash to terminal mark; daily Sharpe/correlation use common UTC day-end returns and are separately labeled. This is not actual exposure, V3 combined NAV, shared fills, netting or risk feedback.

| Window/cost | Baseline native TR / hourly DD / hourly Sharpe | Lowvar+MAX native TR / hourly DD / hourly Sharpe | Lowvar+Signed-risk native TR / hourly DD / hourly Sharpe |
|---|---|---|---|
| Full 1x | 0.0435 / 0.0700 / 0.2266 | 0.0366 / 0.0704 / 0.1948 | 0.0832 / 0.0757 / 0.4152 |
| Full 3x | 0.0225 / 0.0725 / 0.1277 | 0.0065 / 0.0753 / 0.0506 | 0.0359 / 0.0792 / 0.1945 |
| Recent 1x | 0.0212 / 0.0351 / 0.3171 | 0.0152 / 0.0449 / 0.2287 | 0.0163 / 0.0491 / 0.2212 |
| Recent 3x | 0.0087 / 0.0396 / 0.1383 | -0.0014 / 0.0501 / -0.0069 | -0.0043 / 0.0558 / -0.0397 |

The paired **net daily return** correlations, including the first day-end return from initial cash, are approximately 0.9739–0.9791 for MAX and 0.8739–0.9155 for signed-risk; these are not rank correlations. Both mixtures have worse recent drawdown and native hourly Sharpe than the baseline at 1x and 3x. Full-window signed-risk improves native return and hourly Sharpe but worsens hourly drawdown. These are descriptive tradeoffs, not a candidate decision. The seven-arm screen is complete, while these older C3/C4 blends remain curve-only evidence.

## Portfolio contribution protocol

The finite contribution test must keep V3 logic and its 0.55 ceiling fixed, keep the total crypto budget at 0.05, and use one initial account cash balance. For a candidate, the research target is a fixed 50/50 split of the existing crypto budget (0.025 incumbent + 0.025 candidate); it is not a guarantee of realized exposure. The diagnostic curve blend is useful for synchronized correlation and rough marginal return, while a decision-grade result needs a shared ledger with actual fills, fee quantities, cash, max-two crypto positions, risk suppression and terminal marks. No candidate is promoted or eliminated from the derived blend.

A future contribution batch must use frozen candidate definitions only and preserve one initial account balance, existing V3 logic, equity ceiling `0.55`, crypto ceiling `0.05`, gross ceiling `0.60`, minimum cash `0.40`, max two crypto positions and max six total positions. Use the screen's common full `2022-01-01..2026-07-31` and recent `2024-01-01..2026-07-31` windows when comparing these adaptations; keep the older C3/C4 diagnostic's `2021-04-01` full start separate. Require actual shared-ledger fills, fees, cash, marks, risk suppression, synchronized returns, marginal DD and drawdown overlap. Missing PIT, nonfinite, misaligned or unaccounted ledger fields are `BLOCKED`; no cash or zero placeholder is permitted.

No causal regime claim is available from the present curves. A future regime allocator would need completed-bar state, frozen weights and the same total caps. The new screen is complete as a research artifact, but its initial-capital blend remains diagnostic until shared-ledger execution and joint portfolio validation are run.

## Data and capability limits

The crypto source is configured-pool OHLCV with bar-availability/scanner proxy, not historical venue-PIT completeness. Funding/basis carry needs perpetual marks, funding, collateral and liquidation records; orderflow needs trades, aggressor side, quotes/depth and venue timestamps. Equity quality/value/PEAD need revision-safe PIT fundamentals/events. Pairs needs short/borrow and two-leg execution. These gaps are feasibility blockers, not invitations to download paid data or invent proxies.

The frozen equity V3 identity, current allocation and runtime behavior remain unchanged. This report is a finite research map plus one curve-only diagnostic; it contains no PAPER/LIVE recommendation.

## Reproducibility and scope receipt

Attempt 03's frozen screen receipt records raw manifest SHA256 `b84d411e1edcf855afc1505098415e707b605a8517cacdbb4e3cb4b8df4140f2`, canonical/declared manifest SHA256 `0ebb7ad3141add14abaac7abb0f9ec2b2ddd1c36d7d386712b5aa686f8a03ac9`, protocol SHA256 `39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52`, runner SHA256 `8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d`, settings SHA256 `414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`, and input data SHA256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`. Strict `validate_manifest(require_canonical=True)` passed with 9 source pins; targeted native validation was 28 passed. The completed screen contains 42 constituents and 36 fixed initial-NAV blend rows. The independent numeric analysis artifact is [analysis_2026_09_05.json](/Users/batuhankiskac/Desktop/ParayaBuck/data/research/portfolio_expansion_crypto_screen_2026_09/attempt_03_canonical/analysis_2026_09_05.json) (SHA256 `77145cda693dc1ceba3afbbe6e520a7f2fbc7bb540d7118aca8c8837abfde395`).

The independent final audit is [portfolio_expansion_crypto_screen_final_audit_2026_09_05.md](/Users/batuhankiskac/Desktop/ParayaBuck/docs/research/portfolio_expansion_crypto_screen_final_audit_2026_09_05.md), with machine evidence [independent_audit_2026_09_05.json](/Users/batuhankiskac/Desktop/ParayaBuck/data/research/portfolio_expansion_crypto_screen_2026_09/attempt_03_canonical/audit/independent_audit_2026_09_05.json). The audit report SHA256 is `099f324abb2bc06f20fb600535d2170eb6f3fd0a7f05679d1e3c7f8e22bbf94a`; the machine audit SHA256 is `c6dd4a5f83d8da7164830deb814988da947961be5e36d8056f058dcd9ba13129`. It verifies 42 disjoint cells, all pins, native anchors, accounting, C3/C4 parity and 36 blends. The current campaign diff is restricted to research documents, manifests, receipts, result artifacts and research scripts/tests; no `src/`, production config, or frozen/raw result was edited by this report update. The receipt proves reproducibility and completion, not live readiness or permission to promote.
