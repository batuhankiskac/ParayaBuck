# Submission-time market slot admission contract

Frozen after the offline pre-fix production reproduction and before product code changes, 2026-09-24.

For every candidate order in planner order, build a canonical `(market, symbol)` set from fresh nonzero broker positions, durable local nonterminal opening orders, same-cycle reservations, and broker working opening orders. Deduplicate aliases and overlapping representations. A pending SELL, terminal SELL status, or partial SELL does not release an occupied slot. Only a fresh authoritative zero-position observation without an opening reservation releases it.

Admit SELLs without consuming or releasing slots. Admit BUYs to an already claimed symbol without adding a slot, subject to independent gross, cash, and asset gates. For each new opening BUY, require both `len(effective_slots) + 1 <= maximum_positions` and `count(effective_slots in candidate market) + 1 <= explicit_market_limit` when that market has a configured limit. Current production limits are eight total and two crypto; equity has no separate market count limit. Add each admitted opening to the same batch's slot set before checking the next candidate.

Apply this same final gate to immediate, deferred, restart-resumed, catch-up, and event-driven submissions. For deferred replan, if an old position remains after a terminal SELL and the replacement opening is denied by a slot gate, retain a truthful waiting state and retry only from a fresh account observation. Never persist or submit the denied opening. Any newly planned SELL is evidence that the target still needs an exit; the deferred path cannot treat that SELL as proof of free capacity.
