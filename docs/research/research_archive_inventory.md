# Research archive inventory

Audit date: 2026-09-18

This is the pre-deletion inventory for the research-branch consolidation. It
was created from a clean checkout after switching to `main` and running
`git fetch --all --prune`. No branch or tag was deleted before this inventory
was recorded.

## Starting repository state

| Item | Recorded value |
|---|---|
| Local `main` | `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53` |
| `origin/main` | `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53` |
| Checkout | clean; `HEAD` on `main` |
| Fetch | `git fetch --all --prune` completed before inventory |
| Initial tags | none |
| Production reference | `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53` |

The initial branch comparison uses `git diff main...<branch>` and
`git rev-list --left-right --count main...<branch>`. The count column below is
`main-only / branch-only` commits relative to the audited `main`.

## Initial local branches

| Branch | Tip | Main-only / branch-only | Scope observed |
|---|---|---:|---|
| `main` | `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53` | — | production |
| `maintenance/equity-universe-integrity-v1` | `180fd12e7bafb5943856229e85e305d5d9906511` | 1 / 1 | one report-only audit |
| `maintenance/reserved-equity-role-isolation-v1` | `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53` | 0 / 0 | exact `main` alias; no unique commit |
| `research/equity-security-master-source-v1` | `70738106760eec86908b846823b25bd6964b5c0c` | 1 / 2 | one source-design report |
| `research/overnight-intraday-design-v1` | `7ec53efecc4d37cab17d0d96f194389b4513a69b` | 1 / 1 | frozen design and data-feasibility reports |
| `research/overnight-reversal-v1` | `41e1c9026bb34bbe81f98577844c9eca0523da8a` | 1 / 9 | research runner, tests, and large economic evidence |
| `research/spy-defensive-overlay-v1` | `da9a99b6e23df880fb64afa78d1f38aa70edd5ec` | 1 / 6 | research implementation and replay evidence |
| `research/v3-baseline-protocol-repair-v1` | `96910de6a54a3d3154996cad865308c3fff127ed` | 0 / 1 | protocol-repair governance evidence |
| `research/v3-reserved-role-corrected-baseline-v1` | `d34efd3b57cc22c766c5351c12201089bdf55480` | 0 / 1 | corrected replay and superseded baseline decision |
| `research/v3-volatility-managed-v1` | `9dbe4bd78b3276159dd089e4edf4e15b9ed1cb01` | 1 / 5 | research implementation and replay evidence |

## Initial remote branches

After fetch/prune, `origin` contained the same branch tips as the local list:

```text
origin/main                                      bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53
origin/maintenance/equity-universe-integrity-v1 180fd12e7bafb5943856229e85e305d5d9906511
origin/maintenance/reserved-equity-role-isolation-v1 bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53
origin/research/equity-security-master-source-v1 70738106760eec86908b846823b25bd6964b5c0c
origin/research/overnight-intraday-design-v1 7ec53efecc4d37cab17d0d96f194389b4513a69b
origin/research/overnight-reversal-v1 41e1c9026bb34bbe81f98577844c9eca0523da8a
origin/research/spy-defensive-overlay-v1 da9a99b6e23df880fb64afa78d1f38aa70edd5ec
origin/research/v3-baseline-protocol-repair-v1 96910de6a54a3d3154996cad865308c3fff127ed
origin/research/v3-reserved-role-corrected-baseline-v1 d34efd3b57cc22c766c5351c12201089bdf55480
origin/research/v3-volatility-managed-v1 9dbe4bd78b3276159dd089e4edf4e15b9ed1cb01
```

There were no additional unknown local or remote branches after pruning, and
there were no tags at the start of the audit.

## Production-path proof and classification

For every non-main branch above, the production-path comparison
`git diff --name-only main...<branch> -- src config pyproject.toml setup.cfg Makefile`
was empty. The only non-research test addition was the deterministic research
test file on `research/overnight-reversal-v1`; it does not alter production
runtime code. The reserved-role production fix is already on `main` at
`bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`.

| Branch | Unique production code not in `main`? | Research/report/governance evidence | Final disposition |
|---|---|---|---|
| `maintenance/equity-universe-integrity-v1` | No | `UNIVERSE_INTEGRITY_AUDIT.md` | Register findings; archive tag; delete local and remote branch |
| `maintenance/reserved-equity-role-isolation-v1` | No; tip is exactly `main` | None | Delete duplicate local and remote branch; no archive tag needed |
| `research/equity-security-master-source-v1` | No | Current-vs-historical security-master source report | Register findings; archive tag; delete local and remote branch |
| `research/overnight-intraday-design-v1` | No | Frozen design, causality, literature, and data-feasibility reports | Register design; archive tag; delete local and remote branch |
| `research/overnight-reversal-v1` | No | Frozen candidate runner, verifier, tests, compressed cases, and final report | Do not merge; archive tag; delete local and remote branch |
| `research/spy-defensive-overlay-v1` | No | Frozen overlay implementation, verifier, and replay evidence | Do not merge; archive tag; delete local and remote branch |
| `research/v3-baseline-protocol-repair-v1` | No | V2 protocol hash, drawdown semantics, persisted-result verification, and final decision | Extract canonical conclusions; archive tag; delete local and remote branch |
| `research/v3-reserved-role-corrected-baseline-v1` | No | Corrected baseline replay and original G5 failure evidence | Mark decision superseded; archive tag; delete local and remote branch |
| `research/v3-volatility-managed-v1` | No | Frozen volatility scaler, verifier, cost stress, and final report | Do not merge; archive tag; delete local and remote branch |

