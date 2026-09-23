# Physical Clock V2 decision

`PHYSICAL_AVAILABILITY_CLOCK_V2_VALIDATED_WITH_LIMITATIONS`

`MIGRATION_REPLAY_READY`

This is a research infrastructure and causality decision. No cap-8 arm or 6-vs-8 economics was run.

## Starting repository state and publication

The accepted maintenance branch and fast-forwarded local `main` point to `b135d73743ec2652f79b8c840df50bd3f67166e8`. V2 was created as `research/physical-availability-clock-v2` from that clean main, not from the blocked V1 or shared-replay V2 branches. The accepted crypto repair has a local annotated archive tag, `archive/maintenance/crypto-canonical-causal-execution-v1-2026-09`, peeled to that exact commit. The pre-existing annotated V1 tag `archive/research/physical-availability-clock-v1-canonical-stop-2026-09` peels to `377d260835c5a5f8ea23b61e16bf8103a842b0e0` and records `PHYSICAL_CLOCK_REPAIR_EXPOSED_CANONICAL_BASELINE_CORRECTNESS_ISSUE`. The blocked V1 implementation was not merged.

The accepted crypto repair, updated canonical docs, and superseded 455/388 provenance are on local main. Remote publication of the new main, maintenance tag, V1 tag and V2 infrastructure ref is pending explicit approval for those exact refs. Nothing was pushed in this campaign.

## New authoritative crypto baseline and downstream state

