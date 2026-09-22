# Historical sample request — NYSE Group Security Master — 2026-09-22

Minimal evaluation: enough dates to prove vintage semantics, NOT the full 2016–2026 archive.

## Requested dates (trading days; vendor to confirm exact generation dates)

- D1: one 2016 date (early archive, oldest schema era).
- D2: one 2018/2019 date (Pillar/National migration era).
- D3: one 2022 date (ETS-code era, post-2022-10-31 preferred).
- D4: one recent 2024/2025/2026 date (current v4.x layout).
- Optional D5–D8: only if needed to cover event types below with real rows.

For EACH date: the full equity file AND the next-generated delta file (full+delta pair),
byte-identical to entitled archive files for those dates.

## Event coverage (do not hand-curate; normal archive dates suffice, but confirm presence)

Prefer dates whose full+delta pairs together exercise: ticker rename (Old Symbol row), ETF,
ETN, preferred, ADR common, REIT common, warrant/unit, IPO/new listing (Change Type A),
delisting (Change Type D), split flag row, ex-corporate-action row, and one later-corrected
record whose correction arrives on a later dated delta.

## Vintage proof procedure (on receipt, before any purchase)

1. Record SHA-256, byte length, filename generation date, and acquisition time per file; never
   overwrite an observed file.
2. Replay each full+next-delta pair: delta A/M/D rows must reconcile adds/deletes/modifies vs the
   prior full; `modified fields` must name the changed non-daily columns.
3. Cross-check dated facts against independently archived public/reference evidence for the same
   session (e.g. listing events, corporate-action effective dates) — file date alone proves nothing.
4. Verify corrections arrive only on LATER dated files: the earlier full must still show the
   original (incorrect) value; the correction must appear as a dated delta/change.
5. Compare layout versions across eras (field count, flag encodings, new codes) against the spec
   document history; flag any backfilled modern codes in old vintages.
6. Confirm Stock Number continuity across the rename/change rows in the sample.

## Retention of evaluation evidence

Retain only file hashes, row counts, and derived audit counts unless the vendor expressly permits
retaining sample bytes. Ask for that permission in writing (L7.1).

## Acceptance

Sample passes iff: pairs reconcile, corrections are arrival-dated, layouts match their era, and
taxonomy/identifier fields populate as documented. Any restated-only history without arrival
lineage fails per HARD blocker 5.
