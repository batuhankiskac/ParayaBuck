# Current production baseline

This audit is anchored to the local `main` commit
`8123694a71d8fbb0e520f13b4e1692e932754e97` recorded on 2026-09-15 before the
research branch was created. The main worktree already contained unrelated user
edits in `.dockerignore`, `.gitattributes`, `.gitignore`,
`benchmarks/bench_execution_boundary.py`, `docs/live_readiness_gap_analysis.md`,
and `tests/test_web_route_contract.py`; those edits are not part of this
campaign.

## Production identity and equity configuration

The current configured PAPER equity strategy is
`equity_residual_momentum_v3`, implemented by
`EquityResidualMomentum1dV3`:

| Item | Current value | Source of truth |
| --- | --- | --- |
| Signal timeframe | `1d` | strategy class and `config/settings.yaml` |
| Rebalance cadence | weekly; first bar of each ISO week | `rebalance_policy_for`, `RebalancePolicy` |
| Allocation ceiling | `0.55` | `strategy.equity.maximum_allocation` |
| Formation / skip | `252` / `21` residual bars (12–1 month) | strategy parameters |
| Beta window | `273` aligned return observations, requiring 274 completed closes | strategy parameters and `minimum_history_bars` |
| Benchmark | `SPY` | equity strategy / product runner |
| Residualization | timestamp-inner-joined simple stock and SPY returns; rolling OLS stock-on-SPY beta; residual = stock return − beta × SPY return | `equity_residual_momentum.py` |
| Ivol filter | reject the upper `0.30` fraction using nearest-rank threshold at `q=0.70`; ties are retained | shared residual-momentum helper |
| Cross-sectional score | positive 12–1 residual gate, then equal mean of raw 6–1, 9–1, and 12–1 percentile ranks, each with a 21-bar skip | `equity_residual_momentum_v3.py` |
| Selection / weighting | `top_n=8` strategy selection; inverse annualized residual-volatility weights | strategy parameters / momentum helper |
| Per-position cap | `0.10` | strategy parameter and risk overlay |
| Account position cap | `6` total positions | `risk.max_positions` |

The production crypto identity is recorded only for context:
`crypto_low_variance_hysteresis_v1`, 1d signal, 96-hour rebalance, maximum
allocation `0.05`. It is not replayed in this equity-only campaign.

## Shared risk and execution

The configured account limits are gross exposure `0.60`, minimum cash `0.40`,
maximum equity position `0.10`, daily-loss limit `0.005`, and maximum drawdown
`0.05`. Production PAPER/LIVE uses a loopback application, `5m` execution and
market-state timeframes, and LIVE remains disabled (`live.enabled=false`,
hard-dollar cap `0`). The backtest maps the equity sleeve ceiling to the
configured `0.55` maximum allocation while retaining the account risk overlay.

Decisions are made after completed bars. The event engine creates orders from
the completed-bar target and fills them only on the next tradable bar's open.
Sells are processed before buys at a timestamp. The configured 1x equity cost
model is 2 bps adverse slippage plus 1 bp half-spread per fill, zero commission,
and effective-dated SEC/TAF/CAT regulatory fees. The research cost stress
multiplies slippage and spread; the same effective-dated regulatory fee logic
is retained. Participation is capped at 10% of the average of the prior 20
known volumes, so the fill bar's volume is not used to size its own fill.

The price representation is split-adjusted for both signal and execution,
with explicit cash dividends credited from the matching historical corporate-
action source. Split actions are not applied again. The research loader
canonicalizes provider spellings to `1d`/`equity`, drops only explicit zero
placeholder bars, rejects invalid positive OHLC rows, and preserves the
timestamp-inner-join/fail-closed missing-data semantics.

The point-in-time universe is rebuilt from the same broad historical bar panel
for every run using the production trailing scanner: daily snapshots, 60-day
metrics, a 300-symbol Stage-A dollar-volume shortlist, configured price,
liquidity, completeness, ATR, sector and inverse/reference rules, and the
current V3 observation ranker capped at 30 streamed symbols. Candidate and V3
use this single shared snapshot object so neither strategy changes its
opportunity universe.

## Data and survivorship note

The available compatible local market panel is
`data/historical/equity_pit_symmetric_2016_2026_v5.csv`, accompanied by the
v5 lifetime table and v5 corporate-action manifest. It includes broad active
and inactive provider-identity histories, but the provider's historical asset
coverage and zero placeholders are not the same as a complete exchange-wide
delisting record. The campaign therefore reports its PIT tier and remaining
provider/delisting limitations explicitly; no positive result can be promoted
to production on this evidence alone.
