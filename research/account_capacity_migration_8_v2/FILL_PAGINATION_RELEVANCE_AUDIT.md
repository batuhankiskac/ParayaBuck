# FILL activity pagination relevance audit — migration V2

Decision: `FILL_PAGINATION_IRRELEVANT_TO_MIGRATION_V2`

This classification is conditional on a deterministic offline replay initialized
flat, all-cash, and with no pending orders. The replay must create fills, fees,
positions, and cash changes from its own historical bar execution model. It must
not initialize from Alpaca FILL activities, a production `ExecutionLedger`,
broker startup state, or a live account snapshot.

The current offline product replay reads historical bars and study inputs and
constructs `PortfolioBacktestEngine` without persisted engine state
(`src/parayabuck/backtest/product.py:276-389`). With no supplied state, the
engine starts with the configured cash and empty holdings and pending orders
(`src/parayabuck/backtest/portfolio_engine.py:438-465`). Its synthetic fills,
fees, and cash accounting are generated from replay bars
(`src/parayabuck/backtest/portfolio_engine.py:806-963`). This path has no
Alpaca FILL-activity enumeration dependency.

The separate live reconciliation path does consume FILL activities and can
affect execution-ledger fills, fees, and recovered positions. That is a
production correctness concern, but it is outside this offline replay contract.
If a future V2 replay reads broker activities, production ledger state, or a
broker snapshot for initialization or parity, this decision must change to
`FILL_PAGINATION_MATERIAL_TO_MIGRATION_V2` and the migration must stop before
freeze or outcomes.

This gate passes; it does not establish shared-account replay fidelity. The
separate parity audit records that blocker.
