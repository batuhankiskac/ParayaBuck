# Pre-existing Governance and Economic Evidence

This record was assembled before opening the persisted corrected-baseline
economic results. Dates below are commit dates in the repository history and
all precede the corrected replay result.

## Evidence inventory

| File | Commit/date | Meaning | Applicability |
|---|---|---|---|
| `docs/product_foundation.md` | `9220d7a1be79824cbdb4bdcc4d8365846e101050`, 2026-08-25 22:23:04 +03:00; foundation text states 2026-08-12 | Research is optional support; offline backtests record `drawdown_halt_shadow_events` instead of freezing the alpha curve. Drawdown is an operational incident with an explicit risk-epoch reset. | Directly applicable to the backtest/runtime semantic boundary and recovery policy. It does not define an economic alpha max-drawdown threshold. It preserves PAPER and LIVE boundaries. |
| `docs/strategy_architecture.md` | `9220d7a1be79824cbdb4bdcc4d8365846e101050`, 2026-08-25 22:23:04 +03:00 | The portfolio/risk layer halts entries on daily-loss/drawdown breaches in production, while backtest records shadow events and keeps trading. Research does not feed a runtime promotion database or GREEN/RED gate. | Directly applicable. This is the clearest pre-existing statement that account risk control and offline alpha evaluation are different quantities. |
| `src/parayabuck/runtime.py` and tests | `f3c394db5bd87a03ab83d64dfeec2f471da6f36a`, 2026-08-12 09:10:06 +03:00, `fix(risk): drawdown breach is an operational incident with explicit risk-epoch reset` | The operational breach persists, blocks entries while allowing SELL-only decisions, survives restart, and has a verified reconciliation/reset path. | Directly applicable to PAPER/LIVE operational behavior; not an offline strategy-acceptance rule. |
| `docs/strategy_robustness.md` | Content present in the pre-replay history at `9220d7a1be79824cbdb4bdcc4d8365846e101050`, 2026-08-25 22:23:04 +03:00 | The frozen equity strategy's offline full-period report gives maxDD 25.45% and explicitly says frozen production parameters remain unchanged regardless of diagnostic outcome; documented risks are “not gates.” | Applicable as historical economic governance. It disproves the existence of a universal 5% offline alpha maxDD acceptance rule. It is not a claim that every strategy passes. |
| `docs/capital_utilization_cadence_verdict.md` | `9a3ff5883c17d10cfca78d85407bb409a81fb6bd`, 2026-08-18 19:35:10 +03:00 | The cadence acceptance rule compares CAGR, Sharpe/maxDD/capital utilization, cost robustness, walk-forward consistency, and concentration. The accepted weekly equity candidate reported 19.68% maxDD; the report separately classified drawdown-halt shadow days as an operational diagnostic. | Applicable as the pre-existing relative/economic acceptance style. MaxDD was a reported/comparative metric, not the 5% runtime latch applied as an absolute offline bound. |
| `docs/validated_strategies.md` and `tests/test_strategy_spec_freeze.py` | `3b53c70a7a70773733e8d38b179ccdb2ac94513c`, 2026-08-13 16:57:23 +03:00, and subsequent frozen updates | The product contract freezes strategy identities and shared risk/configuration values; it does not define a universal offline maxDD threshold. | Applicable to identity and parameter governance. It does not authorize replacing the invalid G5 with a new threshold. |

## What exists and what does not

Pre-existing governance contains:

1. an account-level 5% drawdown control for production operations;
2. a separate offline shadow-event semantic for backtests;
3. economic acceptance through positive returns/risk-adjusted metrics,
   validation or walk-forward evidence, cost robustness, and portfolio/risk
   controls; and
4. relative maxDD comparisons in cadence research.

The search did not find a pre-existing universal absolute offline strategy
maxDD threshold equal to `risk.max_drawdown_pct`. The only source of the
original corrected-baseline G5 was the task-local
`CORRECTED_BASELINE_PROTOCOL.md` on commit
`d34efd3b57cc22c766c5351c12201089bdf55480` (2026-09-18 21:22:36 +02:00).
That protocol was frozen before its replay output, but it was not an
independent historical governance rule; its wording itself called the 5%
value a production-policy threshold while also using the offline shadow path.

## Option assessment

| Option | Assessment | Decision |
|---|---|---|
| A. No absolute offline DD threshold | Supported by the architecture and historical reports. Report maxDD, require positive risk-adjusted/validation economics, cost robustness, and correct shadow semantics. | **Selected** |
| B. Historical absolute strategy threshold | No applicable pre-existing universal threshold was found. | Rejected: would invent or misapply a threshold. |
| C. Faithful runtime-latch replay | Technically possible only with a predeclared operator reset policy plus reconciliation semantics. The failed protocol did not declare one. | Rejected for this repair: an invented reset schedule would be post hoc. |
| D. Historical relative DD criterion | Relative maxDD improvement existed in cadence research, but it compares candidates against a control and does not apply to this fixed V3 baseline. | Rejected as a V3 gate; retained as evidence of prior governance style. |

## Temporal conclusion

The new protocol can repair G5 without reading the observed replay metrics:
the decision follows from source semantics and pre-existing governance, not
from whether the corrected curve did or did not clear the old 5% value.
