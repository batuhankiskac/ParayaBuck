# Historical vintage question set — NYSE Group Security Master — 2026-09-22

Purpose: move `VINTAGE_SEMANTICS_UNCLEAR` to `ORIGINAL_VINTAGE_CONFIRMED` or
`RESTATED_HISTORY_ONLY`. Each question needs a written, unambiguous vendor answer.
References: spec v4.1 (2026-06-24), history from 2015-12-16, equity full+delta at 12am ET.

## V1 — Historical archive completeness

V1.1 Does the entitlement provide historical NYSE Group Security Master equity files for every
trading date from 2015-12-16 through the current date, in bulk, including contemporaneous schema
versions (2.x–4.1) as originally distributed?

## V2 — Original daily vintages (A/B choice)

V2.1 For a requested historical generation date YYYYMMDD, is the delivered file:
(A) the exact file originally distributed to subscribers for that generation date; or
(B) a later reconstructed/restated file using corrections or reference data known after that date?
V2.2 If (B) for any period: which date ranges are restated, and what distinguishes a restated file
from an original (naming, header, notice)?

## V3 — Immutability

V3.1 If I retrieve YYYYMMDD today, are the bytes/content guaranteed to match what was distributed
for that session historically?
V3.2 Can a past full or delta file be replaced or regenerated? If yes: are every version, the
first-publication timestamp, a revision ID, checksums, and a correction notice all retained and
retrievable?

## V4 — Corrections and correction-arrival reconstruction

V4.1 If historical reference data were later corrected: is the original incorrect record preserved?
V4.2 Is the correction distributed as a dated delta/change (Change Type M with `modified fields`)?
V4.3 Given only the archive, can the correction ARRIVAL date (when subscribers first could have
known) be reconstructed separately from the effective date of the fact?
V4.4 Are intraday replacements possible for the midnight full/delta, or is one immutable generation
per trading date guaranteed?

## V5 — Full + delta coverage

V5.1 Are BOTH historical daily full files AND delta files available for the entire archive?
V5.2 The public sample directory has no equity delta sample: confirm delta layout history
(added spec v2.1, 2015-12-16) and that historical deltas carry Change Type A/M/D + modified-fields.

## V6 — Date semantics

V6.1 Confirm YYYYMMDD is always the generation date and the file is start-of-day for the next
trading session. Which next session does each file serve across weekends/holidays?
V6.2 Confirm delta files are diffed against the prior GENERATED file (not prior session), including
across non-trading days.

## V7 — Historical taxonomy population

V7.1 Were Security Type, Issuer Type, Primary Market, Stock Number, CUSIP/ISIN, ETF/ETN/ETS/ETV
indicators, and preferred/warrant/unit/right classifications populated historically as of each
file's original date — or were some fields backfilled later?
V7.2 Specifically: for non-Arca ETPs (spec: default `ETF` code), are leverage/inverse/asset-class
attributes available historically, or Arca-only as documented?
V7.3 When did each taxonomy-relevant field/code enter the layout (ETS 2022-10-31, flag re-encodings
2024, TXSE 2026-07-06, earlier Pillar/National/Chicago changes), and are pre-change vintages served
in their original layout?

## V8 — Stock Number stability (issue identity)

V8.1 Confirm Stock Number is issue-level, persistent through ticker/CUSIP changes, and never reused
after delisting.
V8.2 Enumerate exactly which corporate-action types allocate a NEW Stock Number.
V8.3 Are predecessor/successor links supplied for mergers, class conversions, relistings, and venue
transfers? If so, in which fields/files?
V8.4 What is the population plan for the `Symbol Reuse Flag` placeholder, and does any historical
file populate it?

## V9 — Universe / venue coverage

V9.1 Which listings are represented: NYSE, NYSE American, NYSE Arca, NYSE National, NYSE Texas
(as applicable by era), Nasdaq-listed, Cboe-listed, IEX/LTSE, FINRA?
V9.2 The spec claims all U.S. NMS securities approved to trade on NYSE Group platforms: does that
equal every Nasdaq-/BZX-primary issue for 2016–2026, or only a tradable subset?
V9.3 `Listing Date` may be first NYSE Group trade date for non-NYSE listings: confirm this caveat
holds historically so we never misread it as the primary-listing date.

## V10 — Trial/evaluation vintage proof

V10.1 Can NYSE provide a historical evaluation sample of 4–8 non-consecutive trading dates
(one 2016, one 2018/2019, one 2022, one recent) with BOTH full and next-day delta files?
V10.2 Will evaluation files be byte-identical to entitled archive files for those dates?
V10.3 May sample bytes/hashes and derived audit counts be retained for the evaluation record?
