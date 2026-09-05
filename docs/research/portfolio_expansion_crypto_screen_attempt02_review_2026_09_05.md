# Portfolio Expansion Crypto Screen — Attempt 02 Review

Date: 2026-09-05

## Current acceptance status

**BLOCKED by frozen-source drift; no current-source native acceptance.** The prior native validation receipt and 27-pass claim are historical evidence for the earlier runner snapshot. The current runner SHA is 8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d, while the frozen manifest and test fixture still pin ca623c3fc79dc67a4e655237cc85fdab3384f1edd0868ea08112d0b6c70df0da. The frozen manifest's own canonical digest remains internally valid for its recorded bytes, but it does not authorize the current runner bytes. Do not update the frozen manifest in this review.

The current focused command, from the repository root, was:

.venv/bin/python -m pytest -q tests/research/test_portfolio_expansion_crypto_screen_2026_09.py

It produced 22 passed and 5 failed. The native positive CLI failed closed at the stale runner source pin. The protocol/settings mutation tests were masked by that same stale source pin, so their individual current-source behavior was not established. This is a provenance/fixture synchronization blocker, not an economic rejection.

## Why the earlier 27 tests missed the downside failure

The earlier suite did not execute the native pipeline for downside, reversal, high-proximity, or low-disagreement. Its downside coverage supplied an interior missing day, causing the daily-window helper to return no window before the score loop reached the faulty strict zip. The only real subprocess/native proof exercised full/low_variance22/1x. Direct score tests for the other adapters therefore could not expose a failure in their inherited native decision path, target construction, scanner history shape, or engine execution. The source line now has a corrected window pair in the current working tree, but that correction is precisely why the old frozen pin cannot be silently reused.

## Required four-arm evidence before PASS

After the owner freezes a new source snapshot and matching manifest/test receipt, native synthetic coverage must enter each of downside22, reversal70, high_proximity364, and low_disagreement5_45 with finite, predictable scores and actual targets. Each case should prove:

- strict daily-window lengths and exact formula: downside uses 23 prices/22 returns; reversal uses 71 prices and negative-only cumulative score; proximity uses 364 prices and high-window ratio; disagreement uses 50 daily prices with exactly 5 recent and 45 prior positive finite volumes;
- future-bar and same-as-of prefix invariance;
- actual score passed into HYST4/top-2/96h policy rather than silently calling the incumbent low-variance score;
- native scanner eligibility, target construction, next execution hour, actual fill/fee, and terminal cash plus marked-position reconciliation;
- missing/duplicate/interior-gap handling and source/protocol/settings/input hash guards;
- shard 42-cell partition, 14 cells per shard, atomic checkpoint, existing-output refusal, and canonical digest excluding only manifest_sha256.

The existing native report proves one low-variance cell only. It cannot be generalized to all seven arms or to a complete 42-cell screen.

## Pin record

- Current runner: 8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d
- Frozen manifest: data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen.json, file SHA 8e2eb96f18879aa32455e6257e38941259a305682cb4c24f69c5d89d4f81f6f2, declared/calc digest 0e6e8f90c03ead1be7f82feb21f4442ec92f851759bfa32ac1451a7e92d0ba8f
- Frozen manifest runner pin: ca623c3fc79dc67a4e655237cc85fdab3384f1edd0868ea08112d0b6c70df0da
- Protocol: 39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52
- Test file currently has stale draft/frozen pin assumptions; its file SHA should be recomputed only after the owner supplies the new stable source/manifest pin.
- Native report: 5657759a019322289be5ef8aea99d9e07278d0324cf90c166b3c2176dcb9537d

No source, test, manifest, or configuration edit was made for this review.

## Corrected validation bundle closure

The corrected validation bundle supersedes the stale-pin finding above for
validation-only acceptance. The corrected frozen manifest is
`data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen.json`;
its declared canonical digest and the corrected receipt value are
`b26c2afae9a03bbb93fe8d09bc26ff0df087f05716659848e3ce07fd9bc56710`.
The manifest's current raw file SHA is
`0457d01dd24cf293ae8271f17e6995c950aff6180a74c110a37b04a2a642fa0e`.
The matching validation receipt is
`data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt02_validation_receipt.json`.

Read-only pin comparison passes for the corrected source set: runner
`8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d`, test
`67a503d43fd429d2baeb7bec27606b8e432f0174e3581d6b13fa5b6fee9e162c`, protocol
`39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52`, settings
`414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`, and data
`73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e` all match
the manifest's current pins. The reported focused command is recorded as
`28 passed in 2.70s`; input validation is recorded as `INPUTS_VERIFIED_NO_RUN;
cells=42`.

The four-arm native evidence is recorded in
`docs/research/portfolio_expansion_crypto_screen_native_validation_attempt02_2026_09_05.md`
and the matching receipt: downside22 4,212 fills, reversal70 10,795,
high_proximity364 68, and low_disagreement5_45 17,327, all full-window 1x
validation runs with nonzero fees and 40,151 curve points. This is a
validation receipt and report, not a 42-cell economic result; the old attempt
01 remains immutable and failed before completion. Therefore the corrected
bundle is **PASS for source/pin/test/native-path validation**, while canonical
screen economics remain unrun and cannot be inferred from these four checks.
