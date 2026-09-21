# Public availability audit

## Required hierarchy

`SEC acceptance -> dissemination/first observation -> strategy decision -> execution`.

The SEC header exposes `ACCEPTANCE-DATETIME`; submissions JSON exposes
`acceptanceDateTime` in UTC. Bulk data exposes only a filing date. The SEC
[Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)
says filings often appear on sec.gov within one to three minutes of EDGAR time,
but no timestamp records when content first became available on sec.gov.

Thus acceptance time is a conservative archival proxy, not direct proof of
first public availability. A live collector should persist both acceptance and
first-successful-observation times, content hash, URL, HTTP metadata, and parser
version, and use `first_observed_at` as usable time.

## Filing-hours and calendar rule

Forms 3/4/5 and amendments can be accepted after 5:30 p.m. and before 10 p.m.
Eastern and retain that day's filing date. Normalize the API UTC timestamp,
retain the raw header, use America/New_York exchange calendars, and reject
timestamp disagreements. Never apply a fixed UTC-5 offset through daylight
saving time.

For historical daily research, first regular-session open strictly after
acceptance is conservative. A month-`t` public-filings aggregation decided
after the final month-`t` close and executed at month-`t+1` first open is even
more conservative and closely matches CMP's calendar-time construction. It is
not authorized here because upstream semantics and mapping are blocked.

## Reporting deadline

The modern general rule is filing by the end of the second business day after
the reportable transaction. Faster disclosure improves the causal cleanliness
of a public-copy strategy but plausibly accelerates price discovery relative to
CMP's older sample.

Sources: [Form 4 instructions](https://www.sec.gov/about/forms/form4data.pdf),
[filing-status guidance](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/determine-status-my-filing),
[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
