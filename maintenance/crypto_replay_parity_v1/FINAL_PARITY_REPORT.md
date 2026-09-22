# Executive conclusion

**CRYPTO_HISTORICAL_BASELINE_SUPERSEDED**. The previous fresh replay was not nondeterministic and the historical control was not fabricated. Both are reproducible, but they describe different observation-context semantics.

The historical engine scored eligible assets outside its streamed observation set. Commit `ac58f34c3d9242150f28c59aedf69a60b6992ab9` subsequently restricted history visible to the strategy to the effective observation set, including stream capacity and managed-position reservations. The historical control predates that correction. On the same frozen hourly scanner environment, the first consequential chain is SHIB omitted from visible candidates → different ranking → different target → different fill and NAV. The old and new return difference is downstream evidence, not the diagnosis.

No production fix or strategy change was necessary. The new independently verified baseline preserves the original hourly scanner input, complete daily strategy aggregation, source bytes, window and configuration. Its 455 decisions and 388 fills exactly match the previously quarantined fresh-path result. **CRYPTO_GIVEBACK_AUDIT_READY_FOR_RERUN**; no giveback conclusion is drawn here.

# Starting repository state

Local `main` and freshly fetched/pruned `origin/main` both equal `754f4060b1eb38593201b2a269e9593d083eb534`. The working tree was clean; main was checked out before refs/tags were fetched and `maintenance/crypto-replay-parity-v1` was created from main. Neither unrelated research branch was used as a starting point or merged. Runtime configuration is PAPER, LIVE disabled with hard cap zero. Exact starting config, data path and archive inventory are in STARTING_STATE.json and HISTORICAL_CONTROL_INVENTORY.md.

# Current crypto strategy contract

`crypto_low_variance_hysteresis_v1` version 1.0.0: daily bars; trailing 22 simple returns from 23 finite positive closes spanning exactly 22 calendar days; sample standard deviation (n−1), ascending volatility rank, symbol tie-break; retain eligible held names through rank 4, retain best ranks first, fill vacancies from rank 1, at most two selected names, equal raw weights. Cadence is UTC epoch-aligned 96-hour blocks, not an entry-age lock. Max crypto allocation 0.05 and two crypto positions; account gross/cash/slot limits apply. No profit-dependent exit added or tested. See CURRENT_STRATEGY_CONTRACT.md for actual implementation, sizing, missing data, order bands and risk semantics.

# Historical control inventory

Twelve artifact families were examined across durable documents, old research files, Git history and archive tags. The strongest old control is `data/research/crypto_t1_promotion_2026_08/parity_audit.json` introduced at `0d792dcd4468971701517b06dd8c10d4710f2cf3` with `scripts/audit_crypto_t1_promotion_parity.py`. The inventory explicitly records code/data/config identity, dates, hashes, independence and authority; earlier strategy-v1/v2, candidate tournaments, summary-only controls and unrelated archive artifacts are not interchangeable canonical controls.

# Historical-control provenance

The archived source was extracted into a temporary directory and run read-only, without switching this worktree or restoring old code into production. It reproduces the original PIT hash and **all six historical hashes** exactly: selected-symbol proxy, retention, selected weights, fills and NAV. The old artifact's “ranking” hash is only selected-symbol history, not independent variance/rank verification. Its original research/production comparison shared the same execution engine. Embedded governance HEADs are not assumed to be its execution source; exact reproduction establishes the source association.

Old environment: same frozen expanded hourly CSV, hourly PIT scanner, complete daily strategy bars, April 2021–July 2026 window, $100,000 cash/empty isolated account, configured costs. It did not include equity slot claims. Historical files are preserved unchanged as SUPERSEDED_HISTORICAL_CONTROL, not overwritten to resemble current output.

# Fresh deterministic replay

| Environment | Decisions | Fills | Ending equity | Authority |
| --- | ---: | ---: | ---: | --- |
| Archived source + original hourly scanner | 453 | 454 | $110,539.89682212 | Reproduced superseded control |
| Current source + original hourly scanner | 455 | 388 | $105,481.51853584 | New canonical v2 |
| Current source + public daily scanner default | 477 | 436 | $110,781.58025724 | Separate adapter diagnostic only |

Each current adapter was run twice. Canonical hourly payloads and deterministic gzip bytes match exactly. The daily adapter was **not** selected despite its higher return: it changes eligible membership and violates continuity of this frozen scanner environment. CANONICAL_REPLAY_RECEIPT.json stores complete config, normalized bars, environment, hashes, state and verification; raw replay files persist decisions, eligible/visible histories, scores, ranks through ordered score lists, targets, orders, fills, costs, positions and NAV.

