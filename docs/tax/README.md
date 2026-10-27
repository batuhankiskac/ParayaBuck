# TL tax ledger: operator guide

This guide covers the `parayabuck tax` commands and the YTD figures in the daily
summary and the dashboard. The rules themselves are in
[turkish_tax_rules.md](turkish_tax_rules.md), the decision record. Change a rule
there first, then the code.

**These are estimates computed by software, not tax advice.** There is no tax
adviser behind them. The figures ignore other income and wage income, and the
rules may change. Check the final declaration with a Turkish tax adviser or a GİB
özelge.

## 1. Supply the rates

The ledger needs two CSV files. Their defaults, relative to the working directory,
are `data/tax/usdtry.csv` and `data/tax/yiufe.csv`. Other paths can be passed with
`--usdtry` and `--yiufe`.

`usdtry.csv` holds the TCMB USD buying rate by publication date:

```
date,usdtry_forex_buying
2026-01-02,43.10
2026-01-05,43.25
```

`yiufe.csv` holds the monthly Yİ-ÜFE index:

```
month,yiufe_index,source
2025-12,3100.5,TCMB EVDS
2026-01,3150.0,TCMB EVDS
```

The format matches the research copies kept at the git tag `archive/research/closeout-2026-10`
(a tag, not a directory; it is not on `main`).
Extra columns (for example `usdtry_forex_selling`) are ignored.

Each trade uses the rate published on the previous business day before the trade
date (rule 4). Weekends are skipped automatically. Public holidays are not known to
the software, so the latest published rate before the trade date is used, as long as
it was published within 10 calendar days before the trade date (a Turkish holiday
block can run nine days). If no rate falls in that window, the run fails and names
the trade date. The same applies to a missing Yİ-ÜFE month. Nothing is guessed.
Dividends, withholding and interest use the same rule, with the payment date as the
date.

### Alternative: download from TCMB EVDS

Get an EVDS API key from the TCMB EVDS portal, then keep it in the environment. Do
not write it into the settings file or the shell history:

```
export PARAYABUCK_EVDS_KEY='...'
parayabuck tax rates-fetch --start 2020-01-01
```

The command fetches USD buying (`TP.DK.USD.A.YTL`) and Yİ-ÜFE (`TP.TUFE1YI.T1`; not confirmed against the EVDS code list, override with `PARAYABUCK_EVDS_YIUFE_SERIES`, and check the fetched values against TÜİK before use). It
merges the results into the two CSV files: existing USD/TRY rows stay, and a fetched
rate replaces an older one for the same date. For Yİ-ÜFE the command refuses the run,
and writes nothing, when a fetched month already holds a different index (the series
code is not confirmed). Check the series code, or remove the differing rows first.
Rows keep their own `source` label. The key is sent in a request header, not in the
URL. Check the series codes in the EVDS data-code list before relying on them.

## 2. Import dividends and interest

```
parayabuck tax import-activities --since 2026-01-01          # PAPER credentials, PAPER ledger
parayabuck tax import-activities --since 2026-01-01 --live   # LIVE credentials, LIVE ledger
```

This reads the account activity types `DIV`, `DIVNRA` (non-resident withholding)
and `INT` (interest) with GET requests only. Each activity is stored in the
`tax_activities` table of the runtime database, keyed by its Alpaca id. Running the
command again is safe: existing rows are not changed. Only the table is written.

The ledger follows the credential mode. `--live` writes to the LIVE ledger
(`data/runtime/live/parayabuck.sqlite3` on the shipped config, the same path the
runtime uses for a LIVE configuration). The command refuses before any network call
in two cases: a PAPER config whose ledger is the LIVE ledger path (the PAPER
ledger itself, for example a custom path), and a PAPER import into a LIVE
configuration's ledger. Use a LIVE configuration for LIVE activities.

Not imported: `DIVCGL`, `DIVCGS` (capital-gain distributions) and `DIVROC` (return
of capital). The decision record has no rule for them, so they are missing from the
figures. The command output and every export note say so; check the broker's
year-end tax statement for them.

## 3. Export a year

```
parayabuck tax export --year 2026 --out data/tax/export/tax_2026.csv
parayabuck tax export --year 2026 --out data/tax/export/tax_2026_live.csv --live
```

Without `--live` the export reads the config's ledger; with `--live` it reads the
LIVE ledger (the same resolution as `import-activities --live`).

The command writes two files:

* `tax_2026.csv`: one row per lot disposal (section `disposal`), then one row per
  dividend (`dividend`), withholding (`withholding`) and interest (`interest`).
  Every input is in the row: dates, USD quantities and fees, both TL rates with the
  day they were published, index months and values, the indexation decision, and the
  TL gain.
* `tax_2026.summary.json`: net gain, estimated tax, dividends gross, withheld and
  net in TL, the dividend tax estimate, the interest tax estimate (reported
  separately), the credit cap, the declaration threshold flag, and notes.

Add `--account-id` if the ledger holds more than one account. By default the latest
account in the ledger is used. The export opens the database read-only.

The summary file is the figure to check first. The notes list anything that needs
attention, such as a net loss, withholding above the 20% treaty cap, or the income
types that are not imported.

## 4. Daily summary and dashboard

When the rates are available, the daily session summary ends with a line such as
`tax YTD 2026: net gain +9,700.00 TL, est. tax 1,940.00 TL`. Otherwise it reads
`tax: rates missing`. The dashboard has a "Vergi (TL, yıl başından beri)" card with
the same figures. The card is cached for one minute.

The YTD status also carries `income_row_count` and `notes`. When no dividend or
interest rows are stored (activities never imported), `income_declaration_status`
reads `veri eksik` (data missing) instead of a threshold verdict, so "threshold not
reached" is never shown for data that was not loaded. Other statuses are
`rates_missing`, `tables_missing` (no bracket table for the year), `ledger_error`,
`unavailable` and `error`.

## Limits

* Only the bot's own fills are used, FIFO per symbol. Shares the bot did not buy
  (manual trades, transfers in) make the export fail with a clear message.
* An ownership adjustment (splits, rebases, liquidations) makes the export refuse
  any year in which that symbol was sold. The adjustment rows carry no effective
  date, so the scope is the symbol: other symbols still report. Those cases need
  manual reconciliation first.
* The estimate applies the 2026 bracket table only (rule 6). Other years fail until
  a table is added to the decision record and `tax/rules.py`.
* Other income and wage income are ignored. The estimate of the dividend tax is
  marginal over the gain base; the interest tax is estimated on top of the dividends
  and reported separately.
* Credit for US withholding is capped at the Turkish tax on the dividends (rule 5);
  interest carries no US withholding and its tax is not creditable. The software does
  not check the W-8BEN; a withholding rate above 20% is only flagged.
* Capital-gain distributions and return of capital (`DIVCGL`, `DIVCGS`, `DIVROC`)
  are not imported (see section 2).
* Trade dates are Türkiye calendar dates (UTC+03). Public holidays are not known to
  the software (rule 4).
* A loss is not carried forward and not offset against other years (rule 3).
* Rule changes: edit the decision record first, then `tax/rules.py` and the tests.
  Keeping the per-leg rates and index months in each row makes a later
  recomputation possible without data loss.
