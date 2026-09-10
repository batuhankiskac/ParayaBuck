# H14/current-V3 comparability closure

Status: `H14_PORTFOLIO_CONTRIBUTION_TESTED_BUT_INFEASIBLE_REMAINS_DIAGNOSTIC`.
This is a research-only artifact. It makes no economic, allocation, PAPER,
LIVE, production, or promotion claim.

## Exact current-V3 parity

The unchanged current control path was executed from
`scripts/research_v3_current_parity_control.py` and
`scripts/research_v3_equity_roadmap.py` through
`TerminalAwareResearchEngine` policy `T1`, using the pinned symmetric v5 bars,
lifetimes, corporate actions, and configuration through the frozen endpoint.
The run loaded 11,686,832 bars, 141,908 corporate actions, and 2,656 PIT
snapshots and produced 2,658 dated NAV points. All five component commitments,
495 rebalance decisions, 1,890 fills, 12 terminal events, and the full metric
object matched `control_r2.json` exactly. The committed equity-curve
commitment is `9d08f9c3b3a6959189fb0a482c7de6d294fd4a72e1cec3ae7fe7f79d6f2e02fb`.

The persisted series is therefore a parity-proven materialization of the
existing aggregate receipt, rather than a proxy. Same-date terminal marks use
the native final-value rule; no interpolation or date substitution is used.
The V3 series ends at `2026-07-29T04:00:00+00:00`, as produced by the frozen
engine call. H14 rows without a same-date V3 point are dropped from the join.

## Frozen H14 joint evaluation

The evaluation uses all 18 frozen H14 cells: candidate, passive, and
calendar-control modes across `full_2022` and `recent_2024` at 1x/2x/3x cost.
It computes normalized same-date joint NAV, maximum drawdown, worst daily
return, V3/H14 daily-return correlation, fixed SPY regime buckets, and shared
capital feasibility. The fixed join is a 95% V3 sleeve plus a 5% H14 sleeve;
rebalance-time weights are forward-filled to sessions. The caps are equity
55%, crypto 5%, gross 60%, cash floor 40%, maximum crypto positions 2, and
total positions 6.

The candidate and calendar controls are infeasible whenever H14 is active while
V3 already has six positions: full-window rejected days are 185 and 166,
respectively, and recent-window rejected days are 110 and 92. Passive is
infeasible for 974 full-window and 569 recent-window days under the same
position cap. Gross exposure remains at 0.5725 at its maximum, so these are
position-cap failures rather than a hidden gross or cash relaxation.

The frozen H14 protocol declares full/recent cells and cost stress but no
walk-forward or holdout cells. No such cells or thresholds were invented. The
result is descriptive research evidence only, and H14 remains diagnostic with
portfolio contribution ineligible until a feasible shared ledger and a frozen
walk-forward/holdout contract exist.

## Owned artifacts and verification

| Artifact | SHA-256 |
|---|---|
| `data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json` file | `2c8b30eca960750a5bf31843d0577c087c835b6d822fa6a09cb6b61e161eab32` |
| artifact canonical payload (`artifact_sha256`) | `2e050b2884441a0f00815df0b51d19dcd19619af9360e316daa237edf48eb039` |
| focused collection receipt | `52993daee93f73d871b36097c6816a85e39b2ec994594012b8aef5d794b97fdc` |
| current V3 receipt `control_r2.json` | `63b254a79a160f080756461b445a7506129980fdd74b7d9830d235ca813f1cdc` |
| current V3 receipt internal digest | `81bfdd8361c9819de0830e38e6fd91595f081bbc2ef473c191719a8ea031bf0a` |
| H14 execution manifest r4 | `014c232e59bd3d6c19ffb00c39e5ad45fdb27d3c1e16c1804f4bd29c6c0bfd0a` |
| H14 native receipt | `dd2de0467c2c7ddea0f506d4856fdcee66d7274e9547ee44a5dca70f2a74510f` |

The persister is
`scripts/research/h14_v3_daily_nav_comparability_2026_09_09.py` and the
focused regression collection is
`tests/research/test_h14_v3_daily_nav_comparability_2026_09_09.py`.

Validation commands:

```text
.venv/bin/python scripts/research/h14_v3_daily_nav_comparability_2026_09_09.py
.venv/bin/python -m pytest -q tests/research/test_h14_v3_daily_nav_comparability_2026_09_09.py
```

The generation completed with exit 0 and 2,658 NAV rows/18 H14 cells; focused
tests completed `6 passed`, as recorded by the focused collection receipt.
Existing H14 native validation remains 11 passed.
