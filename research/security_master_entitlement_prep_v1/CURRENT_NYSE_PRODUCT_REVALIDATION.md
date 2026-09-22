# Current NYSE product revalidation — 2026-09-22

Prior audit receipt: `7366ea51ac819de7772ef67cc650869248ea7d07` (spec v4.1, 2026-09-22).
This document re-checks the live official sources; it does not redo the audit.

## URLs checked (retrieval date 2026-09-22)

- Product catalog: `https://www.nyse.com/data-products/catalog/nyse-group-security-master`
- Sample directory: `https://ftp.nyse.com/Reference%20Data%20Samples/NYSE%20GROUP%20SECURITY%20MASTER/`
- Current spec: `https://www.nyse.com/publicdocs/nyse/data/NYSE_Group_Security_Master_Client_Specification_v4.1.pdf`
- Technical documents index: `https://www.nyse.com/market-data/technical-documents`
- Contracts index: `https://www.nyse.com/market-data/contracts`
- Purchase flow: `https://dashboard.theice.com/data/order/buynyseproduct/720752` (link target only; not opened, nothing submitted)

## Catalog facts (still current)

- Product is Start of Day: multiple files delivered in the evening for the next trading day session.
- Scope claimed: 1.5M+ U.S. instruments, 100+ attributes, cross-asset golden copy of securities
  available for trading on NYSE Group platforms.
- Audience: banks, trading/brokerage/wealth firms (ops/compliance), infra providers, vendors,
  index creators, structured-product issuers, media.
- Resources link points at technical documents; Sample Data link points at the public FTP samples.
- Purchase is via ICE dashboard order page (`buynyseproduct/720752`). No price is shown publicly.

## Spec facts (v4.1 still current)

- Version 4.1, dated 2026-06-24, going live 2026-07-06. No newer Security Master spec found on 2026-09-22.
- Only v4.1 delta vs v4.0.x: new Primary Market value `F` = TXSE Exchange (TXSE).
  Legacy `NYSE Chicago`-named fields (Traded, Orders Accepted, sessions, Trade Unit, closing prices)
  are retained verbatim in v4.1.
- Equity file: daily, every trading session, 12am midnight U.S. ET; TXT/XLS/XML.
  Filename `NYSEGROUP_US_REF_SECURITYMASTER_EQUITY_4.0_YYYYMMDD.ext` (note: `4.0` stem retained in v4.1).
  Delta `..._YYYYMMDD_delta.ext`: changes vs prior generation date, no closing prices.
  Delta rows carry `Change Type` (A/M/D) + `modified fields` (non-daily fields only).
- History: "Historical files will be available on an on-going basis from December 16, 2015 and onward."
- Delivery: SFTP `sftp.nyse.com`, `/NYSEGROUP_US_REF_SECURITYMASTER[_YYYY/YYYYMM]`;
  S3 bucket `nyse.taq.prod.rawfiles`, dir `5_NYSE_GROUP_SECURITY_MASTER`.
- YYYYMMDD is generation date; product is start-of-day for the next session. No row-level
  observed/valid/correction timestamps in the layout.
- Stock Number (field 1): unique Pillar identifier at listing, common across NYSE Group listing/trading
  markets, never reused after delisting; certain corporate actions may allocate a new number.
  Range 00001–80000. `Symbol Reuse Flag` (field 110) is still a future placeholder.
- Security Type (field 9) codes include A/B/C/D/E/F, ETF/ETN/ETS/ETV, G, H–L (ADR), M/N, P/Q/R/S, U.
  Note: "all Nasdaq and CBOE listed ETPs will have ETF as a default code."
- Primary Market (field 12) codes: N/A/P/Q/B/C/D/F(TXSE)/I/J/K/L/M/W/V/X/Y/Z/U.
- FTSE ICB fields (98–105) are best-endeavor, may be blank on listing day.
- `Listing Date` (field 71): initial NYSE Group listing date; for non-NYSE listings the first
  NYSE Group trade date may be used instead.
- Old symbol/SIP/CUSIP (fields 111–113): NYSE-Group-listed only, effective day only.

## Sample directory facts (2026-09-22)

- Equity FULL samples present: `..._EQUITY_4.0_20220927 / _20240328 / _20241202 / _20241203`
  in txt/xls/xml. (20220927 uses the `SECURITYMASTERPREMIUM` stem.)
- NO equity delta sample in the public directory. Deltas exist publicly only for floor files
  (FLOORDIR/CLEARNUM/BADGEDIR) and bonds full/delta pairs — not for equity.
- Directory mtimes (e.g. Dec-2024 files stamped 01-Dec-2025) are server publish times,
  not file generation dates; generation date comes from the filename stem.

## Pricing / contracts / trial (2026-09-22)

- No public fixed price for Security Master. `COMMERCIAL_QUOTE_REQUIRED` stands.
- Contracts index lists generic exhibits (Exhibit C Proprietary Data, vendor agreements, subscriber
  agreements) but no public text resolving backtest, retention, termination, or identifier rights
  for this product.
- No public trial / free-trial documentation found for Security Master. Evaluation access is itself
  a vendor question.

## What revalidation changed vs the prior audit

- Nothing material. Spec v4.1 still current; catalog claims unchanged; sample set unchanged
  (equity fulls only, no equity delta); entitlement-gated history/licensing still unproven.
- One clarification: public equity samples use the `4.0` filename stem and 157-field layout while the
  current spec is v4.1 — parsers must tolerate the stem/layout difference and the new TXSE value.
