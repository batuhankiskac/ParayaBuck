# Production semantics mapping V2

| Production behavior | V2 behavior | Evidence | Match |
| --- | --- | --- | --- |
| Account-wide positions | One market-tagged inventory and combined snapshot | `replay.py` `SharedAccount`, `_SharedEngine._account_snapshot`; current `PortfolioRiskOverlay.apply` | Exact in synthetic account |
| Working opening orders | Market-tagged pending/deferred keys reserve capacity | `_SharedOverlay.apply`, `SharedAccountReplay._sync_reservations` | Exact for synthetic orders |
| Slot admission and exits | Current `reserve_opening_order_slots`; positive positions persist through pending/partial exits | `replay.py` decision/activation; `tests/test_global_slot_accounting.py` | Exact for cap six |
| Crypto rebalance band | Current `PortfolioExecutionPolicy` 0.005; suppressed exit leaves holding | `run_historical.py:_engine`; focused V2 test | Exact offline policy |
| Gross, sleeve, per-name | Current `PortfolioRiskOverlay` on combined snapshot | `run_historical.py:_engine`; combined invariant receipt | Exact at target planning |
| Cash and fills | Shared cash/marks backed by canonical offline fill engine | `_SharedEngine`; isolated parity receipts | Exact offline accounting; broker buying power approximate |
| Same timestamp action order | Crypto then equity with fixed phase sequence | `SAME_TIMESTAMP_ORDERING_V2.md`; event hash | Deterministic; physical timing approximate |
| Equity next-open, costs, actions | Existing offline engine and PIT data | Equity isolated parity receipt | Exact to frozen offline control |
| Crypto next-bar, costs | Existing offline engine and complete hourly-derived daily signals | Crypto isolated parity receipt | Exact to frozen offline control |
| Full exit release | Slot freed only after zero quantity and terminal order | `replay.py` ledger and production helper | Exact synthetic transition |
| Daily-loss/drawdown latch | Canonical offline shadow semantics and one shared NY calendar risk state; runtime restart/operator epochs absent | `_advance_shared_risk`; `REPLAY_LIMITATIONS_V2.md` | Approximate |
| Broker async acknowledgement/reject | No broker state imported | Fill audit; limitations | Not modeled |

The physical availability of both daily source labels is a material open item for cross-market slot priority; isolated parity alone does not resolve it.
