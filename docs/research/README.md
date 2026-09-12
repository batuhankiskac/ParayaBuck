# Current Research Records

This is the current human index and lifecycle policy for research records. It
separates generated local runs from canonical evidence and immutable history.
An **accepted** record is accepted into the research record; it is not an
economic promotion decision and does not make a candidate PAPER-eligible.

## Authoritative locations

- `docs/research/<research-id>.md` — concise human summary and limitations.
- `data/research_manifests/<research-id>.json` — predeclared machine contract.
- `data/research_receipts/<research-id>*.json` — compact control or attestation
  receipt.
- `data/research/<research-id>/**` — only manifest-required, hash-pinned full
  results.
- `scripts/research/<research-id>.py` — retained runner implementation.
- `tests/research/test_<research-id>.py` — focused mission and regression
  contract while research is active; redundant intermediate modules may be
  compacted after a canonical frozen closure.
- `research_runs/<run-id>/**` — ignored, generated, resumable local bundle.
- `docs/archive/**` and `data/research/archive/**` — immutable historical
  lineage.

### Research-script lifecycle

- Active work uses `scripts/research/<mission>.py` and its focused current
  contract.
- Closing a mission does not rewrite its evidence: manifests, receipts,
  reports, result files, datasets, and exact path or identity pins remain
  immutable.
- A minimal final verifier may remain with a closed campaign; an unpinned
  runner may be archived, while redundant intermediate or scratch scripts may
  be deleted when no retained consumer or unique invariant depends on them.
- Immutable evidence is the historical record; every old executable need not
  remain in the active script surface.

## Test lifecycle

`pytest` is the active regression suite: it includes production/current
invariants and the current research contracts, while excluding tests marked
`historical_research`. Current research contracts are also marked `research`,
so they can be selected explicitly with:

```bash
pytest -m "research and not historical_research"
```

For ordinary iteration, use the fast current-research tier:

```bash
pytest -m "research and not historical_research and not r46_deep"
```

This retains the standalone R46 frozen-source pin guard and the cheap
`R46_FAST_ALWAYS`/`R46_PIN_ALWAYS` contracts. The full command above remains
the complete current-research suite and still runs every R46 test. The
externally registered `r46_deep` tests run on R46-relevant changes and on the
scheduled or manual deep workflow; tier metadata is maintained in
`tests/conftest.py`, and full/default collection fails if a registered R46
selector becomes stale.

Closed, superseded, and retrospective research tests remain in their original
paths and are still runnable without moving or rewriting their evidence:

```bash
pytest --include-historical-research -m historical_research
pytest --include-historical-research tests/research/test_x.py
```

Active research keeps its focused tests. Once a campaign has a canonical frozen
closure, redundant intermediate modules may be compacted: the canonical
manifests, receipts, reports, result records, and data remain immutable, while
one final closure/verifier contract may replace many intermediate regression
modules. Deleted implementations remain recoverable from Git history.

The classification is maintained in `tests/conftest.py`. The current allowlist
covers the reusable research platform, the P0/P1 execution-cost contracts,
profit-protection R2, the canonical terminal-engine guards, and the active R46
contract. Other mission-specific tests under `tests/research/` default to
historical until their status is reviewed. The explicit option is required for
marker-based historical collection so those modules can remain excluded before
import. Use it with an explicit path when a path-oriented invocation is clearer;
a directly named test file is also an explicit pytest request.

Normal directory collection skips historical modules before Python import, so
they do not appear as deselected items.

CI runs `pytest -m "not research"` on Python 3.12, 3.13, and 3.14, then runs
the fast current-research command once on Python 3.13. The separate Deep R46
workflow runs the marked change-sensitive and periodic contracts for
R46-relevant pushes or pull requests and on its weekly/manual triggers. Deep
verification is deferred, not deleted; the immutable manifests, receipts,
reports, and archived result files remain available for full reproduction.

## Lifecycle

| Status | Meaning |
|---|---|
| `planned` | A manifest exists; execution has not started. |
| `running` | Execution is in progress; generated run data is not canonical. |
| `accepted` | The completed record is canonical and documented. |
| `rejected` | The completed result failed its tested gates or hypothesis. |
| `superseded` | A later mission or correction replaces the record; the original remains immutable. |

`verdict` and `promotion` are separate fields. A record can be accepted while
its tested candidate is rejected or remains ineligible for PAPER.

## Current records

### `execution_cost_transferability_p0_2026_09`

**Status:** `accepted` technical measurement record<br>
**Verdict:** `UNKNOWN_COST_TRANSFERABILITY`<br>
**Promotion:** none<br>
**Execution model:** unchanged; the shadow comparator mirrors the current
dynamic residual-capacity schedule.
**Technical verification source:** `c3d65d73b6ebd22159f44f5d00eb7eec8c57e30d`<br>
**Result-recording commit:** `c0e167b2df8fc3cb2f5e4e4843a40d480fa56151`<br>

