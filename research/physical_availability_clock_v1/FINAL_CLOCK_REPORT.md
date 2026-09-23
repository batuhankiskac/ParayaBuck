# Physical clock decision

`PHYSICAL_CLOCK_REPAIR_EXPOSED_CANONICAL_BASELINE_CORRECTNESS_ISSUE`

## Starting main SHA

`3f97c02826bc42eda8197dbb7b605d299915155a`; local main V2-closure commit `d87adda9ddd35efbceda04af1162a579f0e6e9b6` is the clean base of `research/physical-availability-clock-v1`.

## Replay V2 closure

The pushed annotated tag `archive/research/shared-account-replay-v2-clock-blocked-2026-09` (tag object `a62b5e47797a15ffb2a62c6dfe8656d3e18bed50`) remotely peels to exact V2 commit `fb66be75aa77d48e78d586808c05ac40d617ca0d`. V2 equity and crypto isolated parity, cap-six accounting (0 over-cap timestamps; maximum 6 actual positions and 6 effective claims), and two-run determinism passed. V2 remains blocked on physical availability. It was not merged as validated replay infrastructure; no cap-eight arm or migration economics ran.

## Raw bar timestamp semantics

Equity v5 daily labels are New York local session-date midnight; crypto Alpaca hourly labels are UTC hourly interval starts; crypto aggregate daily labels are UTC bucket starts. The full equity and crypto source scans and byte hashes appear in the audit files. Canonical OHLCV, symbol, timeframe, session identity, universe, corporate-action joins, and input bytes were not edited.

## Equity physical availability and execution

The XNYS session close is the lower-bound availability of completed equity daily OHLCV. Research code uses installed `exchange-calendars==4.13.2` (via `pandas-market-calendars==5.4.0`) for 2016–2026. DST, weekends, holidays, and early closes were checked. The V3 next-open contract maps to the next valid XNYS open after availability; missing actual price bars remain a fail-closed execution data issue. No arbitrary grace was added.

## Crypto hourly and daily availability; execution

An hourly component is complete one hour after its proven start label. A complete daily aggregate is available only at the maximum completion time of its 24 contiguous components. For 2021-04-01 BTC/USD and ETH/USD, all 24 source components exist and the final 23:00 source hour finishes at 2021-04-02 00:00 UTC.

The frozen repaired canonical crypto control decides with the full 2021-04-01 daily bars at label `2021-04-01T00:00:00+00:00` and fills nonzero BTC/USD and ETH/USD buys at the 2021-04-02 00:00 UTC daily opening prints (`58714.99`, `1968.4`). Those complete signals cannot be known before the same instant as the fills. An order created from them cannot obtain an opening print already occurring at that instant without a causal-ordering assumption unsupported by this historical input. A corrected execution event must be later than order creation, which changes fill timing or requires finer-grained executable prices. This is a material canonical baseline timing issue. No baseline was silently rewritten.

## Label time, availability, decision, execution, ties

`ReplayObservation` keeps `bar`, `label_time`, and `available_at` separate. A future scheduler must enforce consumed availability before decision and order creation before fill. At a shared timestamp, existing orders/actions precede newly completed-bar decisions; a new order cannot capture that timestamp's open. The full total ordering and combined scheduler were intentionally not implemented after the stop rule triggered.

## Corporate-action timing

The v5 CSV uses `ex_date` for cash dividends; `record_date` and `payable_date` may be present. Existing loading maps ex-date to UTC midnight and handles split-adjusted bars without adding explicit non-cash events. The future physical scheduler must apply ex-date actions before the ex-date open to previously held inventory. Corporate-action code was not changed.

## Isolated parity and combined cap-six validation

The calendar and component helper tests passed. Fresh full equity isolated parity was **not run** after the crypto canonical stop. Crypto isolated physical-clock parity is **failed structurally** by the two real first-decision fills above; the full corrected replay was not run because it would require choosing a new canonical execution rule. Combined physical cap-six invariants were **not run**. Their `over_cap_timestamp_count`, maximum actual positions, and maximum effective claims are **unknown**, distinct from the archived V2 label-clock values of 0, 6, and 6. No cap-eight run occurred.

## Cross-market reordering and determinism

No physical-clock combined run, event hash, reordering count, or P&L interpretation was produced after the stop. The archived V2 two-run determinism applies only to its label-time scheduler.

## Canonical baseline correctness, limitations, readiness

The affected control is the repaired `crypto_low_variance_hysteresis_v1` hourly-source canonical baseline, frozen control SHA-256 `251798653eb28bcfb3e5d5080ebc1c347b979d802d340d1875e46c693bea2b01`. Minimum exchange-close/source-component completion is provable; provider publication latency and actual crypto execution quotes are not. The identified fill timing is sufficient to stop; no immateriality claim is made.

`SHARED_ACCOUNT_REPLAY_CLOCK_NOT_READY`

Research-only lower-bound helper: `research/physical_availability_clock_v1/timing.py` on this branch. It is not validated shared replay infrastructure. The V2 archive tag is immutable provenance.

## Validation scope

The complete current-branch suite passed: 1,173 passed, 1 skipped. Focused
physical-clock, global-slot, and open-order-pagination tests passed (34).
Fifteen archived V2 shared-replay unit tests passed from a temporary extracted
copy; no archived combined historical replay was rerun. Ruff check and format,
configured mypy over `src` plus the new research helper, strict mypy on the
helper, JSON parsing, source byte hashes, and `git diff --check` passed.

## Production state and exact next action

`maximum_positions` remains 6. Equity V3, crypto strategy parameters and rebalance band, canonical control files, PAPER, and LIVE were unchanged. No broker API was called.

`ISOLATE_AND_REPAIR_AFFECTED_CANONICAL_BASELINE`

Choose and validate a causal crypto execution price/time rule in a separate canonical-baseline campaign; then restart fresh isolated controls and cap-six combined physical replay before considering capacity migration.

## Final branch/tag SHA

Clock branch final SHA: recorded in Git at completion. V2 archive tag object `a62b5e47797a15ffb2a62c6dfe8656d3e18bed50`, peeled target `fb66be75aa77d48e78d586808c05ac40d617ca0d`.