No experimental strategy implementation or rejected result is eligible for a
production merge.

## Archive plan

The following annotated tags preserve the final evidence commits without
retaining completed branch refs:

| Tag | Target |
|---|---|
| `archive/research/equity-universe-integrity-v1-2026-09` | `180fd12e7bafb5943856229e85e305d5d9906511` |
| `archive/research/equity-security-master-source-v1-2026-09` | `70738106760eec86908b846823b25bd6964b5c0c` |
| `archive/research/overnight-intraday-design-v1-2026-09` | `7ec53efecc4d37cab17d0d96f194389b4513a69b` |
| `archive/research/overnight-reversal-v1-2026-09` | `41e1c9026bb34bbe81f98577844c9eca0523da8a` |
| `archive/research/spy-defensive-overlay-v1-2026-09` | `da9a99b6e23df880fb64afa78d1f38aa70edd5ec` |
| `archive/research/v3-baseline-protocol-repair-v1-2026-09` | `96910de6a54a3d3154996cad865308c3fff127ed` |
| `archive/research/v3-reserved-role-corrected-baseline-v1-2026-09` | `d34efd3b57cc22c766c5351c12201089bdf55480` |
| `archive/research/v3-volatility-managed-v1-2026-09` | `9dbe4bd78b3276159dd089e4edf4e15b9ed1cb01` |
| `archive/research/residual-reversal-local-v1-2026-09` | `a3a54cff64a9312300ef700f2fe9409435a064d3` |

The last tag preserves a previously deleted branch's final corrected-replay
commit. It does not recreate the deleted branch. The exact-main maintenance
alias has no unique evidence and is represented only by this inventory.

## Deletion preconditions

Deletion is permitted only after:

1. the register and canonical documents are committed to `main`;
2. every final SHA and verdict is recorded;
3. no unique production code is stranded;
4. the applicable archive tag is created and pushed;
5. the remote branch is deleted, followed by `git fetch --prune`.

The temporary documentation branch
`maintenance/research-archive-consolidation-v1` is process-only and is not a
campaign branch; it is deleted only after its documentation commit is merged
to `main`.

## Follow-up consolidation audit — 2026-09-21

This follow-up began from a clean checkout after
`git fetch --all --prune --tags`. Local `main` and `origin/main` were both
`839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de`. The commit contains the generic
canonical backtest data, lifecycle, corporate-action, and effective-dated cost
correctness fixes required by the 52-week-high readiness campaign.

The count below is branch-only / main-only commits. Local and remote-tracking
tips matched for every remote research branch.

| Branch | Tip | Ahead / behind main | Unique scope | Production code not on main? | Disposition |
|---|---|---:|---|---|---|
| `main` | `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de` | — | production reference | — | Retain |
| `maintenance/backtest-canonical-data-cost-correctness-v1` | `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de` | 0 / 0 | exact main alias; no unique evidence | No | Delete local alias; no tag |
| `research/52week-high-design-v1` | `72b51a0e5c03f2ce0a8790b197cf360b26b33cd3` | 1 / 1 | 9 design artefacts | No | Register, tag, delete local/remote |
| `research/52week-high-readiness-v1` | `339d62f404a0b924ec78e3a67d8cddad7cee24c7` | 2 / 0 | 18 readiness harness/evidence files | No; generic fixes are ancestors on main | Register, tag, delete local/remote |
| `research/52week-high-economic-v1` | `2d53ba1985a60c67bb727e1092124df08c220842` | 3 / 0 | readiness plus 22 candidate economic files | No | Register, tag, delete local/remote |
| `research/information-discreteness-design-v1` | `a822083eef4123d960ee7886d52951bfb20b29bd` | 1 / 0 | 11 design artefacts | No | Register, tag, delete local/remote |
| `research/information-discreteness-economic-v1` | `9dbc44ce9f62b6e2f1b5d233eee77a7fff3df9d3` | 9 / 0 | design plus 28 standalone economic files | No | Register, tag, delete local/remote |
| `research/v3-id-complementarity-v1` | `a5d355eb8b8045e0438d435312fcc23e59cb3936` | 5 / 0 | 33 combination policy/runner/evidence files | No | Extract generic policy, register, tag, delete local/remote |

The 52-week design branch diverged before `839e92cf`; its branch-only commit
changes only `research/52week_high_design_v1/`. Every other research branch is
based directly on current `main`. Diffs against `src/`, configuration, packaging,
and general scripts are empty. The executable additions and tests are all
candidate-specific research material and are not eligible for production merge.

The nine archive tags already present at the start of this follow-up were:

