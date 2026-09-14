# H06 official-source feasibility — 2026-09-14

## Decision and boundary

**Primary status: `H06_BLOCKED_MULTIPLE_CAUSAL_REQUIREMENTS`.** Free official SEC records are sufficient to acquire and version the Section 16 facts, including July 2026, but the repository does not establish point-in-time common-security continuity or a split-consistent point-in-time market-cap denominator. In addition, the September 9 acceptance-time wording must be superseded by the conservative `H06_TIMING_V2` rule below. No economic calculation, subsequent price inspection, threshold choice, promotion, or PAPER/LIVE/registry change occurred.

## Existing H06 inventory

| Path or family | Classification | Finding |
|---|---|---|
| `docs/research/equity_h01_h16_standalone_overlay_protocol_2026_09_09_v1.md` | protocol/governance | Pre-outcome mechanism, 21-session aggregation, direction, and 63-session holding are frozen; causal inputs remain gated. |
| `scripts/research/acquire_equity_secondary_public_2026_09_08.py` and acquisition receipts referenced by the old package | acquisition / raw receipt | Bounded 20-issuer acquisition; raw external bodies are not tracked in this accepted checkout. |
| `scripts/research/normalize_equity_h06_form4_2026_09_09.py` | normalization | Receipt/hash validation, typed Table I/II parsing, and preliminary dispositions. |
| `docs/research/equity_h06_local_normalization_2026_09_09.md` | diagnostic result | Records 3,993 valid bodies, 10,314 rows, 274 preliminary P/A rows, 62 Form 4/A filings, and zero admission. |
| `tests/research/test_equity_h06_local_normalization_2026_09_09.py` | test | Historical normalization/count/receipt invariants. |
| `data/research_runs/equity_local_diagnostics_2026_09_09/*` | diagnostic result / stale for this decision | Contains retrospective diagnostics and was not opened or used in this audit. |
| This task's script, manifests, probe, tests, and document | governance / bounded acquisition receipt / test | Outcome-free frozen source contract. |

The old normalizer labels every non-amendment, non-derivative row satisfying `transaction_code == "P"` and `acquired_disposed == "A"` as `CANDIDATE_OPEN_MARKET_PURCHASE`. That branch does not test direct versus indirect ownership and does not require explicit open-market rather than private-purchase language. Therefore: `EXISTING_H06_PRELIMINARY_PURCHASE_CLASSIFICATION_NOT_FINAL_ADMISSION`. The 274 rows prove only preliminary parsing; routine status, amendment completeness, 10b5-1 treatment, identity, and market cap were not admitted.

## Official/free source path

The field-level matrix is `data/research_manifests/h06_official_source_matrix_2026_09_14.json`.

* **SEC Insider Transactions Data Sets:** free quarterly as-filed structured Forms 3/4/5 data from January 2006 onward. The accession, issuer, reporting owner, relationship, transaction, footnote, and (when applicable) `AFF10B5ONE` tables provide scalable row acquisition. Coverage comfortably precedes the required 2019 history, but bulk data do not supply an exact first-web-availability timestamp and 2026 Q3 was not posted in the captured page.
* **Complete submission `.txt`:** free immutable accession body with `<ACCEPTANCE-DATETIME>`, submission type, filing header, and embedded ownership XML. It is the primary receipt/version artifact and supplies fields omitted from an index.
* **`.hdr.sgml`:** free header representation of the submission metadata, used independently to recover/verify acceptance. SEC EDGAR timestamps are Eastern Time and must be converted using `America/New_York` for that date, not a fixed offset.
* **Daily/quarterly indexes:** free accession discovery and completeness ledgers by filing date/form. They do not replace acceptance time or the filing body. The captured 2026-07-31 master index contains 736 Form 4/4-A entries and proves that July can be enumerated without waiting for Q3 bulk.
* **`data.sec.gov/submissions`:** free issuer submission metadata useful for reconciliation. It is not a timeless ticker map and is not the authoritative first-public-availability clock.
* **Ownership XML:** free, machine-readable, as-filed transaction detail, ownership form, footnotes, and post-compliance 10b5-1 field. Each amendment remains a distinct accession/version.

The official Form 4 instructions define code `P` as an open-market **or private** purchase. Code P therefore cannot establish the frozen “open-market” mechanism by itself.

## Routine classifier and its distinct history universe

At the beginning of calendar year `Y`, owner identity is reporting-owner CIK (never name). An owner is partitionable only with at least one qualifying trade in each of `Y-3`, `Y-2`, and `Y-1`. A partitionable owner is `ROUTINE` when at least one calendar month occurs in all three prior-year month sets, and `OPPORTUNISTIC` otherwise. Missing any year yields `NON_PARTITIONABLE`. All year-Y rows inherit that frozen owner classification; year-Y and future transactions cannot affect it.

The primary Cohen–Malloy–Pomorski owner-history universe is lineage-resolved, point-in-time Form 4/4-A, non-derivative open-market-code transactions `P` and `S`; purchases and sales both establish an owner's recurring trade timing. This is intentionally broader than the purchase-only H06 signal gate. A trade-level classifier is not H06 primary and may only be introduced later as a separately declared robustness variant.

