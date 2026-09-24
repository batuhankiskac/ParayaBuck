# Canonical V3 baseline

Status: `CORRECTED_V3_BASELINE_VALID`

This document is the permanent reference/control contract for future equity
research. It records the repaired V2 governance decision; it is not a PAPER or
LIVE promotion.

## Production identity

| Field | Frozen value |
|---|---|
| Production `main` SHA | `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53` |
| Strategy | `equity_residual_momentum_v3` |
| Signal timeframe | `1d` |
| Decision cadence | weekly |
| Mode | `paper` |
| LIVE | disabled; `live.enabled: false`, dollar cap `0` |
| Equity allocation | maximum `0.55` of account |
| Signal identity | positive 12-1 residual gate with raw 6/9/12 percentile-rank consensus |
| Sizing | inverse-volatility sizing, maximum single target weight `0.10` |
| IVOL filter | reject quantile `0.70` |
| Benchmark | SPY reference; excluded from ordinary alpha targets |

Frozen strategy parameters are `formation_bars=252`, `skip_bars=21`,
`beta_window_bars=273`, `residual_vol_window=63`, `top_n=8`,
`max_single_weight=0.10`, `ivol_reject_quantile=0.70`,
`exclude_symbols=[SPY]`, and weekly rebalance.

The frozen V3 research control used maximum gross exposure `0.60`, cash buffer
`0.40`, maximum positions `6`, and maximum per-position weight `0.10`. The
current shipped PAPER configuration uses `risk.max_positions: 8` after the
2026-09-24 CAP8 integration (`docs/research/cap8_main_integration_v1.md`); the
metrics below remain the six-position control.
Crypto remains a separate current PAPER sleeve at allocation `0.05`; it is
outside this equity baseline's alpha comparison.

## Target and realized exposure semantics

Governance conclusion: `TARGET_AND_REALIZED_GROSS_DISTINCT`.

The production risk contract constrains decisions and order-caused exposure;
it is not a continuous mark-to-market liquidation rule. At decision time
`t_d`, `target_gross(t_d)` is the risk-adjusted expected post-trade account
gross, including immutable external exposure exactly once. It must not exceed
`min(max_gross_exposure_pct, 1 - cash_buffer_pct)`, and `target_cash(t_d)` must
remain at least the corresponding cash buffer. Every BUY submission or
re-plan uses a fresh account snapshot and must not increase broker gross beyond
the available headroom.

`realized_gross(t) = sum_i(abs(q_i(t)) * P_i(t)) / account_equity(t)` and
`realized_cash(t) = account_cash(t) / account_equity(t)` are separate marked-
to-market quantities. Market moves between scheduled decisions, execution
prices and fees, lot rounding, and partial fills may move realized gross or
realized cash across the target boundary without a trade-caused policy
violation. The current protocol has no continuous gross-drift liquidation
rule. At the next eligible decision or BUY re-plan, current realized exposure
is an input and new buys have no headroom until the risk-adjusted target is
within bounds.

Future research protocols must name the constrained quantity and timing. A
bare statement such as `gross <= 60%` is insufficient. A protocol must state
whether it constrains target gross, realized gross, target cash, realized cash,
a continuous intraperiod limit, or a rebalance-time limit. If realized gross or
cash is a conjunctive gate, freeze before outcomes its valuation source,
observation cadence, tolerance, execution/fee treatment, permitted breach
duration, and remediation behavior.

This clarification is prospective only. It does not reinterpret any frozen
historical gate, including the abnormal-volume experiment's explicit G9
requirement on realized gross and realized cash; that gate remains failed.

## Dataset identity

The corrected baseline uses the following frozen historical inputs:

| Dataset | File | SHA-256 |
|---|---|---|
| Split-adjusted SIP equity bars | `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` |
| Entity lifetimes | `data/historical/equity_entity_lifetimes_full_v5.csv` | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` |
| Corporate actions | `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` |

The bars contain 11,686,832 rows and 9,010 lifetime identities. The common
reference/hedge endpoint is 2026-07-30. The research windows are 2016 warmup,
development 2017-01-03 through 2021-12-31, and validation 2022-01-03 through
2026-07-30. Prices are split-adjusted; cash dividends are applied once and
split/stock-dividend rows are not reapplied.

## Role contract

- `SPY` is reference-only: it remains available for history and observation,
  but is not an ordinary V3 alpha target.
- `SH`, `PSQ`, and `DOG` are approved inverse hedge-only roles. They may remain
  observable and available to explicitly authorized hedge paths, but they are
  excluded from ordinary V3 alpha targeting.
- Ordinary V3 target eligibility for `SPY`, `SH`, `PSQ`, and `DOG` is zero in
  the corrected baseline evidence.
- The broad historical Alpaca `US_EQUITY` universe remains in force. This is
  not a complete common-stock/product taxonomy; see
  [`equity_universe_limitations.md`](equity_universe_limitations.md).

Role isolation is a role boundary, not a claim that the historical dataset has
been converted to common-stock-only. A future authorized security-master
correction would require a separate universe replay and baseline revalidation.

## Drawdown semantics

`risk.max_drawdown_pct = 5%` is a PAPER/LIVE account high-water operational
entry latch. In runtime terms, a breach persists the account drawdown incident,
blocks new entries, and permits risk-reducing sells. It does not automatically
liquidate the account. Recovery requires reconciliation, a fresh authoritative
account snapshot, and an explicit operator risk-epoch reset that rebases the
high-water mark.

Offline backtests use different semantics: a drawdown breach is recorded as a
`drawdown_halt_shadow_event`, while the alpha NAV continues. The offline
maximum drawdown is reported as an economic diagnostic and is not directly
bounded by the 5% runtime latch. The ordinary terminal liquidation at the end
of a backtest is a separate lifecycle event, not a drawdown response.

This separation is why the earlier G5 result,
`CORRECTED_V3_BASELINE_FAILED`, is superseded: it treated an operational
account latch as an offline alpha maximum-drawdown threshold.

## Canonical metrics

The repaired V2 evaluation recomputed these metrics from the persisted replay:

| Cost | Period | Net return | CAGR | Sharpe | Sortino | Max DD | Calmar |
|---|---|---:|---:|---:|---:|---:|---:|
| 1x | Development | 60.9781% | 10.0015% | 0.8770 | 1.0681 | 15.5323% | 0.6439 |
| 1x | Validation | 22.7203% | 4.5736% | 0.4672 | 0.6410 | 18.9593% | 0.2412 |
| 1x | Full | 97.5529% | 7.3714% | 0.6880 | 0.8841 | 20.4359% | 0.3607 |
| 2x | Full | 87.8348% | 6.8071% | 0.6412 | 0.8240 | 21.3095% | 0.3194 |
| 3x | Full | 78.6259% | 6.2476% | 0.5945 | 0.7644 | 22.1282% | 0.2823 |

## Cost semantics

The baseline cost model uses adverse slippage `2 bps`, spread `2 bps`, zero
commission, and effective-dated equity regulatory fees. Cost stress scales
slippage, spread, and effective-dated regulatory fees to `1x`, `2x`, and `3x`;
it does not change the strategy, allocation, data, or execution ordering.
Orders are decided on completed bars and fill at the next tradable bar under the
current provider-open proxy. Rebalance-band and minimum-order semantics remain
the production values (`0.5%` and `$10`).

## Research-control status

`CORRECTED_V3_BASELINE_VALID` means:

- V2 validity/parity passed;
- development and validation 1x return/CAGR were positive;
- full 1x Sharpe, Sortino, and Calmar were positive;
- drawdown semantics/reporting passed without an invented absolute offline DD
  threshold;
- 1x/2x/3x cost controls passed;
- account/role controls passed, including zero ordinary targets for the
  reserved roles;
- independent verification confirmed the protocol hash, data hashes, role
  identity, temporal provenance, metrics, and shadow behavior;
- the anti-post-hoc answer was `NO`.

## Supersession and provenance

The original corrected-baseline evidence is preserved at the archive tag
`archive/research/v3-reserved-role-corrected-baseline-v1-2026-09`. Its
formal failure is superseded by the repaired-protocol evidence at
`archive/research/v3-baseline-protocol-repair-v1-2026-09`, whose final branch
tip was `96910de6a54a3d3154996cad865308c3fff127ed`.

The repaired protocol hash is
`b8a09e2f430c9ff68b707d79e17e4d7154b44e44ac51732716ba51d85eecd8b2`. The
persisted replay was reused only after code/config/data/role/execution identity
and the independent evidence were verified. This document copies conclusions
and compact metrics; it does not copy the replay output into `main`.