- `archive/research/equity-security-master-source-v1-2026-09`;
- `archive/research/equity-universe-integrity-v1-2026-09`;
- `archive/research/overnight-intraday-design-v1-2026-09`;
- `archive/research/overnight-reversal-v1-2026-09`;
- `archive/research/residual-reversal-local-v1-2026-09`;
- `archive/research/spy-defensive-overlay-v1-2026-09`;
- `archive/research/v3-baseline-protocol-repair-v1-2026-09`;
- `archive/research/v3-reserved-role-corrected-baseline-v1-2026-09`;
- `archive/research/v3-volatility-managed-v1-2026-09`.

The follow-up archive plan adds these exact annotated tag targets:

| Archive tag | Target |
|---|---|
| `archive/research/52week-high-design-v1-2026-09` | `72b51a0e5c03f2ce0a8790b197cf360b26b33cd3` |
| `archive/research/52week-high-readiness-v1-2026-09` | `339d62f404a0b924ec78e3a67d8cddad7cee24c7` |
| `archive/research/52week-high-economic-v1-2026-09` | `2d53ba1985a60c67bb727e1092124df08c220842` |
| `archive/research/information-discreteness-design-v1-2026-09` | `a822083eef4123d960ee7886d52951bfb20b29bd` |
| `archive/research/information-discreteness-economic-v1-2026-09` | `9dbc44ce9f62b6e2f1b5d233eee77a7fff3df9d3` |
| `archive/research/v3-id-complementarity-v1-2026-09` | `a5d355eb8b8045e0438d435312fcc23e59cb3936` |

The 52-week economic result is `ALPHA_REJECTED`. The information-discreteness
standalone result remains `ALPHA_VALID`, while its valid canonical 5+1 test is
`COMBINATION_REJECT`; the combined durable classification is
`ALPHA_VALID_PORTFOLIO_REJECTED`. No production code is to be merged in this
follow-up.

The process-only branch for this update is
`maintenance/research-consolidation-2026-09-v2`; it carries only the research
register, this inventory, and the extracted generic combination methodology.

## Abnormal-volume and MAX closure audit — 2026-09-21

This closure began from a clean checkout after
`git fetch --all --prune --tags`. Local `main` and `origin/main` were both
`980c6b90b7f316bcec6a9466dfc9b0e1756cc748`. Fifteen pre-existing annotated
archive tags were present. The starting remote contained `main`, four of the
five abnormal-volume branches (design repair was local-only), and the MAX
design branch. No other unknown or active campaign branch was present.

The count below is branch-only / main-only commits, or ahead / behind `main`.
Branch tips matched their remote-tracking refs wherever a remote branch
existed.

| Branch | Tip | Ahead / behind main | Campaign | Final state | Unique production code? | Archive needed? | Action |
|---|---|---:|---|---|---|---|---|
| `research/abnormal-volume-design-v1` | `4f454f5deb4c77151b574f87c29228c859cfc884` | 1 / 1 | Abnormal-volume design V1 | `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`; superseded by V2 | No; research Markdown/JSON only | Yes | Tag, then delete local/remote |
| `research/abnormal-volume-readiness-v1` | `93e85169b9e1417902d3c13ac91fc1c304dda4f8` | 2 / 1 | Abnormal-volume readiness V1 | `G1_BLOCKED_DATA_OR_EXECUTION` | No; research evidence only | Yes | Tag, then delete local/remote |
| `research/abnormal-volume-design-repair-v1` | `a39cf6dfc72a4a4aa92d95b3f760626e2ce216c6` | 1 / 0 | Abnormal-volume design V2 repair | `QUANTILE_SPEC_REPAIRED`; pre-outcome | No; research Markdown/JSON only | Yes | Tag, then delete local; no remote existed |
| `research/abnormal-volume-readiness-v2` | `ec37b0f1671191f1842f5f952977bb60c772fa4b` | 2 / 0 | Abnormal-volume readiness V2 | `G1_READY_FOR_FROZEN_ECONOMIC_TEST` | No; executable harness/tests only under `research/` | Yes | Tag, then delete local/remote |
| `research/abnormal-volume-economic-v1` | `693b86a16bb7363e98be46aeb25d164c384fe9aa` | 5 / 0 | Abnormal-volume economic V1 | valid `RESEARCH_REJECT`; `ALPHA_REJECTED` | No; executable harness/tests/results only under `research/` | Yes | Tag, then delete local/remote |
| `research/max-lottery-design-v1` | `405103908acd7b872df2d9663fa8166e6d793020` | 1 / 1 | MAX / lottery design V1 | `DESIGN_BLOCKED_DATA_OR_EXECUTION`; no economics | No; research Markdown/JSON only | Yes | Tag, then delete local/remote |

For every branch above,
`git diff --name-only main...<branch> -- src config scripts tests pyproject.toml Makefile`
was empty. Candidate signal code, readiness/economic runners, independent
verifiers, candidate-specific tests, and generated evidence are research-only
and are not eligible for merge. The only generic correctness result discovered
during the abnormal-volume campaign is the candidate-neutral month-end/
first-open primitive already integrated on `main` at `980c6b9`; its fresh V3
parity evidence was exact. This closure therefore merges documentation only.

