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
