# Equity Dual Momentum v2 Sonuçları — equity_etf_dual_momentum_v2

Tarih: 2026-08-05
Statü: **REJECTED / archived** — düzeltilmiş validation gate'i başarısız;
kapılar gevşetilmedi.

## Koşu

- Veri: `equity_etf_1d_v2` (2016-01-04 → 2026-08-05, total-return, hybrid
  SIP+IEX, kalite kapısı geçti)
- Manifest: `config/experiments/equity_dual_momentum_v2.yaml`
- 12 diagnostic ablation (mekanizma bütçesi içinde): momentum_scheme
  {6_1, 12_1, 3_6_12} × absolute_filter {above_200dma, positive_momentum} ×
  volatility_window {20, 60} × volatility_target {null}
- Frozen primary candidate: **6_1 + above_200dma + vol20 + None + top_n=3,
  kaldıraç yok**
- 107 unique OOS calendar ay; holdout kilitli (2026-02-01), registry
  preflight; baseline holdout öncesi kesildi
- Rapor: `reports/portfolio_research/nested/equity_etf_dual_momentum_v2/`

## Frozen candidate sonucu

| Ölçüt | Değer | Gate | Sonuç |
|---|---|---|---|
| unique OOS ay | 107 | ≥ 60 | geçti |
| Aggregate OOS Sharpe | 0,56 | ≥ 0,75 | **KALDI** |
| Aggregate MaxDD | %27,2 | ≤ %10 | **KALDI** |
| DSR | %51,5 | ≥ %95 | **KALDI** |
| PBO | %92,4 | ≤ %20 | **KALDI** |
| Pozitif fold oranı | %62,6 | ≥ %60 | geçti |
| Pozitif ay oranı | %62,6 | ≥ %60 | geçti |
| Stress PF | 1,53 | ≥ 1,00 | geçti |
| Stress expectancy | +166 | ≥ 0 | geçti |
| Parametre platosu | yok (maks 4 komşu < 5) | gerekli | **KALDI** |
| Rejim çeşitliliği | var | gerekli | geçti |
| Tek varlık konsantrasyonu | %50,2 | ≤ %50 | **KALDI** |
| Holdout | 0 | tam 1 kez | **KALDI** |

En iyi aday (positive_momentum + 6_1 + vol20): Sharpe 0,64, MaxDD %27,2,
DSR %60,8, PBO %92,4, plato yok — o da RED.

## Yorum

10,5 yıllık total-return veride stratejinin aggregate OOS Sharpe'ı ~0,56-0,64;
2020 crash + 2022 bear ile MaxDD %27. Eski rapordaki medyan fold Sharpe
1,77'nin veri kapsamı + metrik hatalarına dayandığı doğrulandı. PBO %92 bu
aday ailesinde seçim overfitinin yüksek olduğunu gösteriyor; plato kapısı
diagnostic ablation bütçesiyle (≤12 deneme → maks 4 komşu) zaten sağlanamaz.

## Go/no-go (§10)

- Düzeltilmiş validation: **başarısız** → strateji **arşivlendi**; yeni
  parametre taraması yapılmadı.
- Holdout: **açılmadı** (validation gate geçmeden holdout açılmaz).
- Production: **HAYIR**.
- Runtime/rollout (Faz 3): **uygulanmadı** — araştırma sonucu yeterli değil.
- Kripto XSM: suspended; TSM: challenger/audit.

## Tekrar üretim

```bash
python scripts/run_portfolio_research.py --download-v2 --equity-years 11
python scripts/run_portfolio_research.py --nested --v2
```