The archive plan adds these exact annotated tag targets:

| Archive tag | Target | Durable decision/status |
|---|---|---|
| `archive/research/abnormal-volume-design-v1-2026-09` | `4f454f5deb4c77151b574f87c29228c859cfc884` | `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`; superseded design |
| `archive/research/abnormal-volume-readiness-v1-2026-09` | `93e85169b9e1417902d3c13ac91fc1c304dda4f8` | `G1_BLOCKED_DATA_OR_EXECUTION` |
| `archive/research/abnormal-volume-design-repair-v1-2026-09` | `a39cf6dfc72a4a4aa92d95b3f760626e2ce216c6` | pre-outcome `QUANTILE_SPEC_REPAIRED` |
| `archive/research/abnormal-volume-readiness-v2-2026-09` | `ec37b0f1671191f1842f5f952977bb60c772fa4b` | `G1_READY_FOR_FROZEN_ECONOMIC_TEST` |
| `archive/research/abnormal-volume-economic-v1-2026-09` | `693b86a16bb7363e98be46aeb25d164c384fe9aa` | valid `RESEARCH_REJECT`; `ALPHA_REJECTED` |
| `archive/research/max-lottery-design-v1-2026-09` | `405103908acd7b872df2d9663fa8166e6d793020` | `DESIGN_BLOCKED_DATA_OR_EXECUTION`; no economics |

The process-only documentation branch is
`maintenance/research-consolidation-abnormal-volume-2026-09`. It carries only
the research register, this inventory, and future-protocol exposure-semantics
clarification. It receives no archive tag and is deleted after its
documentation commit is merged to `main`.

## Second-wave pre-outcome consolidation — 2026-09-22

This consolidation began from a clean checkout after
`git fetch --all --prune --tags`. Local `main` and `origin/main` were both
`d3dea037789c16c335744b56d216289a4536096c`. Twenty-one pre-existing annotated
`archive/research/*` tags were present. The only non-main local and remote
branches were the four completed campaigns below; there was no unrelated
active or unclassified branch.

The count is branch-only / main-only commits. Local and remote-tracking tips
matched exactly.

| Branch | Tip | Ahead / behind main | Campaign | Final decision | Unique production code? | Archive needed? | Action |
|---|---|---:|---|---|---|---|---|
| `research/opportunistic-insider-purchases-design-v1` | `d08c94cd93a1f71e9f9b8bf21e3c726b73953f50` | 1 / 0 | Original insider-purchase design | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | No; 17 research artifacts only | Yes | Tag, then delete local/remote |
| `research/opportunistic-insider-source-reassessment-v1` | `e46e9ae0e1c124138c2143b30d93809a97f4282d` | 1 / 0 | Insider source reassessment | `REASSESSMENT_CONFIRMS_DESIGN_REJECT` | No; 15 research artifacts only | Yes | Tag, then delete local/remote |
| `research/cash-based-operating-profitability-design-v1` | `0f77021a2d6a0d08df76f0902da3354e90f95f65` | 1 / 0 | Cash-based operating profitability | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | No; 19 research artifacts only | Yes | Tag, then delete local/remote |
| `research/positive-liquidity-shock-design-v1` | `15644b3c117535eb94a12eb5099d9d82d10cb2cf` | 1 / 0 | Positive liquidity-shock continuation | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | No; 20 research artifacts only | Yes | Tag, then delete local/remote |

Every branch differs from starting `main` only under its campaign-specific
`research/` directory. The executable additions are research-only source,
provenance, structural-count, mapping, and audit helpers. They are potentially
useful evidence and source-capability examples, but are not production runtime
code and are not merged automatically. No unique production correctness code
is stranded.

The archive plan adds these annotated tag targets:

| Archive tag | Target | Durable decision/status |
|---|---|---|
| `archive/research/opportunistic-insider-purchases-design-v1-2026-09` | `d08c94cd93a1f71e9f9b8bf21e3c726b73953f50` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no local candidate economics |
| `archive/research/opportunistic-insider-source-reassessment-v1-2026-09` | `e46e9ae0e1c124138c2143b30d93809a97f4282d` | `REASSESSMENT_CONFIRMS_DESIGN_REJECT`; no local candidate economics |
| `archive/research/cash-based-operating-profitability-design-v1-2026-09` | `0f77021a2d6a0d08df76f0902da3354e90f95f65` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no local candidate economics |
| `archive/research/positive-liquidity-shock-design-v1-2026-09` | `15644b3c117535eb94a12eb5099d9d82d10cb2cf` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no local candidate economics |

The pre-deletion privacy audit covered all accessible history for the four
branches: 534 commits and 4,144 unique blobs (7,107 object/path entries). It
found no persisted user SEC contact address, private key, recognized live-token
form, JWT, embedded URI credential, or campaign-persisted email/User-Agent
contact value. Generic environment/configuration key names and public or
synthetic material in common ancestor history were not private credentials.

