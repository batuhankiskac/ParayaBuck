# Equity secondary causal/schema gate report — 2026-09-08

Research-only acquisition and schema/PIT gate. No economics, PAPER, LIVE, production/configuration or promotion action was run.

Status: `DONE_WITH_CONCERNS`; cutoff: `2026-07-31T23:59:59Z`.

| Candidate | Status | Key coverage | Blockers |
|---|---|---:|---|
| H06 | `PIT_PENDING` | publication_time_rows=3993 | the frozen 20-issuer Form 4/4-A inventory is complete and all 3,993 in-window rows have valid receipt-backed bodies; the local parser has one explicit unbound body outside the 3,993 valid-body denominator; routine-history/exclusion taxonomy, amendment linkage and historical CIK/ticker continuity remain external blockers |
| H07 | `DATA_ACCESS_BLOCKED` | rows=0 | no authorized historical analyst forecast vintages; no publication timestamps, analyst identity, horizon or stale-estimate policy; no exact provider archive through cutoff |
| H08 | `DATA_BLOCKED` | metadata_rows=1407 | 991 latest-attempt-valid bodies and eight preserved prior-valid/latest-invalid conflicts are receipt-backed local normalization evidence only; seven numeric text/table spans, 102 release-date spans and 19 teleconference spans remain explicitly non-admitted; no exact release_at or PIT publication timestamp is established; pre-release consensus vintages and surprise revisions are absent; post-2016 holdout and event-collision policy cannot be evaluated |
| H09 | `DATA_ACCESS_BLOCKED` | relationship_rows=2 | two receipt-backed named-counterparty rows remain excluded because counterparty identity and linked-firm event are absent; no PIT link vintage, direction, termination or event mapping is complete; current relationship labels would violate PIT |
| H10 | `PIT_PENDING` | acceptance_time_rows=333 | the expanded acquisition is terminal for 333 validated documents, but full and recent PIT filing breadth remains incomplete; three exact same-CIK/report-period 10-K/A pairs provide metadata-only causal revision-event evidence; section normalization and amendment breadth remain incomplete; CIK/security continuity and frozen signal direction remain incomplete |
| H11 | `DATA_BLOCKED` | nport_investment_rows=565 | the local H11 package parses one receipt-valid NPORT-P snapshot (565 rows, 516 identity groups, 49 duplicate occurrences) but accession/filed/accepted/publication/available_at are unbound; NPORT primary bodies are receipt-backed samples, not a complete historical fund-flow/holdings archive; no publication-lag and ownership-weight panel; flow/admission remains zero |
| H12 | `DATA_ACCESS_BLOCKED` | route_probe_rows=8 | all documented NYSE/ICE public index routes were probed; none returned dated NYSE Composite constituent or change rows; the public ICE product/platform pages describe membership and rebalance data access but route the historical package behind a purchase-gated data product; methodology defines rebalance announcement/effective timing but no historical membership event panel is available |
| H13 | `DATA_BLOCKED` | daily_bar_rows=11686832 | available data are daily OHLCV and cannot substitute for intraday transactions/quotes; opening/closing auction semantics are absent; historical SIP versus configured live IEX feed parity is unresolved |
| H15 | `DATA_BLOCKED` | deal_event_rows=1 | one receipt-backed DEFM14A row supplies lifecycle metadata only; announcement_at remains null despite two explicit date candidates; agreement 2024-03-12 and amendment/restatement 2024-05-20 are parsed; close/cancel/break/delist timestamps and linked terminal filings remain null; currency remains null/AMBIGUOUS for raw dollar consideration; cash-only terms and target security continuity remain unresolved |
| H16 | `DATA_BLOCKED` | spinoff_event_rows=0 | no receipt-valid Form 10/10-12B/8-K two-step spinoff sample was acquired; zero rows mean NO_RECEIPT_VALID_SAMPLE_ACQUIRED and NOT_GLOBAL_ABSENCE; distribution/ownership conversion and successor mapping are absent; generic successor/effective-date mentions are insufficient |

Every card freezes provider meaning, PIT identity, decision/availability boundary, formula/window/threshold state, execution shift, role, controls, cost 1x/2x/3x, full/recent/holdout cells and minimum breadth/history in the protocol. Missing causal fields fail closed; current snapshots and daily OHLCV are not substituted.

The legacy SEC fixture contains 56 original pilot documents: 20 Form 4 plus 36 filing documents (18 10-K and 18 10-Q). The expanded receipt and inventory are authoritative for acquisition counts; H10 currently reports 333 validated 10-K/Q documents and the legacy 56 total is retained only as fixture context.

Candidate-specific acquisition evidence: `27642` metadata rows; `6836` hashed original bodies; H09 scanned `1337` bodies with zero inferred relationship rows; H11 MCHI accepted-by-cutoff rows `1424`.

Sanitized source matrix: `data/research/external/equity_secondary_public_2026_09_08/source_matrix.json`; every zero-row card records an official/public path, status, hash field, coverage and PIT limitation.
