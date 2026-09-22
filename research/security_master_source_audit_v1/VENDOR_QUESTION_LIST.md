# Exact vendor question list

Ask NYSE first for **NYSE Group Security Master full 2015-present historical backfill plus daily updates and an evaluation sample**.

1. Are historical files immutable byte-for-byte copies of the files first distributed for each generation date?
2. Can a past full or delta file be replaced or regenerated? If yes, are every version, first-publication timestamp, revision ID, checksum and correction notice retained?
3. Can both an original erroneous value and the date/time its correction first became available be reconstructed?
4. Does `YYYYMMDD` always mean generation date, and which next trading session is each file effective for around weekends/holidays?
5. Is every equity full and delta file available from 2015-12-16 through today in bulk, including contemporaneous schema versions?
6. Does the equity file cover every Nasdaq-, NYSE-, Arca-, American- and BZX-primary U.S. NMS issue in our 2016-2026 period, or only securities approved on a subset of NYSE Group books?
7. Is Stock Number permanent across symbol/CUSIP changes, relistings, exchange transfers, class conversions, mergers and spin-offs? Which events allocate a new number, and are predecessor/successor links supplied?
8. Are Security Type and Issuer Type populated historically enough to distinguish common/ordinary, ADR/ADS common, REIT common, ETF, ETN, closed-end fund, preferred, warrant, right, unit and structured product?
9. Are leverage, inverse, asset-class and benchmark fields populated for non-Arca ETPs, including Cboe/Nasdaq listings?
10. Is an issuer identifier or supported CIK bridge available outside the public layout?
11. Provide sample full/delta sequences for: a 2016 date, ticker reuse, symbol/CUSIP change, new/delisted issue, merger/relisting, ADR, REIT, ETF/ETN, leveraged/inverse product and a later correction.
12. Is an evaluation/trial entitlement available, and may sample bytes/hashes and derived audit counts be retained?
13. Is internal quantitative research and historical backtesting expressly permitted?
14. May raw files, checksums, backups and derived effective-dated tables be stored indefinitely, including after cancellation?
15. What deletion obligations apply at termination?
16. What user, device, contractor, company and cloud-region limits apply?
17. What redistribution/publication limits apply to derived taxonomy and aggregate research results?
18. Do CUSIP, ISIN or FTSE fields require separate rights, and what must be removed if those rights end?
19. What are the one-time historical backfill, recurring update and evaluation fees?
20. Is support available to reconcile unmatched/ambiguous historical issues?

If NYSE cannot prove coverage or vintage semantics, send equivalent questions to Nasdaq Daily List/Fundamental Data and Cboe BZX, explicitly asking Nasdaq about all intraday versions and Cboe about purchased-file immutability.
