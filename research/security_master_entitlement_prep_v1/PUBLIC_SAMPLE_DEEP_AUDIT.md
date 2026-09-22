# Public sample deep audit — NYSE Group Security Master equity — 2026-09-22

Source: only legitimate public samples at
`https://ftp.nyse.com/Reference%20Data%20Samples/NYSE%20GROUP%20SECURITY%20MASTER/`.
Retrieved 2026-09-22 to `/tmp/nyse_entprep`; vendor bytes NOT committed.

## Files inspected

| File | SHA-256 | Bytes |
|---|---|---|
| `NYSEGROUP_US_REF_SECURITYMASTER_EQUITY_4.0_20241202.txt` | `4a2095caa6c8b4a8cc2a549c70c9992ca4c928be5a09d7817823e751e54beaaf` | 9,996,848 |
| `NYSEGROUP_US_REF_SECURITYMASTER_EQUITY_4.0_20241203.txt` | `458598295f6d5341ffac103237fb8f066f0c7f37b339a70294787474b2ddb567` | 9,997,328 |

Generation dates: 2024-12-02 (Mon) and 2024-12-03 (Tue). Filenames are the generation-date claim;
server directory mtimes are not evidence.

## Structure

- Latin-1 pipe-delimited, 157 fields/row in both files, uniform width (no ragged rows).
- Rows: 11,152 (12-02) / 11,158 (12-03), including test symbols.
- Stock Number unique within each file. Added 8 / deleted 2 / common 11,150 Stock Numbers day-over-day.
- The sample directory contains equity FULL files only — no public equity delta sample exists,
  so A/M/D + `modified fields` semantics cannot be exercised from public data.

## Identifiers (2024-12-03, test symbols excluded, n=11,106)

- Stock Number: unique, non-blank.
- Stock Symbol / SIP Symbol: populated on all rows (suffix form differs: `AACT WS` vs `AACT.WS`).
- CUSIP: populated on all non-test rows (0 blank). 52 blanks in the raw file are all test symbols.
- ISIN: populated except 124 non-test blanks.
- Old Stock Symbol / Old SIP / Old CUSIP: populated on 4/4/6 non-test rows respectively — change-day
  rows exist in the sample (e.g. Stock Number 70990 CPLB shows Add/Chg `Y` with old values ESGB).
- `Symbol Reuse Flag`: 0 populated (placeholder confirmed unpopulated).

## Venue (2024-12-03 non-test)

- Primary Market: Q 4,803 / N 2,897 / P 2,226 / Z 858 / A 322. No V rows among non-test
  (3 V rows in the raw file are test symbols). All five canonical labels (NASDAQ/NYSE/ARCA/BATS/AMEX)
  map to present codes, but a two-day public sample is not a coverage proof.

## Taxonomy (2024-12-03 non-test)

Security Type: A 5,098 / ETF 3,640 / C 454 / B 447 / H 403 / P 386 / G 147 / R 142 / ETS 110 /
U 104 / ETV 66 / D 54 / ETN 51 / M 4.
Issuer Type: blank 5,662 / Operating company 2,333 / Trust 2,215 / Closed end fund 433 /
REIT 300 / Limited partnership 63 / SPAC 42 / BDC 28 / Other 18 / Royalty trust 12.

- Intersections needed for research exist: ADR common (H) 403, REIT-common = A × REIT issuer,
  preferred (B) 447, warrant (C) 454, right (D) 54, unit (R) 142, fund (P) 386.
- Unknown (U) 104 rows: explicit unknown exists and must stay fail-closed.
- Arca-only ETP fields: leverage populated on 1,544 rows (1x 1,345 / 2x 68 / 3x 42 / -2x 39 /
  -3x 24 / -1x 20 / 1.5x 4 / 4x 2); ETP Segmentation 1 populated on 2,216 rows
  (EQUITY 1,568 / FIXED_INCOME 444 / COMMODITIES_AND_FUTURES 104 / HYBRID 86 / CURRENCY 14);
  Segmentation 5: INDEX 1,173 / ACTIVE 867 / LEVERAGED 98 / INVERSE 78.
- Non-Arca ETP caveat stands: spec defaults all Nasdaq/Cboe ETPs to `ETF`; segmentation/leverage
  fields apply to Arca-listed only, so non-Arca leveraged/inverse resolution needs vendor confirmation.
- IPO Flag: N 11,098 / E (new ETF/ETN/ETV) 7 / I (IPO) 1.
- Test symbols: 52 rows (`Test Symbol Flag` Y), excluded from counts above.

## Status / lifecycle fields present

- Add/Chg Symbol Indicator: 4 non-test rows. Financial Status, SSR, LULD, Halt fields present per spec.
- Ex Corporate Actions / Split flags present (new 2024 encodings). Listing Date present with the
  documented non-NYSE first-trade-date caveat.

## What this proves — and does not prove

PROVES: 157-field pipe schema parses; Stock Number unique in-file; identifier/taxonomy/venue/status
fields populated as documented; change-day old-value rows occur; cross-market/type breadth exists.

DOES NOT PROVE: 2016–2026 archive availability; original historical vintages; immutability or
correction-arrival lineage; delta-file semantics (no public equity delta); commercial storage,
backtest, retention, or identifier rights; complete canonical coverage; historical field population
(spec versions 2.x–4.1 evolved: delta files, Pillar, National, Chicago/Texas, LTSE/MEMX/MIAX prices,
ETS code, flag re-encodings, TXSE value).

No returns or backtests were run. No derived canonical crosswalk is claimed.
