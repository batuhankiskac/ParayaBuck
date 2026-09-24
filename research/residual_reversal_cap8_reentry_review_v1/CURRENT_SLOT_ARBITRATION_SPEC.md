# Proposed current whole-account slot arbitration

This is a pre-outcome **design recommendation**, not a validated production or replay implementation.

1. Follow current physical event order: process eligible SELL fills and reconcile the shared account, then crypto decisions at their causal availability times, then equity decisions after completed XNYS closes. A SELL request does not free a slot; only a fresh zero-position observation does. Pending BUYs and all working opening orders reserve their `(market, canonical symbol)` slot.
2. Crypto uses its unchanged 22-day variance, rank retention, 96-hour cadence, 0.05 allocation, and crypto cap two. It has no permanently reserved global slots. Existing inventory and previously admitted reservations are counted before a new decision.
3. For a treatment equity decision, use V3's own rank and reversal's own negative-first rank. Keep V3 0.4125 and reversal 0.1375 target budgets. Do not compare raw alpha scores across sources. For simultaneous unclaimed equity openings, advance the source with the lower admitted/requested **pre-count target exposure fraction**; break exact ties in V3's favor and then by rank and canonical symbol. This is a capacity fairness rule, not a return-driven 4/2 or 6/2 quota.
4. An overlapping equity symbol consumes one global slot. Preserve its two labeled source contributions, sum them for its account target subject to the existing name cap, and attribute any cap reduction proportionally to the two contributions. Never transfer one source's unused budget to the other.
5. Apply the current risk overlay and pending-order rules to all admitted claims. Infeasible source allocation remains cash; no equity/crypto or V3/reversal budget transfer. Denial records distinguish global count from crypto cap, name cap, gross/cash, missing data, pending SELL, and other causes.

Production has one strategy engine per market. This multi-source treatment must remain research-only and cannot be represented by registering reversal in PAPER/LIVE.