[Manifest](../../data/research_manifests/execution_cost_transferability_p0_2026_09.json) ·
[technical receipt](../../data/research_receipts/execution_cost_transferability_p0_2026_09.json) ·
[human report](execution_cost_transferability_p0_2026_09.md)

### `execution_cost_transferability_p1_paper_observation_2026_09`

**Status:** `planned` — prepared but not yet committed<br>
**Verdict:** `UNKNOWN_COST_TRANSFERABILITY`<br>
**Promotion:** none<br>
**Purpose:** predeclared natural-PAPER sample epoch and read-only 7/30/90-day
observation reports; P0 remains measurement infrastructure, not a cost-model
acceptance.

[Manifest](../../data/research_manifests/execution_cost_transferability_p1_paper_observation_2026_09.json) ·
[human report](execution_cost_transferability_p1_paper_observation_2026_09.md) ·
[runner](../../scripts/execution_cost_transferability_p1_report.py) ·
[test](../../tests/research/test_execution_cost_transferability_p1_paper_observation_2026_09.py)

### `equity_profit_protection_2026_09`

**Status:** `superseded` — archived blocked R1 attempt, preserved for lineage.

[Manifest](../../data/research_manifests/equity_profit_protection_2026_09.json) ·
[superseded runner](research_script_lifecycle_audit_2026_09_11.md) ·
[historical test](historical_root_test_compaction_2026_09_11.md) ·
[human report](equity_profit_protection_2026_09.md)

### `equity_profit_protection_2026_09_r2`

**Status:** `accepted` record<br>
**Verdict:** `KEEP_CURRENT_V3_AFTER_VALID_COMPARISON`<br>
**Promotion:** none of E1/E2/E3 is PAPER-eligible<br>
**Execution/source pin:** `6b0cabe5a03d8d3c02173ebfce5b6430252b12a1`<br>
**Runner implementation commit:** `05837c33e06cf3f548f2b41f8633c3a4128f05e7`<br>
**Repository result-recording commit:** `02859f95459671e9db82af075a37889f6d0d079a`

[Manifest](../../data/research_manifests/equity_profit_protection_2026_09_r2.json) ·
[control receipt](../../data/research_receipts/equity_profit_protection_2026_09_r2_current_control.json) ·
[hash-pinned results](../../data/research/equity_profit_protection_2026_09_r2/) ·
[human report](equity_profit_protection_2026_09_r2.md) ·
[runner](../../scripts/research/equity_profit_protection_2026_09_r2.py) ·
[test](../../tests/research/test_equity_profit_protection_2026_09_r2.py)

### August 2026 historical archive

**Status:** per-mission `rejected` or `superseded`, as recorded in the archive.

[Archive index](../archive/research_2026_08/README.md) ·
[archived result tree](../../data/research/archive/2026_08/)

## Metadata contract

A new canonical record should provide, when applicable:

- research ID and lifecycle status;
- parent, superseded and superseding IDs;
- starting/source commit; runner implementation commit and SHA; clean
  execution commit;
- the repository result-recording commit as a separate identity;
- runner, exact entry point and command;
- dataset, lifetime, corporate-action and input-manifest hashes;
- frozen configuration digest and parameters;
- run timestamp and evaluation window;
- required result artifacts and hashes, plus the compact receipt;
- verdict, promotion status, rejected alternatives, limitations and stop
  conditions;
- linked tests, documentation and archive location.

### Commit identities

Do not edit an existing immutable manifest to insert its own eventual commit
ID. Keep execution/source identity separate from the repository commit that
records the resulting artifacts on the current branch.

## Version-control policy

- `research_runs/` is generated local state and is ignored; local bundles may
  be retained or regenerated without becoming canonical evidence.
- Ordinary `data/research/` output remains ignored. Only manifest-required,
  reviewed and hash-pinned result artifacts may be intentionally force-added by
  explicit path; never use a blanket force-add.
- Manifests, receipts, summaries, current indexes and archive indexes remain
  tracked.
- Supersession never silently deletes historical evidence.

### Data and artifact lifecycle

- `data/research_manifests/` and `data/research_receipts/` are immutable
  read-only inputs; exact path, hash, size, source and result identities keep
  their referenced artifacts pinned.
- Retain one canonical final result for each accepted or frozen record, plus
  the immutable inputs and receipts needed to reproduce or audit it.
- An exact-path or exact-hash pin is never moved or deleted. Unpinned
  historical payloads may move under `data/research/archive/<period>/` only
  with an index entry; this cleanup moved none.
- After reference and content proof, exact duplicates, invalid placeholders,
  and superseded intermediates may be deleted. Git history remains the
  recovery path for deleted intermediates; economic results and provenance
  claims are not rewritten.
