# Optimization Experiments — 2026-08-21

Backtest evidence for the candidate optimizations proposed after the strategy
review. **No project code was changed**: every variant runs the production
harness (`run_product_backtest`) with a strategy-parameter override or an
in-memory `dataclasses.replace` copy of the loaded config. The driver is
committed alongside the artifacts
(`data/research/optimization_2026_08/run_experiments.py`); raw per-run JSON
(curves, weight history, metrics) lives in the same directory.

This document is EVIDENCE ONLY. Per `docs/validated_strategies.md`, adopting
any parameter change (e.g. `top_n: 3`) is a NEW unvalidated strategy and needs
its own promotion decision; allocation and risk-limit changes are operator
decisions on `config/settings.yaml`.

## Method and validation

- Datasets: `data/historical/equity_1200_2016_2026.csv` (1,738,468 bars) and
  `crypto_18_1h_extended.csv` (537,936 1h bars aggregated to 22,156 1d bars,
  `require_complete=True`). Windows match the canonical research: equity
  2017-01-03..2026-07-30, crypto 2021-04-01..2026-07-31. PIT universes built
  once per market (equity shortlist 300, avg pool 300.0; crypto from 1h
  scanner bars, 2,037 snapshots, avg pool 11.0) and reused across variants.
- Harness parity check: the control runs reproduce the published canonical
  numbers EXACTLY — equity weekly v2 +84.61% / CAGR 6.61% / Sharpe 0.62 /
  maxDD 19.68% (`docs/capital_utilization_cadence_verdict.md` §3) and crypto
  96h v2 +6.56% / 1.20% / 0.23 / 13.25% (§4). All variant deltas below are
  therefore like-for-like.
- Walk-forward: anchored calendar 6-month folds via the shared research
  helper (`research_helpers._calendar_fold_window`): equity 16 folds from
  2019-01-01, crypto 10 folds from 2022-01-01.

## Results summary

| # | Experiment | Full-window effect | Walk-forward | Verdict |
|---|---|---|---|---|
| B1 | Cash yield on idle cash (3%/4.5%) | combined CAGR **+2.99pp / +4.38pp**, maxDD improves | n/a (overlay) | **A — biggest risk-free lever; needs a cash-parking feature** |
| B3 | Equity sleeve 0.50 → 0.60 | CAGR 6.61→8.09%, Sharpe 0.62→0.65, maxDD 19.68→23.83% | 12/16 positive both; median fold 3.25→4.16%; 060 wins 11/16 folds | **B — consistent mechanical improvement; more drawdown by design** |
| A1 | Crypto sleeve off / halved | common-window CAGR 4.51→3.83% (off), maxDD 20.30→13.95% (off) | n/a | **B — crypto deepens 2022 co-drawdown; Sharpe ~neutral** |
| B5 | Crypto top_n 2 → 3 | ret +6.56→+18.32%, Sharpe 0.23→0.56, maxDD 13.25→10.58% | median fold WORSE (0.90 vs 1.35%), delta concentrated in 2023H1 (+7.76pp of it) | **C — fails WF concentration standard; mission required** |
| B6 | Equity shortlist 300 → 500 | +84.61→+86.70%, same maxDD | not run | **C — marginal; pool avg 480 vs 300** |
| B2 | Drawdown-halt calibration | threshold sweep (below) | n/a | decision input for a risk-policy mission |

---

## B1 — Cash yield on idle cash (largest risk-free lever)

The engine records `cash_rate_annualized` as a hurdle metric only; it never
credits interest to the equity curve (`backtest/portfolio_engine.py`
`_cash_hurdle_return`). The effect was therefore measured as an overlay on
the simulated path: between consecutive curve points the uninvested fraction
(average 52.7% equity / 90.5% crypto) earns a constant annual rate while the
invested fraction follows the simulated move (`step = rel + c·(g−1)`).

| Sleeve | Control CAGR | +3% CAGR | Δ | +4.5% CAGR | Δ | maxDD @3% |
|---|---|---|---|---|---|---|
| Equity (0.50) | 6.62% | 8.28% | **+1.66pp** | 9.10% | +2.49pp | 19.68→17.66% |
| Crypto (0.10) | 1.20% | 3.94% | **+2.75pp** | 5.31% | +4.11pp | 13.25→11.84% |
| Combined (0.50+0.10) | 4.51% | 7.49% | **+2.99pp** | 8.88% | +4.38pp | 20.30→18.00% |

Caveats: constant-rate assumption overstates 2017-2021 (near-zero rates); a
time-weighted average T-bill rate for the window is ≈3%, making the 3%
scenario the central estimate. Implementation requires a cash-parking
mechanism (e.g. pinned T-bill ETF sleeve excluded from alpha selection) —
feature work, deliberately NOT done here.

## B3 — Equity sleeve allocation

