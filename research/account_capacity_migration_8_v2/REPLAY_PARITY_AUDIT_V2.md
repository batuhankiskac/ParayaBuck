# Shared-account replay parity audit — V2

Audit base: corrected `main` at `8e48b301413a679e6d2b097c28e8711c50ab1a8b`.

## Finding

`ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE`

The required shared-account historical replay is not present on corrected main.
No A/B outcomes were generated. Separate canonical equity and crypto results
cannot answer this question because they do not compete for the same positions,
cash, reservations, pending orders, or account gross.

## Production-to-replay comparison

| Concern | Current production behavior | Existing historical replay | Parity status |
|---|---|---|---|
| Cash and account inventory | One broker `AccountSnapshot` is shared across market decisions in a cycle. | `run_product_backtest` constructs one engine for the requested market; each engine owns cash and positions. | Fails shared-ledger requirement |
| Global count and sleeves | `PortfolioRiskOverlay` sees cross-market positions, slot reservations, open-order claims, sleeve limits, per-name limits, gross, and cash buffer. | Overlay runs within one market engine's account state; separate runs do not see each other's claims. | Policy components exist; joint state is absent |
| Simultaneous ordering | Crypto decisions are processed before equity; only actual planned BUY symbols reserve slots for the later market. | A product run contains one strategy/market and cannot exercise cross-market tie ordering. | Fails |
| Pending orders | Working orders in the local ledger reserve account slots; broker-visible symbols suppress duplicate planning. | Pending orders are scoped to one engine and its market bars. | Fails cross-market coverage |
| Fills and partials | Broker orders have asynchronous accepted, rejected, partial, cancelled, and filled states; deferred BUYs wait on SELL completion. | Bars drive synthetic next-bar-open fills, participation-limited partials, modeled slippage/spread/fees, and same-cycle sell-before-buy processing. | Different state machine; unproven equivalence |
| Failed or rejected orders | Persisted rebalance phases re-evaluate after terminal order states and fresh account snapshots. | No Alpaca rejection or account refresh lifecycle is represented by the product engine. | Fails |
| Cash after orders | Account snapshots and deferred BUY sizing apply cash and gross constraints; same-cycle slot reservations do not reserve dollar notional. | Single-engine cash is updated by its synthetic fills, with no second market competing. | Joint cash behavior unproven |
| Strategy timing | Equity and crypto markets have separate bar timeframes and schedules, processed on observable completed-bar events. | Each product replay accepts one market/timeframe per call. | Cross-market event clock absent |
| Broker pagination | Corrected production open-order enumeration is exhaustive and regression-tested. | Offline backtest does not query broker order endpoints. | FILL/order API is not the blocker; no replay integration |

Relevant current paths:

- `src/parayabuck/backtest/product.py:276-389` filters to one market and builds
  one `PortfolioBacktestEngine`.
- `src/parayabuck/backtest/portfolio_engine.py:438-465,806-963` owns one
  engine's cash, positions, pending orders, synthetic fills, and modeled costs.
- `src/parayabuck/runtime.py:3073-3120,3227-3240` defines crypto-first shared
  cycle ordering and reservations based on actual planned BUY orders.
- `src/parayabuck/execution/portfolio.py:265-337` applies global count and
  account-wide slot claims to the production account snapshot.
- `tests/test_multi_market_portfolio.py` covers overlay arithmetic and stubbed
  coordination, not historical fills or one changing cross-market cash ledger.

## Gate result

Shared-account correctness, data/timing parity, and accounting parity are not
demonstrated. A V2 freeze would make unverified claims about the central
experimental unit. Stop before freezing and before generating outcomes. The
next action is `RESOLVE_SHARED_ACCOUNT_REPLAY_LIMITATION`.
