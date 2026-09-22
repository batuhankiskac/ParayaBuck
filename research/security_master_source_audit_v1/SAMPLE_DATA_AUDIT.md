# Sample data audit

## NYSE public equity samples

Legitimate source: [official sample directory](https://ftp.nyse.com/Reference%20Data%20Samples/NYSE%20GROUP%20SECURITY%20MASTER/). Retrieved 2026-09-22 to `/tmp`; vendor bytes were not committed.

| File | SHA-256 | Bytes | Rows | Fields/row |
|---|---|---:|---:|---:|
| `NYSEGROUP_US_REF_SECURITYMASTER_EQUITY_4.0_20241202.txt` | `4a2095caa6c8b4a8cc2a549c70c9992ca4c928be5a09d7817823e751e54beaaf` | 9,996,848 | 11,152 | 157 |
| `NYSEGROUP_US_REF_SECURITYMASTER_EQUITY_4.0_20241203.txt` | `458598295f6d5341ffac103237fb8f066f0c7f37b339a70294787474b2ddb567` | 9,997,328 | 11,158 | 157 |

Both parsed as Latin-1 pipe-delimited rows; Stock Number was unique in each file. From 2024-12-02 to 2024-12-03 there were 8 added and 2 deleted Stock Numbers, with 11,150 common. The large number of changed common rows is expected because full files contain daily market fields; identity comparison must ignore daily price/volume/quote columns.

The 2024-12-03 sample demonstrated: common 5,131; ETF 3,646; warrants 454; preferred 448; ADR common 403; funds 386; structured products 147; units 142; unknown 116; single-security ETF 110; ETV 66; rights 54; ETN 51; registered shares 4. Primary markets included Nasdaq 4,811; NYSE 2,917; Arca 2,236; Cboe BZX 860; American 331; IEX 3. It also contained 300 REIT issuer rows, 2,216 populated ETP segmentation rows and 1,544 populated leverage values. Old-symbol/SIP/CUSIP fields were populated on change-day rows. This proves schema parseability and cross-market/type breadth, not continuous history or original vintage.

## Cboe public sample

Legitimate source: [official sample ZIP](https://datashop.cboe.com/download/sample/247), retrieved 2026-09-22 outside the repository. ZIP SHA-256 `892ddb7e48ed48edcd35b93c0ead9294de711815e623dce2f81bfba969e0c2db`, sample date 2024-08-21.

It contained 769 listed rows, 29 distributions and 3 corporate actions. Listed types were 734 ETF shares, 12 commodity-based trust shares, 6 trust-issued receipts, 6 tracking fund shares, 5 primary equities, 2 warrants, 2 futures-linked securities, 1 managed portfolio share and 1 equity index-linked security. The no-CUSIP sample had no stable issue ID. Action/distribution rows sometimes collapsed detailed listed taxonomy to generic `ETF`; loaders cannot assume equivalence across report types.

## Nasdaq

No legitimate public historical Daily List or Fundamental Data sample was found. Live public current directories were not treated as historical samples.

## Result

Parsing and taxonomy structures are feasible. Identifier stability, corrections, class changes and original daily vintage cannot be validated from the public samples. Vendor evaluation data must include early-period full/delta/event files and a later correction sequence.
