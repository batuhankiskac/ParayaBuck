# Public-data alpha discovery V4: frozen screening rubric

Frozen before candidate screening on 2026-09-24, from `origin/main` at `7724a2bc9e61ba325a6a252c8f518cabd99d6c69`.

## Common score scale

Each dimension receives an ordinal 0–3: **0** contradicted or unavailable; **1** weak, indirect, or materially unresolved; **2** credible but qualified; **3** direct, independently verifiable, and strong for this account. Unknown evidence receives 0, never an optimistic midpoint. The 0–42 sum orders surviving hypotheses only; it cannot override a hard gate. No local future returns may be used to score.

| ID | Dimension | What a 3 requires |
| --- | --- | --- |
| R1 | External hypothesis strength | Primary research with a clear, defensible mechanism and recoverable method |
| R2 | Long-high evidence | Separately reported positive/high long portfolio result, not only a spread |
| R3 | Modern evidence | Later independent evidence through modern liquid U.S. markets, accounting for decay/costs |
| R4 | Public/free data | Reproducible official or genuinely open critical inputs |
| R5 | PIT causality | Defensible public `available_at` and revision policy before trading |
| R6 | Historical identity | Auditable event/issuer-to-historical-security mapping without current-ticker hindsight |
| R7 | Natural rank | Literature/economics defines signal strength before outcomes |
| R8 | Concentrated expression | Tail monotonicity, continuous strength, tested narrow tail, or naturally sparse threshold |
| R9 | Liquidity | Evidence in liquid/larger issuers and feasible contemporaneous liquidity checks |
| R10 | Holding/slot efficiency | Faithful holding period with manageable simultaneous positions in a shared eight-slot account |
| R11 | Mechanism distinction | Distinct information from residual momentum V3 and crypto hysteresis |
| R12 | Source burden | Minimal custom extraction and historical entity resolution |
| R13 | Replication risk | Independent corroboration and no dependence on one tiny sample |
| R14 | Implementation ambiguity | One literature-faithful base specification without return-driven parameter selection |

For every row, score 2 when the criterion is substantially met with documented limitations; score 1 when only a plausible but unproven path exists; score 0 when absent, contradicted, or unknown. This rule applies to every dimension, including source burden (3 means low burden).

## Hard dispositions, evaluated before numeric rank

`REJECT_LONG_HIGH`: only short-leg or high-minus-low evidence with no credible high long leg. `REJECT_CONCENTRATION`: broad diversified evidence only, with no justified few-name tail or sparse event rule. `REJECT_EVENT_TIMING`: response is exhausted before/at public disclosure. `REJECT_EXECUTION`: causal first trade occurs after the documented mechanism is exhausted. `BLOCK_PUBLIC_PIT`: public historical availability/revision trail cannot be reconstructed. `DEFER_EXTERNAL_DATA_BURDEN`: faithful critical inputs require commercial/proprietary data. `REJECT_MAPPING`: historical security identity needs retrospective ticker matching. `REJECT_PARAMETER_SEARCH`: selecting a base design requires return-driven choice among horizons, ranks, filters, or event subtypes. `REJECT_SLOT_STRUCTURE`: faithful expression needs dozens of simultaneous names with no external concentration case. `EXCLUDE_PRIOR_CLOSED`: prior rejection or deferral with no material new external evidence/public source. `REJECT_METHOD_UNAVAILABLE`: exact primary method cannot be recovered. Multiple reasons may be recorded; one primary final disposition per family.

## Stage rules

Broad screen: at least 50 distinct economic families, roughly balanced across mechanisms. Stage A: at most 15 with a credible external hypothesis, plausible positive long leg, plausible public route, non-obvious concentration route, and no prior exclusion. Stage B: at most 8 after recovering exact signal, timing, formation, holding, high leg, source, rank, and modern evidence. Stage C: at most 5 serious design candidates after inspecting official source samples, PIT/mapping and concentration feasibility without future prices. Final shortlist: 0–3; every survivor must pass all hard gates and have positive long-high, modern relevance, public/PIT/mapping route, natural rank, concentrated expression, reasonable liquidity/slots, and material V3 distinction. No minimum numeric score can rescue a failed hard gate.

Final rank uses the 14 scores, then: stronger long-high evidence; public/PIT feasibility; concentrated expression; modern independent support; lower source/mapping burden; V3 distinction. No candidate backtest, local forward return, or economic selection is authorized.
