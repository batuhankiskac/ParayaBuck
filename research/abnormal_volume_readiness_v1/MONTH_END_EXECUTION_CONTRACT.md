# Month-end execution contract

For month `M`, use the NYSE exchange schedule rather than dataset row adjacency:

1. join daily bars to their `America/New_York` session date;
2. identify the final NYSE session belonging to `M`;
3. finalize the signal only after that schedule row's `market_close`;
4. identify the first NYSE session whose date belongs to calendar month `M+1`;
5. schedule the pending target at that row's `market_open`;
6. require `decision_timestamp < fill_timestamp`;
7. retain the filled target until the following month-first-open, except for risk-reducing lifecycle exits;
8. never replace a pending name that becomes ineligible between decision and fill.

The canonical daily bar timestamp is a New York local-midnight session marker, not an executable open or completed close. It must not be used directly as either execution timestamp.

Deterministic schedule expectations:

| Case | Decision session / close UTC | Fill session / open UTC |
|---|---|---|
| ordinary | 2024-01-31 / 21:00 | 2024-02-01 / 14:30 |
| Friday month-end / weekend | 2024-05-31 / 20:00 | 2024-06-03 / 13:30 |
| first weekday holiday | 2024-08-30 / 20:00 | 2024-09-03 / 13:30 |
| year boundary | 2024-12-31 / 21:00 | 2025-01-02 / 14:30 |
| shortened month-end | 2024-11-29 / 18:00 | 2024-12-02 / 14:30 |
| leap February | 2024-02-29 / 21:00 | 2024-03-01 / 14:30 |

This contract eliminates the generic extra-day path by comparing the scheduled fill directly with the calendar's first next-month `market_open`; the following exchange session must be different. The contract is fully specified, but it was not encoded into a candidate runner because signal selection is blocked upstream.

