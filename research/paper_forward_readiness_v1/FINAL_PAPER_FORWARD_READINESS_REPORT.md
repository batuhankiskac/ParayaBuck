# PAPER readiness decision

PAPER_FORWARD_OBSERVATION_BLOCKED. Current main is not yet evidentially ready for the frozen, two-market forward observation. See READINESS_DECISION.json for the machine-readable gate.

# Starting origin/main SHA

7724a2bc9e61ba325a6a252c8f518cabd99d6c69, fetched with tags/prune on 2026-09-24.

# Final audited SHA

Production origin/main remains 7724a2bc9e61ba325a6a252c8f518cabd99d6c69. Its source tree is 41a94eeb32cba807348cb6a4d5d75578bd637225. Current config and lock hashes are in CURRENT_PRODUCTION_AUTHORITY.json. This audit branch has documentation only and is not published.

# Current production identity

Runtime PAPER; equity_residual_momentum_v3 3.0.0 at allocation 0.55; crypto_low_variance_hysteresis_v1 1.0.0 at allocation 0.05; gross target 0.60; cash buffer 0.40; rebalance band 0.005; minimum order notional 10.

# Global cap

8.

# Crypto cap

2.

# Current equity strategy

equity_residual_momentum_v3.

# Current crypto strategy

crypto_low_variance_hysteresis_v1.

# LIVE state

DISABLED: live.enabled=false, hard_dollar_cap=0, runtime.mode=paper.

# Auto-start behavior

app.auto_start_engine=true. Starting the server with PAPER credentials schedules engine start through FastAPI lifespan. Missing credentials leave the configured trading service unready. A failed start keeps /healthz live and /readyz at 503. Details: AUTOSTART_CALL_GRAPH.md.

# Offline doctor

Synthetic placeholder credentials and --strict --paper --offline returned 0. Both active universes were REFRESH_NEEDED because this fresh worktree has no runtime files. This is not trading readiness. OFFLINE_DOCTOR_RECEIPT.json records each check. No real key was read or printed.

# Startup reconciliation

Broker reconciliation is the startup barrier before portfolio decisions and workers. Synthetic tests cover managed inventory, OPEN orders, offline fills, incomplete/malformed FILL pages, ambiguous states and drift; failure halts or retries only for transient network errors. STARTUP_RECONCILIATION_AUDIT.md.

# Scanner readiness

Both dynamic scanners prohibit static fallback. Equity active age is 36 h; crypto 12 h; config digest must match. A fresh clone can become ready only after normal online refresh succeeds at actual launch. No scanner network call occurred here. SCANNER_STARTUP_AUDIT.md.

# Warmup readiness

V3 requires the SPY-aligned 274-bar minimum daily history, with 273 beta, 252 formation, 21 skip and 63 residual-vol windows. Crypto needs 23 consecutive completed daily bars for the 22-day variance. Incomplete symbols remain entry-blocked. WARMUP_READINESS_AUDIT.md.

# Startup catch-up

After reconciliation and warmup, a mid-cycle launch may immediately target crypto then equity using the latest completed bars. Global reservations and final BUY gates apply. The separate catch-up path currently lacks a PAPER receipt. STARTUP_CATCHUP_AUDIT.md.

# Slot/risk safety

Shipped cap8/cap2 and final fresh gross/cash admission passed existing synthetic tests, including working and partial orders, cross-market commitments and risk-reducing SELLs. POSITION_CAP_READINESS.json and BUY_HEADROOM_READINESS.json. These are offline proofs, not broker execution observations.

# PAPER receipt readiness

BLOCKED. The runtime receipt writer is inside an equity V3 name guard, so crypto decisions do not create runtime receipts. Startup catch-up also bypasses it. The recorded positions_after is an intended-order projection and fills is empty at write time. PAPER_RECEIPT_SCHEMA_AUDIT.md.

# Health/readyz

