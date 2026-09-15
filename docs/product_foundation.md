# ParayaBuck Product Foundation (FROZEN)

Status: **FROZEN** · 2026-08-12 · HEAD: see `git log` after the hardening
loop (G1–G9 closed; G4 deferred as optional optimization; G6 classified
reporting-only).

Documentation only. Nothing at runtime reads this file.

## Frozen principles

ParayaBuck is an automated trading bot. The product foundation is complete
and must not be redesigned. Future missions may only address: trading
strategy selection, systematic portfolio/risk logic where economically
necessary, PAPER forward validation, small operator polish, and genuine
correctness or safety defects.

1. **Autonomous operation.** Configure (PAPER mode default), supply Alpaca
   PAPER credentials, start (`parayabuck`), leave running. The engine
   discovers/maintains its universe, ingests market data, runs enabled
   strategy logic on schedule, constructs portfolio targets, enforces risk,
   submits and reconciles orders, monitors positions, and recovers from
   restart — including across the session-day boundary — without operator
   intervention.
2. **Offline tooling is optional support.** Backtest/optimize/diagnostics are
   finite, optional tooling. PAPER/LIVE runtime has ZERO dependency on
   generated offline artifacts or optimizer output.
   Offline backtests record drawdown breaches as shadow events
   (`drawdown_halt_shadow_events`) instead of freezing the alpha curve on
   the monotonic peak.
3. **Same strategy semantics across backtest/paper/live.** One
   `PortfolioStrategy` interface, one target/risk meaning, one
   execution policy (`PortfolioExecutionPolicy`) shared by the simulator
   and the live broker path.
4. **Execution/ownership/risk infrastructure is frozen** unless a genuine
   correctness or safety defect is discovered. Do not reopen architecture
   because an experimental strategy is being evaluated.
5. **Fail closed, self-heal bounded.** Permanent incidents (ownership drift,
   protection failure, ambiguous execution, …) persist across
   restart/midnight/reboot until a verified recovery (successful
   reconciliation or a granular operator reset). Transient failures
   (network, scanner, startup) retry with bounded backoff; entries stay
   blocked while a market's universe is not verified.
6. **Drawdown has an explicit recovery.** A drawdown breach is an
   operational incident with a monotonic peak; recovery is a verified
   risk-epoch reset (operator action rebasing the peak baseline to the
   current equity) — never a silent date-based clear.
7. **LIVE is never automatic.** Triple-gated: config, `PARAYABUCK_ENABLE_LIVE=YES`,
   and a short-lived HMAC operator approval bound to account + configuration
   digests + dollar cap, plus a fresh Web account-suffix confirmation.

## Hardening loop closure (G1–G9)

| Gap | Status |
|---|---|
| G1 crash-window rebalance wedge | CLOSED (previous audit; now in chaos gate) |
| G2 cross-day persisted safety state | CLOSED (`safety_incidents`, date-independent) |
| G3 bounded autonomous startup recovery | CLOSED (`TransientStartupError` + [5,15,30,60]s retry) |
| G4 disabled-crypto scanner load | DEFERRED (optional optimization) |
| G5 scanner refresh self-healing | CLOSED (bounded retries, equity gated pre-market) |
| G6 accounting/legacy | CLASSIFIED (reporting-only; no change) |
| G7 live-arming prerequisites UI | CLOSED (`/api/live/prerequisites`, no secrets) |
| G8 restart scenarios in chaos gate | CLOSED (16 scenarios, `paper_chaos` marker) |
| G9 Web bind failure orphan | CLOSED (pre-bind probe, verified live) |

## Verification summary

The repository quality gates are ruff, format, mypy, the normal pytest suite,
build, paper-chaos fault injection, and dependency auditing. Run them against
the current checkout before release; this document records the product
contract and is not a test receipt. Controlled PAPER operation must still
establish persisted startup-catch-up and scheduled decisions before any live
consideration.
