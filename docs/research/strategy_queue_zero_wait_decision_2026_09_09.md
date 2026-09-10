# Strategy queue zero-wait kararı — 2026-09-09

Kullanıcının aylarca forward veri beklememe kısıtı uygulanmıştır. Bu belge araştırma kapanışıdır; Strateji ailesi hakkında global çürütme iddiası değildir.

34 adayın bugün gerçek `true holdout + cost + caps + V3 complement` sınavına ulaşan sayısı `0`. Hiçbir aday ekonomik olarak kabul edilmedi, PAPER/LIVE veya production değişmedi.

## Sayılar

| Karar | Sayı |
|---|---:|
| `CLOSE_ZERO_WAIT` | 2 |
| `CLOSE_NOT_IMMEDIATE` | 7 |
| `CLOSE_UNAUTHORIZED_PAID_DATA` | 25 |
| `IMMEDIATE_RETEST` | 0 |

`CLOSE_ZERO_WAIT`: H14, K11. Bunlar ileriye dönük collector/shadow book olmadan bugün tamamlanamaz; kullanıcı zaman kısıtı altında kuyruktan kapatılmıştır.

`CLOSE_NOT_IMMEDIATE`: H06, H10, H13, K10, X04, X05, X06. H06/H10'da diagnostic çıktı bulunması yeterli değildir; rank/notional ve gerçek overlay sınavı eksiktir. H13/K10/X04/X05/X06'da execution ve veri kimliği bağımlılıkları bugün tamamlanamaz.

`CLOSE_UNAUTHORIZED_PAID_DATA`: H01, H02, H03, H04, H05, H07, H08, H09, H11, H12, H15, H16, K01, K02, K03, K04, K05, K06, K07, K08, K09, K12, X01, X02, X03. Bu adaylar mevcut authorized/local/free kapsam ile bugün tamamlanamaz; gerekli tarihsel PIT, lisanslı veya yetkilendirilmiş kaynak bu çalışmada kullanıma açık değildir.

Bu sınıflandırma aynı backtesti tekrarlamaz: örnek/kol değişmeden tekrarlama PIT, holdout, execution veya veri kimliği açığını onarmaz.

## Kanıt bağlantıları

Karar JSON: `data/research_runs/strategy_queue_zero_wait_2026_09_09/decision.json`; manifest: `data/research_runs/strategy_queue_zero_wait_2026_09_09/manifest.json`; receipt: `data/research_runs/strategy_queue_zero_wait_2026_09_09/receipt.json`.

ZERO_WAIT_PRUNE_COMPLETE
