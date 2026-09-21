# Accession-vintage model

Every usable fact must retain:

`CIK + accession + acceptance timestamp + form + fiscal context + taxonomy namespace/version + concept + raw value + unit + decimals + dimensions`

## Availability

- `ACCEPTANCE-DATETIME` in the raw EDGAR header is information time.
- Filing date alone is not an intraday availability timestamp.
- A decision may use only facts from an accession accepted before its frozen
  cutoff.
- Today's Company Facts response is a discovery snapshot, not the historical
  database state.

## Amendments and restatements

- Original 10-K facts are valid from original acceptance.
- A 10-K/A creates a new vintage only from amendment acceptance.
- It never overwrites an earlier decision.
- A Part-III-only/nonfinancial amendment does not replace financial facts.
- If an amendment contains restated financial statements, only the facts
  actually carried by that amendment form the new vintage.
- `dei:AmendmentFlag` alone is insufficient to infer financial replacement.

## Comparative prior-year rule

For a score based on annual accession `A_t`:

- current flows use annual consolidated duration contexts in `A_t`;
- current working-capital balances use current fiscal-year-end instants in
  `A_t`;
- prior balances and lagged assets use comparative prior-year instants reported
  in the same `A_t` accession.

The separately filed previous 10-K value is retained as a restatement/
non-articulation diagnostic but is not silently mixed into the delta. If the
current accession lacks a valid comparative value, the filing fails closed.

## Fiscal periods

Actual fiscal start/end dates are retained. A 52/53-week annual duration is not
forced to 365 days. `10-KT`/transition reports are excluded; the next full
10-K re-enters only when current and comparative periods are semantically
comparable. Non-December fiscal years are not converted to calendar years.
