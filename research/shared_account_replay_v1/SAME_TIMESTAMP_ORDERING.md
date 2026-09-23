# Same-timestamp ordering

The coordinator uses the order below whenever both markets have bars at the
same UTC timestamp. The market tiebreak follows the runtime's current
crypto-first processing within a shared finalized-bar batch. The historical
execution phases follow the canonical backtest's completed-bar and next-open
fill contract.

The event queue sorts absolute source timestamps before applying this tiebreak.
Because equity daily labels are New York midnight and crypto daily labels are
UTC midnight, bars from the same calendar date usually do not tie; the replay
processes the UTC crypto label first by four or five hours. This is a frozen
data-clock convention, not a claim about actual intraday availability.

1. Check overdue terminal inventory for each market.
2. Ingest all current execution bars and all signal bars with timestamps no
   later than this event time. Apply due market-scoped corporate actions.
3. Fill existing SELL orders across markets, crypto first then equity, symbols
   ascending.
4. In the combined two-market replay, if any SELL order remains working after
   that pass, defer all account BUY fills for this event. Otherwise fill
   existing BUY orders crypto first then equity, symbols ascending. A
   completed SELL pass precedes BUY cash use. Single-market controls retain
   the canonical engine's isolated fill cycle for exact baseline parity.
5. Update close marks for bars that actually occurred. Closed-market marks
   remain unchanged.
6. Advance the shared risk state once.
7. Decide for markets with a completed execution-bar cycle, crypto first then
   equity. A crypto BUY created here is visible as a reservation to the equity
   decision at the same timestamp.
8. Apply due terminal-bar lifecycle closures for each market.
9. Record one account NAV/cash observation for this timestamp.

Prior orders fill at the current bar open before the current bar close can
produce a new decision. This preserves the repository's canonical
decision-on-close, next-tradable-open execution convention. The current
timestamp's completed close is used for the subsequent mark and risk/decision
snapshot; it is never used as the fill price for an order created from that
close.

The intra-timestamp SELL-before-BUY phase is deterministic. Production order
updates are asynchronous; the combined replay represents a sell as terminal
only when the canonical historical fill model completes it. If any sell is
partial or remains unfilled, combined-account BUY fills wait for a later
merged timestamp. Isolated parity controls preserve the canonical offline
engine behavior, including its same-cycle sell-then-buy fill sequence.
