# Current capability audit

Audit date: 2026-09-22. This is source and design research only.

## Starting state and boundaries

The local and remote production references were both verified at `16db2eaad9061a1669166ef9ab6717853337fafc`. This branch, `research/earnings-announcement-return-design-v1`, was created directly from that clean `main`. The completed buyback and management-guidance branches were neither merged nor modified.

The six required governance documents were read before design work. They preserve canonical V3, crypto, broad-universe limitations, the six-account-position constraint, and the standalone-before-combination policy. No production strategy, configuration, PAPER/LIVE state, scanner, risk limit or broker path is changed here.

## Repository capability

| Surface | Verified capability | EAR-specific gap |
|---|---|---|
| Daily market data | Canonical equity input contains split-adjusted OHLCV for 2016 through 2026-07-30, with 11,686,832 rows and 9,010 lifetime identities. Cash dividends are separate and split actions are not reapplied. | No earnings-event date/time, Compustat RDQ, CRSP PERMNO, PIT size, book equity, book-to-market or Fama–French portfolio membership. |
| Bar visibility | Historical views include only bars at or before the decision timestamp; PIT universe construction adds bars strictly before the selection timestamp. | A three-session EAR cannot be known before the final event-window bar completes. |
| Execution | Portfolio reports define decisions on completed bars and fills at the next tradable bar open. Fill logic rejects an order whose creation time is not earlier than the candidate bar. | No event-aware scheduler converts public release timestamps into the correct event session or waits for a complete EAR window. |
| Scanner | Current filters cover active/tradable status, exchange, price, 20-day median dollar volume, ATR, completeness, leverage/inverse policy and optional spread. | Current or scanner-derived eligibility is not a PIT 2016–2026 event universe; the broad `US_EQUITY` history is not common-stock-only. |
| Entity lifetimes | Effective intervals, historical/canonical symbols, rename chains and terminal events are represented; ambiguous terminal states fail closed. | The 16-field lifetime contract contains no issuer CIK or universal SEC-to-security-class bridge. |
| SEC research | Archived research demonstrates a bounded fail-closed CIK/as-filed-symbol/date/lifetime method for issuer filings. | There is no production or canonical historical Item 2.02 earnings-event corpus, release-time archive or Compustat RDQ panel. |
| V3 | V3 ranks 6/9/12-month residual momentum, requires positive 12–1 residual momentum, rejects the highest idiosyncratic-volatility tail, and sizes selected names by inverse volatility. | A short announcement reaction is mechanically capable of entering later momentum, but no local future-return or overlap economics may be inspected in this campaign. |
| Account | Settings allow six account-wide positions, 10% per name, 55% equity and 5% crypto allocations within 60% gross and 40% cash-buffer limits. V3's internal `top_n=8` does not override the account cap. | A research candidate would need an explicit one-slot or up-to-six ownership contract; none is frozen after a non-READY decision. |

The current bars can support a future contemporaneous event-window return calculation, but they cannot identify an event or its exact paper benchmark by themselves. Opening the price corpus was unnecessary for this design audit; no candidate EAR or rank was computed.

## Production validation scope

The existing full normal test suite and protected-path isolation check are the relevant product safety checks. They are not evidence that an EAR dataset, event clock or strategy exists. Final observed results are recorded in the final report and no unrun check is described as passing.
