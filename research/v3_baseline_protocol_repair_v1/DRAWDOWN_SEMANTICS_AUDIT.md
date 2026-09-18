# Drawdown Semantics Audit

Status: frozen before the persisted corrected-baseline economic results were
opened for re-evaluation.

Audit branch: `research/v3-baseline-protocol-repair-v1`

Production reference: `main` at
`bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`.

Scope is read-only with respect to production behavior. This audit does not
change strategy parameters, allocation, risk limits, PAPER/LIVE posture,
crypto behavior, or the production branch.

## Finding

The configured `risk.max_drawdown_pct: 0.05` is an account-level operational
entry-latch threshold. It is not a direct bound on the offline alpha NAV's
maximum drawdown.

The original corrected-baseline G5 therefore compared two different
quantities:

* production account high-water risk control; and
* an offline alpha curve that is intentionally allowed to continue trading
  after a drawdown breach.

Classification: **`G5_MISSPECIFIED`**.

## Exact semantics

| Question | Determined behavior | Evidence |
|---|---|---|
| What does 5% measure? | `AccountSnapshot.drawdown_pct = max(0, (peak_equity - equity) / peak_equity)`, where `peak_equity` is the account high-water mark. It is account equity, not strategy return, and includes the account state used by the runtime. | `src/parayabuck/core/models.py:206-222`; `config/settings.yaml:184`; `src/parayabuck/execution/portfolio.py:112-123` |
| What happens on PAPER/LIVE breach? | The runtime records `portfolio_drawdown_halt`, sets the entry-risk latch, emits the incident, and persists the safety incident. | `src/parayabuck/runtime.py:2202-2253`; `src/parayabuck/runtime.py:2325-2377` |
| Liquidate or only block new entries? | Only risk-increasing entries are blocked. The runtime caps targets at current weights so SELL-only/risk-reducing decisions continue. There is no automatic liquidation in the drawdown-latch path. | `src/parayabuck/runtime.py:2205-2207`; `src/parayabuck/portfolio/runtime_service.py:955-1008` |
| How does it resume? | A verified operator risk-epoch reset is the explicit recovery path: the engine must be running, reconciliation must succeed without drift, a fresh account snapshot is taken, the broker/repository high-water is rebased, and the latch is re-evaluated. | `src/parayabuck/runtime.py:4291-4356` |
| Can it recover automatically from market appreciation? | Not while the monotonic peak leaves the account at or below the threshold. The documented automatic-clear case is an external equity change such as a deposit; otherwise the operator reset is required. | `src/parayabuck/runtime.py:2209-2215`; `src/parayabuck/runtime.py:4295-4301` |
| What does offline backtest do? | The default engine passes `enforce_drawdown_halt=False`; it records `portfolio_drawdown_halt` as a shadow reason but leaves the target uncapped for a drawdown-only breach. Daily-loss enforcement remains separate. | `src/parayabuck/backtest/portfolio_engine.py:354-358`; `src/parayabuck/backtest/portfolio_engine.py:1028-1042`; `src/parayabuck/execution/portfolio.py:154-189` |
| Does the alpha NAV freeze, deleverage, liquidate, or continue? | It continues through the shadow drawdown event. The event is derived from rebalance metadata; the result stores it as `drawdown_halt_shadow_events`. Offline final liquidation, when enabled, is the normal end-of-sample lifecycle and is unrelated to a shadow drawdown halt. | `src/parayabuck/backtest/portfolio_engine.py:549-583`; `src/parayabuck/backtest/portfolio_engine.py:1701-1705` |
| Is offline max drawdown directly bounded by 5%? | No. The architecture explicitly separates the production entry halt from the offline shadow event, so a 5% comparison is not semantically valid. | `docs/strategy_architecture.md:177-181`; `docs/product_foundation.md:24-27` |

## Why the original G5 was not rescued by a different replay

The old G5 did not merely choose a strict threshold. It applied a production
control to a different state transition model. A faithful runtime-latch replay
would require a predeclared policy for the operator's risk-epoch reset. The
production code requires reconciliation and an operator action; the baseline
protocol supplied no deterministic reset schedule. Inventing reset dates or
silently freezing the alpha NAV would create a new, post hoc simulation.

The repair therefore does not replay the 11.7-million-row dataset under an
invented latch policy. It uses the existing persisted run and independently
verifies the declared offline shadow semantics.

## Consequence for protocol repair

Option A is selected: there is no absolute 1x offline alpha maximum-drawdown
acceptance threshold. The protocol reports and independently recomputes
maximum drawdown, retains the positive-risk-adjusted and validation gates,
retains pre-existing cost-stress gates, and requires the shadow-event
semantics to be correct.

The pre-existing 3x cost-stress catastrophic-loss guard is retained as a
separate stress gate because it was frozen before this replay. It is not a
normal 1x alpha drawdown bound and is not used to replace the invalid G5.
