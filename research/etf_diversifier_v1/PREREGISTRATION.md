# ETF diversifier v1 — pre-registration

Status: **frozen before any candidate outcome was computed** (2026-09-25).
Branch: `research/etf-diversifier-v1`. Research only: no production strategy,
configuration, risk, PAPER or LIVE change is made by this campaign.

## Question

Does adding a small long-only ETF sleeve whose returns come from a *different
source* than US single-stock momentum (Treasuries, gold, commodities, T-bills)
improve the **combined account** (V3 equity + crypto T1 + ETF sleeve) on a
risk-adjusted basis, especially in the regime where V3 struggles (2022)?

Prior work (see `docs/research/research_register.md` and the August
`reports/portfolio_research` artifacts) tested ETF trend only **standalone
versus SPY** and never as a complement to V3. Cross-asset allocation checks
were unregistered diagnostics. This campaign answers the complementarity
question only.

## Frozen candidates (exactly two, no parameter search)

Both trade daily split-adjusted SIP bars with explicit cash dividends, decide
on the completed first bar of each calendar month (`RebalancePolicy monthly`)
and fill at the next tradable open through the unchanged
`PortfolioBacktestEngine` cost model (2 bps slippage, 2 bps spread,
effective-dated regulatory fees; 1x/2x/3x stress).

- **C1 `etf_static_defensive_v1`** — fixed weights SPY 25%, TLT 25%, GLD 25%,
  BIL 25% of the sleeve, restored monthly. No signal.
- **C2 `etf_trend_12m_v1`** — risk assets SPY, TLT, IEF, GLD, DBC. Each gets
  1/5 of the sleeve when its trailing 252-bar close-to-close price return
  (split-adjusted, decision bar inclusive) is strictly greater than BIL's
  trailing 252-bar price return; otherwise its 1/5 goes to BIL. An asset with
  fewer than 253 bars is treated as failing the trend test.

## Frozen allocation

- **Control A:** V3 0.55 + crypto 0.05 (shipped configuration).
- **Treatment C1 / C2:** V3 0.40 + ETF 0.15 + crypto 0.05. Gross stays 0.60,
  cash buffer 0.40; every other risk limit unchanged.
- Sensitivity only (not used for the decision): V3 0.55 + ETF 0.15 + crypto
  0.05 (gross 0.75).

## Combination method and its known limitation

Each sleeve is replayed in its own standalone account through the production
engine at its sleeve allocation; the combined account return on each date is
the sum of the sleeve account returns (equivalent to daily re-sizing of every
sleeve against total account equity, which is how the runtime sizes targets).
V3 at 0.40 is the 0.55 replay scaled by 0.40/0.55 (V3's per-name weights never
reach the 0.10 account cap, so the scaling is exact up to rebalance-band and
minimum-notional rounding). Crypto uses the canonical causal replay A (1x
only; identical in every arm, starts 2021-04-01).

This is a **Stage A screen**, not a shared-ledger replay: it does not model the
account-wide `max_positions` slot contention between sleeves. Passing Stage A
authorizes a shared-ledger Stage B, not production.

## Windows

Evaluation 2017-01-03 → 2026-07-30 (2016 is warmup). Development 2017–2021,
validation 2022-01-03 → 2026-07-30, stress year 2022.

## Frozen gates (a candidate passes only if all hold)

- **G1 validity:** complete bar coverage for every candidate symbol over the
  window, all fills strictly after their decision, dividends applied once.
- **G2 full period 1x:** combined Sharpe ≥ control Sharpe + 0.05 **and**
  combined max drawdown ≤ control max drawdown.
- **G3 CAGR guard:** full-period 1x combined CAGR ≥ control CAGR − 0.50 pp.
- **G4 validation 1x:** validation-window Sharpe ≥ control, max drawdown not
  worse than control by more than 1.0 pp.
- **G5 development 1x:** development-window Sharpe ≥ control − 0.05 (the
  benefit must not be a validation-only artifact).
- **G6 2022:** calendar-2022 combined return ≥ control 2022 return.
- **G7 costs:** at 3x costs the combined Sharpe still ≥ control Sharpe at 3x.
- **G8 concentration:** no single ETF contributes more than 60% of the
  absolute ETF-sleeve P&L.

Sharpe/volatility use daily returns on the combined NY-trading-day calendar,
annualized with √252, zero risk-free rate (consistent across arms).

If both candidates pass, the one with the higher full-period combined Sharpe
is preferred. Results are reported regardless of outcome.
