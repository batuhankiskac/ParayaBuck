# Zero-wait final decision — 2026-09-10

Karar: `CLOSED_ZERO_WAIT_CONSTRAINT`. Kullanıcının hemen sonuç ve no-wait/no-paid-data kısıtı altında mevcut araştırma yolları kesin olarak kapatılmıştır.

Bu kapanış herhangi bir strateji ailesini küresel olarak çürütmez. Yalnızca bugün test edilen yolların kanıt, zaman ve yetki sınırları altında admission/integration için uygun olmadığını kaydeder.

## Kapsam ve sayımlar

- Original 34: immediate `0`; `CLOSE_ZERO_WAIT=2`, `CLOSE_NOT_IMMEDIATE=7`, `CLOSE_UNAUTHORIZED_PAID_DATA=25`; immediate retest `0`.
- A01-A06: `POST_OUTCOME_PROTOCOL_CHANGE_INVALID`; admitted `0`, integration `0`, dropped `6`. Duplicate dividend ve joint NAV/capacity kanıtı çözülmemiştir; iki review `NEEDS_FIX` verdi.
- Zero-wait overlays: `CLOSE_ZERO_WAIT_TESTED_FAIL_AND_INVALID_EVIDENCE`; admitted `0`, integration `0`, dropped `5`; `45` vaka ve primary holdout x 2x altında beş adayın tamamı `FAIL`.
- Tüm 45 primary attempt için integration-ready `0`; promotion `0`.

## Zero-wait overlay hükmü

Metrikler tanısal olarak korunur ve tekrar admission kanıtı olarak kullanılamaz. CVaR metriği frozen tanımla eşleşmiyor: runner negatif gözlemlerin yüzde beşini kullanıyor; exact-date serisinin en kötü yüzde beşini kullanması gerekiyordu. Ekonomik runner/verifier freeze içinde hash ile bağlanmamış ve sonuç artefaktları review sırasında yeniden yazılmıştır. Bu nedenle mevcut sonuç paketi kanıt olarak geçersizdir.

Aynı örneklem üzerinde yeni ekonomik rerun yapılmayacaktır. A01-A06 zaten invalidated/dropped durumundadır; ZWO için aynı paketi yeniden çalıştırmak endpoint ve provenance kusurlarını çözmez. İleride kısıtlar değişirse yeni freeze, runner hash'i, düzeltilmiş CVaR ve bağımsız review gerekir.

## Değişmeyen sınırlar

Admission, integration ve promotion kapalıdır. Production config, PAPER ve LIVE dokunulmadan bırakılmıştır. Bu closure builder ekonomik worker çalıştırmadı ve ağ kullanmadı.

## Hash kanıtı

- `decision`: `data/research_runs/zero_wait_final_decision_2026_09_10/decision.json`
- `manifest`: `data/research_runs/zero_wait_final_decision_2026_09_10/manifest.json`
- `receipt`: `data/research_runs/zero_wait_final_decision_2026_09_10/receipt.json`
- `zwo_results_sha256`: `f2665c9d48b5993d1d219a79637559a0241345c6ffbaf13baa826d6c0c91d70f`
- `zwo_report_sha256`: `e75691cc168d9884e75e89e1968dc57becf632e40a56bce619be70bf4a7c980c`
- `a01_invalidation_sha256`: `1e215f325a05293683c5181bfd609954c631b6f09613c8487e062b2c01ca404e`

ZERO_WAIT_FINAL_CLOSURE_COMPLETE
