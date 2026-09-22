# Original-vintage semantics audit

The hard requirement is the subscriber knowledge state on decision date `t`, not a clean reconstruction made at `t+n`.

| Source | Evidence | Classification |
|---|---|---|
| NYSE Group Security Master | Full/delta files keyed by generation date and archived from 2015-12-16; no immutable-original, replacement, revision or correction-arrival guarantee | `VINTAGE_SEMANTICS_UNCLEAR` |
| Nasdaq Daily List | Distribution timestamp and intraday refreshes; historical event access since 1999; no promise that every intraday/final historical version or pre-correction value survives | `VINTAGE_SEMANTICS_UNCLEAR` |
| Nasdaq Fundamental Data | Daily full current-month master; historical full-file backfill and revision behavior undocumented | `VINTAGE_SEMANTICS_UNCLEAR` |
| Cboe BZX reports | Dated full/event reports with Updated/Cancelled states since 2012; no immutable-byte or replacement-history guarantee | `VINTAGE_SEMANTICS_UNCLEAR` |
| Original EDGAR filing document | Accession and SEC acceptance time preserve the filed document | `ORIGINAL_VINTAGE_CONFIRMED` for that filing only |
| SEC ticker/submissions current metadata | Periodically refreshed current associations/view | `VINTAGE_SEMANTICS_UNCLEAR` |

None of the exchange leads can presently receive `ORIGINAL_VINTAGE_CONFIRMED`; none is documented as definitely `RESTATED_HISTORY_ONLY`. A dated archive name is insufficient.

Stage 2 must preserve `source_file_date`, `first_acquired_at`, source object key, byte length and SHA-256 without overwriting. A later vendor correction must append a new evidence version with `known_at`; it must never silently mutate the original local observation. Vendor proof must answer whether the retroactively supplied 2016-2026 objects are the exact originally distributed bytes, whether files can be replaced, and how every correction's arrival is observable.
