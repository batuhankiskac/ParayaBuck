# PAPER config v2: crypto sleeve removed, BIL sweep not adopted

**Decision (2026-09-29).** Adopt **S1**. The crypto sleeve is disabled at the
config level. The equity allocation and `risk.max_position_pct` go from 0.93
to 0.98. The BIL idle-cash sweep (S2) is implemented behind a flag but ships
**off**: it failed its preregistered gate at 2x costs.

Stage B, 2017-01-03 to 2026-07-30, $100k start. Full CAGR at 1x costs:

- S0 (current main): 28.98%
- S1: 29.35%
- S2: 29.49%

Max drawdown moves the same way: 29.2% → 27.7% → 27.4%. At 2x costs S2
(29.11%) trails S1 (29.16%), so the sweep fails.

Preregistration: `research/paper_config_v2/PREREGISTRATION.md`, committed
before any run. Results: `research/paper_config_v2/RESULTS.json`.

## Results

These runs use the production-engine shared-ledger replay
(`research/paper_config_v2/run_stage_b.py`, derived from
`research/execution_model_audit_v1/run_stage_b_audit.py`). The accounting
invariants hold in all six runs. S0 at 1x reproduces the audited baseline
exactly, with an ending NAV of 1,141,851.

| Arm | Costs | CAGR | Sharpe | Max DD | CAGR 2017-21 | CAGR 2022-26 | Ending NAV | Turnover/yr | Fills |
|-----|------:|-----:|-------:|-------:|-------------:|-------------:|-----------:|------------:|------:|
| S0 main (equity 0.93 + crypto 0.05) | 1x | 28.98% | 1.068 | 29.16% | 36.42% | 21.36% | 1,141,851 | 4.13x | 1290 |
| S0 | 2x | 28.70% | 1.060 | 29.53% | 36.20% | 21.03% | 1,118,324 | 4.13x | 1278 |
| **S1 crypto off, equity 0.98** | 1x | **29.35%** | 1.054 | **27.70%** | 37.24% | 21.31% | 1,173,538 | 4.24x | 615 |
| S1 | 2x | 29.16% | 1.049 | 27.84% | 37.03% | 21.13% | 1,156,890 | 4.24x | 616 |
| S2 S1 + BIL sweep | 1x | 29.49% | 1.065 | 27.39% | 36.90% | 21.91% | 1,185,461 | 8.71x | 1308 |
| S2 | 2x | 29.11% | 1.054 | 27.48% | 36.51% | 21.54% | 1,152,635 | 8.71x | 1309 |

How the columns are defined:

- **Turnover:** traded notional ÷ mean session NAV ÷ 9.57 years. For S0 it
  includes the crypto trades.
- **Sharpe:** daily returns, zero risk-free rate.
- **Periods:** as defined in `research/sleeve_candidates_v2/evaluate.py`.

## Gates

| Gate | Result |
|------|--------|
| S1 CAGR ≥ S0 − 0.5 pp (1x) | PASS: 29.35% vs 28.48% threshold |
| S1 MaxDD ≤ S0 + 2 pp (1x) | PASS: 27.70% vs 31.16% threshold |
| S2 CAGR > S1 (1x) | PASS: 29.49% vs 29.35% |
| S2 CAGR > S1 (2x) | **FAIL**: 29.11% vs 29.16% |
| S2 MaxDD ≤ S1 + 0.5 pp (1x / 2x) | PASS / PASS |

**S1 is adopted and S2 is not.**

### Why S1 wins

Removing crypto and handing its 0.05 to the equity strategy adds 0.37 pp of
CAGR. It also lowers max drawdown by 1.5 pp.

The 2017-21 period gains 0.8 pp. The 2022-26 period is flat (−0.05 pp).

Sharpe dips slightly, from 1.068 to 1.054, because the account holds more of
the 2x ETF. Costs fall because crypto paid the 25 bp taker fee: fees drop from
$4,848 to $194.

### Why BIL fails

The sweep earns the T-bill yield on idle equity budget. It adds about 0.4-0.6
pp per year in 2022-26, when yields were 4-5%.

The cost is that BIL traded notional equals QLD traded notional:

- S2 at 1x trades $19.0M of BIL and $19.1M of QLD.
- Every daily volatility-target resize of QLD needs an offsetting BIL trade.
- That doubles turnover and execution costs (slippage plus spread: $5.5k in S1
  vs $11.4k in S2 at 1x).
