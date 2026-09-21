# Current capability audit

Status date: 2026-09-21. Repository snapshot: `86123374e41735d8d163c7fbed5bf5edfc43468d` on `research/abnormal-volume-design-v1`; local `main` and `origin/main` resolved to the same commit before this branch was created.

## What exists

- The canonical V3 equity baseline is the enabled PAPER equity strategy and uses positive 12-1 residual momentum, 6/9/12-month consensus, a 0.70 IVOL rejection rule, inverse-volatility sizing, weekly decisions, `top_n=8`, and a 0.10 name cap. LIVE is disabled.
- Account constraints are gross exposure at most 0.60, cash at least 0.40, equity allocation at most 0.55, and at most six positions account-wide. The frozen candidate below inherits the equity cap, name cap, global slot limit, risk model, next-open convention, and 1x/2x/3x costs.
- The canonical market artifact has 11,686,832 daily rows, 9,010 security identities, and dates from 2016-01-04 through 2026-07-31. The canonical benchmark endpoint is 2026-07-30. Development is 2017-01-03 through 2021-12-31 and validation is 2022-01-03 through 2026-07-30.
- The equity scanner supplies a point-in-time tradability/liquidity-screened `US_EQUITY` set. It is not a historically reconstructed common-stock-only taxonomy. `SPY` is reference-only; `SH`, `PSQ`, and `DOG` are hedge-only and cannot be candidate holdings.
- Alpaca daily stock acquisition requests SIP data with split adjustment. The bar model carries volume, and the cache keys adjustment basis explicitly.
- Strict terminal-lifecycle handling exists in the backtest engine, but the ordinary CLI does not load/pass the v5 lifetime artifact. The future experiment therefore requires a dedicated research harness.
- The existing generic `monthly` policy decides on the completed first bar of a new month and fills one bar later. It is not the frozen prior-month-close to next-month-first-open convention.

## Starting-state control checks

| Control | Finding |
|---|---|
| Repository | `/Users/batuhankiskac/Desktop/ParayaBuck` |
| Starting branch/status | `main`, clean before branch creation |
| Local main / origin main | both `86123374e41735d8d163c7fbed5bf5edfc43468d` |
| Research branch | `research/abnormal-volume-design-v1` |
| PAPER equity | `equity_residual_momentum_v3` |
| LIVE | disabled |
| Corrected loaders | active for canonical v5; raw volume blank-to-zero behavior requires experiment prevalidation |
| Strict lifecycle | implemented in engine; not wired through ordinary CLI, so dedicated harness required |
| Corporate actions | split-adjusted bars; cash dividends loaded/deduplicated; split and stock-dividend cash replay skipped |
| All-in costs | slippage, spread, crypto fee, and effective-dated equity regulatory fees share the 1x/2x/3x multiplier |
| Reserved roles | `SPY`, `SH`, `PSQ`, `DOG` isolated from ordinary alpha selection |

## What does not exist

- There is no abnormal-volume strategy, its month-end scheduler, its exact rank/secondary-order handling, or its calendar-month hold logic.
- The generic production portfolio cadence is not proof of the candidate's calendar-month formation semantics.
- There is no point-in-time shares-outstanding series suitable for a Lee-Swaminathan turnover signal.
- There is no point-in-time security-product taxonomy capable of restricting all historical rows to operating-company common shares.
- There is no evidence yet about this candidate's returns, hit rate, buckets, IC, turnover economics, or complementarity. Those outcomes were deliberately not computed.

## Reuse decision

Reuse the canonical data artifact, scanner snapshots, lifecycle filters, role exclusions, capital/risk model, execution convention, and cost model. Build a research-only signal and scheduler later. Do not change strategy configuration, PAPER, LIVE, or production code in this design stage.

The future harness must explicitly load v5 lifetimes, reject unresolved identity `EOSEW`, prevalidate raw CSV volume before the loader can normalize blanks to zero, cap all arms at 2026-07-30, and implement the frozen month-end schedule. Until these G1 items exist, an economic run is prohibited.
