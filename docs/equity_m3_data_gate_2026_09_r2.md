# M3 DATA GATE R2 corrective closure

## Verdict

- Corrective infrastructure: `PASS`
- Underlying data verdict: `M3_DATA_GATE_BLOCKED`
- Candidate-owned M3 OCP: `NOT_RUN_OUT_OF_SCOPE`
- Economic mission readiness: `false`

The R2 implementation preserves the R1 economic/data conclusion. No raw IWM
bundle or deterministic PIT scanner population was supplied, so no IWM was
fetched, fabricated, substituted, or consumed from normalized-only evidence.

## Append-only lineage

| Phase | Commit | Constraint |
| --- | --- | --- |
| A2 | `18a9828486fae4636f28fff10f614d29186a948f` | Direct child of R1 C `69fc1e11606dec5f5e84b2da68a0bb24fae81fbd`; implementation/tests/CI only |
| B2 | `3425a03899237bf83b3dedecc7b77f16c7c2687c` | Direct child of A2; manifest-only |
| C2 | `e7693b0787771f8d432f5684d7ab8a280076800a` | Receipt and this report only |

B2 changed exactly:

`data/research_manifests/equity_m3_data_gate_2026_09_r2.json`

The R2 manifest was loaded from and compared with the committed Git blob. It
pins the A2 tree, critical Git blobs and raw SHA-256 values, configuration
semantic digest, calendar dependency, source contract, numerical contract,
PIT-population contract, composite identity contract, OCP boundary, and all
mandatory data-pass gates.

## Local execution

Command executed from clean B2:

```text
.venv/bin/python scripts/research_equity_m3_data_gate.py --canonical-bars data/historical/equity_pit_symmetric_2016_2026_v5.csv --canonical-manifest data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json --lifetimes data/historical/equity_entity_lifetimes_full_v5.csv --lifetimes-expected-sha256 ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596 --corporate-actions data/historical/equity_survivorship_full_corporate_actions_v5.csv --corporate-actions-manifest data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json --contract data/research_manifests/equity_m3_data_gate_2026_09_r2.json --receipt-out data/research_receipts/equity_m3_data_gate_2026_09_r2.json
```

Outcome: exit `20`; `data_gate_verdict=M3_DATA_GATE_BLOCKED` and
`corrective_infrastructure_verdict=PASS`.

The receipt self-digest is valid. The receipt records canonical bars and
corporate actions as external untracked inputs, the tracked lifetime table as
`git_blob`, and descriptor-based staging identities. It contains no absolute
personal paths.

## Mandatory gate state

| Gate group | State | Evidence |
| --- | --- | --- |
| Commit topology, implementation blobs, configuration, dependency, clean worktree | `PASS` | A2/B2 Git ancestry, exact manifest-only B2 path, pinned blobs, config/calendar identity |
| Canonical bars, lifetime, corporate actions | `PASS` | Frozen manifests and descriptor-staged hashes; lifetime newline-normalized Git-blob match |
| IWM raw bundle, provider contract, pagination, normalization | `NOT_RUN_BLOCKED` | No operator-supplied raw IWM bundle |
| Reference calendar, row quality, common sessions, weekly coverage | `FAIL` or unavailable for complete panel | IWM reference rows are absent; no complete three-reference panel |
| Composite dataset identity | `NOT_RUN_BLOCKED` | Valid raw IWM bundle is required |
| Deterministic PIT population | `NOT_RUN_BLOCKED` | No PIT scanner-universe input supplied |
| Exact numerical matrix feasibility | `NOT_RUN_BLOCKED` | Required IWM/composite/population prerequisites were not met; no ranking was run |
| Reference-role contract | `PASS` | Contract-level separation only; PIT/runtime execution evidence remains unrun |
| Receipt forbidden-field gate | `PASS` | No forbidden economic fields |

The explicit state machine permits `M3_DATA_GATE_PASS` only when every
mandatory gate is `PASS`; `UNKNOWN` and `NOT_RUN_BLOCKED` cannot pass. A
nonzero, all-passing exact matrix audit and a valid composite identity are
also mandatory for any future data PASS.

## Repaired false-PASS paths

- R1 manifest/run topology is now Git-object verified and rejects a
  manifest commit that changes runner code.
- The R2 runner uses the committed manifest blob and pins A2 tree/blob/raw
  identities.
- Secret-scan fixture entropy was reduced to `fixture_v1`; the scan itself
  remains enabled.
- Composite construction is explicit and required; dead merge arguments were
  removed.
- Raw IWM provider identity and payload continuation tokens are frozen and
  compared page by page.
- Descriptor-based staging uses `O_NOFOLLOW`, `fstat`, mutation checks,
  `fsync`, and atomic rename without an unsafe fallback.
- Every malformed reference row is accounted for; rejected and zero-volume
  rows block the gate.
- Weekly coverage distinguishes warm-up, outage, stale, misaligned, and no
  feasible-window states.
- Numerical candidates must come from a deterministic prior PIT scanner pool;
  the diagnostic symbol option cannot contribute to PASS.
- The legacy runner now binds a future PASS to Git, canonical data, the IWM
  sidecar, composite identity, panel digest, calendar identity, all gate
  states, matrix counts, and the self-digest. A self-consistent fake receipt
  is insufficient.

## Scope exclusions

No market-data network call, IWM fetch, IWM fabrication, economics run,
candidate alpha ranking, optimization, promotion, PAPER/LIVE change,
production strategy/risk/execution/broker/runtime/config change, or
candidate-owned OCP was performed. The same-run V3-control requirement remains
declared but unexecuted.

## Validation

- Focused M3 data-gate tests: `164 passed`.
- M3 data-gate plus legacy M3/M4 tests: `167 passed`.
- Full local suite: `1343 passed`, `11 skipped`, and `6 unrelated sandbox
  socket-bind failures` in webhook/web tests; remote CI remains authoritative
  for those environment-dependent checks.
- `ruff check .`, formatting, and Python compilation passed locally.
- Remote CI run `33550567688` is green on Python 3.12, 3.13, and 3.14. Each
  matrix job passed lint, formatting, type checks, focused OCP tests, focused
  M3 data-gate tests, the full pytest suite, production isolation, source and
  wheel builds, wheel smoke import, dependency audit, and Secret scan.
- GitHub reported only the existing Node.js action deprecation annotation;
  it did not affect the successful conclusion.
