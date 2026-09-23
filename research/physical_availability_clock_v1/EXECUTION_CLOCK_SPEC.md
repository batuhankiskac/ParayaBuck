# Execution clock and stop

Equity V3's economic contract is completed daily signal, then next valid NYSE session open. `equity_next_execution_open` resolves that session through XNYS; missing actual price bars must not be synthesized. This preserves the intended session/price mapping while correcting the decision's physical time.

Crypto's current daily backtest decides at the **start label** of a daily bar and fills at the **next** daily bar's open (`PortfolioBacktestEngine.run` and `_fill_side`). Its first frozen decision is 2021-04-01 00:00 UTC, consumes 2021-04-01 complete BTC/USD and ETH/USD bars, and creates orders filled at the 2021-04-02 00:00 UTC opening prints. The consumed bars first complete at that same boundary. A decision created after final component completion cannot have placed an order in time to obtain the exact already-occurring opening print without an unsupported zero-latency/zero-ordering assumption. `RebalancePolicy` says orders become effective on the next tradable bar; live `process_closed_bars` receives closed bars before order creation.

The next causally priceable event with the available daily OHLCV is a later daily open; a finer source bar or tick execution model would require a separately frozen baseline. Either choice changes incumbent fill timing/pricing, so this study must stop without silently revising the canonical crypto control.
