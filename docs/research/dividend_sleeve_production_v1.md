# Dividend-growth sleeve: production integration (2026-09-25)

Decision: **shipped.** The PAPER equity identity is now
`equity_residual_momentum_v3_dividend_sleeve_v1`: the unchanged V3 core capped
at 0.40 plus a static dividend-growth ETF sleeve of 0.15 (SCHD, VIG and DGRO at
0.05 each). The market allocation (0.55), crypto (0.05) and every risk limit are
unchanged. LIVE stays disabled. The candidate was selected by
`docs/research/sleeve_candidates_v2_v3_closure.md` (`dividend_growth_v1`). This
note covers the production code path and its shared-ledger validation.
Evidence (runner, evaluator, runs, `RESULTS.json`) is preserved at tag
`archive/research/dividend-sleeve-production-v1-2026-09`.

## What changed

- **Strategy.** `src/parayabuck/portfolio/dividend_sleeve.py` is one parent
  identity per market, because the runtime runs one strategy per market and
  ownership is per symbol, not per strategy. V3 decides on a context with the
  sleeve symbols hidden, so its scoring pool is unchanged. The sleeve intent
  travels in `metadata["sleeve"]`. It is decided on the first weekly decision
  of each calendar month (or whenever the sleeve is empty) and held otherwise.
  The month rule is stateless: it reads the previous ISO week's first session
  from the benchmark history, so restarts cannot skip or repeat a month.
- **Risk overlay.** `PortfolioRiskOverlay.apply` splits a sleeve target. The
  core goes through the canonical overlay at `core_allocation`, then the sleeve
  is appended inside the remaining gross, cash-buffer, equity and market
  allocation room, including a LIVE dollar cap. Any halt, entry latch or
  intermediate risk reduction caps each sleeve weight at its current weight.
  Targets without sleeve metadata take the unchanged path.
- **Slots.** `risk.slot_exempt_equity_symbols` makes the sleeve ETFs additive.
  They never count toward the 8-position account cap, in the overlay, in slot
  admission (`reserve_opening_order_slots`) or on the dashboard.
- **Universe.** `universe.equity.sleeve_trade_symbols` pins the ETFs into the
  30-symbol IEX stream, like the inverse hedges, and reserves them from
  ordinary alpha. V3 keeps 23 ranked stream slots (was 26).
- **Consistency.** The strategy's `sleeve_symbols`, the universe pins and the
  slot exemption must list the same symbols, and core + sleeve must fit the
  market allocation; configuration loading fails otherwise.

## Validation (shared-ledger physical-clock replay, 2017-01-03 → 2026-07-30)

Arm P runs the shipped configuration through the production strategy, overlay
and 30-symbol stream. The only harness change passes the configured slot
exemption into its slot admission, as every production caller does. Arm A
(shipped V3 0.55 configuration) was re-run under the new code.

| Arm | CAGR | Sharpe | Max DD | 2022 | Ending NAV |
|---|---:|---:|---:|---:|---:|
| A shipped, 1x (archived) | 6.69% | 0.675 | 19.4% | −15.3% | 185.8k |
| **P production, 1x** | **7.22%** | **0.780** | **17.2%** | **−12.5%** | **194.9k** |
| P production, 2x costs | 6.72% | 0.730 | 18.0% | −13.3% | 186.4k |
| P production, 3x costs | 6.25% | 0.682 | 18.7% | −14.0% | 178.6k |
| Research arm C (33-symbol stream), 1x | 7.00% | 0.767 | 18.2% | −13.5% | 191.1k |

A at 2x/3x costs: 6.04% / 5.34% CAGR.

All gates pass:

- The new code reproduces arm A's decision and fill hashes and its ending NAV
  exactly, so behavior without a sleeve is unchanged.
- Accounting invariants hold.
- Every observation stream has at most 30 symbols.
- Non-sleeve slot claims never exceed 8.
- Against A, P has higher CAGR and Sharpe, a lower max drawdown and a smaller
  2022 loss. P's CAGR is above A's at every cost multiplier.

Sleeve P&L at 1x: SCHD +12.2k, VIG +9.0k, DGRO +9.3k per 100k start.

## Caveats

- **Weaker in the validation window.** The gain is concentrated in the
  development window: CAGR 10.05% vs 8.77% for 2017–2021, but 4.23% vs 4.47%
  for 2022–2026 (Sharpe still 0.488 vs 0.475). 2024 and 2025 trail A
  (8.5% / 7.7% vs 11.6% / 12.9%) because the sleeve takes allocation from V3 in
  strong momentum years.
- **P vs research C.** P beats C by about $3.9k. That gap includes the 23-slot
  stream and shared-ledger path dependence (about ±$1–4k), so it is not evidence
  of extra alpha. Holm p ≈ 0.15 from the campaign still applies.
- **Canonical v5 data lacks SCHD.** A single-market product backtest on
  canonical data drops the unpinnable SCHD and trades VIG/DGRO only. The
  validation above adds SCHD from the campaign file.
- **Mid-month start.** A PAPER start mid-month buys the sleeve on the first
  decision, because an empty sleeve is decided.
- **Universe refresh.** The new identity invalidates the persisted equity
  universe artifact (`observation_strategy_mismatch`). Startup
  (`ensure_active_universes`) regenerates it.

## Rollback

Set `strategy.equity.name: equity_residual_momentum_v3`, then remove:

- the `core_allocation`, `sleeve_allocation` and `sleeve_symbols` parameters
- `universe.equity.sleeve_trade_symbols`
- `risk.slot_exempt_equity_symbols`
- SCHD/VIG/DGRO from `fallback_trade_symbols`

`equity_residual_momentum_v3` remains a registered product identity.
