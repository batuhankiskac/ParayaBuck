# Abnormal-volume readiness v1

## Design freeze result

PASS. The design branch is `research/abnormal-volume-design-v1`; frozen SHA is `4f454f5deb4c77151b574f87c29228c859cfc884`. It is clean, pushed, exactly one commit ahead of main, and contains only the 14 design artifacts. The scope-limited no-peek receipt was reverified; no auditable candidate economic output exists.

## Readiness branch

`research/abnormal-volume-readiness-v1`, created directly from the frozen design SHA.

## Starting production main

`86123374e41735d8d163c7fbed5bf5edfc43468d`, unchanged.

## Frozen candidate identity

`AV49_D1_NORMALRET_M1_LONG`: split-consistent dollar volume, 49 prior sessions plus one formation day, own-history volume rank 46–50, own-history formation-return rank 16–35, calendar-month cadence, next-month first-open entry, long-only equal weight, at most six names.

## Data identity

- bars: `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`;
- lifetimes: `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`;
- corporate actions: `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`;
- normalized daily timeframe aliases: `1d` and `1Day`;
- common endpoint: `2026-07-30`, 2,658 sessions and 11,681,230 rows.

## Dedicated month-end execution contract

The contract derives the final session close and next calendar month's first actual exchange open from the NYSE schedule. Bar-midnight markers are never treated as executable timestamps. The target is held to the following month-first-open absent risk-reducing lifecycle action.

## Proof of next-month first-open timing

Calendar evidence covers ordinary, weekend, holiday, year, shortened-session, and leap-February transitions. The required timestamps are fully specified in `MONTH_END_EXECUTION_CONTRACT.md`.

## Proof extra-day bug is absent

The proposed contract directly selects the first next-month schedule row and explicitly rejects the following row. The existing generic monthly engine path is not used. Candidate execution proof was not emitted because reconstruction is blocked before targets exist.

## Lifecycle handling

Strict v5 identity/lifecycle rules are available and specified, including unresolved `EOSEW`, pending invalidation without replacement, terminal holding exits, new listings, and FB→META continuity. The future runner must load lifetimes explicitly rather than use the ordinary CLI path.

## Split-consistent volume reconstruction

Provider split-adjusted price and volume preserve dollar volume under reciprocal scaling. Raw input must be prevalidated; zero-containing signal windows fail closed. The common endpoint is enforced.

## Middle-40% conditioning verification

The frozen condition is ranks 16–35 among the security's own inclusive 50-return time series, not a cross-sectional percentile. This component is unambiguous but was not applied to population data after the upstream score-definition block.

## Target reconstruction

No target was reconstructed. The frozen `DV(t)/Q0.90(prior49)` secondary ordering does not state the quantile estimator/interpolation convention. That choice can change the selected six names. Guessing would violate design immutability.

## Reserved-role verification

Canonical exclusions resolve to `SPY`, `SH`, `PSQ`, and `DOG`. The future runner must keep them observable but never target them. No candidate target set was emitted.

## Decision coverage

Zero boundaries and zero population decisions were reconstructed, deliberately fail-closed before candidate selection. No economic output was produced.

## Independent verification

Independent review verified all frozen and canonical hashes, branch ancestry, the absence of source/config changes or economic outputs, and the blocker itself. A synthetic construction proves that accepted quantile methods can reverse two otherwise eligible rank-50 candidates and change the six-name set. The independent verdict agrees with `G1_BLOCKED_DATA_OR_EXECUTION`. A selection-by-selection verifier cannot truthfully run before a uniquely defined runner exists.

## G1 result

`G1_BLOCKED_DATA_OR_EXECUTION`

Blocking reason: `FROZEN_Q90_CONVENTION_UNSPECIFIED` at G1.2. The design remains unchanged.

## Generic issues discovered

The generic monthly engine decides after the first new-month bar and fills on the following bar. A later candidate-neutral maintenance task should add/test an explicit prior-month-close to next-month-first-open primitive if production or other research needs it. Nothing is merged to main here.

## Tests

The focused lifecycle/loader/PIT suite passed 36 tests. Full pytest produced 1,120 passes and one skip in the sandbox; its six loopback-socket tests were blocked by sandbox permissions and all six passed on the required permitted rerun. Ruff check, Ruff format check (247 files), and mypy (132 source files) passed. No candidate economic or performance test was permitted.

## Production state

Production `main`, configuration, PAPER, and LIVE are unchanged.

## Files

The readiness directory contains frozen-input, capability, timing, lifecycle, volume, signal, coverage, test, independent-verification, decision, and final-report artifacts. It contains no economic result.

## Frozen design SHA

`4f454f5deb4c77151b574f87c29228c859cfc884`

## Final readiness branch SHA

Recorded in the final delivery after committing these artifacts. A commit cannot embed its own SHA without changing that SHA.