# Earliest divergence

| Stage | First mismatch | Evidence / consequence |
| --- | --- | --- |
| Raw bytes / normalized daily bars | None | Same SHA and all 28,890 daily bars |
| Scanner eligible pool / scanner scores | None | All 2,037 hourly snapshots equal |
| Observation ordering/schema | Warmup | Old set serialization versus ordered/capacity-aware snapshots; not immediately economic |
| Full strategy context | 2021-04-01 | Current excludes ineligible MKR; both rankings/targets still agree |
| Actual snapshot observation membership | 2022-09-17 | UNI absent in current observation membership; pool unchanged |
| Eligible context | 2023-03-28 | SHIB pool-eligible but absent capacity-limited context; not yet rankable |
| 22-day scored list / rank | 2023-04-09 | Historical SHIB sigma 0.024949160469622127, rank 3; current does not observe it |
| Economic target / order | 2023-05-11 | Historical BTC+SHIB (SHIB sigma 0.018930346236856514, rank 1) versus current BTC+ETH |
| Fill / position / economic NAV | 2023-05-12 | Historical SHIB purchase versus current ETH purchase |

Every common-symbol trailing history at every common decision is equal. The variance formula did not change. Order IDs/universe identity can differ before economic quantities; comparison.py separates economic fields from metadata. Two additional current decisions (2023-06-24 and 2025-01-20) arise after this upstream split through changed completeness/observation state, not a new 96-hour clock. FIRST_DIVERGENCE_AUDIT.json persists exact before/after rows and missing timestamp sets.

# Root cause

- **STALE_HISTORICAL_BASELINE**: the control predates generic observation-parity maintenance.
- **UNIVERSE_DRIFT**: specifically observation membership and capacity-limited visibility. The underlying scanner-eligible universe is unchanged in the same-hourly comparison.

The root cause is not dataset drift, changed 22-day alpha, hysteresis bug, cost drift or shared-account interference. Configuration identity metadata changed through the strategy promotion, but the explicitly instantiated T1 parameters and relevant crypto risk/execution/cost values are the same. There are genuine economic differences, so SERIALIZATION_ONLY_MISMATCH is not the overall explanation.

Separately, `a20379991b848977200be79bc3867f77fdec65a4` replaced duplicate final NAV timestamps with an in-place final settlement mark: old curve 1947 points with a repeated last timestamp versus current 1946 unique points. This secondary bookkeeping/serialization change affects direct curve comparisons and potentially path-derived metrics, but it does not cause the earlier SHIB target divergence. No later fill/cost/NAV difference is counted as a separate bug merely because its value differs.

# Data differences

None for the common historical comparison: raw SHA `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`,50,157,874 bytes,705,541 hourly rows,25 symbols; no duplicate keys, out-of-order rows or nonpositive OHLC;220,499 zero-volume rows. Aggregation produces 28,890 complete daily bars; incomplete hourly days are discarded, zero-volume bars are not missing bars. Independent CSV-to-daily verification covers every normalized row. No appended future data, API refresh, backfill or revised cache was substituted.

Provider limitation: the exact expanded CSV's original download receipt is unavailable. Adjacent dataset documentation is not treated as proof of this file's provider/exchange completeness. Historical broker active/tradable status, renames/delistings and omitted-provider assets cannot be inferred beyond the supplied data and configured pool. DATA_PROVENANCE_AUDIT.md and DATA_AUDIT.json preserve these limitations.

# Universe differences

Same hourly contract: identical configured-pool intersection, scanner eligibility, scanner scores and elimination reasons across all 2,037 snapshots. Current ordered observation metadata and managed reservations change what the strategy sees. Independent eligibility code checks 27,162 candidate assessments and 308 configured-pin bypasses with zero mismatches; pins BTC/ETH bypass scanner filters only where trailing bars exist.

The current public daily scanner adapter differs on 766 common-window eligible snapshots. It is retained as a distinct diagnostic, not allowed to silently replace the historical hourly environment. Current hourly adapter's observation ranker receives hourly history while T1 requires daily closes; it therefore falls back to scanner observation ordering. This is now explicit in the frozen replay contract. It is not claimed identical to PAPER's separately warmed daily observation ranker. Correcting/changing that adapter would change the environment and is outside this no-universe-change task.

# Configuration differences

CONFIG_DIFF.json contains a full fieldwise comparison. Changes include v2 → T1 selected/observation identity, equity allocation 0.60 → 0.55, and observability defaults. The archived promotion runner explicitly instantiated T1, so its configured v2 name is not evidence it ran v2.22/2/96, frozen rank 4, crypto allocation 0.05, crypto limits, gross/cash limits, minimum notional, turnover band, participation and fee settings remain the pertinent controlled values. Equity allocation cannot affect this isolated empty-equity replay. No config was edited.

