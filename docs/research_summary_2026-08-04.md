# Portföy Strateji Araştırma Özeti — 2026-08-04

> **STATUS: SUPERSEDED — araştırma doğruluğu denetimi gerekli.**
>
> Bu rapordaki metrikler (medyan fold Sharpe ≈ 1,77; MaxDD ≈ %11,7; DSR ≈
> %98,5; PBO ≈ %24,9; pozitif fold ≈ %58,5) aşağıdaki nedenlerle geçerli
> üretim kanıtı DEĞİLDİR:
>
> 1. Baseline ve raporlar tam veri setiyle (outer holdout başlangıcı ≈
>    2026-02-01 dahil) çalıştırıldı → outer holdout contamination.
> 2. SPY download listesinde iki kez bulunuyordu (duplicate bars).
> 3. Equity verisi yalnız split-adjusted; temettü ayarlaması yok.
> 4. DSR/PBO/ay oranı hesapları hatalıydı (bkz. 4. bölüm).
> 5. Gate eşikleri (MaxDD %35, pozitif fold %60 uygulanmıyordu) sonuca göre
>    gevşekti.
>
> Yerine geçen denetim raporu: `docs/research_correctness_audit_2026-08-05.md`.
> Düzeltilmiş koşu çıktısı: `reports/portfolio_research/nested/
> equity_dual_momentum_v1_correctness_rerun/`.

Üç yeni portföy stratejisi gerçek Alpaca verisiyle baseline ve kilitli-holdout
nested validation'dan geçirildi. Sonuçlar `reports/portfolio_research/`
altında tekrar üretilebilir biçimde saklanır (rapor + manifest + fold
detayları + gate tablosu).

## 1. Veri kapsamı

| Veri seti | İstenen | Fiilen alınan | Not |
|---|---|---|---|
| Equity ETF core 1d | 2016-08 → 2026-08 (10 yıl) | 2020-07 → 2026-08 (~6 yıl) | IEX feed'i 2019'u hiç sunmuyor; 2018-11'de yalnız 2 bar var. Momentum bu boşlukta fail-safe davranır (yetersiz geçmiş → nakit). |
| Crypto spot 1h | 2024-05 → 2026-08 (27 ay) | 2024-05 → 2026-08 | SOL 2024-08-26'da başlıyor (geç listeleme), ADA 2025'ten itibaren. PIT evren geç listeleme ve eksik veriyi trailing likidite kapısıyla ele alır. |

Kaynak: Alpaca IEX (equity) / Alpaca crypto, adjustment=splits. Dataset
hash'leri ve kapsam ayrıntısı `data/research/*/manifest.json` içinde.

**Veri yetersizliği açıkça raporlanır:** equity için 10 yıllık hedef
karşılanamadı; elde edilen ~6 yıl, 60 aylık OOS rebalance kapısını (53 ay)
karşılamaya yetmiyor. Kapı gevşetilmedi; sonuç "yetersiz kanıt" olarak işaretlendi.

## 2. Baseline sonuçları (base maliyet)

| Strateji | Dönem | Getiri | SPY/BTC | Sharpe | MaxDD | Ort. nakit | Turnover |
|---|---|---|---|---|---|---|---|
| equity_etf_dual_momentum_v1 (12_1, pozitif momentum) | 2018-11→2026-08* | %32,2 | %183,6 | 0,48 | %9,7 | %52,8 | 21,8 |
| equity_time_series_momentum_1d_v1 (12m, aylık) | 2018-11→2026-08* | %37,3 | %183,6 | 0,41 | %20,5 | %21,3 | — |
| crypto_cross_sectional_momentum_1h_v1 (7d, 24h, top3) | 2024-05→2026-08 | %34,2 | −%7,5 | 0,63 | %38,1 | %74,3 | 79,5 |

\* 2019 veri boşluğu nedeniyle fiili kararlar 2021-07 sonrası; yıllık dilimler
  `reports/portfolio_research/baseline/*/report.json` içinde.

Maliyet duyarlılığı (base→stress): dual momentum %32,2 → %30,4 (1,8pp drag);
kripto `cost_comparison/` altında ayrıntılı.

## 3. Nested validation (tüm denemeler DSR/PBO'ya dahil)

Deneme bütçeleri: dual momentum 36 aday (3 mekanizma × 3 filtre × 2 vol × 2
vol-hedef), TSM 48 aday, kripto 48 aday — hepsi manifest bütçeleri içinde
(≤30 mekanizma, ≤200 parametre).

### equity_etf_dual_momentum_v1 — en iyi aday (6_1 + above_200dma, vol 20)

