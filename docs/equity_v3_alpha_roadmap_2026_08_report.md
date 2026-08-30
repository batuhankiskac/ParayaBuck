# Equity V3 sonrası alpha araştırması — performans raporu

Tarih: 2026-08-30  
Kapsam: canonical symmetric PIT v5, T1 terminal lifecycle, 2016-01-01–2026-07-30, güncel equity allocation `0.55`.

## Sonuç

En güçlü aday `equity_residual_path_momentum_v1`. V3 control ile aynı koşuda:

- Net return: `+123.866%` vs `+100.994%` (`+22.872` puan)
- CAGR: `7.923%` vs `6.828%` (`+1.095` puan)
- Sharpe: `0.705` vs `0.626` (`+0.079`)
- Max drawdown: `20.179%` vs `21.198%` (`-1.019` puan)
- Turnover: `125.859` vs `137.010` (`-8.14%`)
- Terminal equity uplift: control’a göre `+11.38%`

Bu sonuç henüz promotion kararı değildir. 20-day bootstrap’te candidate’ın control’ü geçme olasılığı return için `0.776`, Sharpe için `0.7605`; return delta’nın %5 persentili `-1.30` puandır. Bu nedenle aday araştırma aşamasında tutuldu, runtime’a alınmadı.

## Aday karşılaştırması

`return` toplam net getiridir; tablo yüzdelere çevrilmiştir. `headline`, yalnız tam dönem kontrol karşılaştırmasıdır; `contract`, tam doğrulama sözleşmesidir.

| Aday | Kapsam | Net return | CAGR | Sharpe | Max DD | Turnover | Control delta return |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V3 control | contract | 100.994% | 6.828% | 0.626 | 21.198% | 137.010 | — |
| Path momentum | contract | 123.866% | 7.923% | 0.705 | 20.179% | 125.859 | +22.872 puan |
| Hysteresis | headline | 99.769% | 6.766% | 0.621 | 20.377% | 134.644 | -1.224 puan |
| Breadth | headline | 83.264% | 5.898% | 0.553 | 20.796% | 136.806 | -17.730 puan |
| Cap-aware IVOL | headline | 66.660% | 4.951% | 0.614 | 15.634% | 105.618 | -34.334 puan |
| Score-risk | headline | 70.717% | 5.190% | 0.627 | 16.290% | 106.248 | -30.277 puan |
| 52-week high | headline | 6.089% | 0.561% | 0.110 | 22.381% | 342.152 | -94.904 puan |
| Short-term reversal | headline | 53.136% | 4.114% | 0.404 | 24.912% | 131.100 | -47.858 puan |

Cap-aware ve score-risk daha düşük drawdown üretti, fakat exposure da düştü (`0.337` / `0.345`) ve getiri kaybı büyüdü. Hysteresis küçük drawdown/turnover iyileşmesi sağladı, alpha artışı sağlamadı. 52-week ve reversal reddedildi.

## Path robustness

- 20 bağımsız 6 aylık fold: median return `3.883%`, mean `4.379%`, pozitif fold `14/20`, worst `-11.118%`.
- Control’e paired fold delta: return median `+0.372` puan, candidate wins `12/20`; Sharpe median `+0.032`, wins `12/20`.
- Fold max-DD delta median `+0.352` puan; DD control’den yalnız `8/20` fold’da daha iyi. Headline DD iyileşmesi dönemsel olarak uniform değil.
- Maliyet `5x` olduğunda path net return `93.401%`, control `71.587%`; Sharpe `0.588` vs `0.497`.
- 2022-01 sonrası recent: path `36.003%`, control `49.039%`; aday son dönemde control’ün gerisinde.
- 2024: path `11.095%`, control `8.902%`; 2025–2026’da path control’ün gerisinde kaldı.
- 2020 hariç curve diagnostic: path return `69.964%`, Sharpe `0.561`; control return `63.946%`, Sharpe `0.527`.

Operasyonel diagnostics: path actual average exposure `0.4578`, max `0.55`; mean qualified pool `82.64` (median `84`, en az 6 şartı tüm kararlarda sağlandı); mean cap-bound names `6.47`; control overlap Jaccard `0.610`; active-return correlation `0.910`.

## Uygulananlar

- Production `equity_residual_momentum_v3` değiştirilmedi.
- Production config, strategy registry, risk overlay, allocation ve runtime sayısı değiştirilmedi.
- Research-only hook’lar ve 7 candidate identity eklendi.
- Predeclared manifest, dataset hash’leri, frozen parameters ve validation contract eklendi.
- Scorer/rank parity, hysteresis, path ID, breadth gate, water-fill, future-bar rejection ve reversal için regression testleri eklendi.
- Çıktılar runtime tarafından okunmayan `data/research/equity_v3_alpha_roadmap_2026_08/` altında üretildi.

Kod: [runner](/Users/batuhankiskac/Desktop/ParayaBuck/scripts/research_v3_equity_roadmap.py) · [manifest](/Users/batuhankiskac/Desktop/ParayaBuck/data/research_manifests/equity_v3_alpha_roadmap_2026_08.json) · [summary](/Users/batuhankiskac/Desktop/ParayaBuck/data/research/equity_v3_alpha_roadmap_2026_08/summary.json) · [fold deltas](/Users/batuhankiskac/Desktop/ParayaBuck/data/research/equity_v3_alpha_roadmap_2026_08/fold_deltas.json)

## Doğrulama durumu

- Targeted roadmap/V3 tests: `19 passed`.
- Full suite, localhost socket izni ile: `1168 passed, 11 skipped, 3 warnings`.
- Ruff check + format: temiz.
- Full-suite warnings: mevcut Starlette/config deprecation uyarıları. Research runner ayrıca mevcut tarihsel incomplete-batch log’larını üretti; yeni exception/test failure yok.

## Ertelenenler

M5 proxy multi-factor için IWM/QQQ reference-slot operator seçimi ve ayrı parity işi; M7 PIT fundamentals için typed feature view/veri feed; M8 max-IR, M9 ensemble, M10 cluster, M12 dispersion, M13 twice-weekly ve allocation governance ayrı fazlara bırakıldı. Hiçbir aday otomatik promote edilmedi.