| Variant | Return | CAGR | Sharpe | maxDD | Avg cash | Fees |
|---|---|---|---|---|---|---|
| 0.50 (control) | +84.61% | 6.61% | 0.62 | 19.68% | 52.7% | $182 |
| 0.60 | +110.56% | 8.09% | 0.65 | 23.83% | 44.0% | $227 |
| 0.70 | identical to 0.60 — the cap stops binding above 0.60 |

Walk-forward: both 12/16 positive folds; median fold 3.25% → 4.16%; 0.60
beats 0.50 in 11/16 folds. Losing folds lose proportionally more (worst fold
−6.94% → −8.40%). Shadow-halt events at the 5% policy rise 202 → 223.

## A1 — Crypto sleeve sizing (combined account, common window 2021-04..2026-07)

Sleeves sized independently against one 100k account (robustness-pack
semantics): combined = equity curve + crypto curve − 100k.

| Variant | Return | CAGR | Sharpe | maxDD |
|---|---|---|---|---|
| 0.50 + 0.10 (control) | +26.45% | 4.51% | 0.43 | 20.30% |
| Equity only (crypto off) | +22.13% | 3.83% | 0.40 | **13.95%** |
| 0.50 + 0.05 | +23.88% | 4.10% | 0.41 | 16.56% |

Reading: over this window the crypto sleeve adds +0.68pp CAGR but DEEPENS the
combined max drawdown by +6.35pp — the 0.35 daily-PnL correlation does not
prevent the 2022 co-drawdown. Risk-adjusted the difference is marginal
(Sharpe 0.43 vs 0.40). Disabling or halving crypto is primarily a DRAWDOWN
decision, not a return decision. (The robustness pack's 2024-25 window, where
the crypto sleeve was negative, shifts the return case against crypto too.)

## B5 — Crypto top_n 3

| Variant | Return | CAGR | Sharpe | Sortino | maxDD | Fees | Shadow halts |
|---|---|---|---|---|---|---|---|
| top_n=2 (control) | +6.56% | 1.20% | 0.23 | 0.33 | 13.25% | $4,290 | 169 |
| top_n=3 | +18.32% | 3.21% | 0.55 | 0.83 | 10.58% | $3,868 | 86 |

Full-window every headline metric improves — but walk-forward REJECTS it
under the project's own standard: median fold 0.90% vs 1.35% (control), same
positive count 7/10, and +7.76pp of the full-window delta comes from the
single 2023H1 fold. Period-concentrated, exactly the failure pattern that
rejected `crypto_low_disagreement_v1`. Any promotion attempt needs a
dedicated pre-declared mission with concentration/jackknife diagnostics.

## B6 — Equity candidate pool widening (shortlist 300 → 500)

Pool avg 480.2 vs 300.0. Result +86.70% / CAGR 6.74% / Sharpe 0.63 /
maxDD 19.68% — +2.09pp return over the control, drawdown unchanged. Marginal;
not worth the extra scanner surface without a broader data-quality review.

## B2 — Drawdown-halt calibration inputs

Days at/below a drawdown threshold and breach episodes (account level,
full-window curves):

| Threshold | Equity | Crypto | Combined |
|---|---|---|---|
| 3% | 1274d / 52ep | 1273d / 29ep | 854d / 40ep |
| **5% (production)** | **975d / 27ep** | **785d / 16ep** | **653d / 13ep** |
| 7.5% | 825d / 13ep | 371d / 21ep | 522d / 8ep |
| 10% | 678d / 24ep | 212d / 3ep | 408d / 12ep |
| 15% | 246d / 19ep | 0d / 0ep | 98d / 8ep |

Engine shadow events (decision points where production would latch):
equity 202 (alloc 0.60: 223), crypto top_n=2: 169, top_n=3: 86. Under the
production policy each event is a halt until an operator starts a new risk
epoch — the sweep quantifies how much idle-latch exposure the current 5%
threshold carries relative to the strategies' realized drawdown profiles.

## Not backtestable here (unchanged from the proposal)

- A2 cost-model verification against paper fills — requires paper telemetry
  (`portfolio_fills.slippage`/`fill_delay_seconds`), not historical bars.
- B4 maker/passive crypto execution — daily bars cannot model fill
  probability (also ruled INFEASIBLE in `docs/optimization_tournament_2026_08.md`
  §12); testable only as a PAPER pilot.

## Suggested adoption order (operator decisions)

1. **B1**: build the cash-parking feature, then re-run this harness with the
   parking sleeve enabled (expected ≈ +1.7pp equity / +3.0pp combined CAGR at
   a 3% central rate estimate).
2. **B3**: raise `strategy.equity.maximum_allocation` 0.50 → 0.60 if the
   +4.15pp maxDD is acceptable; 0.70+ is provably a no-op.
