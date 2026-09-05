# Portfolio Expansion: Equity and Cross-Asset Candidate Batch

Date: 2026-09-05

This is a research inventory, not an economic result, investment recommendation, or promotion decision. Equity V3 logic and configured allocation remain fixed: equity gross up to 0.55, crypto gross up to 0.05, account gross up to 0.60, minimum cash 0.40. An additional sleeve may use only independently verified remaining capacity; this report does not split, replace, or retune the 0.55 equity sleeve. If no capacity remains, that is a capital constraint.

## Repository scope

The equity strategy is weekly residual momentum; crypto incumbent is 22-day low-variance selection on a 96-hour cadence. The archived ETF foundation used the same nine ETFs with inverse volatility and a binary 12-month own-price trend gate, so that exact C1 recipe is a duplicate standalone-alpha family. Changing only its window or threshold would not make it new.

build_product_universe can replace only the observation ranker while retaining scanner, risk, costs, and execution (src/parayabuck/backtest/product.py:116-205). run_product_backtest runs one strategy per product (src/parayabuck/backtest/product.py:208). The runtime architecture describes a shared account, cash ledger, risk overlay, and order repository across market strategies (docs/strategy_architecture.md:145-180). The archived combined_portfolio_validation.py adds independently generated sleeve curves; that is a sizing diagnostic, not proof of shared fills, cash netting, partial fills, order priority, or risk feedback.

Every portfolio test must preserve one initial cash balance, current V3 policy, current gross/cash ceilings, sell-before-buy ordering, next-tradable-bar fills, fees, slippage, participation limits, residual cash, terminal marking, and halt semantics. A same-market candidate needs a research-only coordinator that feeds both targets into one risk/execution ledger. A cross-market candidate still needs shared-account accounting parity.

## Finite candidate inventory

| Family | Formula/mechanism | Difference from V3 | Status | Regime hypothesis |
|---|---|---|---|---|
| C2 low-beta sleeve | 252 prior aligned stock/SPY returns; lowest OLS beta; monthly top group | Risk sensitivity, not residual price momentum | Signal feasible; portfolio contribution untested | May reduce synchronized equity drawdown |
| Overnight/intraday | O_t/C_(t-1)-1 versus C_t/O_t-1; one frozen gap rule | Session-boundary return | OHLC exists; adjustment/open causality unproven | May help when overnight and intraday forces offset |
| Quality | PIT gross profitability/assets or one safety/profitability measure | Published fundamentals | PIT data blocked | May be steadier during weak markets |
| Value | PIT book/market or earnings/price, one measure | Valuation level | PIT data blocked | May normalize after valuation dispersion |
| PEAD | Timestamped EPS surprise versus timestamped consensus | Company event timing | Event/consensus data blocked | May help when idiosyncratic news dominates |
| Cross-asset trend | Fixed ETF/futures time-series trend | Cross-asset covariance | Exact nine-ETF recipe is archived duplicate; data accounting gate | May help in persistent directional regimes |
| Volatility-managed exposure | Lagged realized-vol scale on an unchanged sleeve | Risk overlay, not alpha | Prices feasible; requires new coordinator | May reduce high-volatility losses |
| Pairs/stat-arb | Two-leg spread, long cheap/short rich | Relative value and shorting | Future capability; current long-only contract blocks it | May help in sideways markets |

These are hypotheses, not claims of profitability or regime coverage.

### C2 low-beta as a complementary sleeve

The exact existing rule is monthly first eligible session; calculate OLS beta to SPY from the prior 252 aligned daily returns; rank ascending; select the lowest-beta valid names with stable ties. No momentum overlay. Existing scanner exclusions, per-name cap, equity cap, and next-bar execution remain in force.

Frazzini and Pedersen's *Betting Against Beta* studies low-beta/high-beta factors across equities and other assets: [JFE DOI](https://doi.org/10.1016/j.jfineco.2013.10.005). Its leveraged long-short construction does not transport to this long-only capped bot. Local C2 standalone economics were rejected; that does not test marginal portfolio contribution.

The candidate should therefore be assessed only as a complementary sleeve using independently verified remaining capacity. Do not split or replace V3 in this report. Before measurement, freeze any incremental sleeve capacity without increasing the 0.55 ceiling. Compare V3-only, the combined shared-account portfolio, and a same-capital neutral replacement. Required metrics are matched NAV-increment correlation, combined and marginal CAGR/Sharpe/max drawdown, synchronized drawdown, turnover, fees, fills, cash, exposure, concentration, and cap compliance. Failure modes include sector concentration, low-beta crowding, high-beta rallies, and overlap with V3 residualization.

### Overnight/intraday decomposition

Daily bars permit the identities overnight = O_t/C_(t-1)-1 and intraday = C_t/O_t-1. A candidate may use one predeclared trailing statistic of overnight returns, one fixed cadence, next eligible open entry, and a fixed holding/exit rule. Direction, window, horizon, and breadth are not frozen and must be fixed before results, with no sweep.

