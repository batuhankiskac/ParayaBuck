# Formula reconstruction audit

## Deterministic pilot

Fourteen predeclared issuers were selected before inspecting accounting
coverage: 12 operating-company cases spanning R&D, deferred revenue,
non-calendar and 52/53-week years, plus two current-SIC financial exclusions.
The cache contains 190 Company-Facts-linked 10-K accessions filed from 2013
through 2026-Q2 and 12 raw latest iXBRL documents.

Results:

- 78/190 accessions have acceptance timestamps in the current Submissions
  response; older accessions require archived-submission/header retrieval.
- 0/190 satisfy every deliberately narrow standard-tag plus comparative-instant
  discovery condition.
- 0/12 raw latest filings expose every required narrow standard tag family.
- all 12 expose revenue, COGS and total assets;
- only 6 expose combined standard SG&A, 8 standard R&D, 9 receivables, 8
  inventory, 9 deferred revenue, 11 AP, and 8 accrued liabilities;
- 0/12 expose narrow `PrepaidExpenseCurrent`.

## Raw-value reconciliation: Apple 2025 10-K

The deterministic raw-filing example is Apple accession
[`0000320193-25-000079`](https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm),
accepted `2025-10-31T10:01:26Z`. The filing imports US-GAAP 2025. All amounts
below are USD millions, parsed from raw inline-XBRL lexical values and scales.
The annual flow context is `2024-09-29` through `2025-09-27`; the balance-sheet
contexts are the dimension-free consolidated instants `2025-09-27` and
`2024-09-28`.

| Formula item | Raw concept | t | t-1 | Normalized use |
|---|---|---:|---:|---|
| Revenue | `RevenueFromContractWithCustomerExcludingAssessedTax` | 416,161 | — | `REVT` |
| COGS | `CostOfGoodsAndServicesSold` | 220,960 | — | `COGS` |
| Reported SG&A excluding sibling R&D | `SellingGeneralAndAdministrativeExpense` | 27,601 | — | `XSGA-XRD` |
| R&D sibling | `ResearchAndDevelopmentExpense` | 34,550 | — | proves `XSGA=27,601+34,550`; not double-counted |
| Receivables | `AccountsReceivableNetCurrent` | 39,777 | 33,410 | delta 6,367 |
| Inventory | `InventoryNet` | 5,718 | 7,286 | delta -1,568 |
| Deferred revenue total | `ContractWithCustomerLiability` | 13,700 | 12,800 | `DRC+DRLT` delta 900 |
| Accounts payable | `AccountsPayableCurrent` | 69,860 | 68,960 | delta 900 |
| Lagged assets | `Assets` | — | 364,980 | denominator |
| Other accrued liabilities, diagnostic only | `OtherAccruedLiabilitiesCurrent` | 44,452 | 44,024 | rejected as unproven `XACC` equivalent |

The arithmetic trail is:

```text
XSGA proxy           = 27,601 + 34,550 = 62,151
XSGA - XRD           = 62,151 - 34,550 = 27,601
OP                   = 416,161 - 220,960 - 27,601 = 167,600
known partial CbOP N = 167,600 - 6,367 - (-1,568) + 900 + 900
                     = 164,601
CbOP                 = null (fail closed; do not divide)
```

The filing has no narrow `PrepaidExpenseCurrent` fact, and the available
`OtherAccruedLiabilitiesCurrent` fact has no accession-specific proof of exact
`XACC` equivalence. Substituting broader current-asset/liability facts or zero
would change the paper formula. The sample therefore deliberately persists a
null CbOP rather than a fabricated score.

`STRUCTURAL_COVERAGE_AUDIT.json` retains every matching fact ID and value,
duplicate-consistency result, decimals, scale, unit, entity identifier, context
dates/dimensions, normalized value, formula arithmetic, filing URL/hash, issuer
CIK, accession, filed/accepted timestamps, retrieval time and taxonomy URIs.
No 10-K/A or fiscal-year-change accession was present in this
raw-latest mini-sample, so amendment/restatement and transition-year execution
remain untested; the prospective rules are documented, not claimed as runtime
proof.

## Interpretation

These are discovery and one-accession reconciliation results, not a statement
that every accession is unscoreable. Raw extension schemas and calculation/
presentation networks could validate some custom or aggregated disclosures.
That full census was not continued because the design independently fails the
modern long-only and concentration gates. No accounting value was joined to a
future return.

Exact rows, per-component counts, calculation trail and provenance receipts are in
`STRUCTURAL_COVERAGE_AUDIT.json` and are reproducible with
`run_structural_coverage_audit.py`.
