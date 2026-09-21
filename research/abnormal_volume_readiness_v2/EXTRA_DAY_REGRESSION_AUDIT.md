# Extra-day regression audit

The preserved legacy fixture proves the old generic monthly behavior: it observes the first completed bar of the new month, creates the decision after that bar, and the engine fills on the following session. In the January 2024 fixture this is a 2024-02-01 decision followed by a 2024-02-02 fill.

Readiness V2 does not use that path. For each of all 115 scheduled candidate boundaries it calls the candidate-neutral `month_end_first_open` contract. The runner and independent verifier both reconstructed:

- decision after the actual final exchange close of month M;
- fill at the actual first exchange open of month M+1;
- `decision_timestamp < fill_timestamp`;
- no completed daily bar required between those timestamps;
- no later-session fallback when the intended first-open bar is absent or nonpositive.

Coverage begins with 2016-12-30 close to 2017-01-03 open and ends with 2026-06-30 close to 2026-07-01 open. Independent comparison matched all 115 timestamps exactly. The focused calendar suite also covers weekends, NYSE holidays, December-to-January, leap February, a shortened close, missing/nonpositive first-open rows, and the absence of an accidental extra session.

Result: `PASS`.
