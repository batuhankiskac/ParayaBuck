# Araştırma Doğruluğu Denetimi — 2026-08-05

Statü: **RESEARCH CORRECTNESS AUDIT** — bu rapor `docs/research_summary_2026-08-04.md`
dosyasındaki sonuçların geçerliliğini denetler ve düzeltilmiş pipeline'ın
aynı veri üzerindeki koşusunu raporlar.

## 1. Eski sonuçlar neden geçersiz (superseded)

| Hata | Kaynak | Etki |
|---|---|---|
| Outer holdout contamination | Baseline ve raporlar tam veriyle (holdout başlangıcı ≈ 2026-02-01 dahil) çalıştı | Eski holdout `contaminated_by_full_sample_baseline`; production kararı için kullanılamaz |
| Duplicate SPY | `(*EQUITY_ETF_UNIVERSE, "SPY")` — SPY zaten universe'de; manifest `duplicate_bars.SPY=1514` | Veri setinde iki kat SPY; hash/coverage bozuk |
| Adjustment | Equity verisi yalnız `splits` | Temettü dağıtımları momentum sinyallerini bozabilir; total-return uyumlu değil |
| DSR | Medyan fold Sharpe / yıllıklaştırıcı + gün toplamı; skew/kurtosis sabit 0/3 | %98,5 → düzeltilmiş hesapla %34,4 |
| PBO | İlk 2000 lexicographic split | %24,9 → düzeltilmiş dengeli örneklemeyle %50,2 |
| MaxDD / Sharpe | Fold bazında medyan/maks; stitched yok | Medyan fold Sharpe 1,77 → aggregate OOS Sharpe 0,81; MaxDD %11,7 → %11,9 |
| Ay/fold oranı | Fold seviyeleri nominal zincirlendi; gate uygulanmıyordu | %58,5 ve gate'te `minimum_positive_fold_ratio` hiç kontrol edilmiyordu |
| OOS ay sayısı | `sum(weight_history)` = rebalance sayısı | `unique_oos_calendar_months` ile değiştirildi (53) |
| Gate eşikleri | Equity MaxDD %35, pozitif ay %0, PBO %20 sessiz | Sabit sıkı eşikler: MaxDD %10, pozitif fold %60, pozitif ay %60, konsantrasyon %30 |

## 2. Duplicate SPY kaynağı

`scripts/run_portfolio_research.py` indirme döngüsü `(*EQUITY_ETF_UNIVERSE, "SPY")`
kullanıyordu; SPY `EQUITY_ETF_UNIVERSE` içinde zaten vardı. Düzeltme:
- canonical unique sembol listesi + indirme öncesi duplicate kontrolü,
- `save_dataset` exact-duplicate → tek bar (`input_duplicate_bars` audit),
  conflicting duplicate → fail (veri seti yazılmaz),
- final manifest `duplicate_bars` = 0 zorunlu.