The process-only documentation branch is
`maintenance/research-consolidation-second-wave-2026-09`. It carries only this
inventory and the research register, receives no archive tag, and is deleted
after its documentation commit is integrated into `main`.

## Exit-audit closure — 2026-09-22

Fresh `git fetch origin --prune --tags` verified local `main` and `origin/main`
at `b81e9d9fef3744723449f4c019f2a5dd75f2e2cb`; starting checkout was clean.

| Completed branch | Exact evidence SHA | Annotated archive tag | Durable decision |
|---|---|---|---|
| `research/position-exit-giveback-audit-v1` | `4444147ae8e53a2b33b0648077ee2589f4920e5d` | `archive/research/position-exit-giveback-audit-v1-2026-09` | Equity: `EQUITY_EXIT_RESEARCH_NOT_CURRENTLY_JUSTIFIED`; crypto V1: `AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`, superseded by V2 |
| `research/crypto-exit-giveback-audit-v2` | `4dfd63c80f5b79f14dccf3e9d7aa8890be6cd959` | `archive/research/crypto-exit-giveback-audit-v2-2026-09` | `CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED`; `MIXED_PROFIT_RETENTION_BEHAVIOR` |

Both branch-only diffs contain research artifacts and audit-specific tests;
no unique production implementation is stranded. Merge only this inventory
and `docs/research/research_register.md`. Preserve V1 history unchanged.
Create and push the annotated tags, verify remote peeled targets, commit/push
durable documentation, then delete the two completed audit branches where
present. No deletion precedes verified archive protection.

The unrelated local/remote `research/schedule-13d-activism-design-v1` branch
at `c4e9e30` is outside this exit-audit closure and is retained. Thus the preferred
main-only inventory is not asserted; this preservation does not leave either
exit audit open. Stage B requires successful exit-audit closure and a clean,
updated main checkout.

Closure validation: full normal suite `.venv/bin/python -m pytest -q` passed
with 1,138 passed, 1 skipped, 2 warnings (99.55s). The first sandboxed run had
six localhost-bind PermissionErrors; rerunning the same full suite with local
port permission passed without code changes. Independent read-only review
matched persisted evidence at both exact SHAs; it did not rerun economics.
Both annotated tags were pushed and their remote peeled SHAs matched the
above targets. Documentation diff is confined to the two requested files.

## Event-driven pre-outcome consolidation — 2026-09-22

This consolidation began from a clean checkout after
`git fetch --all --tags --prune`. Local `main` and `origin/main` were both
`16db2eaad9061a1669166ef9ab6717853337fafc`, and 27 pre-existing annotated
`archive/research/*` tags were present. Commit `16db2ea` is two commits newer
than, and descends from, `b81e9d9fef3744723449f4c019f2a5dd75f2e2cb`;
the intervening commits are documentation consolidation. The only non-main
local and remote branches were the four completed campaigns below. Local and
remote-tracking tips matched exactly; no unrelated active branch was found.

The count is branch-only / main-only commits relative to the starting `main`.

| Branch | Tip | Ahead / behind main | Merge base | Final decision | Unique production code? | Action |
|---|---|---:|---|---|---|---|
| `research/schedule-13d-activism-design-v1` | `c4e9e30039eb4bef8005fdb0700e15a99884070c` | 1 / 3 | `754f4060b1eb38593201b2a269e9593d083eb534` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | No; 19 campaign artifacts only | Tag, then delete local/remote |
| `research/share-repurchase-announcement-design-v1` | `73fe3e0065c46f813508a08013fb2198629d9bda` | 1 / 0 | `16db2eaad9061a1669166ef9ab6717853337fafc` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | No; 19 campaign artifacts only | Tag, then delete local/remote |
| `research/management-guidance-drift-design-v1` | `9648192713c5d881bf033e4d505780e70273a98a` | 1 / 0 | `16db2eaad9061a1669166ef9ab6717853337fafc` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | No; 22 campaign artifacts only | Tag, then delete local/remote |
| `research/earnings-announcement-return-design-v1` | `1f7c461be32f11bb23a8c85a89f19621244a270f` | 1 / 0 | `16db2eaad9061a1669166ef9ab6717853337fafc` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | No; 18 campaign artifacts only | Tag, then delete local/remote |

Every branch-only path is confined to its campaign-specific `research/`
directory. Schedule 13D includes one structural-count helper; the other three
campaigns contain Markdown and JSON evidence only. No production source,
configuration, strategy registration, risk, persistence, broker, PAPER, or
LIVE change is stranded. No candidate design or future economic protocol was
created, and no local candidate economics were calculated.

The archive plan adds these annotated tag targets:

| Archive tag | Target | Durable decision/status |
|---|---|---|
| `archive/research/schedule-13d-activism-design-v1-2026-09` | `c4e9e30039eb4bef8005fdb0700e15a99884070c` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no economic peek |
| `archive/research/share-repurchase-announcement-design-v1-2026-09` | `73fe3e0065c46f813508a08013fb2198629d9bda` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no economic peek |
| `archive/research/management-guidance-drift-design-v1-2026-09` | `9648192713c5d881bf033e4d505780e70273a98a` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no economic peek |
| `archive/research/earnings-announcement-return-design-v1-2026-09` | `1f7c461be32f11bb23a8c85a89f19621244a270f` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no economic peek |

