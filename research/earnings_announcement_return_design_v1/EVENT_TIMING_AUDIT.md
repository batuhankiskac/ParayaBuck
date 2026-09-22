# Event timing audit

## Timing conclusion

SEC acceptance can drive a deterministic daily alignment, but it cannot retroactively identify the original issuer-release time when filing lags. The convention below is causal for EDGAR-observed events and preserves the complete three-day EAR. It does not establish that the resulting sample is equivalent to Compustat RDQ.

## Source clock

Use the raw complete-submission `<ACCEPTANCE-DATETIME>` and retain the original value. The SEC describes acceptance time as Eastern and says public website availability is often one to three minutes later, with no timestamp for first availability. A live strategy should also persist its own first-seen timestamp. Historical research must not fabricate an exact first-seen time.

Official sources: [SEC timestamp FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), [EDGAR data access](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), and [NYSE core hours](https://www.nyse.com/trade/trading-information).

## Deterministic session mapping for an accepted EDGAR event

Let `a` be the SEC acceptance time and use the official exchange calendar for the mapped primary security:

1. If `a` is before the 09:30 ET core open on a trading day, event session `t` is that session.
2. If `a` is from 09:30 ET up to, but not including, the 16:00 ET core close, `t` is that session. The daily bar contains pre-disclosure trading but also the observable reaction; no intraday reconstruction is claimed.
3. If `a` is at or after the 16:00 ET core close, `t` is the next trading session.
4. If the source event is observed on a non-trading day, `t` is the next trading session.
5. If the stated issuer release date precedes the EDGAR acceptance date, do not shift the known public event forward and call it equivalent. Mark `release_precedes_sec_clock` and exclude it from a paper-faithful sample until the original timestamp is sourced.

For the Brandt formula, compute the compounded stock and matched-portfolio returns for complete sessions `t-1`, `t`, and `t+1`. The signal first becomes final after the `t+1` bars and benchmark return are complete. The first daily open at which that value can be used is `t+2` open. This is consistent with Brandt's method section, which begins post-announcement accumulation at `t+2`.

The paper uses a date-only Compustat `RDQE` and does not disclose an intraday realignment rule. The mapping above is therefore a conservative operational adaptation, not a quoted paper rule.

## Sample timing facts

The purposive eight-row specimen has raw acceptance timestamps for every row:

| SEC-clock class | Count | Specimens |
|---|---:|---|
| Before 09:30 ET | 2 | JPMorgan 2020, American Airlines 2022 |
| During core hours | 1 | Banner 2016 |
| After 16:00 ET | 5 | Edwards 2018 amendment, Lam 2020, Microsoft 2024, Post 2026, Booking 2026 |

One of seven initial filings states a release date before its acceptance date (Banner). One additional record is an amendment six days after the original release (Edwards). These counts describe only the selected specimens.

The Post filing is a useful negative control for metadata interpretation: the index reports `periodOfReport=2026-05-05`, but Exhibit 99.1 says “St. Louis - May 7, 2026” and the filing was accepted May 7 at 16:21:20. The earlier date relates to other reported matters and must not become the earnings date.

## Boundary and failure rules

- If acceptance is so close to the open that dissemination before 09:30 cannot be established, map to the next session or fail closed. A historical `ACCEPTANCE-DATETIME` alone is not proof of first availability before the auction.
- Keep early closes and exchange holidays from the exchange calendar; do not hard-code 16:00 for those dates.
- An amendment is not a new earnings episode. Preserve it as a revision and contamination flag.
- Multiple same-day Item 2.02 accessions for one issuer and fiscal period require exhibit comparison and a single first-event identity.
- Do not use the exhibit's fiscal-period end as the release date.
- Do not use today's earnings calendar to repair a missing historical clock.

## Feasibility boundary

For records accepted on the stated release date and comfortably away from an opening boundary, the SEC clock supports a causal daily event session and the full-window `t+2` entry logic. For lagged filings, SEC alone is too late to reproduce the immediate announcement window. A population-level event-timing gate therefore remains conditional on acquiring and validating a historical release-time source or proving, with structural comparison only, that the fail-closed SEC subset has adequate coverage.
