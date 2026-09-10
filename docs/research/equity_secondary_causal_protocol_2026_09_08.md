# Equity secondary causal/schema protocol — 2026-09-08

Research-only acquisition and causal/schema gate for H06, H07, H08, H09,
H10, H11, H12, H13, H15 and H16. The authoritative definitions are taken
from `strategy_queue_full_report_2026_09_06.md`,
`strategy_queue_equity_review_2026_09_06.md`, and the independent review. No
economic returns, PAPER, LIVE, production, configuration, registry or
promotion action is permitted by this package.

## Common frozen boundary

The historical cutoff is `2026-07-31T23:59:59Z`. Common cells are
`full=2022-01-01..2026-07-31` and `recent=2024-01-01..2026-07-31`, with cost
stress `1x/2x/3x`. Controls are the incumbent, isolated candidate,
passive/cash, fixed or matched-exposure control when applicable, and the
shared-capital diagnostic. Holdout dates remain `UNSPECIFIED` unless the
authoritative review gives a requirement (H08 requires a post-2016 holdout).

Every candidate must use the point-in-time universe and CIK/security identity
history, preserve event/report/publication/acceptance/retrieval/decision times
separately, and require an explicit timezone-aware availability boundary.
Current labels and OHLCV are acquisition evidence only and cannot substitute
for causal fields. A missing field or revision policy fails closed.

## Frozen candidate contracts

| ID | Native fields and provider meaning | Decision/availability | Formula/window/role | Minimum history and blockers |
|---|---|---|---|---|
| H06 | SEC Form 4 issuer CIK, reporting owner/role, transaction code, shares, price, ownership, amendment | EDGAR acceptance/publication time; next executable NYSE bar | Unusual open-market purchases after publication; grants/exercises/gifts/amendments excluded by frozen taxonomy; long-only | Form 4 history, transaction taxonomy, routine history, CIK/ticker continuity |
| H07 | Historical analyst forecast estimate, horizon, analyst/date identity, company identity, publication/vintage | Forecast publication/vintage before decision; next executable bar | Positive revision aggregation and rank/hold rule must be frozen before results; long-only | Full forecast vintages and stale-estimate policy; no authorized historical forecast feed |
| H08 | Earnings release timestamp, actual EPS, pre-release consensus, revisions/restatements, event identity | Release timestamp; next executable session | Positive surprise after release with fixed event horizon; post-2016 holdout required; long-only | Actual/consensus vintages, collision policy, release times; no typed event feed |
| H09 | Customer/supplier link, vintage, direction, identifiers, termination, linked-firm event | Relationship vintage and linked event publication; next bar after fixed lag | Customer-to-supplier information spillover; fixed side/hold; long-only adaptation unproven | PIT relationship feed and event mapping; current labels cannot substitute |
| H10 | Original 10-K/10-Q versions, CIK, section text, accession and acceptance | EDGAR acceptance time; next executable bar | Filing text change versus prior filing; section/pair/version and long-only direction frozen ex ante | Full filing versions, text normalization, section vocabulary, CIK history |
| H11 | Fund flow, holdings vintage, fund identity, ownership weight, publication | Holdings/flow publication and fixed lag; next bar | Flow multiplied by known ownership pressure; buy-after-pressure direction/hold fixed | PIT holdings/flows, rebalance events, capacity; no full fund-flow archive |
| H12 | Named index, methodology/membership vintage, announcement and implementation timestamps | Announcement/application boundary and event-window execution | Index addition/removal pressure; direction and hold fixed; weakened effect retained | PIT membership, event timestamps, methodology versions and corporate actions |
| H13 | Raw/adjusted intraday transactions or quotes, session/auction calendar, opening/closing fills | Explicit session timestamps and feed availability | Overnight/intraday split per source construction; daily OHLC proxy prohibited; long-only execution | Intraday SIP/live parity, auction semantics, executable fills |
| H15 | Cash deal terms, target identity, amendments, announcement/close/cancel/break/delist events | Announcement and lifecycle timestamps; next bar | Cash-only merger spread; fixed entry/exit/cancel rules and cash ledger | Complete deal lifecycle and terminal identity; no authorized event archive |
| H16 | Two-step spinoff announcement/effective date, distribution, ownership conversion, successor IDs and lifecycle | Announcement/effective timestamps; next bar | Narrow two-step ownership-conversion mechanism; direction not established | Event/distribution/successor mapping and adjusted prices; no lifecycle feed |

No unspecified threshold, direction, event window, hold period, universe,
provider or holdout date is inferred from outcomes.