The pre-deletion privacy audit covered every unique tracked artifact in all
four campaigns. It found no persisted SEC User-Agent email address, other
email address, private-key block, recognized provider token/API key,
secret-valued credential assignment, JWT, or credential-bearing URL. Public
issuer and source identifiers are not private credentials.

The process-only documentation branch is
`maintenance/research-consolidation-event-wave-2026-09`. It carries only this
inventory and the research register, receives no archive tag, and is deleted
after its documentation commit is integrated into `main`.

## Security-master procurement closure — 2026-09-22

This closure began from clean `main` at
`91adb80a6157a10457e1b526e4a96b7fb8f293f3` (local and origin matched). The only
non-main local and remote branches were the three completed security-master
campaigns below; no unrelated active branch was found.

| Branch | Tip | Ahead / behind main | Unique scope | Production code not on main? | Action |
|---|---|---:|---|---|---|
| `research/security-master-source-audit-v1` | `7366ea51ac819de7772ef67cc650869248ea7d07` | 1 / 0 | 17 source-audit artifacts | No; research Markdown/JSON/CSV only | Tag, then delete local/remote |
| `research/security-master-entitlement-prep-v1` | `951e609042431cfbaad13cc590db4479151f290a` | 1 / 0 | 13 entitlement-prep artifacts | No; research Markdown/JSON only | Tag, then delete local/remote |
| `research/security-master-vendor-outreach-v1` | `2d5b2b3d36e84851182da7a8f7208be51138d697` | 1 / 0 | 8 outreach-prep artifacts | No; research Markdown/JSON only | Tag, then delete local/remote |

For every branch above,
`git diff --name-only main...<branch> -- src config scripts tests pyproject.toml Makefile setup.cfg`
was empty. Durable classification: `DEFERRED_EXTERNAL_DATA_NOT_WORTH_PROCUREMENT_BURDEN`
(neither `SOURCE_INVALID` nor solved). Outreach facts: `NO_VENDOR_CONTACT_MADE`,
`NO_PURCHASE`, `NO_CONTRACT_ACCEPTED`, `NO_TRIAL_STARTED`; nothing pending with NYSE.

The archive plan adds these exact annotated tag targets:

| Archive tag | Target | Durable decision/status |
|---|---|---|
| `archive/research/security-master-source-audit-v1-2026-09` | `7366ea51ac819de7772ef67cc650869248ea7d07` | `SECURITY_MASTER_SOURCE_LEAD_VALID_BUT_ENTITLEMENT_REQUIRED` |
| `archive/research/security-master-entitlement-prep-v1-2026-09` | `951e609042431cfbaad13cc590db4479151f290a` | `READY_TO_CONTACT_NYSE`; contact deliberately not made |
| `archive/research/security-master-vendor-outreach-v1-2026-09` | `2d5b2b3d36e84851182da7a8f7208be51138d697` | `OUTREACH_READY_AWAITING_OPERATOR_SEND_APPROVAL`; superseded by deferral |

The process-only documentation branch is
`maintenance/public-data-research-reset-v1`. It carries only the register, this
inventory, the limitations update, and the new public-data policy; it receives
no archive tag and is deleted after its documentation commit is integrated
into `main`.

## Public-data Discovery V3 and spin-off design closure — 2026-09-22

The checkout was clean at start. After `git fetch origin --prune --tags`, local
`main` and live `origin/main` both resolved to
`82e534f10bba0c7afa0e08a2ba449b3953314c82`. The only non-main local and
remote branches were the two completed campaigns below. Both had current
`main` as merge base and exactly one branch-only commit; the branch-only diffs
added only their respective `research/` evidence directories. No production,
configuration, strategy, risk, persistence, broker, PAPER or LIVE path differed
from main.

| Completed branch | Verified local and remote tip | Branch-only evidence | Durable status | Annotated archive tag |
|---|---|---:|---|---|
| `research/public-data-alpha-discovery-v3` | `f37820f11e35da1d360600d7adbf10f501e88f09` | 12 text/JSON artifacts in `research/public_data_alpha_discovery_v3/` | `PUBLIC_DATA_DISCOVERY_V3_EXHAUSTED_AFTER_SINGLE_DESIGN_CANDIDATE`; one nominee, no economic peek | `archive/research/public-data-alpha-discovery-v3-2026-09` |
| `research/spinoff-child-drift-design-v1` | `4b38d4db4a62459b4b60f271ce98beaf1e98f7f9` | 20 text/JSON artifacts in `research/spinoff_child_drift_design_v1/` | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no candidate economic test | `archive/research/spinoff-child-drift-design-v1-2026-09` |

