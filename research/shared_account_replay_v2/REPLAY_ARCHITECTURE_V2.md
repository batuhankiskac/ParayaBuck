# Replay architecture V2

Branch base: corrected `main` at `3f97c02826bc42eda8197dbb7b605d299915155a`. `replay.py` is research-only and not imported by runtime. The V1 coordinator was reviewed against current `PortfolioBacktestEngine`, `PortfolioRiskOverlay`, `reserve_opening_order_slots`, and the global-slot repair. Reused components are listed in `REPLAY_V1_PROVENANCE_RECEIPT.json`.

A `SharedAccount` holds one cash value, `(Market, canonical_symbol)` keyed positions and marks, both markets' working orders, and one risk high-water state. `_SharedEngine` delegates existing strategy, fill, cost, corporate-action and target planning to the canonical offline engine while mapping its cash/position/mark properties to that account. `_SharedOverlay` feeds one combined account snapshot to the production risk overlay. The event coordinator merges source timestamps and applies current `reserve_opening_order_slots` after each decision and again when deferred buys activate. Only configured V3 equity and low-variance hysteresis crypto identities are accepted in historical runs. The constructor rejects every cap except six.

No production code, config, PAPER/LIVE mode, broker connection, or strategy setting changes in this branch. The V2 runner derives strategy parameters and policy from current config. Initial state is flat, all-cash, no pending orders, and NAV 100000 per current `backtest.initial_cash`.

The determinism runner releases the first large event stream after retaining its SHA-256 and compact cash/NAV/final-state proof, before loading the second PIT universe. This bounds repeat-run memory without weakening exact event-stream comparison.
