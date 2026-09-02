# Equity M3 data gate R4

## Mission and scope

R4 is a research-only provenance, acquisition, reference-data, and point-in-time
population gate. It does not run OCP, economics, P&L, ranking, optimization,
PAPER, LIVE, promotion, or V3 changes. Automatic promotion remains disabled.

The frozen wire interval is
`[2016-01-01T00:00:00Z, 2026-07-31T00:00:00Z)`. The required reference
sessions are the 2,658 NYSE sessions from `2016-01-04` through `2026-07-30`.
Bars at the July 31 boundary are outside the evaluation range and must block;
they are never silently trimmed.

## Frozen lineage

The append-only chain is:

`C5 5d2b5347ccb1595a2c836fdcf2d6c11088784145`
` -> D5 890d7633559e2982c3894aae15f28b901e5874a4`
` -> A6 d3b9db8006371f11411050638364a7a8e88e4fcb`
` -> B6 cdc3d5c4cf886208435a33ac100673f6ab0247ee`
` -> A7 f58fe1916224756f1ef770c55990a82fccf86e7d`
` -> B7 9a6b81a8c78471850cd58c09253562d73ba1a8e1`
` -> A8 c7729bb7aadca8b3cd8bf91dac5eb8895395c987`
` -> B8 b8ca990a641f38f43a9b272be598d725c27e53a1`
` -> A9 b0d8ef807dfcfb0a96bac9cf7d60c94f768f587c`
` -> B9 b86ff7f5c75258ccabac98c2a1bcf4e4a1513384`
` -> C6 4f1ddd970207406f4a70d278651796bccd5b8f7a`.

D5 is documentation-only and records C5 plus successful GitHub Actions run
`33605283021`. B6, B7, B8, and B9 are manifest-only commits. Each recovery
manifest preserves the exact captured-byte replay and forbids automatic
refetch.

## IWM acquisition

One live, read-only acquisition was performed against only
`data.alpaca.markets/v2/stocks/bars`, symbol `IWM`, feed `sip`, timeframe
`1Day`, split adjustment, ascending order, and the frozen wire interval.

- Attempt: `20260902T090731450518Z_e094e857784c`
- Result: `ACQUIRED`, `PROMOTED_ACCEPTED_BUNDLE`
- Pages / rows: `1 / 2658`
- Sessions: `2016-01-04` through `2026-07-30`
- Raw bundle SHA-256: `9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258`
- Aggregate page digest: `39a87290b4ebafbba3c29663f5577c05c7548252e6699e5f39589849211c93eb`
- Page-chain digest: `a5b5e400bb97aff3f6342bbe0b969f5d0ae5600406d5cd186ec62e0360480e1b`
- Source receipt SHA-256: `2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003`
- Compact result contains no credentials or tokens.

The accepted raw pages and source receipt remain ignored external artifacts.
All later work used replay/local bytes only; no refetch occurred.

## PIT scanner population

The production `EquityUniverseScanner` (`equity-scanner-v3`) was used with the
config-backed daily cadence, strict prior data, explicit empty snapshots, and
pre-shortlist exclusion of `SPY`, `IWM`, and `QQQ`. No alpha or economic ranking
was used.

The corrected replay build produced 2,659 scheduled snapshots, including one
explicit empty snapshot, 890 unique candidates, and raw PIT SHA-256
`bfdf6e3f87d5c42a1923bc4009f2fa1892ade7317e11caf139d30239bac3f5ae` at
24,774,560 bytes. Its final snapshot is
`2026-07-31T04:00:00.000000Z`, with input through
`2026-07-30T04:00:00.000000Z`.

That July 31 snapshot is outside the required evaluation endpoint. The gate
rejected the PIT artifact and compact PIT manifest; neither was trimmed or
promoted as a passing population.

## Gate result

The final offline gate receipt is `M3_DATA_GATE_BLOCKED`.

| Area | Result |
|---|---|
| Commit topology, implementation blobs, manifest-only commit | PASS |
| Configuration, dependency, acquisition script, capture journal | PASS |
| Canonical, lifetime, corporate-action, and composite identities | PASS |
| IWM bundle, provider contract, pagination, normalization, boundary | PASS |
| Required reference sessions, calendar, row quality, common sessions | PASS |
| Full and recent weekly coverage | PASS |
| Reference-role contract and forbidden-receipt fields | PASS |
| PIT artifact, PIT manifest, PIT reference boundary, PIT cadence | FAIL |
| Deterministic population and strict-prior mandatory gates | FAIL |
| Design matrix feasibility | NOT_RUN_BLOCKED |
| Clean worktree at gate start | FAIL; result inputs were present as uncommitted C6 candidates |

The blocking reasons are the July 31 PIT boundary, the consequently blocked
design-matrix phase, and the recorded clean-worktree failure. The source and
reference-data gates themselves pass at exactly 2,658 required sessions.

## Recovery record

After B6, the first replay-only PIT attempt exposed an extended R4 population
contract mismatch; A7/B7 corrected that contract. The next attempt exposed
valid frozen-v5 `1Day` daily rows; A8/B8 normalized that provider spelling to
internal `1d`. The next attempt exposed valid same-session rows appended in
non-symbol order; A9/B9 validated timestamp monotonicity, rejected duplicate
symbols per session, and sorted only each in-memory session group.

The resulting PIT build succeeds, then the unchanged July 31 boundary is
rejected by the R4 gate. No raw capture, sidecar, or PIT bytes were rewritten,
trimmed, or refetched during recovery.

## Verification and artifacts

Final focused acquisition/PIT/gate tests pass: `187 passed`. Ruff, formatting,
and Python compilation pass for the changed files. Earlier repository-wide
validation also passed Ruff, formatting, mypy, package build, wheel smoke, and
dependency audit; the six loopback-only tests that cannot bind under the
sandbox passed under approved local socket execution.

Tracked C6 artifacts are the compact acquisition receipt, blocked gate
receipt, composite manifest, compact PIT manifest, and this report. Raw JSON
pages, credentials, normalized sidecar, and full PIT JSON are ignored and not
tracked.

Remote CI for C6 completed successfully in GitHub Actions run
`33615339965`. The required matrix jobs all passed: Python `3.12` (`quality
(3.12)`), Python `3.13` (`quality (3.13)`), and Python `3.14` (`quality
(3.14)`).

FINAL VERDICT: M3_DATA_GATE_BLOCKED
