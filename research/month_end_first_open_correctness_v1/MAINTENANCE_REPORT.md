# Month-end first-open correctness maintenance

## Defect reproduction

The legacy `RebalancePolicy("monthly")` path observes the first completed bar of a new month. `PortfolioBacktestEngine` fills pending orders before making that bar's decision, so a February 1 decision first fills on February 2. The candidate-neutral regression fixture reproduces this exact Jan-31/Feb-1/Feb-2 sequence.

## Narrow fix

`parayabuck.markets.month_end` adds an explicit reusable NYSE-calendar contract:

- final exchange session of month M and its actual market close;
- first exchange session of month M+1 and its actual market open;
- `decision_timestamp < fill_timestamp`;
- exact-symbol first-open bar validation;
- missing, duplicate, or nonpositive-open first-session observations fail closed;
- no later-session fallback.

The completed fill-day daily volume is never used to decide opening availability because it is unknown at the open. Liquidity/capacity logic must use information available by the month-end decision or a separate contemporaneous opening-status source.

The legacy `RebalancePolicy("monthly")` API is not silently reinterpreted. Safely replacing it end-to-end would also require runtime order activation/deadline semantics and backtest pending-order expiry. The corrected primitive is the generic path for callers that require the exact month-end/first-open contract, including the later research readiness harness.

## Caller impact

| Caller/path | Classification | Impact |
|---|---|---|
| `PortfolioBacktestEngine._decide` → `RebalancePolicy.is_period_start` | production backtest and research execution | Legacy monthly behavior unchanged; V3 uses weekly. |
| `PortfolioRuntimeService.process_closed_bars` → `is_period_start` | production PAPER/LIVE scheduling | Legacy monthly behavior unchanged; configured PAPER V3 uses weekly. |
| `run_product_backtest` / `rebalance_policy_for` | production/research construction | Can construct legacy monthly policies; no call to the new primitive. |
| `equity_residual_momentum_v3` | active PAPER production strategy | Weekly; no monthly branch or new-primitive call. |
| `crypto_low_variance_hysteresis_v1` | active PAPER production strategy | `hours:96`; crypto rejects monthly policies. |
| `equity_residual_momentum_v1` | production-capable rollback strategy, inactive | Legacy monthly behavior preserved. |
| runtime health/catch-up and dashboard `next_boundary_after` | production observability/display | Legacy period-start semantics preserved. |
| PIT-universe `_period_key` monthly snapshots | separate research/data-selection semantics | Independent implementation; unchanged. |
| existing and new tests | tests | New primitive/calendar contract covered without reinterpreting legacy fixtures. |
| `month_end_first_open` / `require_first_open_bar` | new candidate-neutral infrastructure | Zero non-test callers in Stage A; readiness v2 will consume it explicitly. |

LIVE remains disabled with hard-dollar cap zero. The addition is unreferenced by current production paths, so no current production behavior changes.

## V3 parity

A fresh current-main pre-fix control and post-fix replay used canonical v5 bars, lifetimes, corporate actions, the production V3 strategy/scanner/risk/execution stack, and 1x/2x/3x costs. Exact target, fill, terminal-event, chronology, count, reserved-role, input, configuration, and window checks all match. Metrics match at `1e-12` relative/absolute tolerance. Verdict: `V3_PARITY_PASS`.

Counts remain:

| Cost | Decisions | Target entries | Fills | Terminal events |
|---:|---:|---:|---:|---:|
| 1x | 495 | 2,807 | 2,054 | 5 |
| 2x | 495 | 2,807 | 2,055 | 5 |
| 3x | 495 | 2,807 | 2,052 | 5 |

`SPY`, `SH`, `PSQ`, and `DOG` have zero ordinary targets and remain observable in all 495 decisions at each cost multiplier.

## Validation

- Pre-fix RED: `ModuleNotFoundError` for the absent generic primitive.
- Calendar/caller focused suite: 98 passed.
- Current crypto identity/parity suite: 112 passed.
- Full suite in permitted serial execution: 1,138 passed, 1 skipped.
- Ruff: passed.
- Ruff format: 250 files already formatted.
- mypy: 133 source files passed.
- `git diff --check`: passed.
- Fresh production-isolation bracket around the successful full run: passed.

The supplementary legacy `benchmarks/parity.py` was attempted but its checked-in reference uses a stale schema without `combined_hash`; it is not a current V3/crypto gate. The fresh V3 exact-hash comparison and current crypto focused suite are the authoritative maintenance evidence.

An initial parallel validation attempt overlapped the full suite with that stale legacy canary and triggered the repository's protected SQLite signature guard. The run was rejected. The full suite was rerun serially with a fresh isolation baseline and passed without protected-path mutation. No PAPER strategy, configuration, risk limit, allocation, LIVE setting, or broker state was changed.