3. **A1**: decide crypto sizing on the drawdown evidence (off/half = −6.3/−3.7pp
   combined maxDD, −0.68/−0.41pp CAGR over 2021-2026).
4. **B2**: pick halt threshold/epoch-reset policy from the sweep before going
   live; pair with the alerting webhook (still empty in config).
5. **B5/B6**: only via pre-declared missions; current evidence is insufficient
   (B5) or marginal (B6).

Artifacts: `data/research/optimization_2026_08/{eq_*,cr_*,b6_*,analysis,walkforward}.json`,
driver `run_experiments.py`. Baselines reproduce canonical numbers exactly;
all runs `persist=False` (no report/database writes).

# Batch 2 — Data completeness & second candidate set (2026-08-21 evening)

Driver: `data/research/optimization_2026_08/run_experiments2.py` (+ fetch script).
New datasets committed under `data/historical/`:
`equity_1200_dividends.csv` (16,878 cash dividends, 619 payers, ex-date,
Alpaca corporate-actions API) and `crypto_expanded_1h.csv`
(705,541 rows / 25 pairs).

## D1 — Dividend feed (MEASUREMENT GAP FOUND AND FIXED)

Every prior equity backtest in this repo (including the canonical +84.61%)
EXCLUDED dividends: no research harness passed `corporate_actions`, despite
full engine support and the product wrapper documenting them as required for
split-adjusted prices. Fetched per-symbol from Alpaca (batch requests silently
truncate at 1,000 rows — paginate or go per-symbol).

| Equity v2 weekly, alloc 0.50 | Return | CAGR | Sharpe | maxDD |
|---|---|---|---|---|
| without dividends (old control) | +84.61% | 6.61% | 0.62 | 19.68% |
| **with dividends** | **+98.55%** | **7.43%** | **0.69** | **18.28%** |

0.82pp CAGR, Sharpe +0.07, maxDD IMPROVES (dividend cash cushions drawdowns).
All published equity expectations were understated; live trading receives these
cash flows regardless. **Adopt as measurement standard**: research runs must
pass `corporate_actions`.

## Stacked adoption scenario (equity sleeve)

dividends + allocation 0.60 + cash-yield overlay on the uninvested fraction:

| Stack | Return | CAGR | Sharpe | maxDD |
|---|---|---|---|---|
| old baseline (no div, 0.50, 0% yield) | +84.61% | 6.61% | 0.62 | 19.68% |
| div + 0.60 | +129.29% | 9.06% | 0.71 | 22.24% |
| **div + 0.60 + 3% cash yield** | **+159.4%** | **10.47%** | **0.81** | **20.72%** |
| div + 0.60 + 4.5% cash yield | +175.5% | 11.17% | 0.86 | 20.01% |

## D6 — Equity top_n=9

Full window: **+114.76% / CAGR 8.31% / Sharpe 0.78 / maxDD 18.78%** vs
top_n=8's +84.61% / 6.61% / 0.62 / 19.68% — better on EVERY axis including
drawdown. Walk-forward PASSES where crypto top_n=3 failed: 12/16 positive
folds (same as control), median fold 4.28% vs 3.25%, beats control in 11/16
folds. Strongest parameter finding so far; still requires a pre-declared
mission (jackknife/concentration diagnostics) before touching the frozen spec.

## D5 — Foundation ETF systems (existing harness, local dataset)

`strategy_foundation.py evaluate`, 9-ETF panel, standalone sleeves at ~99%
exposure, 2017-01..2026-08:

| System | CAGR | Sharpe | maxDD | Turnover |
|---|---|---|---|---|
| B0 (SMA200-timed) | 8.90% | 0.80 | 23.98% | 0.2 |
| B1 (vol-targeted) | 8.05% | 0.81 | 22.79% | 0.2 |
| SPY buy-and-hold | 15.41% | 0.89 | 33.79% | 0.0 |
| C1/C2 (tactical) | 4.56/6.52% | 0.45/0.61 | ~29% | 1.8-2.5 |

B0/B1 deliver near-SPY returns with ~10pp less drawdown at near-zero turnover
— a credible low-correlation third sleeve or eventual replacement candidate
for the active equity sleeve. Sleeve-integration backtest (allocation caps +
overlay through `run_product_backtest`) is the next step.

## D4 — Crypto universe widening (18 → 25 pairs, pool 11.0 → 14.5)

| Variant (96h) | Return | CAGR | Sharpe | maxDD |
|---|---|---|---|---|
| 18 pairs, top_n=2 (control) | +6.56% | 1.20% | 0.23 | 13.25% |
| **25 pairs, top_n=2** | **+9.64%** | **1.74%** | **0.32** | 13.23% |
| 25 pairs, top_n=3 | +13.33% | 2.37% | 0.43 | 10.82% |
| 18 pairs, top_n=3 (batch 1) | +18.32% | 3.21% | 0.56 | 10.58% |

