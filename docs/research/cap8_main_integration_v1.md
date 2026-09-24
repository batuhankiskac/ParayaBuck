# Cap-8 main integration V1

## Pre-change freeze — 2026-09-24

- Starting remote/local main: `d0c68057234a240a778e5adb06e42c6a3c136c34`.
- Reconciliation correctness: `96254b7de6e045212002cfa807abd448bc6e5d0d` (ancestor of main).
- BUY gross/cash/working-order admission: `8b0b8bbadc71cfd8504757faa3f8df9e5c71a297` (ancestor of main).
- Account-capacity migration V3: `9c5e61382e8cdb71fdd3442d945719024accedd9`, `ACCOUNT_ARCHITECTURE_MIGRATION_8_ACCEPTED`.
- Production parity V2: `d20b70c0333b888b6d0e5438091ede540f3a27ee`, `CAP8_PRODUCTION_PARITY_READY_FOR_PAPER_CHANGE`; `MIGRATION_V3_STILL_VALID`.
- Starting `config/settings.yaml` is PAPER, with `live.enabled: false`. Global `risk.max_positions: 6`; crypto positions 2; gross 0.60; cash buffer 0.40; equity/crypto per-name 0.10/0.05; crypto sleeve 0.10; equity/crypto allocations 0.55/0.05; rebalance band 0.005.

The sole intended production configuration value change is `risk.max_positions: 6 -> 8`. Strategy, universe, scanner, schedules, other risk values and execution model remain unchanged. The rollback is the reverse single-key change; a lower cap does not force liquidation of positions already held. This campaign performs offline checks only. It does not start PAPER, connect to Alpaca, read a broker account, or enable LIVE.