# 24/7 time-boundary analysis

Daily buckets are UTC-midnight labeled completed calendar days. The source's UTC timestamps imply no DST transition. The 96-hour schedule uses floor(epoch_seconds/345600) transitions and first-observation scheduling, not four calls or 96 elapsed hours since entry. A nonaligned first observation may be followed by a decision in less than 96 elapsed hours. Warmup/start/completeness determine whether a scheduled decision can occur. Historical and current definitions agree. Tests cover 24/48/72/exact 96/>96 hours and nonaligned starts. No time rule was redesigned.

# Hysteresis analysis

Retention is stateful rank eligibility at scheduled decisions, not a profit rule or fixed minimum holding period. The independent verifier reconstructs all 815 retained holds,57 vacancy acquisitions and every target change/full exit. Fixtures cover rank 4 retention, rank 5 displacement, eligibility loss, replacement and missing daily windows. Historical/current ranking-input visibility differs; identical hysteresis math receives different candidates and holdings later. No hysteresis parameter change is warranted or made.

# Execution / costs

Signals and targets use completed-day data; order quantities use the decision mark; pending orders fill only on later bars at open plus adverse 2 bps slippage and half of 2 bps spread.25 bps taker fee applies to executed notional; zero fixed commission. Positive-volume history observable at order creation controls 10% participation; partial residual orders age forward; new targets cancel/recompute stale pending orders; insufficient cash skips a buy instead of clipping it. Band 0.005 and minimum $10 remain unchanged. The independent simulator reproduces every order, 388 fills,spread/slippage/fee amount,partial fill and final liquidation. Full settings and secondary final-NAV bookkeeping change are in EXECUTION_COST_AUDIT.md and receipts.

# Shared-account versus isolated-replay analysis

Both compared controls are **CRYPTO_STRATEGY_ISOLATED_CANONICAL**: starting cash $100,000, no equity positions or slot claims. Account-wide six-slot and gross/cash rules still exist, but no equity occupies them here. This is not **TOTAL_ACCOUNT_SHARED_CANONICAL**. No shared-account historical ledger was found and none was synthesized. Isolated crypto metrics must not be presented as a live combined-account expectation.

# Correctness repair

No current production correctness defect needed repair. Only maintenance instrumentation, independent verifiers, tests and baseline documentation were added. Historical code remains in an isolated temporary reconstruction; no old visibility bug was restored to current production. Changing the standard strategy or its parameters to recover old hashes would be incorrect.

# Historical baseline status

**SUPERSEDED_HISTORICAL_CONTROL**. Original artifact retained intact with exact source/date/data/config provenance and an exact historical reproduction. The result remains valid as an old-code observation; it is not a current-production parity oracle.

# New canonical crypto baseline

**CRYPTO_CANONICAL_REPLAY_VALID**, contract v2 in docs/research/canonical_crypto_baseline.md. Main source `754f4060b1eb38593201b2a269e9593d083eb534`; same hourly scanner environment/current visibility; no strategy promotion.

| Identity | SHA256 |
| --- | --- |
| Target | `ab52ec2e09586462504dbe631dec51585aff18603c03254c0a5102197dc325d4` |
| Fill | `c8bf27f1d21846bf754a53a72e5da29416c79dd817162606cecc6ce19fe1e07b` |
| Final state | `e09c993fd46b81bfdf9878feb0e13079bc84e18aabbaa1b0bf7e533f980c7964` |
| Daily normalized bars | `92f9b745eb925e9994c184b712ac500e82be973be38839b8bd93aab7a60d54f9` |
| Full payload | `016bc06a7381d19434e6d21b4bafc40f1890abee57b7ead71a9b684ab40fb8dd` |
| Gzip bytes, both runs | `251798653eb28bcfb3e5d5080ebc1c347b979d802d340d1875e46c693bea2b01` |

# Independent verification

PASS, without importing production scoring/strategy/risk/execution/accounting implementations: source hash and all 28,890 daily bars,455 decisions,388 fills,1,946 NAV observations,815 retained holds,57 vacancies,55 full exits plus forced final liquidation, complete positions/cash/final state. Floating comparisons use relative 1e−10/absolute 1e−8 tolerances; repeated main-run bytes use exact equality. The separate standard-library eligibility verifier checks all 2,037 snapshots and27,162 candidates.

