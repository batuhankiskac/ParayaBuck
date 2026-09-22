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