| Ölçüt | Değer | Kapı | Sonuç |
|---|---|---|---|
| OOS rebalance ayı | 53 | ≥ 60 | **KALDI** (veri kapsamı) |
| Medyan fold Sharpe | 1,77 | ≥ 0,75 | geçti |
| MaxDD | %11,7 | ≤ %35 | geçti |
| DSR | %98,5 | ≥ %95 | geçti |
| PBO | %24,9 | ≤ %20 | **KALDI** |
| Stress expectancy | +213 | ≥ 0 | geçti |
| Parametre platosu | var | gerekli | geçti |
| Rejim çeşitliliği | var | gerekli | geçti |
| Tek varlık konsantrasyonu | %44,4 | ≤ %50 | geçti |

12_1 varyantları aynı pencerede zayıf (Sharpe 0,3–0,7); 6_1'in üstünlüğü ~6
yıllık veride 12 aylık momentumun dönemle uyumsuzluğuyla tutarlı.

### equity_time_series_momentum_1d_v1 — en iyi aday (norm_3_6_12, aylık)

| Ölçüt | Değer | Kapı | Sonuç |
|---|---|---|---|
| OOS rebalance ayı | 53 | ≥ 60 | **KALDI** (veri kapsamı) |
| Medyan fold Sharpe | 1,76 | ≥ 0,75 | geçti |
| MaxDD | %11,5 | ≤ %35 | geçti |
| DSR | %97,8 | ≥ %95 | geçti |
| PBO | %100 | ≤ %20 | **KALDI** (aday korelasyonu; normalleştirilmiş bileşik momentum varyantları neredeyse aynı getiri serisini üretiyor) |
| Tek varlık konsantrasyonu | %51,6 | ≤ %50 | **KALDI** |

### crypto_cross_sectional_momentum_1h_v1 — en iyi aday (7d, 6h, top2)

| Ölçüt | Değer | Kapı | Sonuç |
|---|---|---|---|
| OOS pozisyon | 190 | ≥ 100 | geçti |
| Medyan fold Sharpe | −1,54 | ≥ 0,75 | **KALDI** |
| DSR | %0,0 | ≥ %95 | **KALDI** |
| PBO | %38,4 | ≤ %20 | **KALDI** |
| Stress expectancy | +49 | ≥ 0 | geçti |
| Pozitif ay oranı | %40 | ≥ %60 | **KALDI** |
| Parametre platosu | var | gerekli | geçti |
| Tek varlık konsantrasyonu | %51,3 | ≤ %50 | **KALDI** |

Baseline'ın tüm penceredeki pozitifliği (%34 vs BTC %−7,5) birkaç sıcak
döneme bağlı; aylık fold dağılımı ortanca getiriyi ~%0'a çekiyor. 27 aylık
pencerede yalnız bir ayı rejimi var — kanıt zayıf.

## 4. Production gate karar tablosu (2026-08-04)

| Strateji | Karar | Gerekçe |
|---|---|---|
| equity_etf_dual_momentum_v1 | **RED / insufficient evidence** | 53 OOS ay < 60 (veri kapsamı); PBO %24,9 > %20 |
| equity_time_series_momentum_1d_v1 | **RED / insufficient evidence** | 53 OOS ay < 60; PBO %100; konsantrasyon %51,6 |
| crypto_cross_sectional_momentum_1h_v1 | **RED** | negatif medyan Sharpe, DSR %0, PBO %38,4, pozitif ay %40, konsantrasyon %51,3 |

Holdout hiçbir aday için açılmadı (hiçbir finalist gate'i geçmedi; kilitli
holdout dokunulmadan korunur). Hiçbir strateji canlı allowlist'e eklenmedi;
shadow/paper gereksinimleri karşılanmadı.

## 5. Sonuç ve öneriler

1. **Equity 6_1 + above_200dma** en umut verici mekanizmadır (Sharpe 1,77,
   DSR %98,5, stress dayanıklı, plato var). 60 OOS ayı tamamlamak için veri
   kapsamı 2020-07 öncesine uzatılmalı (IEX dışı feed veya ücretli veri
   kaynağı) ve aynı kilitli holdout korunarak yeniden değerlendirilmeli.
2. **Kripto XSM** mevcut 27 aylık veride gate'i aşmıyor. Daha uzun 1h geçmişi
   (≥ 4 yıl) veya daha düşük frekanslı mekanizmalar (günlük yeniden sıralama)
   araştırılabilir; yeni deneme, yeni deney manifesti ve yeni holdout ile
   yapılmalıdır.
3. Veri kapsamı iyileştirilmeden parametre taraması tekrarlanmayacaktır —
   deneme bütçeleri manifestlerde sabittir.

## 6. Tekrar üretilebilirlik

```bash
python scripts/run_portfolio_research.py --download --equity-years 10 --crypto-days 800
python scripts/run_portfolio_research.py --baseline
python scripts/run_portfolio_research.py --nested
```

Her koşu deterministiktir (seed=7, RNG yok); aynı veri + aynı kod aynı
sonucu üretir (`tests/test_portfolio_engine.py::test_portfolio_backtest_is_deterministic`).
Manifestler git revision, data hash ve deneme bütçelerini kaydeder.
