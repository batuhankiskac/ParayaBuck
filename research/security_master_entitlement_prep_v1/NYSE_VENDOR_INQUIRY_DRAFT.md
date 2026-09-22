# NYSE vendor inquiry draft — DO NOT SEND without explicit user approval

> Status: DRAFT ONLY. No contact made. No form submitted. No terms accepted. No purchase.
> The operator sends this manually only after authorizing external contact.

---

Subject: Historical entitlement questions — NYSE Group Security Master (internal research use)

Hello NYSE Market Data team,

We are evaluating the NYSE Group Security Master for historical internal quantitative
research / point-in-time security classification (one user, no redistribution).

We have reviewed spec v4.1 and the public equity samples. Before any purchase, we need
written answers to the following:

**History and vintage**

1. Does the entitlement include historical equity full AND delta files for every trading date
   from 2015-12-16 to present, in bulk, in contemporaneous layouts?
2. For a historical date, is the file (A) the exact file originally distributed on that date,
   or (B) a later reconstruction? If (B) anywhere, which ranges and how are restatements marked?
3. Are retrieved historical files byte-guaranteed to match the original distribution? Can files
   be replaced — and if so, are all versions, first-publication timestamps, revision IDs,
   checksums, and correction notices retained?
4. Can both an original erroneous value and its correction-arrival date be reconstructed from
   the archive (correction as a dated delta)?
5. Confirm YYYYMMDD is the generation date (start-of-day for the next session), including
   weekend/holiday handling, and that deltas diff against the prior generated file.

**Coverage, identity, taxonomy**

6. Does the equity file cover every Nasdaq-/NYSE-/Arca-/American-/BZX-primary U.S. NMS issue
   for 2016–2026, or only securities approved on a subset of NYSE Group books?
7. Is Stock Number issue-level, persistent through ticker/CUSIP changes, and never reused?
   Which corporate actions mint a new number, and are predecessor/successor links supplied?
8. Are Security Type / Issuer Type populated historically enough to separate ordinary common,
   ADR common, REIT common, ETF, ETN, closed-end fund, preferred, warrant, right, unit, and
   structured product — including leverage/inverse for non-Arca ETPs?

**Licensing and retention**

9. Is internal quantitative research / historical backtesting and derived internal
   classification expressly permitted?
10. May we store original files, checksums, and backups locally while subscribed, and retain
    derived effective-dated tables (a) while subscribed and (b) after termination? What must
    be deleted at termination?
11. Are there separate CUSIP/ISIN/FTSE license requirements or fees for this use?

**Evaluation and pricing**

12. Can you provide an evaluation sample — 4 historical dates (one 2016, one 2018/2019, one
    2022, one recent), each full plus next-day delta, byte-identical to archive files — and may
    we retain sample hashes/audit counts?
13. Please quote one-user internal-research use: one-time historical backfill, recurring daily
    updates, any evaluation or identifier fees. No redistribution rights needed.

Thank you,
[YOUR NAME — placeholder, do not invent]
[COMPANY / LEGAL ENTITY — placeholder]
[EMAIL — placeholder] · [PHONE — placeholder]

---

End of draft. Placeholders must be filled by the operator at send time; do not persist personal
contact details in Git.