`CRYPTO_CANONICAL_BASELINE_CAUSAL_REPAIR_ACCEPTED` at `b135d73743ec2652f79b8c840df50bd3f67166e8` is authoritative: 454 decisions, 406 fills. The complete daily signal is available after its final hourly component; a new order at `T` can first consume an actual source-hour OPEN strictly later than `T`. The first known BTC/ETH regression creates orders at `2021-04-02T00:00:00Z` and first permits `2021-04-02T01:00:00Z` source opens. The accepted control is `maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz`: gzip SHA-256 `8ec31d0aa7fd350bf011c5a00ac25cd64fb9be20240a001496bb6e8353792cae`, uncompressed payload SHA-256 `cdc3004dbbfe1e13e8f9f9deb5ec80ecbe628236210649a68a8f47aa55f33697`, fill SHA-256 `645d444ff3c95ba2f5eab1f0162acc903275e4642c70c79eb03b8248829e1fba`, and pinned hourly source SHA-256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`. Independent verifier: PASS.

The crypto giveback audit and old total-account comparisons that use the old fill episodes are `STALE_REQUIRES_RERUN`. Shared-account replay V1/V2 and physical-clock V1 are superseded provenance. Migration V1/V2's blocked or inconclusive conclusions remain valid as decisions; their old economics cannot authorize a new cap. Those downstream studies were not rerun here.

## Physical time model and equal-time order

`PhysicalReplayTime` separates market, source bar, label, availability, decision, order creation and optional execution price event. Equity daily bars become available at the official XNYS session close using installed `exchange-calendars==4.13.2`; next valid XNYS open is the execution event. This covers historical DST, holidays and early closes. The strategy still receives its original source session label for cadence and point-in-time universe membership. Crypto daily bars become available at the maximum completed component time, normally next UTC midnight. A source-hour open must be strictly after order creation; missing hours advance to the next actual eligible source hour, at most one attempt per UTC day.

At an equal physical timestamp: effective corporate actions precede open fills; old sells precede eligible old buys; precommitted terminal exits then run; completed bars update marks and risk; decisions run in fixed crypto-then-equity order; global slot admission and new orders follow. A new order cannot consume an earlier price event from that timestamp. Full policy is in `SAME_TIMESTAMP_ORDERING_V2.md`.

The equity action audit found 141,751 loaded cash actions, including five non-session ex-date rows mapped to the next valid XNYS open. The scheduler preserves split-adjusted source prices, canonical cash-dividend entitlement and terminal lifecycle amounts. Its full run applied 131,488 in-window action events and reconciled cash and position quantity. Payable-date settlement is not separately modeled by the source.

## Isolated physical parity

Equity: PASS against canonical V3 with identical source/universe identity; 495 target decisions, 2,054 fills, 5 terminal events and 2,406 curve points. Target hash `6773406fd5bdd37d64e20de1c6a252bb19987300fbc21591d92ca9de9370450e` and session-normalized fill hash `cb46efe20372da28f40626b7a86f2421cebb8761ee5d7b4350861eae6d142160` match the canonical control. All four session-normalized target, fill, terminal and chronology hashes match; development, validation and full economic metrics match within `1e-12`. Raw physical UTC event hashes intentionally differ from source-label hashes.

Crypto: PASS against the corrected causal baseline, with 454 decisions, 406 fills and 1,945 NAV observations. Decision and target sequences, order identities/creation, exact fill timestamps/prices/costs, account snapshots, curve, terminal behavior and final economic state all match. Internal reservation log ordering and risk-session metadata are non-economic representation differences, documented in `CRYPTO_PHYSICAL_PARITY_V2.json`.

## Combined cap-6 physical replay and causality

Starting flat with 100,000 cash, the shared account maintained one cash ledger, inventory, gross calculation and global slot state. `maximum_positions` was 6 throughout. `over_cap_timestamp_count=0`, maximum actual positions `6`, maximum occupied/reserved slot claims `6`, unowned fills `0`, duplicate fill applications `0`, and unresolved deferred buys `0`. There were 2,297 fills, 2,063 reservations created and 2,063 released, and 86 dividend-cash events. Cash reconciliation error was `-5.820766091346741e-11`; position quantity error was `0`. Target gross, sleeve and name limits passed, with maximum target gross `0.5500000000000002` and maximum target name weight `0.1`. NAV was used solely for ledger and risk reconciliation; no return attribution is made.

`CLOCK_CAUSALITY_RECEIPT_V2.json` passed 4,351 history checks plus 949 decision, 2,063 order, 2,297 fill and 131,488 action checks, with zero failures. Every decision uses completed data, every order follows its decision, every fill uses a later source event, equity fills occur at valid XNYS opens, and crypto fills follow its strict source-hour contract. No new generic PAPER/LIVE correctness issue was found.

## Cross-market reordering and determinism

Projecting the *same physical run's* completed-bar mark and decision events back onto archived label keys changes the relative order of 9,030 equity/crypto event pairs, involving 7,087 events; 56 decision pairs change order. This is a structural comparison only, without P&L. Archived label-clock V2 retained aggregate receipts but no full event trace. Exact scarce-slot first-claimant flips and fill-time shifts caused *solely* by clock semantics are therefore unmeasurable from that archive; the old crypto baseline also changed. These two unavailable counterfactual counts do not enter future cap-6-vs-8 slot ordering: both future arms must run this same validated physical scheduler and data identity. No old-clock result is promoted as an equality oracle.

The combined replay was run twice. All 216,141 event records, decisions, orders, fills, reservations, positions, cash path, NAV path and final state reproduced exactly. Both event SHA-256 values are `841091dd6ebedbc03e3dde27eaf7bee09e54474e660554c3c4b28423d3eb186d`. `CLOCK_DETERMINISM_RECEIPT_V2.json` contains component hashes.

## Validation, limitations and next action

Full pytest: 1,178 passed, 1 skipped, 2 warnings when rerun with local loopback socket permission. The first sandboxed run had 1,172 passed and six loopback-bind denials; those six passed in the permitted rerun. Focused physical-clock, causal crypto, equity, slot-accounting and open-order pagination tests: 55 passed. Ruff, Ruff format, configured strict mypy on production and research separately, and JSON parsing passed. `git diff --check` and credential-scan results are recorded in the final operator handoff. No broker API call or PAPER/LIVE order action occurred.

The hourly and daily historical sources do not supply broker queue or provider-publication latency. Official close and completed source-hour end are the research contract's earliest data-availability bounds. The shared replay is reusable only with its pinned inputs, code and cap-6 validation. Cap 8 remains a separate migration experiment.

The validated infrastructure is preserved under the annotated local ref `infrastructure/physical-availability-clock-v2-cap6-2026-09`. Its peeled commit is the exact code/data receipt for future work and must be verified when publishing. The exact next action is `FREEZE_ACCOUNT_CAPACITY_MIGRATION_8_V3_USING_VALIDATED_PHYSICAL_REPLAY`, in a separate authorized campaign.

Final local main: `b135d73743ec2652f79b8c840df50bd3f67166e8`. Final branch/tag target is the peeled commit of the infrastructure ref; record its exact SHA in the operator handoff after committing. Production remains at `maximum_positions=6`; equity V3, corrected crypto strategy parameters, rebalance band, and PAPER/LIVE modes are unchanged.