Lou, Polk, and Skouras study overnight/intraday continuation and cross-period reversal in US equities: [JFE 2019](https://doi.org/10.1016/j.jfineco.2019.03.011). Their sample and execution setting do not establish the sign or profitability here. OHLC presence does not prove split/dividend consistency or historical open executability. A data gate must verify signal versus raw/split-aware execution series, previous-session alignment over holidays, corporate actions, delistings, opening auctions, stale/zero opens, and the exact decision timestamp. Repository evidence shows overnight-gap risk diagnostics, not an overnight alpha. This remains distinct only if it stays gap-specific rather than becoming generic daily reversal.

### Quality

A minimal quality rule would use one frozen published measure, such as gross profitability/assets, or a small predeclared profitability/safety composite. Values must be available at the decision timestamp; rebalance slowly; hold capped long-only positions; add no V3 momentum filter.

Novy-Marx reports a gross-profitability premium: [NBER working paper](https://www.nber.org/papers/w15940). Asness, Frazzini, and Pedersen define quality through profitability, growth, and safety and report QMJ evidence across countries: [Springer DOI](https://doi.org/10.1007/s11142-018-9470-2). These historical broad/long-short settings do not establish this bot's outcome. The family is currently blocked by revision-safe PIT fundamentals, filing availability timestamps, first/restated values, CIK-ticker lifetimes, accounting definitions, delisted names, and survivorship-complete coverage. EDGAR/XBRL access is not automatically PIT data. Quality may be tested only after that receipt.

### Value

Keep value separate from quality during research. One frozen PIT ratio, such as book-to-market or earnings-to-price, can be ranked monthly or quarterly into capped long-only positions without a momentum overlay. The mechanism is valuation normalization after dispersion; failure modes include prolonged growth leadership, impairment hidden in cheap firms, distress, and sector concentration.

The same PIT, revisions, identifier, and survivorship gates apply. Current data does not establish them, so value is data-blocked. Any future success still requires a portfolio-contribution test; standalone value return is insufficient.

### PEAD

PEAD requires the actual release timestamp, an estimate vintage known immediately before release, and an unexpected-earnings measure. A fixed positive-surprise rule would enter at the next tradable bar and exit at a predeclared event horizon or invalidating event. Quarter-end labels, filing dates without release time, and reconstructed consensus are invalid substitutes.

Bernard and Thomas are the primary historical source: [JSTOR](https://www.jstor.org/stable/2491062). The local shortlist already marks PEAD blocked because event and consensus inputs are absent. Distinct information does not make it currently feasible. Timezones, corrections, halts, opening gaps, stale estimates, overlapping events, and delistings require explicit treatment.

### Cross-asset ETF/futures trend

A cross-asset sleeve could use the archived SPY, IWM, EFA, EEM, TLT, LQD, GLD, XLRE, DBC universe with one fixed time-series rule and cash for failures. The exact archive recipe is not a fresh alpha candidate, but a portfolio-diversification test could still be relevant if run with shared capital and accounting. Moskowitz, Ooi, and Pedersen report time-series momentum in equity-index, currency, commodity, and bond futures: [JFE DOI](https://doi.org/10.1016/j.jfineco.2011.11.003). Futures evidence does not transport directly to ETFs.

The ETF manifest reports adjustment=all and dividends embedded in prices; pairing those bars with explicit corporate-action cash flows could double count. Signal/execution adjustment roles must be proven first. Expected benefit is covariance change during persistent directional regimes; failure includes joint equity/bond/commodity selloffs and monthly whipsaw. Compare with matched gross/cash controls, not with an unscaled higher-cash curve.

### Volatility-managed exposure

Volatility management is an overlay, not an alpha signal. One frozen rule could scale an unchanged sleeve by lagged realized volatility subject to existing account caps. Moreira and Muir study volatility-managed versions of momentum, profitability, and betting-against-beta: [Journal of Finance DOI](https://doi.org/10.1111/jofi.12513). This does not authorize changing V3 or its allocation. It is a possible future research overlay only with a coordinator that keeps V3 signal logic fixed.

The hypothesis is lower drawdown in high-volatility states, traded against reduced exposure during recoveries. Daily prices are sufficient for the signal; shared cash/risk feedback and exact exposure accounting are not optional.

### Pairs/statistical arbitrage

A pairs rule estimates a two-stock spread, buys the cheap leg, shorts the rich leg, and exits at convergence, stop, or age limit. Gatev, Goetzmann, and Rouwenhorst provide the classic distance-method reference: [NBER](https://www.nber.org/papers/w7032). The family is genuinely relative-value, but current TargetPortfolio is long-only and lacks borrow availability/costs, recalls, locate timing, two-leg partial fills, and spread-risk accounting. A one-leg substitute is a different strategy. This remains future capability.

## Static sleeves versus causal regimes

A static comparison assigns fixed, predeclared shares of the same account capacity; both signals feed one overlay and ledger. This tests incremental contribution under identical capital, costs, fills, and cash.

A causal regime allocator is a separate candidate. Its state must use completed bars strictly before each decision, with frozen state rules and weights and unchanged total gross/cash limits. A label inferred from later drawdowns is descriptive only. Compare it against the static combined control, or an apparent benefit may only be lower exposure or more cash. The present product backtest does not prove either same-market multi-sleeve netting or a regime allocator.

## Minimum research gate

Freeze candidate identity, formula, universe, PIT rule, schedule, execution timestamp, costs, controls, and source hashes before economics. Require complete history, adjustment-role proof, common start, and a future/unseen validation receipt; previously examined 2016–2026 windows are retrospective.

Persist selected symbols, raw scores, eligible pool, targets, orders, fills, fees, cash, positions, equity curve, terminal mark, and rejection/coverage reasons for every arm. Use 1x/2x/3x costs and the same halt/risk semantics. Evaluate synchronized NAV correlation, combined and marginal CAGR/Sharpe/max drawdown, drawdown overlap, turnover, fees, fills, exposure, cash, concentration, and terminal reconciliation. No isolated return or lower volatility should be called diversification without a shared-account replay.

This batch does not approve a candidate, alter V3, change allocation, or imply profitability.

