# CMP classification feasibility

## Verdict

`CMP_CALENDAR_CLASSIFICATION_FEASIBLE_FAIL_CLOSED`

At the start of calendar year Y, each reporting-owner CIK is evaluated using
only original Form 4 non-derivative code-P/code-S transaction months in years
Y-3, Y-2, and Y-1. The owner is:

- `unclassified` unless all three prior years contain activity;
- `routine` if at least one calendar month occurs in all three years;
- `opportunistic` otherwise.

Routine is absorbing. Opportunistic status is rechecked at each later year
boundary and may become permanently routine. Current-year transactions never
alter that year's precomputed status. A transaction month enters history only
when its original filing date precedes the annual cutoff. Names do not merge
owners.

The implementation is deterministic in `run_structural_reassessment.py`.
Across its explicit owner/year evaluation grid it generated counts recorded in
`STRUCTURAL_OPPORTUNITY_AUDIT.json`.

## Safety boundaries

- Original Form 4 establishes the initial public state. From an owner's first
  later 4/A onward, that owner is `amendment_ambiguous` and excluded because
  the bulk schema cannot reliably identify the corrected original row. This
  exclusion applies immediately to events on or after the amendment filing,
  including within the same calendar year.
- Reporting-owner CIK is the identity key.
- A multi-owner bulk filing is excluded from classified opportunities because
  transaction rows cannot be assigned to one co-filer from the bulk schema.
- Filing date is a day-level public-information proxy, not an intraday
  acceptance timestamp.
- Code P remains open-market-or-private; classification does not change that.

This establishes fail-closed calendar-classification feasibility, not complete
replication of every CMP sample filter and not predictive value.
