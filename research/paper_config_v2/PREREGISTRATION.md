# paper_config_v2: preregistration

Written 2026-09-29, before any Stage B run of this campaign.

## Question

Two operator decisions for the PAPER configuration:

1. Remove the crypto sleeve (config-level disable, phase 1) and hand its 0.05
   to the equity strategy: `strategy.equity.maximum_allocation` and
   `risk.max_position_pct` 0.93 -> 0.98. Evidence for removal:
   `docs/research/crypto_sleeve_decision_v1.md`.
2. Sweep the equity strategy's idle budget into BIL (SPDR 1-3 month T-bill
   ETF): the part of the equity allocation not held in QLD (the whole budget
   in a downtrend, `1 - weight` when volatility targeting shrinks the QLD
   position) is held in BIL, under the same rebalance band. The 0.02 account
   cash buffer stays cash. Evidence: `docs/research/stress_robustness_v1.md`,
   `docs/research/rule_variants_v1.md`.

## Arms

All arms use the production engine shared-ledger replay
(`research/paper_config_v2/run_stage_b.py`, derived from
`research/execution_model_audit_v1/run_stage_b_audit.py`), 2017-01-03 ->
2026-07-30, $100,000 start, at 1x and 2x costs. Configs are in `configs/`.

| Arm | Config |
|-----|--------|
| S0 | current main `config/settings.yaml` (equity 0.93 + crypto 0.05) |
| S1 | crypto strategy and universe disabled; equity 0.98; `max_position_pct` 0.98 |
| S2 | S1 + `strategy.equity.parameters.cash_sweep_symbol: BIL`, BIL added to `universe.equity.symbols` |

S0 at 1x must reproduce the audited result (ending NAV 1,141,851; 28.98%
CAGR) before any other arm is interpreted. If it does not, the campaign stops.

BIL data: `research/rule_variants_v1/data/` (Alpaca SIP daily bars, split
adjusted for the 2017-11-30 1:2 reverse split; dividends restated to post-split
shares). QQQ/QLD/SPY data and the crypto inputs are unchanged from the audit.

## Metrics

From the session-close NAV path (same definitions as
`research/sleeve_candidates_v2/evaluate.py`): full-period CAGR, Sharpe
(daily, annualized, zero risk-free rate), max drawdown; CAGR for 2017-01-03 ->
2021-12-31 and 2022-01-03 -> 2026-07-30; ending NAV; turnover (traded notional
/ mean session NAV, per year). Accounting invariants must hold in every run.

## Gates (fixed before running)

- **Adopt S1** if, at 1x costs, CAGR(S1) >= CAGR(S0) - 0.5 pp and
  MaxDD(S1) <= MaxDD(S0) + 2 pp. Simplicity justifies a small return give-up.
  If S1 fails, the crypto removal is reported as not validated and the config
  is left unchanged pending an operator decision.
- **Adopt S2 (BIL sweep on)** only if S1 is adopted and CAGR(S2) > CAGR(S1)
  on the full period at both 1x and 2x costs, and MaxDD(S2) <= MaxDD(S1) +
  0.5 pp (checked at 1x and 2x). Otherwise the sweep ships with the flag off.

No further arms, parameters or windows are added after results are seen.
