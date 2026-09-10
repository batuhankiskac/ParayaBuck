# Immediate A01-A06 invalidation — 2026-09-10

Karar: `POST_OUTCOME_PROTOCOL_CHANGE_INVALID`. A01-A06 ekonomik çıktıları fiziksel olarak korunur; prespecified evidence olarak yeniden kullanılamaz.

Frozen manifest `8cec049f…` değişmemiştir. Üretim config/scanner kurallarının freeze öncesinde mevcut olması, sonradan runner'a eklenen ve üretim seam'ini birebir uygulamayan davranışı bu çalışmaya geriye dönük bağlamaz.

İki bağımsız review `NEEDS_FIX` sonucundadır. Eksikliği açık kalan duplicate-dividend çözümü bu run için çözülmemiştir; adaylar dropped edildiği için full rerun gerekli değildir.

Joint NAV/capacity kanıtı eksiktir. Bu paket admission, integration ve promotion kapılarını kapatır. Mevcut metrikler yalnızca tanısal arşivdir.

## Sabit karar alanları

| Alan | Değer |
|---|---|
| Sınıflandırma | `POST_OUTCOME_PROTOCOL_CHANGE_INVALID` |
| Adaylar | `A01-A06` |
| Diagnostic metrikler | Korundu, yeniden kullanım yasak |
| Duplicate dividend | Çözülmemiş |
| Full rerun | Gerekli değil; run dropped |
| Joint NAV/capacity | Eksik |
| Admission / integration / promotion | `false / false / false` |
| Production / PAPER / LIVE | Dokunulmadı |

## Hash kanıtı

- `decision`: `data/research_runs/immediate_candidates_invalidation_2026_09_10/decision.json`
- `manifest`: `data/research_runs/immediate_candidates_invalidation_2026_09_10/manifest.json`
- `receipt`: `data/research_runs/immediate_candidates_invalidation_2026_09_10/receipt.json`
- `frozen_manifest_sha256`: `8cec049f1d61d929f001b53af43a7e46ede1619243a1605e30d1dc312dd9965b`
- `results_sha256`: `a06992dc1bab8785d28fcced9b627b456cdfb1739a751266e30ff80b9cab443f`
- `runner_sha256`: `6db5546a9aaee4d47f4e4deb86dafcabe1d6c3bd62cfcaa11fe5d6a3a55ab6de`

Bu karar dosyası ile aynı run yeniden ekonomik kanıt olarak kullanılamaz. Yeni bir ekonomik değerlendirme gerekirse yeni freeze, exact production seam pinleri ve bağımsız contract review gerekir.

IMMEDIATE_CANDIDATES_INVALIDATION_COMPLETE
