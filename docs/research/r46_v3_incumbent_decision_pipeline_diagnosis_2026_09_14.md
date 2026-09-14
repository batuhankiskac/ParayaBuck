# R46 V3 incumbent decision-pipeline diagnosis — 2026-09-14

Status: `ROOT_CAUSE_STILL_UNPROVEN`

This note is an outcome-free reconstruction of the frozen scheduling and
pre-decision gates. It reads structural V5 timestamps/symbols and frozen
schedule/config metadata. It does not replay the engine, rank candidates, or
invoke a strategy decision method.

## Finding

The earlier `PRE_FIRST_NATIVE_INCUMBENT_TARGET_EVENT_BOUNDARY` hypothesis is
rejected. History support becomes available at
`2017-02-02T05:00:00Z` (`SPY` history length `274`), before the apparently
eligible native weekly boundary at `2017-02-06T05:00:00Z`. The failing R46
ENTRY is later, at `2017-02-10T05:00:00Z`; it is not an event that precedes the
first apparently supported cadence.

The exact reason the February 6 cadence did not materialize
`_last_incumbent_target` cannot be proven from the preserved artifacts. The
V3 runtime did not persist the exact point-in-time observation snapshot,
observation membership, or runtime universe hash. The frozen product builder
also obtains the streamed observation membership from the strategy ranker.
Replaying that ranker would be an alpha decision and is outside this
diagnosis. The M3 observation files were not substituted: they belong to a
different contract whose reference set includes `SPY`, `IWM`, and `QQQ`, while
the frozen R46 configuration pins `SPY`, `SH`, `PSQ`, and `DOG`.

## Gate vocabulary

These are distinct states:

1. History support means the context has at least the strategy's required
   history. Here the required value is `beta_window_bars + 1 = 274`.
2. A scheduled cadence means the frozen weekly policy observes an ISO
   year/week transition. The policy is evaluated against the immediately
   preceding processed timestamp.
3. A pre-decision gate pass additionally requires the product-universe guard,
   strategy support, and the streamed-observation completeness check.
4. A target can materialize only after those gates, when the engine would call
   the strategy decision method. This diagnostic records that control-flow
   point but never calls it.

The available data envelope is consistent with a complete February 6 set:
all V5 symbols present at `2017-02-03T05:00:00Z` are also present at
`2017-02-06T05:00:00Z`. That is not the missing exact runtime observation
record, however, and therefore does not prove the February 6 completeness
gate or identify another gate as the cause.

## February 6, 2017 exact path

The immediately preceding processed timestamp is
`2017-02-03T14:30:00Z`. Consequently:

- native weekly scheduling is `true`;
- the R46 extra-boundary result is `false`;
- combined scheduling is `true`;
- `SPY` history is `276`, above the required `274`;
- the product guard is `PASS` under the frozen construction contract;
- the exact runtime universe version and observation membership are absent;
- the observation completeness result is `UNPROVEN_EXACT_EXPECTED_SET`.

The requested final enum is recorded conservatively as
`SKIPPED_OTHER_PRE_DECIDE_GATE`, with reason
`ROOT_CAUSE_STILL_UNPROVEN_EXACT_R46_OBSERVATION_METADATA_MISSING`. This enum
is a required catch-all for an unresolved path; it is not an attribution to a
specific other gate. The diagnostic did not call the strategy decision
method, so no February 6 target was fabricated.

## February 10, 2017 exact path

The failing boundary is event `2017-02-09:912810RV2`, boundary type `ENTRY`,
at `2017-02-10T05:00:00Z`. The immediately preceding processed timestamp is
`2017-02-09T14:30:00Z`. Therefore:

- native weekly scheduling is `false` because the timestamp is still in ISO
  week 6;
- the R46 extra-boundary result is `true`, so combined scheduling is `true`;
- `SPY` history is `280`, so strategy support passes;
- the product guard passes under the frozen constructed-universe contract;
- R46 `_effective_observation_symbols()` returns `None` on event decision
  timestamps, bypassing the normal streamed-observation completeness set;
- control flow would reach the event branch;
- `_last_incumbent_target` is `None` immediately before that branch;
- the preserved terminal condition is
  `{"error": "BLOCKED_R46_INCUMBENT_BASE_MISSING", "status": "BLOCKED"}`.

The event is farther along the decision path than February 6 because it is
explicitly scheduled and has the R46 completeness override. It requires an
already materialized incumbent target rather than creating a new incumbent
decision at the event boundary. The diagnostic records the frozen failure
without invoking that method. Because the exception occurs inside the base
decision path before the R46 bookkeeping restoration, the base timestamp
write remains `2017-02-10T05:00:00Z` and restoration is not reached.

## Previous-timestamp consumption

The frozen base engine writes the current timestamp before product, support,
and completeness gates. The relevant sequence is:

| Timestamp | Previous before | Native weekly | Extra event | Previous after |
|---|---|---:|---:|---|
| `2017-02-03T14:30:00Z` | `2017-02-03T05:00:00Z` | no | no | `2017-02-03T14:30:00Z` |
| `2017-02-06T05:00:00Z` | `2017-02-03T14:30:00Z` | yes | no | `2017-02-06T05:00:00Z` |
| `2017-02-07T05:00:00Z` | `2017-02-06T14:30:00Z` | no | no | `2017-02-07T05:00:00Z` |
| `2017-02-08T05:00:00Z` | `2017-02-07T14:30:00Z` | no | no | `2017-02-08T05:00:00Z` |
| `2017-02-09T05:00:00Z` | `2017-02-08T14:30:00Z` | no | no | `2017-02-09T05:00:00Z` |
| `2017-02-10T05:00:00Z` | `2017-02-09T14:30:00Z` | no | yes | `2017-02-10T05:00:00Z` |

Thus the February 6 cycle consumes the native weekly transition even if a
later gate skips it. February 7, 8, and 9 are not new native weekly
transitions. February 10 is scheduled independently by the event boundary;
it does not become native weekly merely because February 6 was skipped.

## Root-cause classification and account proof

Classification: `ROOT_CAUSE_STILL_UNPROVEN`.

Sub-classification:
`EXACT_R46_RUNTIME_OBSERVATION_AND_UNIVERSE_SNAPSHOT_NOT_PERSISTED`.

The preserved failure proves the incumbent cache was absent immediately
before event handling. It does not identify whether February 6 failed at
observation completeness, another pre-decision gate, or an unrecorded runtime
divergence. The first actual materializable native target is therefore
`NOT_PROVABLE_FROM_PERSISTED_METADATA`; the history-supported and apparent
cadence timestamps are not used as proxies.

The account conclusion is `ACCOUNT_STATE_NOT_PROVEN`. No claim is made about
a cash-only state or about prior order creation from this gate-only evidence.

## Boundary and next review

The diagnostic leaves R46 V3/V4 execution, strategy decisions on real
history, state transitions, parameters, PAPER, LIVE, and registry unchanged.

Repair design deferred to independent orchestrator review.
