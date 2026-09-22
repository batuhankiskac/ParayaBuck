# Nasdaq / Cboe contingency — 2026-09-22

Do NOT contact Nasdaq/Cboe yet. Contact only if NYSE answers fail the gates below.

## Trigger for Nasdaq acquisition (Daily List + Fundamental Data)

- NYSE coverage answer (V9) shows Nasdaq-primary issues missing or partial for 2016–2026, OR
- evaluation sample fails ordinary-vs-ETF/ETN/preferred/warrant/unit separation for Nasdaq listings, OR
- Stock Number continuity cannot bridge Nasdaq ticker-reuse cases.
- Then ask Nasdaq the equivalent vintage set: all intraday versions retained? pre-correction values
  surviving? bulk 2016–2026 delivery method/fee? backtest/derived/termination rights? CUSIP terms?
- Known price anchor: $3,500/month/organization (official 2024 notice/filing). Re-quote current.

## Trigger for Cboe acquisition (BZX Listings, Distributions and Corporate Actions)

- 937 `BATS`-labelled canonical lifetimes cannot be reconciled from the NYSE sample/entitlement, OR
- NYSE disclaims BZX-primary coverage, OR
- BZX ETP/primary-equity rows fail taxonomy gates.
- Then ask Cboe the equivalent vintage set, explicitly: purchased-file immutability, replacement
  history, Updated/Cancelled lineage, full/event report pairing, CUSIP-version entitlement.

## Cross-validation-only use of current public data (no purchase)

- `nasdaqlisted.txt` / `otherlisted.txt` and Cboe public samples: current-snapshot cross-checks only.
  They are not historical evidence and never substitute for entitled vintages.

## Sequencing rule

NYSE first (single-feed lead with the only non-reused issue ID). Escalate to the
NYSE + Nasdaq + Cboe + SEC stack from the prior audit's multi-source architecture only after a
recorded NYSE HARD FAIL. Never purchase multiple products prematurely.
