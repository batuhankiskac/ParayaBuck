# Opening-slot invariant

At every immediate BUY admission or deferred BUY submission boundary:

`|nonzero broker position keys ∪ nonterminal local order keys ∪ broker OPEN order keys ∪ earlier same-cycle BUY keys ∪ admitted new BUY keys| <= 6`

Keys are `(Market, canonical_symbol)`. Existing-held adds deduplicate into
the occupied key. A pending SELL or partial exit leaves that key occupied.
SELL intent itself does not release capacity. A filled opening transitions
from order claim to nonzero position without double counting. A canceled or
failed opening ceases to claim a key when both order sources show it terminal
or absent. No in-memory decrement is needed, so repeated terminal updates
cannot release capacity twice. Planner order is preserved for competing
openings, and each admitted new key is inserted before considering the next.

The account-level admission helper is in `execution/portfolio.py`; PAPER and
LIVE both use it in `PortfolioRuntime.process_closed_bars()` for an immediate
BUY batch and `_submit_deferred_buys()` after a fresh account snapshot. Actual
SELL legs retain the existing two-phase protocol. No broker order was placed
for this campaign.