Both tags are annotated, locally verified, and pushed. Live remote peeled tag
targets match the exact evidence SHAs in the table. The branch tips themselves
are **not** merged to main; their full reports, source records and receipts are
recoverable by checking out either tag. The maintenance branch
`maintenance/public-data-discovery-v3-closure` carries only
`docs/research/research_register.md` and this inventory, and is not an evidence
archive tag.

Pre-deletion privacy and no-peek review covered all 32 added evidence files.
No contact details, credentials, private key, live token shape, private vendor
dataset, or local candidate-economic result artifact was found. Both no-peek
receipts deny forward-return/CAR/P&L/Sharpe/CAGR/IC/backtest computations;
return numbers appearing in spin-off reports are attributed external studies.
`git diff --check` passed on both evidence branches. Nothing from either
campaign is registered as a production strategy.

**Archived-count caveat:** the discovery headline reports 32 families
screened, but its `LONG_LIST.md` contains 33 rows; its printed disposition
counts sum to 34 and report six concentration rejects where the table has
five. This unresolved bookkeeping discrepancy is preserved unchanged at the
specified discovery SHA. It does not change the one shortlisted candidate,
withheld second slot, or subsequent spin-off design rejection. The main
[register](research_register.md) identifies the 32-family number as
campaign-reported rather than independently reconciled.

Before this closure there were 34 local annotated `archive/research/*` tags.
These two pushed tags bring the local count to 36. After documentation is
merged and pushed, delete only the two completed research branches locally and
remotely, then the process-only maintenance branch locally. No unrelated
branch is in scope.

## Shared-account replay V1 — 2026-09-23

`research/shared-account-replay-v1` ended at
`1c1c21ef1631fd426b4e90188ac446db4eaa0fd2` and was never pushed. Its
annotated evidence tag `archive/research/shared-account-replay-v1-blocked-2026-09`
was pushed; the remote peeled target is the exact branch tip. The replay branch
is closed without merging its implementation. The archived decision is
`SHARED_ACCOUNT_REPLAY_BLOCKED_BY_GENERIC_CORRECTNESS` because a deterministic
combined cap-six run recorded 21 timestamps with seven slot claims. Both
isolated parity checks passed. No cap-eight comparison or migration economics
was performed, and production root cause was not proven by this replay.

## Corrected crypto giveback audit V3 closure — 2026-09-24

| Campaign branch | Final evidence SHA | Annotated archive tag | Causal baseline | Durable decision |
|---|---|---|---|---|
| `research/crypto-exit-giveback-audit-v3` | `d46338454cb7f8d59525d88fd775f98e2b927d45` | `archive/research/crypto-exit-giveback-audit-v3-2026-09` | `b135d73743ec2652f79b8c840df50bd3f67166e8` | `CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED`; `MIXED_PROFIT_RETENTION_BEHAVIOR` |

V3 supersedes V2 as diagnostic input authority and reconfirms its broad
conclusion. The branch-only diff contains research evidence and one focused
test; no production path was changed. The annotated tag preserves the full
evidence commit. Publication of local refs is separate from this local closure.

## Final architecture/correctness cleanup — 2026-09-24

Preflight began at clean, synchronized `main` and `origin/main`:
`be9dd048b362e71fa1076ef1919c4f2d4ff77545`. The remote had only
`main` and the seven completed branches below. Remote annotated tags were
checked by their peeled targets before any branch deletion.

| Branch | Tip | Durable authority | Cleanup action |
|---|---|---|---|
| `audit/cap8-production-parity-v1` | `8d7f0989b3c830a67cf66d8d8dc7dec1d1642b6d` | `archive/audit/cap8-production-parity-v1-blocked-2026-09` at `e99d4226c2b74e23ee7dd6e6b01f1b4ddaadff57` | Delete branch; the later `.fix-worktrees` gitlink commit is accidental and intentionally not archived. |
| `audit/cap8-production-parity-v2` | `d20b70c0333b888b6d0e5438091ede540f3a27ee` | `archive/audit/cap8-production-parity-v2-ready-2026-09` at same commit | Delete branch after tag verification. |
| `fix/production-correctness-cap8-readiness-v1` | `f107009c1308c152d326a8647a1df6c8bc55b225` | Main-history `d0c68057234a240a778e5adb06e42c6a3c136c34`; both trees `7e751ed9fdc8fd460962e7b710b3db8919279b92` | Delete branch after tree comparison. |
| `fix/production-restart-reconciliation-v1` | `5229a061b1d5921fc4dffef8c9bb54393b3dd833` | Main-history `96254b7de6e045212002cfa807abd448bc6e5d0d`; both trees `9e35fa26741c2a2713ad706df892c50bd88f1a26` | Delete branch after tree comparison. |
| `research/account-capacity-migration-8-v3` | `9c5e61382e8cdb71fdd3442d945719024accedd9` | `archive/research/account-capacity-migration-8-v3-accepted-2026-09` at same commit | Delete branch after tag verification. |
| `research/physical-availability-clock-v1` | `377d260835c5a5f8ea23b61e16bf8103a842b0e0` | `archive/research/physical-availability-clock-v1-canonical-stop-2026-09` at same commit | Delete branch after tag verification. |
| `research/shared-account-replay-v2` | `fb66be75aa77d48e78d586808c05ac40d617ca0d` | `archive/research/shared-account-replay-v2-clock-blocked-2026-09` at same commit | Delete branch after tag verification. |

