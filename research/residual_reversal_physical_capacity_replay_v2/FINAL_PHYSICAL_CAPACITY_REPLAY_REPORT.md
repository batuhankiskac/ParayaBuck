# Final no-return physical capacity replay review

## Decision

`RESIDUAL_REVERSAL_PHYSICAL_CAPACITY_REPLAY_BLOCKED`.

The production crypto-cap defect was reproduced against the real offline runtime and repaired on local `main` at `7724a2bc9e61ba325a6a252c8f518cabd99d6c69`. The replay branch descends from the original blocked evidence and incorporates that production authority. Its immediate and deferred BUY admission now uses the same global and crypto-specific slot helper. The BTC residual plus LTC plus deferred DOGE regression passes: no third crypto opening is admitted.

## Original invalid cap8 evidence

The original receipt and trace remain unchanged in `../residual_reversal_physical_capacity_replay_v1/`, with SHA256 values recorded in `REPAIRED_PROVENANCE.md`. The invalid DOGE fill at `2026-06-29T02:00:00+00:00` is provenance only. None of its candidate materiality results are used.

## Fresh structural runs

Cap6 ran twice and cap8 ran twice under the same pinned source files and frozen arm specification. Every individual arm passed the independent physical verifier, the frozen terminal-window check, global claim count, crypto cap two, and causal fill checks. Repeat event hashes match within each cap. Both arms have the same price-event clock hash. The terminal cleanup removes unfilled deferred intents before later corporate-action-only feed events; the earlier non-inert tail failure is retained in `TAIL_INTEGRITY_FAILURE_RECEIPT.json`.

The independent cross-arm verifier returned `BLOCKED` because equity source demand differs between cap6 and cap8. Its frozen identity gate remains unchanged. Cap6 source-demand SHA256 is `88f205d9ddcffb1a105a11e2ddc207f804259a918871dec1a804788fc6a15d68`; cap8 is `3c0245a476aefbc07fcd580860c8ceb64b199d9ce35a87878efd688a3bacaec7`. The first differing request is V3 rank one at `2017-02-21T21:00:00+00:00`: AMAT for cap6, BAC for cap8. Reversal requests also diverge then. This occurs before the crypto series. `SOURCE_DEMAND_DIAGNOSTIC.json` holds both request sets.

The engine's bounded observation context pins held positions and pending orders before filling remaining stream slots. This gives account state a path to affect subsequent source demand. The exact pin responsible for the first difference has not been isolated. The observed cross-arm request difference is sufficient to fail the precommitted identity check; any materiality comparison would mix slot-cap effects with changed source requests.

## Gate interpretation

The two independent arm checks and determinism checks passed. The cross-arm source-demand identity prerequisite failed, so **M1–M8 candidate materiality is not interpreted**. The comparison's blocked status does not imply a noncausal fill or excess slot claim within either arm. The frozen comparison requires source-demand identity before publishing a materiality decision.

The new cap6 and cap8 raw traces and complete per-arm independent receipts are retained here. A separate verification pass reloaded both traces, reproduced the saved receipts and event hashes, and again obtained `BLOCKED` from the unchanged cross-arm comparator. The original invalid receipt and trace hashes were rechecked byte-for-byte. All 11 JSON artifacts passed syntax and economic-field audits. The replay-focused tests passed (29), the complete normal suite passed (1,234 passed, 2 skipped), Ruff passed for touched code, the touched-code Ruff format check passed, project-configured strict source mypy passed (133 files), and `git diff --check` passed. A repository-wide research format check encounters one inherited formatting difference in `v1/candidate.py`; that file was not changed in this repair.

## Boundaries and next action

New economics calculated: `NONE`. Production strategy changes: `NONE`. PAPER/runtime started: `NO`. LIVE: `DISABLED`. Global cap remains `8`; crypto cap remains `2`. The research candidate remains closed.

Exact blocker: cap6 and cap8 produce different equity source requests under the physical bounded observation stream, violating frozen cross-arm source-demand identity. A separate, precommitted causal observation/demand protocol and a fresh no-return comparison would be required to revisit materiality. No threshold or gate was changed after seeing these outcomes.
