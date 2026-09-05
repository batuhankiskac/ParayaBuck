# C4 signed-risk novelty protocol — 2026-09-05

**Status:** pre-measurement protocol; immutable after source/test freeze.
No performance, PnL, fills, economics, PAPER or LIVE decision is authorized by
this file.

## Frozen candidate

`crypto_signed_risk_22d_hyst4` computes
`sum(min(r,0)^2) / sum(r^2)` over the last 22 completed single-day returns,
requiring 23 consecutive completed D1 prices. Invalid values and zero return
energy are ineligible. Scores sort ascending, then symbol. Only the ranking
score changes; incumbent HYST4 retention, top-2 equal weights, 96-hour cadence,
and 5% account-level crypto allocation remain unchanged.

## Windows and observations

Primary full window is `2021-04-01T00:00:00Z` through
`2026-07-31T23:59:59Z`. Recent is `2024-01-01T00:00:00Z` through
`2026-07-31T23:59:59Z`, a descriptive slice of the same full trace with no
independent reset or gate. The common configured raw pool and native
candidate-specific observer stream capped at 12 are reported separately; this
is not a venue-PIT completeness claim.

## Novelty comparisons and gate

Compare C4 against the native `crypto_low_variance_hysteresis_v1` std22 score
and the archived T3 downside score
`sqrt(sum(min(r,0)^2) / 22)`, using the same eligibility, causal PIT policy,
decision timestamp and snapshot key. Each comparison requires at least 30
valid paired full-window samples. Spearman uses average tied ranks on the
common finite-score symbol intersection; fewer than two symbols or a
zero-variance rank vector is undefined and excluded from the denominator. Raw
top-2 uses deterministic score plus symbol tie-break, then exact unordered
symbol membership on the same paired set.

If either comparison has median Spearman `>= 0.95` and raw top-2 identical
fraction `>= 0.90`, status is `NO_ECONOMIC_RUN_NEAR_DUPLICATE`. If either
comparison has fewer than 30 valid pairs, status is
`BLOCKED_INSUFFICIENT_PAIRED_SNAPSHOTS`. Only when both comparisons pass the
novelty test is status `NOVELTY_GATE_OPEN`.

MAX and 22-day cumulative momentum are descriptive diagnostics only; momentum
ranks descending with the high winner predeclared. Hysteresis overlap is also
descriptive. No parameter sweep, retuning, economic arm, or promotion follows
from this protocol.
