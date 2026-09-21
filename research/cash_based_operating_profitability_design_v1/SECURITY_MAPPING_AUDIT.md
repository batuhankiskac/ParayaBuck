# Security and entity mapping audit

## Verdict

`SECURITY_MAPPING_FEASIBLE_FAIL_CLOSED`

Proposed key:

`filing CIK + accession acceptance time + as-filed DEI class title/symbol + active internal entity lifetime`

Required checks:

1. header CIK equals `dei:EntityCentralIndexKey`;
2. cover-page `TradingSymbol` and `Security12bTitle` refer to the same security
   context;
3. the title is accepted common equity, not debt/preferred/warrant/unit;
4. exactly one lifetime entity carries that historical symbol at acceptance;
5. the accumulated CIK/entity bridge is nonconflicting as of that date;
6. symbol reuse, simultaneous entities, multiple common classes, or unclear
   class identity fails closed.

Current ticker metadata is never historical proof. Microsoft filings are a
useful sentinel because common stock and notes can share the `MSFT` trading
symbol; ticker-only matching is insufficient.

Requiring a US-GAAP operating-company 10-K, nonfinancial filing-time SIC and
common-equity DEI class removes much ETF/ETN contamination. It does not solve
SPAC/shell, REIT, multiclass, ticker-reuse, or the full historical security
master. Unknowns remain excluded.
