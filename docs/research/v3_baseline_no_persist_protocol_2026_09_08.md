# Unchanged V3 baseline no-persist protocol — 2026-09-08

Status: frozen runner plan, verification only. The baseline is not launched
until an independent reviewer checks this protocol and its input hashes.

The baseline identity remains `equity_residual_momentum_v3`, weekly,
long-only, with a maximum allocation of `0.55`. It uses the current
configuration's risk, execution, terminal, and universe rules exactly as
stored. No candidate is allowed to alter V3 parameters. PAPER/LIVE and
production state are outside this protocol.

## Exact inputs

| Input | SHA-256 |
|---|---|
| `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` |
| `data/historical/equity_entity_lifetimes_full_v5.csv` | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` |
| `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` |
| `config/settings.yaml` | `414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353` |

## Runner contract

The research runner must call the existing V3 roadmap/backtest seam with
`persist=False`, `state_path=None`, and a task-specific output directory
outside runtime/cache databases. It must use the frozen 2016-01-01 through
2026-07-31 evaluation window, completed-bar decisions, next-tradable-open
fills, existing split-adjusted bars, explicit corporate-action cash, and
current risk/cost settings. The runner must emit source hashes, configuration
digest, universe digest, curve timestamps/equity, fills, cash, exposure, and
finite-accounting checks. It must not write settings, registry, strategy
files, runtime databases, or PAPER/LIVE state.

This protocol is a readiness gate only. An independent review is required
before launch, and no V3 result is used in cross-asset economics until that
review passes.

