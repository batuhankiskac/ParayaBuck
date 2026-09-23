# Execution and isolated parity

## Equity incumbent

`equity_residual_momentum_v3` observes completed daily equity bars and uses its
configured weekly cadence. The equity product universe and entity-lifecycle
mapping use the canonical V3 control inputs. Existing orders execute at the
next tradable daily bar open. The fill engine uses its canonical participation,
slippage, spread, commission, and effective-dated regulatory-fee settings.
Bars are already split adjusted; supplied split actions must not be applied a
second time. Explicit dividend actions credit cash. Terminal entities close on
their last real execution bar.

The isolated comparison uses the current canonical V3 1x control and requires
exact target/fill/terminal/chronology hashes, counts, reserved-role behavior,
and the harness's predeclared 1e-12 metric tolerances. A fresh canonical
1x/2x/3x control was run as required by the existing V3 harness; only the 1x
arm is used for shared-engine parity. Cost multipliers are not changed by this
shared replay.

## Crypto incumbent

`crypto_low_variance_hysteresis_v1` observes complete epoch-aligned UTC daily
bars aggregated from the supplied hourly file. Its 22-day realized-volatility
ranking, top-two selection, rank-four hysteresis, and 96-hour cadence remain in
the canonical strategy implementation. Orders execute at the next available
daily bar open with the configured slippage, spread, 25 bps taker fee, and zero
commission. There are no crypto corporate actions.

The isolated comparison uses the harness at
`archive/maintenance/crypto-replay-parity-v1-2026-09` (source commit
`00722a726b2afcaa86e8ffcb5acee3cdd481c938`) against the current checkout with
its frozen `1h` product-universe timeframe, then runs the shared engine with
only crypto enabled. It compares captured decisions, account snapshots, fills, terminal
events, daily bars, NAV timestamps/values, final state, and universe identity.
Single-market mode retains the canonical offline overlay call exactly; the
shared pending-order and same-cycle reservation inputs are enabled only when
both incumbent engines are attached. Isolated parity therefore tests the
ledger adapter without injecting cross-market behavior into the control.
Canonical JSON hashes are also recorded after normalizing integer and float
JSON encodings to the same exact numeric value; the numeric tolerance is zero.
Historical crypto provider receipts and historical tradability metadata are
not available; that remains a source-data limitation.

## Shared account fills

Each strategy retains its own market-specific history, PIT universe, cadence,
and execution costs. Fills from both markets update one cash amount and one
position inventory. The merged replay uses crypto-first deterministic ordering
at a shared timestamp and attempts all account sells before account buys. A
remaining working sell blocks BUY fills until a later event. It does not model
broker acknowledgements, exchange queue priority, market impact, or live
partial-fill timing.

## Scope of combined result

The combined run is a current-cap correctness smoke test. Its generated NAV
and cash paths are hashed for deterministic replay and accounting checks only;
the artifact omits combined CAGR, Sharpe, returns, and drawdown and makes no
diversification or performance claim.