Scope boundary: scanner quality-score/correlation ordering is not independently reimplemented. Its exact unchanged historical/current score records, causal source windows and independent eligibility checks are evidence; current strategy volatility ranks and observation visibility are independently recomputed. Provider historical tradability is unavailable, not silently validated. No new profit-retention interpretation was performed.

# V3 safety

Production `src/` and `config/` are byte-unchanged versus main; tree identities are in PRODUCTION_ISOLATION.json. Full tests include existing V3 contracts. A fresh V3 canonical replay was **not rerun**, since this task makes no runtime or production configuration change; the user's conditional runtime-change gate is not triggered. No claim is made of a new V3 replay run.

# Production impact

No forward PAPER decision change results from this branch. Existing PAPER holdings were not queried, altered or rebalanced; no broker order was placed. LIVE remains disabled. No operator review before the next decision is required by these documentation/evidence-only changes. The current code already contains the observation correction being documented.

# Main changes

None. Main remains `754f4060b1eb38593201b2a269e9593d083eb534`. Maintenance scripts and diagnostic evidence are retained on the requested maintenance branch; no blanket merge of them into main was performed. The durable baseline document is prepared on that branch. Unrelated research branches remain untouched.

# Tests

Full pytest: **1163 passed,1 skipped,2 existing deprecation warnings**.25 new focused tests cover strategy/variance/rank,96-hour boundaries, hysteresis/missing data,normalization,independent target/order/fill/cost/NAV tamper detection,and independent universe eligibility/no-lookahead. Ruff,format,mypy,strict JSON/gzip validation and git diff checks pass; exact commands/results in VALIDATION.json. Source and maintenance modules are included in mypy. All work is offline; local socket capability was used by existing web tests.

# Final parity conclusion

**CRYPTO_HISTORICAL_BASELINE_SUPERSEDED**.

# Exit-audit handoff

**CRYPTO_GIVEBACK_AUDIT_READY_FOR_RERUN**. The previous quarantined path's455 decisions/388 fills and ending NAV agree with the now-authorized environment. Its previous inconclusive finding was appropriate while authority was unresolved. Do not reinterpret its missing episode diagnostics as already complete: rerun the separate lifecycle reconstruction and independent verification using canonical v2. No take-profit,trailing-stop,exit optimization or alpha experiment belongs to this task.

# Files

All required evidence: maintenance/crypto_replay_parity_v1/, including HISTORICAL_CONTROL_INVENTORY.md,CURRENT_STRATEGY_CONTRACT.md,DATA_PROVENANCE_AUDIT.md,CONFIG_DIFF.json,UNIVERSE_DIFF.json,FIRST_DIVERGENCE_AUDIT.json,TIME_HYSTERESIS_AUDIT.md,EXECUTION_COST_AUDIT.md,ROOT_CAUSE_DECISION.json,CANONICAL_REPLAY_RECEIPT.json,INDEPENDENT_VERIFICATION.json and this report. Full compressed observations are preserved for historical/current/daily-comparator runs. Durable contract: docs/research/canonical_crypto_baseline.md.

Reproduction from repository root with existing pinned CSV:

```sh
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY.json.gz
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY_B.json.gz
.venv/bin/python maintenance/crypto_replay_parity_v1/verify.py maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY.json.gz --out maintenance/crypto_replay_parity_v1/INDEPENDENT_VERIFICATION.json
.venv/bin/python maintenance/crypto_replay_parity_v1/universe_verify.py maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY.json.gz maintenance/crypto_replay_parity_v1/UNIVERSE_HOURLY_INDEPENDENT_VERIFICATION.json --hourly-csv data/historical/crypto_expanded_1h.csv
.venv/bin/python maintenance/crypto_replay_parity_v1/comparison.py
.venv/bin/python -m maintenance.crypto_replay_parity_v1.establish_baseline
```

Historical reconstruction: extract `git archive 0d792dcd4468971701517b06dd8c10d4710f2cf3 src config` into an isolated directory; pass that directory as runner.py `--source-root`, keep `--universe-timeframe 1h`, and write HISTORICAL_REPLAY.json.gz. The runner's explicit T1 construction reproduces the archived promotion harness; never switch/replace current production source to recover old results. The comparator also expects the separately persisted daily-adapter payload.

# Final branch/main SHAs

Main/origin-main source: `754f4060b1eb38593201b2a269e9593d083eb534`. Final maintenance branch SHA is supplied in the delivery message and can be obtained with `git rev-parse maintenance/crypto-replay-parity-v1`. A commit cannot embed its own resulting hash; the source SHA is not mislabeled as the final deliverable SHA.
