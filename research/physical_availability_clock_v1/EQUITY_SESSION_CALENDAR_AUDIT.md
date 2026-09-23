# Equity session calendar

The installed and locked dependency is `exchange-calendars==4.13.2`, indirectly supplied by `pandas-market-calendars==5.4.0`. `timing.py` uses `XNYS` over 2016–2026. The library provides session membership, official open, and official close, including planned early closes. No network service or hand-maintained holiday table is used.

Observed checks: 2016-03-11 closes 21:00 UTC and 2016-03-14 closes 20:00 UTC across DST; 2016-11-25 closes early at 18:00 UTC; 2024-07-03 closes early at 17:00 UTC; 2024-07-04 is closed and the next open after July 3 is 2024-07-05 13:30 UTC. A non-session date raises rather than fabricates a bar. No arbitrary finalization grace is added to the lower-bound availability clock.

An unexpected exchange closure or provider publication delay after official close would require separate evidence; this lower-bound calendar rule cannot claim an earlier availability.