The validated infrastructure tag
`infrastructure/physical-availability-clock-v2-cap6-2026-09` remains at
`11890e93ad1a5223cb11483488deb4d5a6aec3d3`. The two fix trees are
identical to their main-history authorities, so no unique production code is
stranded. Research and audit evidence remains available through its tags.
Main is authoritative; cap 8 is integrated in code/config, with crypto cap 2
and LIVE disabled. No runtime or broker call was made in this cleanup.

Local-only `audit/production-restart-reconciliation-v1`,
`maintenance/crypto-canonical-causal-execution-v1`, and the merged
`integration/cap8-main-v1` are outside the seven-branch deletion list and are
retained for separate review. See `FINAL_REPOSITORY_CLEANUP_RECEIPT.json` for
the frozen ref and worktree inventory and post-cleanup verification.

## Post-readiness research and audit archive — 2026-09-24

The following annotated tags preserve full evidence; only compact conclusions are on main.

| Archive tag | Peeled evidence commit | Decision |
|---|---|---|
| `archive/research/earnings-acceleration-design-v1-2026-09` | `88c461819cc5684766af6f42e4313de925c37bcf` | Design rejected; no defensible concentrated expression |
| `archive/research/government-contract-award-design-v1-2026-09` | `959085355442fea1ffa59236bfd03d6dfa31f289` | Design blocked by data or execution |
| `archive/research/customer-momentum-design-v1-2026-09` | `dfa967d8501831287048838d3238be7b389b1aa7` | PIT public customer graph unvalidated |
| `archive/research/public-data-alpha-discovery-v4-exhausted-2026-09` | `9294bd05664e4ffc0c3397a9e6abc954fe1053a7` | 66 screened; zero shortlisted; no candidate economics |
| `archive/research/residual-reversal-cap8-reentry-review-v1-2026-09` | `690ce42e1852ead0ccaaddbf3822b10a65a6a77c` | Re-entry blocked; old standalone result unchanged |
| `archive/research/residual-reversal-physical-capacity-replay-v1-blocked-2026-09` | `4812b94b051354e4893e21d5332adf74ca2a65dc` | Physical replay V1 blocked |
| `archive/research/residual-reversal-physical-capacity-replay-v2-blocked-2026-09` | `ad2eaf4e0676f35a3002fc67dcb9ad7778332e6f` | Physical replay V2 blocked; keep reversal closed |
| `archive/audit/paper-forward-readiness-v1-blocked-2026-09` | `2920adfd6e01bebf13cbb477a1bf09c262034deb` | Superseded pre-repair audit: `PAPER_FORWARD_OBSERVATION_BLOCKED` |

The remote replay V1 branch tip `54cf5923b508de40af35e409fd746d671a592d3a` adds unrelated `agent_benchmark.sh` and `agent_benchmark.txt`; it is **not** an archive authority. The archive points to its parent campaign commit `4812b94b051354e4893e21d5332adf74ca2a65dc`.

Current operational authority is main commit `7f007c8f9e5edc33a5065fc86c1c6fb61e55f3f3`: `PAPER_OBSERVABILITY_REPAIR_ACCEPTED` and `PAPER_FORWARD_OBSERVATION_READY`. The frozen observation period is 14 calendar days. Before PAPER, refresh both active universes and pass provider-backed readiness and reconciliation checks. PAPER has not started; no broker call was made by this cleanup; LIVE remains disabled. The blocked audit is historical, not current readiness.

## ETF diversifier v1 — 2026-09-25

| Archive tag | Peeled evidence commit | Decision |
|---|---|---|
| `archive/research/etf-diversifier-v1-stage-b-fail-2026-09` | `36b99e53bd1e6ccb989a401e7668fd4796fba74e` | Stage A pass; Stage B shared-ledger FAIL (C2 misses only the CAGR guard); shipped config stays. Summary: `docs/research/etf_diversifier_v1_closure.md` |

## Sleeve candidates v2 and v3 — 2026-09-25

| Archive tag | Peeled evidence commit | Decision |
|---|---|---|
| `archive/research/sleeve-candidates-v2-v3-2026-09` | `094c7fb7b1a7723c2c116bfb7fc5e1f85ff71c15` | v2: no pass. v3: `dividend_growth_v1` and `gold_v1b` pass Stage B; design-only authorization, shipped config unchanged. Summary: `docs/research/sleeve_candidates_v2_v3_closure.md` |

## Dividend-growth sleeve production integration — 2026-09-25

| Archive tag | Peeled evidence commit | Decision |
|---|---|---|
| `archive/research/dividend-sleeve-production-v1-2026-09` | `e4e1f11ac698719279253aaed78ecdc061abc64c` | Shipped: `equity_residual_momentum_v3_dividend_sleeve_v1` passes every production-path validation gate. Summary: `docs/research/dividend_sleeve_production_v1.md` |
