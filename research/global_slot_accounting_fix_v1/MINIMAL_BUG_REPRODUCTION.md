# Minimal production-logic reproduction

Before the patch, a direct synthetic call through the production
`PortfolioRiskOverlay.apply()` and `TargetToOrderPlanner.plan()` established:

| State | Value |
|---|---|
| Cap | 6 |
| Actual broker positions | Equity ALL, CMG, EME, ETN; crypto BCH/USD, LTC/USD |
| Working orders | None |
| Crypto desired target | BCH/USD 0.025, BTC/USD 0.025 |
| LTC current weight / target | 0.001 / 0 |
| Band condition | `abs(0 - 0.001) < 0.005`, so no LTC SELL |
| Generated orders | BCH/USD BUY add; BTC/USD BUY open |
| Occupied/reserved count | 6 before, 7 after BTC BUY reservation |

The first archived June 2024 trace has the same structure: four equities,
BCH and LTC held, BCH and BTC BUY intents, no LTC SELL, seven slot claims at
2024-06-14T00:00:00Z. Its quantities and order sequence are in the archived
`COMBINED_SMOKE_INVARIANTS.json`; no return or strategy performance was used.
The synthetic test in `tests/test_global_slot_accounting.py` is the primary
regression. It now admits the BCH add and blocks the BTC opening while LTC
remains nonzero. This is a production interaction, not merely a replay artifact.
