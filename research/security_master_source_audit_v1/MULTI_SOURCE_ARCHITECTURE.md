# Conceptual source architecture

## Current decision

Do not declare multi-source mandatory yet. NYSE Group Security Master claims the complete U.S. NMS universe approved to trade on NYSE Group platforms and its public sample spans all five canonical venue labels. It is therefore the lowest-complexity primary lead. Entitled history must first prove interval coverage, taxonomy and vintage semantics.

Fallback if that gate fails:

```text
NYSE Group daily full/delta
  + Nasdaq Daily List/Fundamental Data for Nasdaq-primary gaps and event validation
  + Cboe BZX reports for BZX-primary gaps and event validation
  + SEC original filings/Series-Class for issuer and fund-class evidence
                         |
                         v
raw append-only evidence -> issue/listing/issuer graph -> as-of classification
```

## Model boundaries

- `issuer_id`: economic/legal filer; CIK is evidence here.
- `security_id`: distinct share class/issue; never ticker or CIK alone.
- `listing_id`: venue-specific listing interval.
- `alias_interval`: symbol/CUSIP/ISIN/source ID with effective and knowledge time.
- `classification_evidence`: raw source field, file hash, `observed_at`, claimed effective interval.
- `policy`: separate derivation of ordinary-alpha eligibility and reserved roles.

## Precedence and conflicts

No source wins by convenience. For listing facts, prefer the primary-listing exchange's contemporaneous event record; for issuer identity and filed class evidence, prefer original SEC filings; for NYSE-group trading eligibility, use NYSE's Stock Number/full snapshot. Preserve all raw values.

Material disagreements in symbol, type, venue, effective date or identifiers create `UNKNOWN`/`CONFLICT`, close the affected interval and block ordinary-alpha eligibility. Never overwrite earlier evidence or backfill a later correction to an earlier knowledge date. Share classes remain separate even when CIK matches.

Corporate-action accounting remains in the canonical action dataset. The master supplies identity/lifecycle links; it does not reapply dividends, splits or terminal cash flows.
