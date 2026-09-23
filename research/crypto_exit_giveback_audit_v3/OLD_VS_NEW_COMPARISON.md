# Superseded V2 versus corrected causal V3

Both use the frozen V2 episode economics and the same pinned daily source and universe. V2 is historical provenance only. Values below are descriptive; no significance test or alternative exit was run.

| Diagnostic | Old superseded baseline | New causal baseline |
| --- | ---: | ---: |
| Decisions | 455 | 454 |
| Fills | 388 | 406 |
| Position episodes | 57 | 56 |
| Natural closures | 55 | 54 |
| Endpoint-open, terminal settled | 2 | 2 |
| Natural episodes with positive MFE | 50 | 51 |
| Natural final-profit episodes | 30 | 30 |
| Positive-MFE natural episodes closing negative | 20 | 21 |
| >=30% MFE natural cohort | 15 | 15 |
| Final-profit rate, >=30% natural cohort | 15/15 (100%) | 15/15 (100%) |
| Median retention, >=30% natural cohort | 87.1497% | 87.4143% |
| >=30% cohort retention P25 / P75 | 62.9961% / 95.3427% | 60.4100% / 94.9219% |
| Median retention, all positive-MFE natural episodes | 37.2671% | 28.8599% |
| Median natural holding days | 36 | 39.5 |
| Natural episode net P&L | $6,009.561372 | $5,957.352162 |
| Full-account net P&L after terminal costs | $5,481.518536 | $5,423.133436 |
| Fees / slippage / spread | $899.972438 / $71.998184 / $35.999092 | $919.654030 / $73.572710 / $36.786355 |

## Change channels

- **Execution timing.** The corrected order is created when a completed daily bar becomes available, and its first fill uses a strictly later observed hourly open. The first BTC/ETH fills moved from the old same-boundary daily opening proxy to 2021-04-02 01:00 UTC. This is the accepted upstream repair.
- **Fills.** There are 18 more fill events overall. Among 55 shared symbol-local ordinal IDs, seven have different fill counts; examples include DOT/USD:1 (30→48) and LTC/USD:12 (14→21). Changed fill prices, participation and cash/holding trajectories change economic P&L and MFE.
- **Episode boundaries.** There are 56 rather than 57 continuous-inventory paths. Nine shared ordinal IDs have different entry/exit calendar dates; some later ordinals are no longer one-to-one identities. Old ETH/USD:14 and LTC/USD:13 do not exist in V3, while BTC/USD:8 appears. The old BTC/USD:5 ended in 2025-06; the corrected BTC/USD:5 ended in 2024-08. Ordinal matches across divergent boundaries must not be interpreted as identical trades.
- **Hysteresis state.** The alpha algorithm, rank-four retention, and parameters are unchanged. Of 454 common labels, archived causal-repair parity found equal raw ranks at 451, equal alpha targets at 448, and equal risk-adjusted targets at 446. The first alpha-target difference was 2024-08-29 (old LTC/BTC versus corrected LTC/DOT), driven by execution-dependent held quantities; first rank-context difference was 2024-09-26. V3 natural final-source reasons are 37 rank-above-retention, eight qualified-history loss, five capacity displacement and four scanner-ineligible. These are observed state differences, not an exit-rule test.
- **Terminal liquidation.** The corrected source-common terminal is the actual 2026-07-31 00:00 UTC hourly open, with no later daily decision. It produces two censored settlements, BTC/USD and DOGE/USD, totaling -$534.218726 versus V2's -$528.042836. The changed terminal quote and earlier path alter total-account P&L; these censored episodes remain outside natural-exit rates.

These channels overlap: timing changes fills, fills change holdings and retention choices, and those choices change later episode boundaries. The table does not assign additive causal shares to them. The archived `SIGNAL_PARITY_AUDIT.json` and `OLD_VS_CORRECTED_BASELINE.md` at accepted commit `b135d73743ec2652f79b8c840df50bd3f67166e8` establish the upstream comparison.
