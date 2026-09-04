# Candidate feasibility receipt — 2026-09-05

## Preliminary classification

`C1 NO_NEW_ECONOMICS_DUPLICATE_FAMILY`

This is a preliminary feasibility and novelty record. It is not a fresh economic failure result, a promotion decision, or an acceptance of C2.

The current source checkout is `main` at `ed11beba916202dcb4986853e848652af6e7a3ef`.

## Earlier C1 foundation evidence

The archived [strategy foundation script](../../scripts/archive/research_2026_08/strategy_foundation.py) defines C1 as B1 inverse-volatility allocation plus a binary 12-month own-price total-return gate (`C1_RETURN_LOOKBACK = 252`; `_trend_gate_c1`). Its fixed universe is the same nine ETFs: SPY, IWM, EFA, EEM, TLT, LQD, GLD, XLRE, and DBC.

The resulting [foundation report](../../data/research/strategy_foundation_v1/report.md) records monthly month-end decisions and a completed-bar/next-execution convention. Its C1 row is historical evidence from that archived run (CAGR 4.56%, Sharpe 0.45, maximum drawdown -28.75% at 1x costs), with a reported negative increment versus B1. That result is retained as historical context; this receipt does not reclassify it as a new C1 experiment.

## Data panel evidence and blocking input issue

The [ETF foundation manifest](../../data/research/equity_etf_foundation_1d_v1/manifest.json) exists and identifies `equity_etf_foundation_1d_v1`, `timeframe=1d`, `start=2016-01-04`, `end=2026-08-11`, and nine symbols. It reports 2,666 bars for each symbol, zero duplicate and conflicting duplicate bars, source `sip+iex_hybrid`, and data hash `ba5268eff7431172b7886e253219ce26e20ac13e518dad141d1d732db1c5f56b`.

The manifest says `adjustment=all` and explicitly says dividends are inside the prices and must not receive separate corporate-action cash flows. The production-shaped product path assumes split-adjusted execution prices plus explicit dividend actions. The engine's safer dual-series contract is [`MarketDataset`](../../src/parayabuck/data/market_dataset.py:71): adjusted signal bars may be paired with raw/split-aware executable bars. C1 therefore cannot be declared engine-safe from this manifest alone. Data preparation must fail closed until the signal/execution adjustment roles are proven; passing the total-return series as executable bars and adding dividends would double count.

## Reusable execution and accounting semantics

[`PortfolioBacktestEngine`](../../src/parayabuck/backtest/portfolio_engine.py:227) already provides completed-bar decisions, next tradable-bar open fills, sell-before-buy ordering, participation-capped partial fills, turnover bands, minimum order notionals, and residual cash. [`RebalancePolicy`](../../src/parayabuck/portfolio/models.py:168) supports monthly first-calendar-bar decisions.

The shared [`PortfolioRiskOverlay`](../../src/parayabuck/execution/portfolio.py:123) remains the owner of gross, sleeve, per-name, position-count, minimum-cash, daily-loss, and drawdown behavior. Research defaults record drawdown halts as shadow events; daily-loss halts remain enforced. C1 work must not duplicate or bypass those rules.

Cash-rate observations currently calculate a hurdle diagnostic in [`_cash_hurdle_return`](../../src/parayabuck/backtest/portfolio_engine.py:1393); they do not compound the engine's actual cash balance. Also, [`run_product_backtest`](../../src/parayabuck/backtest/product.py:301) passes corporate actions but not `cash_rates`. A cash-aware research run must use `run_dataset` or explicitly treat cash accrual as diagnostic only.

## Why a C2 novelty gate is still useful

C2 may proceed only as a separately specified novelty gate: it needs a genuinely different economic information source or mechanism, a frozen control and overlap test against the archived foundation C1, and the same adjustment, causal timing, risk, and cost gates. A different lookback, threshold, or parameterization of own-price trend is insufficient. C2 is not accepted by this receipt; it remains pending the other agent's novelty and feasibility review.