- In 2017-21, near-zero yields do not cover the extra costs (−0.34 pp vs S1).
- At 2x costs the full-period edge turns negative (−0.05 pp).

This matches the `rule_variants_v1` warning that the sweep depends on yields.

The code stays because a variant that trades BIL less often could pass. One
example is sweeping only when the trend is down. That would be a new
preregistered test, not a change to this one.

## What changed

### Config (`config/settings.yaml`)

- `universe.crypto.enabled: false`
- `strategy.crypto.enabled: false`. The block and name are kept so the loader
  and the crypto code still resolve.
- `strategy.equity.maximum_allocation: 0.93 → 0.98`
- `risk.max_position_pct: 0.93 → 0.98`

These stay unchanged:

- `risk.max_crypto_*`, which are in the configuration digest
- `engine.crypto_*`
- `backtest.crypto_taker_fee_bps`

The capital invariant holds: 0.98 ≤ `max_gross` 0.98 ≤ 1 − 0.02.

### Equity strategy (`portfolio/equity_leveraged_trend.py`)

The new optional parameter is `cash_sweep_symbol`, off by default. When set,
the budget not targeted to QLD goes to the sweep ETF:

- all of it in a downtrend;
- `1 − weight` when volatility targeting shrinks QLD.

Orders use the same rebalance band. The 0.02 account cash buffer stays cash.
If the sweep ETF has no history or is outside the universe, that part falls
back to cash.

When the parameter is unset or empty, it is left out of `parameter_tuple()` and
out of decision metadata. Configuration digests and decisions without the sweep
are unchanged. The S0 decision and fill hashes and the NAV path match the
execution-model audit run exactly. When the parameter is set, it enters the
digest.

`config.py` rejects a sweep symbol that is not listed in
`universe.equity.symbols`. BIL is therefore only in the universe when the
sweep is on.

### Runtime (`runtime.py`, one helper)

On startup, after managed symbols are pinned, `_warn_disabled_market_inventory`
checks each market whose strategy is disabled. If bot-owned inventory is pinned
there, it:

- logs a warning;
- emits a `risk_incident` alert naming the symbols.

Such positions stay pinned and observed as before. No strategy exits them, and
startup is not blocked. The operator must liquidate them or re-enable the
strategy.

When there is no crypto inventory, a disabled crypto market starts cleanly:

- equity-only strategy set;
- no crypto streams;
- no crypto product universe;
- no crypto entry halt;
- no alert.

Tests: `tests/test_crypto_disabled_startup.py`.

### Soak auditor (`scripts/audit_paper_soak.py`)

- `EXPECTED` is equity-only at 0.98.
- The config gate requires `strategy.crypto.enabled: false`.
- A crypto decision now fails gate S1.

## Deploy notes (operator, GCP VM)

- **Configuration digest changes.** The equity `maximum_allocation` and the
  risk limits (`max_position_pct`) are in `configuration_digest`. The first
  start opens a new runtime session with a new digest.
- **Start a new soak window.** `audit_paper_soak.py` flags a digest change
  inside one observation window. Start the new window at the restart time with
  `--start <restart UTC>`.
- **No state migration is needed.** Nothing is deleted.
  `data/runtime/active_crypto_universe.json` is no longer refreshed or read,
  because the preflight skips a disabled crypto universe.
- **Check for crypto before restarting.** The live PAPER account is reported
  to hold none. If any bot-owned crypto remains, startup logs and alerts a
  `risk_incident` and leaves the positions alone. Liquidate them manually.
- **The first equity decision buys a little more QLD.** In an uptrend, it
  targets 0.98 × weight instead of 0.93 × weight: about 5% of equity at full
  weight. This is an ordinary rebalance through the band.
- **Keep BIL off.** Do not add BIL to `universe.equity.symbols`, and leave
  `cash_sweep_symbol` unset.

## Reproduction

Set up a scratch root as described in the `run_stage_b.py` docstring. Also copy
the two `research/rule_variants_v1/data/*.csv` files to
`<root>/data/historical/`. Then run the following for each arm and cost:

```
python <root>/research/profit_max_v1/run_stage_b.py \
  --config research/paper_config_v2/configs/S{0,1,2}.yaml --cost {1.0,2.0} \
  --out runs/S{n}_{cost}.json.gz
python research/paper_config_v2/evaluate.py --runs runs
```

Each run takes about 100 s and 6 GB of RAM. Run at most two in parallel.