/healthz is process liveness. /readyz requires healthy expected trading; failed auto-start, scanner, reconciliation, account, worker or queue health is not ready. HEALTH_ENDPOINT_AUDIT.md.

# Alerting

Critical incidents have a logged event; webhook delivery is bounded, throttled and failure-isolated. The operator matrix is in ALERTING_OPERATOR_MATRIX.md. No webhook was called in this audit.

# SQLite persistence

WAL, migrations and transaction state are covered by synthetic crash/reopen tests. The shipped worktree has no runtime DB; no real state was touched. SQLITE_DURABILITY_AUDIT.md.

# Backup/restore

SQLite online backup, verification-before-prune, fsync, retention and collision behavior have tmp_path coverage. No production backup/restore was run. BACKUP_RESTORE_AUDIT.md.

# Restart drills

Fake broker tests cover pending submission, offline fill, SELL/BUY phase and repeated restart. Real SIGTERM/systemd on a target host remains an operator drill after stable PAPER launch. RESTART_DRILL_RESULTS.json.

# LIVE boundary

LIVE credentials alone cannot arm LIVE. Mode, enabled flag, positive cap, explicit environment and account/config/credential-bound approval proof are separate gates. LIVE_BOUNDARY_AUDIT.md.

# Documentation drift found/fixed

The deployment guide incorrectly claimed offline doctor fails on stale universe; corrected. Operations fallback wording was corrected for the shipped no-fallback config. The older LIVE analysis gained a current-state pointer. Historical cap-6 research evidence was preserved. STALE_DOCUMENTATION_AUDIT.md.

# Production code changes

NONE. No strategy, risk, execution or runtime configuration changed. The three operational observability defects below remain blockers.

# Full test result

1232 passed, 4 skipped, 2 deprecation warnings in 105.67 s after allowing synthetic local-loopback fixtures. Focused readiness selection: 155 passed. Ruff check, Ruff format, strict mypy, JSON/YAML validation, privacy scan and git diff checks passed. The initial sandbox attempt had six loopback PermissionError failures; all passed in the authorized rerun. VALIDATION_RECEIPT.json.

# PAPER started during campaign

NO.

# Broker calls during campaign

NONE.

# First PAPER start command

After defects are repaired and a new readiness decision passes: sudo systemctl enable --now parayabuck.service on the documented VM topology, or .venv/bin/parayabuck web for direct CLI. Neither was run. FIRST_PAPER_START_RUNBOOK.md has the operator checks and graceful stop.

# Frozen observation duration

14 consecutive calendar days, with at least two natural weekly equity opportunities and three 96-hour crypto opportunities; extend if the calendar does not produce these events. FORWARD_OBSERVATION_PROTOCOL.md.

# Observation success criteria

Zero unexplained drift, duplicate orders, global effective claims above 8, crypto effective claims above 2, BUYs violating fresh gross/cash, unresolved ambiguous orders or missing lifecycle evidence. Verify scanner recovery, one supervised restart, complete receipts and at least 99% expected ready time. P&L is recorded but never used to retune this initial soak.

# Branch protection recommendation

Disallow main force pushes and branch deletion. Required CI checks and PR review are optional operator governance for a single-operator repository. No GitHub settings were changed.

# Remaining blockers

1. Complete decision-unique PAPER receipts for crypto and startup/scheduled decisions, with confirmed lifecycle semantics.
2. Read-only current-production soak auditor covering both strategies, caps, gross/cash, lifecycle and no-data cases.
3. Operator-visible crypto effective slot claims, including working BUYs, alongside cap 2.

# Exact next action

Repair these three defects with pre-fix synthetic reproductions and focused regressions, rerun the full offline gates, and issue a new readiness decision. Do not start PAPER under this blocked decision.

# Final branch SHA

Run git rev-parse HEAD on audit/paper-forward-readiness-v1 after the local documentation commit; the SHA is also reported in the audit delivery message. A tracked file cannot include its own final commit SHA.
