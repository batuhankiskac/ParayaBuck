# Cap-8 main integration V1

## Pre-change freeze — 2026-09-24

- Starting remote/local main: `d0c68057234a240a778e5adb06e42c6a3c136c34`.
- Reconciliation correctness: `96254b7de6e045212002cfa807abd448bc6e5d0d` (ancestor of main).
- BUY gross/cash/working-order admission: `8b0b8bbadc71cfd8504757faa3f8df9e5c71a297` (ancestor of main).
- Account-capacity migration V3: `9c5e61382e8cdb71fdd3442d945719024accedd9`, `ACCOUNT_ARCHITECTURE_MIGRATION_8_ACCEPTED`.
- Production parity V2: `d20b70c0333b888b6d0e5438091ede540f3a27ee`, `CAP8_PRODUCTION_PARITY_READY_FOR_PAPER_CHANGE`; `MIGRATION_V3_STILL_VALID`.
- Starting `config/settings.yaml` is PAPER, with `live.enabled: false`. Global `risk.max_positions: 6`; crypto positions 2; gross 0.60; cash buffer 0.40; equity/crypto per-name 0.10/0.05; crypto sleeve 0.10; equity/crypto allocations 0.55/0.05; rebalance band 0.005.

The sole intended production configuration value change is `risk.max_positions: 6 -> 8`. Strategy, universe, scanner, schedules, other risk values and execution model remain unchanged. The rollback is the reverse single-key change; a lower cap does not force liquidation of positions already held. This campaign performs offline checks only. It does not start PAPER, connect to Alpaca, read a broker account, or enable LIVE.

## Offline integration decision

**CAP8_CODE_INTEGRATION_ACCEPTED** for repository integration and publication only. This decision does not authorize a PAPER startup or LIVE activity.

- The parsed YAML semantic diff against starting main contains exactly `risk.max_positions: 6 -> 8`.
- The shipped config regression proves PAPER mode, disabled LIVE, cap 8, crypto cap 2, and unchanged strategy, allocation, gross, cash, per-name, crypto sleeve, and rebalance values.
- Synthetic tests using the shipped config cover occupied and restored working slots, the eighth and ninth slot boundary, crypto's two-position limit, gross/cash BUY admission, and committed working BUY headroom. Existing restart, reconciliation, deferred BUY, and fail-closed suites were also run.
- Focused offline matrix: 177 passed, 1 skipped. Full offline suite: 1220 passed, 4 skipped. Ruff check and format check, strict `mypy src`, and `git diff --check` passed. No credentials were used.
- No PAPER or LIVE runtime was started, and no broker connection, account read, order, or fill occurred during this integration.

The config-change commit is identified in `CAP8_CONFIG_INTEGRATION_RECEIPT.json`. The published-main readback is recorded separately in `CAP8_REMOTE_VERIFICATION_RECEIPT.json`.
