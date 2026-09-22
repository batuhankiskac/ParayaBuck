# NYSE first inquiry — SEND-READY DRAFT — DO NOT SEND without explicit operator approval

> Status: DRAFT ONLY. No contact made. No form submitted. No terms accepted. No purchase.
> The operator sends this manually only after explicitly approving submission and providing
> contact details outside committed files. Do NOT persist personal data in Git.

Recommended channel: `Contact Us` inquiry on the NYSE data-products / reference pages
(see `OFFICIAL_CONTACT_PATH.md`). Do NOT use the ICE dashboard order page.

---

Subject: Historical entitlement questions — NYSE Group Security Master (internal research use)

Hello NYSE Market Data team,

We are evaluating the NYSE Group Security Master for internal historical quantitative
research / point-in-time security classification (one user, no redistribution).

We have reviewed spec v4.1 and the public equity samples. Before any purchase, please answer
the following in writing:

1. Is complete historical equity coverage (full files) available from December 16, 2015 through
   the current date, in bulk and in contemporaneous layouts?
2. For a historical trading date T, is the file available today (A) the exact file originally
   distributed on date T, or (B) a retrospectively reconstructed/restated file? If (B) anywhere,
   which ranges, and how are restatements marked?
3. If data from date T were corrected later: is the original record preserved, is the correction
   arrival date recoverable, and do corrections arrive via dated delta files or another auditable
   mechanism?
4. Are both daily full and daily delta files available throughout the archive?
5. Confirm Stock Number behavior: non-reuse after delisting, persistence through ticker changes,
   and behavior through venue transfers, mergers, relistings, and share-class conversions.
   Which events mint a new number, and are predecessor/successor links supplied?
6. Do historical files distinguish ordinary/common equity from ADR/ADS, REIT common, ETF, ETN,
   preferred, warrant, right, unit, closed-end fund, and structured product (including
   leverage/inverse for non-Arca ETPs)?
7. Which U.S. listings are represented: NYSE, NYSE American, NYSE Arca, NYSE Texas/National as
   applicable, Nasdaq-listed issues, Cboe-listed issues?
8. Does the license permit historical backtesting, internal quantitative research, and creation
   of derived effective-dated classifications?
9. May historical raw files be stored locally, may derived tables be retained, and what must be
   deleted if the subscription ends?
10. Are there separate restrictions/fees for storing/using CUSIP, ISIN, or NYSE Stock Number?
11. Can you provide a non-binding historical evaluation sample (e.g. 2016, 2018/2019, 2022,
    recent), each date full plus next-day delta, byte-identical to archive files?
12. Please quote one internal research user: historical archive entitlement, current feed only if
    required for history, any one-time backfill charge, and identifier fees if applicable.
    No redistribution rights needed.

Thank you,
[YOUR NAME — placeholder, do not invent]
[COMPANY / LEGAL ENTITY — placeholder]
[EMAIL — placeholder] · [PHONE — placeholder]

---

End of draft. Checklist mapping: Q1←V1.1/V5; Q2←V2; Q3←V3.2/V4; Q4←V5.1; Q5←V8; Q6←V7; Q7←V9;
Q8←L2; Q9←L1/L3; Q10←L4; Q11←V10/L7; Q12←L5/L8/L9. Full internal detail lives in
`INTERNAL_FOLLOWUP_CHECKLIST.md`. No attachments. No strategy, returns, repository, or broker detail.