Widening improves even the CONTROL strategy (+3.08pp, Sharpe +0.09, same DD).
Note: top_n=3's apparent edge shrinks on the wider pool (part of the batch-1
result was a narrow-universe artifact). Usable new pairs from Alpaca:
SHIB, GRT, CRV, PEPE (full), MKR (to 2025-09), TRX/NEAR (to 2023) — Alpaca has
DROPPED many pairs historically; several candidates return zero rows.

## D3 — Backfilling existing 1h holes: INFEASIBLE from source

Gap detection confirms the canonical holes (e.g. SOL/USD 2023-07-06 ..
2024-08-26 ≈ 10,013 h). Re-fetching those exact ranges from Alpaca returns
ZERO rows today — the gaps are source-side, not download artifacts. Backfill
would require a second data source (price-basis inconsistency risk) and is
NOT recommended for validation-grade research.

## D7 — Rebalance band 0.25%

Equity: +85.53% (+0.92pp over control — noise). Crypto: +5.17% vs +6.56%
(WORSE — more fee events). Rejects both directions combined with the
tournament's 0.75%/1.0% results: 0.5% stands.

## D2 — Intermediate drawdown-triggered de-risk: naive design FAILS

Activated the dormant `requires_intermediate_risk_rebalance` hook via a
driver-side subclass (no project code changed): trigger at account DD ≥ 7.5%/10%
between scheduled decisions, halve every position.

| Variant | Return | CAGR | Sharpe | maxDD | Rebalances |
|---|---|---|---|---|---|
| Control (hook off) | +84.61% | 6.61% | 0.62 | 19.68% | 495 |
| DD≥7.5% halve | +41.53% | 3.70% | 0.48 | 17.89% | 1532 |
| DD≥10% halve | +68.57% | 5.61% | 0.64 | **21.32%** | 1455 |

Value-destructive: repeated re-triggering sells into declines (3x rebalances)
and DD≥10% even WORSENS maxDD (re-entry waits for the next boundary). The
machinery works; a viable design needs one-shot-per-episode semantics,
hysteresis and a re-arm rule — mission territory, do NOT ship the naive form.

## Revised adoption order

1. **Dividends standard** (D1): always run research with `corporate_actions`;
   refresh the CSV periodically. Zero-risk, already in place.
2. **Config decisions** (operator): equity allocation 0.60; crypto sizing per
   batch-1 evidence; alerting webhook.
3. **Feature work**: cash-parking sleeve (B1) — stack evidence now +10.5%
   CAGR at Sharpe 0.81.
4. **Missions, pre-declared**: equity top_n=9 (WF-passing), foundation sleeve
   integration (D5), crypto universe widening (D4 — config + pipeline),
   intermediate de-risk redesign (D2), halt calibration (B2).
5. **Do not pursue**: band changes (D7+tournament), naive intermediate halving,
   source-side backfill (D3).
---

# ADOPTED — 2026-08-21 operator decisions (config + tests updated)

Applied to `config/settings.yaml` (full suite green: 607 passed; the freeze
and contract tests now encode the new operator decisions):

| Change | Old → New | Backtest evidence (this document) |
|---|---|---|
+| `strategy.equity.maximum_allocation` | 0.50 → **0.60** | B3: WF 11/16 folds beat 0.50; with dividends CAGR 9.06%, Sharpe 0.71 |
+| `strategy.crypto.maximum_allocation` | 0.10 → **0.05** | A1: halves the sleeve's combined-maxDD contribution; widened-universe @ 0.05: +5.01%, Sharpe 0.30, maxDD 6.75% |
+| `universe.crypto.trade_symbols` | 18 → **22** pairs (+SHIB/GRT/CRV/PEPE, verified ACTIVE on Alpaca) | D4: pool 11.0 → 14.5; control +6.56% → +9.64%, Sharpe 0.23 → 0.32 |

Adopted-config reference runs (canonical windows, production harness):
equity sleeve with dividends + 0.60: +129.29% / CAGR 9.06% / Sharpe 0.71 /
maxDD 22.24% (`eq_div_alloc060`); crypto sleeve widened + 0.05: +5.01% /
CAGR 0.92% / Sharpe 0.30 / maxDD 6.75% (`crx_alloc005_ADOPTED`).

Interaction note: 0.60 + 0.05 nominally exceeds the 0.60 gross cap — the
`PortfolioRiskOverlay` arbitrates (equity headroom = gross − crypto exposure
while crypto is deployed); `tests/test_combined_account_risk.py` encodes the
new contract.

Not adopted here (requires the pre-declared missions above): equity top_n=9,
cash-parking sleeve, foundation third sleeve, intermediate de-risk redesign,
drawdown-halt calibration. Dividend data (`data/historical/
equity_1200_dividends.csv`) is now the research standard for equity runs.
