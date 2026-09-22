# Current strategy and exit audit

Source revision: `754f4060b1eb38593201b2a269e9593d083eb534`. Read-only production inspection; this document does not change strategies, exits, thresholds, configuration, or PAPER/LIVE state. File references are repository-relative, one-based source lines.

## Equity V3

- Selected identity is `equity_residual_momentum_v3`, daily signal bars, weekly rebalance, 0.55 maximum sleeve allocation. Parameters: formation 252, skip 21, beta window 273, residual-volatility window 63, top eight, maximum raw single weight 0.10, ivol rejection quantile 0.70, SPY excluded (`config/settings.yaml:141-158`; `src/parayabuck/portfolio/equity_residual_momentum_v3.py:148-181`).
- Positive 12-1 residual momentum qualifies names before equal-mean cross-sectional percentile ranks of 6-1, 9-1 and 12-1 residual momentum. This positive signal gate is not a condition on the held position's profit since entry. Raw signal returns and trade P&L are different quantities (`equity_residual_momentum_v3.py:10-24,95-145`, under `src/parayabuck/portfolio/`; `src/parayabuck/portfolio/equity_residual_momentum.py:440-465`).
- Selection is the first `top_n` ranked names; V3 has no held-name rank hysteresis. Sizing uses capped inverse residual volatility. The target carries alpha rank; a name can be omitted because it lost eligibility, ranking, or data support, and a retained name can be resized (`src/parayabuck/portfolio/equity_residual_momentum.py:526-568,583-649`). Missing benchmark or an empty qualified pool gives a cash target, not a profit-sensitive exit (`:587-598,651-664`).
- Top eight is the **raw alpha target**, not a promise of eight actual positions: the risk overlay enforces the account-wide six-slot cap and keeps ranked equity targets in alpha order. Cross-market holdings, reserved targets and open orders consume slots (`src/parayabuck/execution/portfolio.py:265-337`).

## Crypto T1 hysteresis

- Selected identity is `crypto_low_variance_hysteresis_v1`, daily bars, 96-hour rebalance, 0.05 maximum sleeve allocation, 22 daily returns, top two (`config/settings.yaml:159-174`).
- Ranking is ascending sample realized volatility, with symbol-order tie breaking; qualified names require PIT-universe membership and positive finite closes in the 23-bar window spanning 22 calendar days (`src/parayabuck/portfolio/crypto_low_variance.py:70-101`).
- Held names are retained if still eligible/rankable and rank <=4. Retained names sort by rank, then vacancies fill from the best ranks. A rank >4, missing rank or ineligibility removes retention; no eligible candidates gives cash. Selected names have equal raw weights (one name 100%, two names 50% each), subsequently constrained by risk and the 5% sleeve cap (`src/parayabuck/portfolio/crypto_low_variance_hysteresis.py:9-19,48-85`; `src/parayabuck/execution/portfolio.py:339-374`).
- Retention rank four is frozen, rejected as a tunable parameter (`crypto_low_variance_hysteresis.py:50-56`). The implementation does not inspect entry price, unrealized profit, MFE, or giveback. Low variance and rank retention do not imply profit protection.

## Cadence, sizing and actual exit execution

Weekly boundaries compare ISO year/week; 96h boundaries compare Unix-epoch-aligned blocks, not an individual position's age. First observed period is eligible; runtime also has explicit forced scheduled catch-up (`src/parayabuck/portfolio/models.py:196-207`; `src/parayabuck/portfolio/runtime_service.py:718-729`). Consequently a threshold reached between scheduled decisions is not itself a strategy exit.

Current shared constraints: 10% equity asset cap, 5% crypto asset cap, two crypto positions, six global positions, 60% gross, 40% cash minimum, 0.5% daily-loss threshold and 5% account drawdown threshold (`config/settings.yaml:175-184`). Maximum allocation is a ceiling, not guaranteed investment. Exposure scaling and position limits can force reductions independent of an individual trade's profit.

The order planner computes target quantity from target weight, account equity and current price. Target omission means zero target weight. It sells only managed inventory, respects broker available quantity, quantity increments and minimum order sizes. The configured 0.5 percentage-point weight band and $10 minimum can suppress small reductions/full-close remnants. Existing orders can block a new order (`src/parayabuck/execution/portfolio.py:481-542`; `config/settings.yaml:222-228`). Thus a raw target exit is not proof of an executed flat position.

Portfolio broker submission is a market order, GTC for crypto and DAY for equity. That request contains no stop-loss, take-profit, trailing-stop or profit-lock parameter (`src/parayabuck/execution/alpaca_orders.py:165-225`). Generic legacy managed-position protection code exists (`src/parayabuck/execution/alpaca_crypto.py:65-135`; `src/parayabuck/execution/alpaca_protection.py:270-334`) but is not evidence that the selected portfolio strategy's fills receive those exits. The portfolio request schema itself has no stop/target fields (`src/parayabuck/execution/portfolio.py:48-59`). Do not attribute legacy bracket/stop lifecycle behavior to canonical V3/T1 trades merely because the repository contains it.

## Runtime versus canonical offline behavior

- Runtime applies risk with its default enforced drawdown policy (`src/parayabuck/portfolio/runtime_service.py:1022-1031`). The overlay defines daily loss and portfolio drawdown from account metrics; when halted, target weights are capped at current weights, so it permits risk reduction but does not mechanically liquidate every holding (`src/parayabuck/execution/portfolio.py:174-190,259-263`). A portfolio drawdown halt is distinct from a per-position trailing stop.
- Canonical offline engine defaults `enforce_drawdown_halt=False`, records drawdown breaches as shadow events and continues the alpha curve; daily-loss breaches remain enforced (`src/parayabuck/backtest/portfolio_engine.py:312,386,1071`; `src/parayabuck/execution/portfolio.py:156-190`). Report which setting a replay actually used. Historical alpha replay is not literal PAPER account operation after an enforced halt.
- Canonical fills occur only on a later bar (`created_at < bar.timestamp`), at its open plus adverse modeled costs, subject to known volume participation (`src/parayabuck/backtest/portfolio_engine.py:855-882`). Decision timestamps, target changes, fills and lifecycle close timestamps must remain separate.
- Existing reusable V3 replay harness: `research/month_end_first_open_correctness_v1/replay_v3_parity.py:1-16,38-60`; it uses production `run_product_backtest`, frozen equity v5 bars/lifetimes/corporate actions, and a 2017-01-03 start. Its receipt is not a lifecycle audit and requires the requested diagnostic reconstruction.

## Direct answer: is existing profit protection present?

In the inspected selected strategy-to-portfolio-order path, **no explicit individual-position rule depends on unrealized profit, prior peak profit, breakeven after a gain, profit tiers, trailing profit, or MFE giveback**. Existing mechanisms are signal/universe/rank selection, crypto rank hysteresis, weight rebalancing and account-level risk constraints. These may sell a winner or loser, but do not promise to preserve an earlier unrealized gain. This is a code-path conclusion, not a recommendation for a new exit and not evidence that an added exit would improve outcomes.

Validation scope: source inspection only for this document; no new exit implementation or experiment, no broker/runtime action, and no physical/live execution claim.