Orijinal `equity_etf_1d_v1` audit için DEĞİŞMEDİ; düzeltilmiş veri yeni
kimlikle (`equity_etf_1d_v1_corrected` / Faz 1'de `equity_etf_1d_v2`) kaydedilir.

## 3. Adjustment değişikliği

Araştırma sinyali için dividend+split ayarlı (total-return) veri politikası
getirildi (`EQUITY_ADJUSTMENTS_FOR_PRODUCTION = ("all", "adjusted")`).
`ProviderMetadata` bloğu (provider_name, source_feed, vendor_symbol,
adjustment_method, currency, timezone, exchange_calendar, downloaded_at,
provider_version, research_vs_execution_farkı) manifeste zorunlu;
adjustment eksik/`unknown` ise dataset production araştırmasına kabul edilmez.
Harici provider (Stooq) kullanıldığında `reconcile_providers` ile Alpaca `all`
overlap'i üzerinde doğrulama zorunludur.

## 4. Correctness rerun sonuçları (aynı `equity_etf_1d_v1` verisi, holdout kilitli)

Koşu: `python scripts/run_portfolio_research.py --rerun`
Çıktı: `reports/portfolio_research/nested/equity_dual_momentum_v1_correctness_rerun/`
36 aday, 53 validation fold (2021-09 → 2026-01, tamamı kilitli holdout
2026-02-01 öncesi), base+stress maliyet.

Eski rapordaki en iyi aday **6_1 + above_200dma + vol20 + hedefsiz**:

| Ölçüt | Eski (2026-08-04) | Düzeltilmiş | Gate | Sonuç |
|---|---|---|---|---|
| OOS ay (unique) | 53 (rebalance sayısı) | 53 | ≥ 60 | **KALDI** |
| Sharpe | 1,77 (medyan fold) | 0,81 (aggregate OOS) | ≥ 0,75 | geçti |
| MaxDD | %11,7 (fold maks) | %11,9 (stitched) | ≤ %10 | **KALDI** |
| DSR | %98,5 | %34,4 | ≥ %95 | **KALDI** |
| PBO | %24,9 | %50,2 | ≤ %20 | **KALDI** |
| Pozitif fold oranı | %58,5 (uygulanmıyordu) | %58,5 | ≥ %60 | **KALDI** |
| Stress expectancy | +213 | +213 | ≥ 0 | geçti |
| Stress PF | — | 1,68 | ≥ 1,00 | geçti |
| Pozitif ay | %58,5 | %58,5 | ≥ %60 | **KALDI** |
| Plato | var | var (5+ komşu) | gerekli | geçti |
| Rejim çeşitliliği | var | var | gerekli | geçti |
| Tek varlık konsantrasyonu | %44,4 | %44,4 | ≤ %50 | geçti |
| Holdout | 0 | 0 (kilitli) | 1 kez | **KALDI** |

**Gate kararı: RED.** Düzeltilmiş validation gate'i geçilemedi. Eski rapor
doğru kabul edilmez; kapılar sonuca göre gevşetilmedi.

## 5. Holdout contamination durumu

- Eski (v1) holdout: `contaminated_by_full_sample_baseline` — registry'de
  "açılmamış" görünse bile pristine değildir; production kararı için
  kullanılamaz.
- Yeni koruma: baseline yalnız `outer_holdout_start` öncesi barlar;
  `locked_holdout_start` manifeste yazılı; holdout registry fingerprint
  kilidi (market, timeframe, universe hash, pencere, adjustment, provider)
  holdout barlarına erişimden ÖNCE kontrol edilir; aynı holdout yeni
  experiment_id ile tekrar açılamaz.
- İleri tarihli, hiç görülmemiş forward holdout: yeterli forward veri
  birikene kadar sonuç yalnız provisional validation olarak raporlanır.

## 6. Gate eşikleri — eski / yeni

| Eşik | Eski | Yeni (sabit) |
|---|---|---|
| Equity MaxDD | %35 | %10 |
| Pozitif fold | %60 (uygulanmıyor) | %60 (uygulanıyor) |
| Pozitif ay | %0 (equity) | %60 |
| Tek sembol brüt kâr | %40 | %30 |
| OOS örneklem | rebalance ayı ≥ 60 | unique calendar ay ≥ 60 |
| Sharpe | medyan fold ≥ 0,75 | aggregate OOS ≥ 0,75 |

%10 drawdown sınırı ürün sahibi kararıyla değiştirilirse bu, sonuçlara
bakılmadan önce alınmış ayrı bir risk kararı olarak kaydedilmelidir; kod
içinde eski %35 sınırı korunmamaktadır.

## 7. Statü

- `equity_etf_dual_momentum_v1` (tüm adaylar): **REJECTED / archived** —
  düzeltilmiş validation gate'i başarısız; yeni parametre taraması yapılmadı.
- Crypto XSM: ~27 aylık veriyle suspended; en az 4 yıllık güvenilir 1h veri
  gelmeden parametre taraması yok.
- Equity TSM: challenger/audit olarak kalır; dual momentum ile otomatik
  birleştirme yok.
- Production'a geçiş: **HAYIR** — pristine holdout yok, validation RED.
- Faz 1: `equity_etf_1d_v2` verisi (Stooq, adjusted) alınır ve
  `equity_dual_momentum_v2.yaml` frozen adayıyla yeniden değerlendirme
  yapılır; sonuç ayrı raporlanır.
