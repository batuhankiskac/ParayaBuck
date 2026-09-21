# Causality audit

## Information clock

The only defensible clock is:

`raw SEC acceptance timestamp -> public availability -> completed decision -> next permitted regular-session execution`.

Transaction date and bulk `FILING_DATE` are not public-availability timestamps.
The quarterly bulk files omit the acceptance timestamp, so raw EDGAR filing
headers/indexes are required for event-level causality.

## Conservative execution candidates

The literature's principal calendar-time portfolios aggregate trades in month
`t` and measure/hold in month `t+1`. That design maps cleanly to ParayaBuck's
exchange-calendar month-end close and next-month first open, while ensuring
every included filing was already public. It is the leading causal analogue,
but it is not frozen because mapping, purchase semantics, and ranking remain
blocked.

Event-level first-open-after-acceptance is causal only with the exact SEC
acceptance timestamp, New York conversion, regular-session calendar handling,
and an after-hours/weekend rule. The repo lacks that event scheduler.

## Amendments

An original filing may affect state only from its own acceptance time. A Form
4/A may affect future state only from the amendment's acceptance time. A later
amendment must never rewrite what a historical decision knew. Because the bulk
files do not link every amendment semantically to a corrected transaction,
raw submission comparison is required.

## Conclusion

Causal daily or monthly representation is conceptually possible, but the
authoritative timestamp and event-state layer is absent. No execution rule is
authorized for economics.
