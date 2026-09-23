# Shared account ledger

The replay starts with one cash amount equal to the configured $100,000 NAV.
Every engine reads and writes that same cash value. No equity or crypto cash
bucket exists.

For the combined input window the first execution timestamp is the earliest
equity bar in the configured evaluation window
(`2017-01-03T05:00:00+00:00`, a New York midnight session-date label).
Both incumbent sleeves are flat at that timestamp: zero positions, zero
pending orders, and all $100,000 in cash. Each engine's empty strategy state is
initialized independently while its account state points to the same ledger.
This is the canonical daily-bar clock; it does not claim that the completed
equity close was available at 05:00 UTC.

Inventory and marks use `(Market, canonical symbol)` keys. The strategy engine
gets a market-scoped view, while the risk overlay receives a snapshot of all
non-flat positions. NAV is cash plus quantity times the latest causally known
mark for every position. Gross is the sum of absolute market values divided by
NAV. Sleeve and per-name values use the same account NAV denominator.

The canonical `PortfolioRiskOverlay` continues to apply the configured global
position cap, market-specific position cap, gross limit, sleeve limit,
per-name limit, cash buffer, daily-loss rule, and offline drawdown-shadow rule.
Current production values are used, including global `maximum_positions = 6`.
Combined target exposure is checked at each market decision; realized gross is
also observed after every merged bar event. Mark-to-market drift between
decisions is recorded rather than corrected by the replay.

Cash conservation is independently recomputed as:

`starting cash - BUY fill notional - BUY fees + SELL fill proceeds - SELL fees + dividend cash + terminal-liquidation proceeds`

Fill price already includes the canonical adverse slippage and spread model;
those costs are not deducted a second time. Fees are deducted once per fill.
Equity dividends credit the shared cash ledger once for the managed quantity.
Split actions adjust share quantity and average price without changing cash.
Crypto does not receive equity corporate actions.

Buying power is represented by available cash at execution. A working BUY
reserves a position slot but does not reserve its full notional from cash;
actual fills cannot make cash negative. This matches the current runtime's
distinction between account slot claims and actual cash movement. Production
broker buying-power fields and the fresh-account deferred-buy resize are not
available in the historical dataset.

Accounting receipts include cash conservation, NAV reconciliation, fill
ownership, duplicate-fill checks, quantity reconciliation, fee and dividend
counts, position/sleeve/name constraints, and deterministic event hashes.
