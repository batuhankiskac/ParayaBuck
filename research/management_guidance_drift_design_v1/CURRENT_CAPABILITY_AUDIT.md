# Current capability and repository state

2026-09-22, source/design only. The six requested governance documents were
read before research work: research register, archive inventory, canonical V3,
canonical crypto, equity-universe limitations and core-satellite policy.
Their existing economic records were not rerun and are not guidance outcomes.

## Provenance and boundaries

Fresh fetch verified local main and origin/main at
`16db2eaad9061a1669166ef9ab6717853337fafc`. Starting working tree was clean on
`research/share-repurchase-announcement-design-v1` at
`73fe3e0065c46f813508a08013fb2198629d9bda`. Switched to clean main, then created
`research/management-guidance-drift-design-v1`; no buyback commit was merged or
modified. Main is not modified by this campaign.

## Code inspection, no execution of candidate economics

CodeGraph preceded targeted code searches. Current paths and implications:

| Component | Static evidence and implication |
|---|---|
| V3 | `src/parayabuck/portfolio/equity_residual_momentum_v3.py`: positive 12-1 residual gate, raw 6/9/12 rank consensus, IVOL filter and inverse-volatility sizing. This consensus is price ranking, not analyst EPS consensus. |
| Scanner | `src/parayabuck/universe/scanner.py`: active/tradable/venue, price, 20-day median dollar-volume, ATR and completeness checks. Config: $5 minimum price, $20m minimum median dollar volume, ATR 0.5%–12%. Small/opaque academic firms cannot be assumed to survive this pool. No scanner run or eligibility return comparison. |
| Observation set | Strategy ranking and scanner fallback are separate paths; fallback sector diversity is not a universal final-position cap. New guidance cannot bypass the existing observation-set contract by assumption. |
| Lifetimes | `src/parayabuck/backtest/lifetimes.py`: strict 16-field schema, entity/provider IDs, dated symbol/rename intervals, unresolved lifecycle handling. No built-in CIK or EPS-period bridge. |
| Execution | `src/parayabuck/backtest/portfolio_engine.py::_fill_side` requires order.created_at < bar.timestamp. Completed-bar strategy decisions precede later tradable opens. A pre-open filing requires event ingestion/scheduling not demonstrated by a bar-only contract. |
| Account | `config/settings.yaml`: six account positions, 0.10 per name, 0.55 equity sleeve, 0.05 crypto sleeve, target gross <=0.60 and cash buffer0.40; V3 raw top_n8 does not supersede the account cap. |
| Safety | Static configuration PAPER; LIVE disabled and live dollar cap0. No broker access or operational state change. |

Targeted `src`, config and data filename searches found no canonical analyst
EPS consensus panel or management-guidance parser. No candidate OHLCV file,
price/market-cap panel, actual EPS or future-return file was loaded.

## Reusable SEC capability is narrower than guidance readiness

The register records historical Form 4 acquisition and fail-closed mapping.
Its archived `SECURITY_MAPPING_REASSESSMENT.md` was read at
`archive/research/opportunistic-insider-source-reassessment-v1-2026-09`.
It demonstrates an as-filed symbol/date/lifetime and historical CIK collision
approach for a bounded subset; it does not provide a universal CIK master,
guidance extraction or analyst expectations. Do not claim SEC work starts
from zero, or that Form 4 success solves this new signal.

## Governance implications

Standalone alpha, if ever authorized, need not beat V3. Only a subsequent,
separately frozen combination experiment can establish portfolio contribution.
No combination protocol or candidate is frozen here.

Target gross/cash at decisions and BUY replans are distinct from realized
marked-to-market gross/cash between decisions. Future G9 would specify source,
cadence, tolerance and timing; passive drift is not automatically an order-
caused breach. The 5% PAPER/LIVE drawdown latch is not an offline drawdown
ceiling. No limits or semantics are changed.