## Signal transaction taxonomy

Every signal row must be an active point-in-time lineage version, non-derivative Table I, code `P`, acquired (`A`), explicitly direct (`D`), with positive valid shares and price; the owner must be `OPPORTUNISTIC`; acceptance, decision availability, identity, 10b5-1, and open/private gates must all pass.

Official filing text/footnotes/explanation classify a P row as `OPEN_MARKET_EXPLICIT`, `PRIVATE_PURCHASE_EXPLICIT`, or `P_PURCHASE_AMBIGUOUS`. Only explicit phrases such as “open market purchase” or “purchased in open market transactions” pass. Private-placement/private-purchase language fails. Silence is ambiguous and fails. Market-price proximity is prohibited evidence. The bounded filing probe includes official explicit-open, explicit-private, and ambiguous P examples. When filing text contains conflicting private and open-market wording, the private exclusion takes precedence and fails closed.

## Rule 10b5-1

The amended Form 4/5 checkbox applies to filings on or after **2023-04-01**. Checked/`AFF10B5ONE=true` is excluded. An explicitly false/unchecked field under the applicable post-compliance schema may pass. Before the compliance date, a missing field is `10B5_1_DISCLOSURE_NOT_REQUIRED_AT_THE_TIME`, never false; primary H06 fails closed unless strong official filing text explicitly establishes that the transaction was not plan-based.

## Amendments

Originals are never overwritten. For each decision timestamp, only versions accepted on or before it are visible. A Form 4/A accepted at `T_A` can change future routine-history or signal interpretation at/after `T_A`, but cannot rewrite an earlier decision. Versions use `ORIGINAL_ACTIVE`, `AMENDED_VERSION_ACTIVE`, `SUPERSEDED_AFTER_AMENDMENT`, and fail-closed `AMENDMENT_LINEAGE_UNRESOLVED`. Accession, issuer CIK, owner CIK, transaction identity, original-filed date, and official amendment explanation are retained as linkage evidence.

## Acceptance and availability

`ACCEPTANCE-DATETIME` comes from the official complete submission/header. Filing date is not substituted. SEC acceptance and first public SEC.gov availability are not identical, and SEC supplies no official per-filing timestamp for the latter. The decision is `ACCEPTANCE_TIME_REQUIRES_CONSERVATIVE_DELAY_RULE`, not an equality assertion. `H06_TIMING_V2` permits no decision before the next NYSE open following one complete US federal business day after SEC acceptance. This is a versioned conservative governance approximation and does not relabel acceptance as observed availability.

## Identity, market cap, and sample design

Issuer CIK is the corporate anchor. Admission additionally requires an as-filed ticker/security title to map to a historical common-share-class interval covering decision time. Outcomes are `IDENTITY_EXACT`, `IDENTITY_AMBIGUOUS`, `IDENTITY_MISSING`, or `NON_COMMON_EQUITY`; current ticker alone is ambiguous. Deterministic design cases cover ticker changes, same-CIK continuity, reporting-owner changes, amendments, and multiple classes. The repository has no established interval source satisfying this contract, so security continuity is blocked.

The denominator requires decision-safe price times point-in-time, split-consistent shares outstanding (or an already frozen equivalent). Existing H01–H05 research explicitly leaves historical common-security continuity and price/share-basis equivalence unresolved. Today's shares and current metadata are prohibited. Status: `H06_BLOCKED_PIT_MARKET_CAP`.

## Coverage and bounded probe

Routine history begins `2019-01-01`; signals begin `2022-01-01`; cutoff remains `2026-07-31T23:59:59Z`. Acquire quarterly official bulk data from 2019 through the latest published quarter, then enumerate the full 2026-07-01..31 daily/quarterly indexes and download each accession `.txt`, header, and ownership XML. Reconcile index and archive accession sets; do not shorten the holdout. The receipt hashes the captured SEC bulk page, Form 4 instructions, July index, pre-compliance filing, post-compliance checked and false examples, indirect/direct evidence, an amendment, and an ambiguous-P purchase. This is feasibility evidence, not full acquisition.

## Local-package reclassification

The accepted snapshot does not contain the old external bodies/row JSON, so inventing exact field-level counts would violate provenance. The historical evidence fixes 274 preliminary P/A rows and 149 unresolved/non-unique amendment row references. With no re-readable official-text evidence, all 274 are fail-closed pending text reprocessing (`P_PURCHASE_AMBIGUOUS` operational disposition), all 274 lack the required complete three-year owner history and PIT identity proof, and zero are structurally admitted. Indirect, explicit-open/private, pre-compliance, and checked counts are reported as unavailable—not fabricated. These are diagnostic counts, not an economic sample.

## Blockers and next action

Free SEC sources make full filing acquisition feasible, including July 2026. H06 still cannot proceed to economics because (1) PIT security/share-class continuity is absent, (2) a PIT split-consistent market-cap basis is absent, (3) the prior local 274-row source package is unavailable for exact field reclassification and lacks full routine history, and (4) timing requires a protocol version change. Return this commit for independent blocker review; do not run acquisition or H06 economics.
