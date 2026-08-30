# Equity V3 sonrası alpha araştırması — ilk dalga

Bu çalışma araştırma-only’dir. `equity_residual_momentum_v3` değiştirilmedi; config ve product registry’ye yeni aday eklenmedi. Adaylar aynı `PortfolioStrategy.decide(context) -> TargetPortfolio` / `rank_candidates` sözleşmesini, long-only hedefleri ve mevcut `PortfolioRiskOverlay` sınırlarını kullanır.

## Dondurulmuş kontrol

- Canonical symmetric PIT v5 dataset
- T1 terminal lifecycle + corporate-action input
- Güncel equity allocation: `0.55`
- Formation/skip/beta/residual-IVOL: `252/21/273/63`
- `top_n=8`, per-name cap `0.10`, IVOL q`0.70`, SPY hariç
- Haftalık karar, completed-bar signal, next-tradable execution

## Uygulanan research identities

| ID | Mekanizma |
| --- | --- |
| `equity_residual_momentum_hysteresis_v1` | Exact V3 ranking; entry rank ≤8, held eligible rank ≤12; overlay için gerçek alpha rank korunur |
| `equity_residual_path_momentum_v1` | V3 6/9/12 rank consensus + fixed 25% residual information-discreteness continuity rank |
| `equity_residual_momentum_breadth_v1` | 6/9/12 residual horizon’larından en az 2’si pozitif gate |
| `equity_residual_momentum_cap_aware_ivol_v1` | Aynı seçim; clipped inverse-vol bütçelerini capped water-fill ile 0.55 hedefe dağıtma |
| `equity_residual_momentum_score_risk_v1` | Consensus percentile / IVOL capped water-fill |
| `equity_52week_high_proximity_v1` | Completed close / trailing 252-bar high; mevcut SPY IVOL filtresi + V3 portfolio construction |
| `equity_residual_short_term_reversal_v1` | Son 5 completed residual return’ın negatifi; haftalık, top-8, mevcut IVOL filtresi |

Kod: [research_v3_equity_roadmap.py](/Users/batuhankiskac/Desktop/ParayaBuck/scripts/research_v3_equity_roadmap.py)

Manifest: [equity_v3_alpha_roadmap_2026_08.json](/Users/batuhankiskac/Desktop/ParayaBuck/data/research_manifests/equity_v3_alpha_roadmap_2026_08.json)

Çalıştırma:

```bash
.venv/bin/python scripts/research_v3_equity_roadmap.py --scope contract
```

`--scope headline` yalnız tam dönem control karşılaştırması; `--scope fold` buna ek olarak 20 bağımsız 6 aylık fold; `--scope contract` ise 1x/2x/3x/5x cost, yıllık/recent pencereler, control top-5/top-3 jackknife ve 20 günlük 2.000 bootstrap çalıştırır. Her kapsam seçim/exposure/qualified-pool/cap/turnover/overlap diagnostics yazar. Sonuçlar `data/research/equity_v3_alpha_roadmap_2026_08/` altında kalır ve runtime tarafından okunmaz.

Bu koşunun performans özeti: [rapor](/Users/batuhankiskac/Desktop/ParayaBuck/docs/equity_v3_alpha_roadmap_2026_08_report.md).

M5 proxy multi-factor (IWM/QQQ ref slotları), PIT fundamental branch, max-IR, ensemble, cluster/dispersion diagnostics, twice-weekly cadence ve allocation governance kararı açık operator seçimi veya ayrı correctness mission istediği için bu dalgada uygulanmadı.
