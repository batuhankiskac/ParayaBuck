# Final structural decision

`RESIDUAL_REVERSAL_PHYSICAL_CAPACITY_REPLAY_BLOCKED`.

The cap-eight physical replay breached the frozen crypto position cap. Candidate capacity and materiality are not interpreted. No economic outcome was calculated or reported.

# Current main authority

Production `main`: `be440821e34d859f2e693c45532566eb0dafb1f8`.

# Parent re-entry review authority

Committed parent: `690ce42e1852ead0ccaaddbf3822b10a65a6a77c`.

# Old candidate authority

Archived residual reversal: `a3a54cff64a9312300ef700f2fe9409435a064d3`; identity `equity_short_term_residual_reversal_v1`.

# Physical replay authority

Tagged V2: `11890e93ad1a5223cb11483488deb4d5a6aec3d3`. The [infrastructure provenance](PHYSICAL_INFRASTRUCTURE_PROVENANCE.json) records copied file hashes and intentional research-only instrumentation. The accepted cap migration is `9c5e61382e8cdb71fdd3442d945719024accedd9`; corrected causal crypto is `b135d73743ec2652f79b8c840df50bd3f67166e8`.

# Reversal physical-clock equivalence

`REVERSAL_PHYSICAL_CLOCK_EQUIVALENT`; see the [clock receipt](REVERSAL_PHYSICAL_CLOCK_EQUIVALENCE.json). No target or fill chronology mismatch was observed under its stated comparison scope.

# Incumbent replay parity

`PASS`; the normalized combined cap-six V2 physical event hash matched `841091dd6ebedbc03e3dde27eaf7bee09e54474e660554c3c4b28423d3`. The [parity receipt](INCUMBENT_PHYSICAL_PARITY_RECEIPT.json) also records isolated crypto and V3 checks.

# Frozen arbitration

The [pre-outcome freeze](FROZEN_CAPACITY_MATERIALITY_SPEC.md) was committed before treatment replay. Crypto precedes equity; existing positions and working opening BUYs occupy slots; pending SELLs do not free slots. V3 and reversal retain separate ranks and budgets; overlap shares one account slot, with proportional per-name reductions. No sleeve transfers are allowed.

# Arm identity

Both arms use V3 `0.4125`, reversal `0.1375`, crypto `0.05`, crypto cap `2`, the same canonical inputs and execution rules, and the same flat starting account. Global cap `6` versus `8` is the only configured arm difference. Five inert, flat postterminal V2 feed events were excluded from the frozen July 31 window under an explicit [tail receipt](TERMINAL_TAIL_RECEIPT.json). This window check passed on cap six; cap eight failed the independent full-trace check before a windowed result was accepted.

# Cap6 candidate capacity

Two cap-six executions passed the independent per-arm ledger and frozen endpoint checks. Their candidate capacity metrics are withheld because the cap-eight arm failed integrity before a valid comparison and determinism receipt could be completed.

# Cap8 candidate capacity

The first cap-eight execution failed at `2026-06-29T02:00:00+00:00`. The account had eight global claims and three active crypto symbols: `BTC/USD`, `LTC/USD`, and `DOGE/USD`. The frozen crypto cap is two. The [failure receipt](CAP8_INTEGRITY_FAILURE_RECEIPT.json) and compressed [structural trace](CAP8_BLOCKED_STRUCTURAL_TRACE.json.gz) locate the exact events.

# Crypto slot consumption

At `2026-06-29T01:00:00+00:00`, the BTC SELL order completed but left BTC inventory. LTC was also still held. The physical V2 deferred BUY activation checked the global position cap, admitted DOGE, and its next hourly fill opened a third crypto position. This is a research replay integrity failure under the specified account contract.

# V3 capacity

Not interpreted because cap eight failed the independent crypto cap check.

# Reversal capacity

Not interpreted because cap eight failed the independent crypto cap check.

# V3/reversal overlap

Not interpreted because cap eight failed the independent crypto cap check.

# Candidate-specific count denials

Not interpreted. No count-denial reduction is claimed.

# Count-induced unused reversal exposure

Not interpreted. No capacity improvement is claimed.

# Decision-level admission differences

Not interpreted. No materiality percentage is claimed.

# M1–M8 gate table

| Gate | Result |
| --- | --- |
| M1 candidate blocking at cap six | Not evaluated |
| M2 count-denial reduction | Not evaluated |
| M3 count-induced unused exposure reduction | Not evaluated |
| M4 candidate admission changes | Not evaluated |
| M5 no incumbent displacement pathology | Not evaluated |
| M6 deterministic two-run arms | Not established; cap eight stopped after its first failed run |
| M7 physical causality | Not certified for a valid cross-arm result |
| M8 slot correctness | **FAIL**; cap eight reached three crypto positions against cap two |

# Determinism

Cap six ran twice and each execution passed the per-arm independent verifier. A complete two-run comparison was not performed because cap eight's first run failed. Determinism is not claimed.

# Independent verification

The independent verifier reconstructed positions, working reservations, source claims and fill chronology from structural events. It rejected cap eight at event index `242384` for the crypto cap violation. See [verification](INDEPENDENT_CAPACITY_VERIFICATION.json).

# New economics calculated

`NONE`.

# Economic leakage audit

`PASS`; JSON artifacts and the compressed cap-eight structural trace contain no prohibited economic outcome fields. See the [audit receipt](NO_ECONOMIC_LEAKAGE_RECEIPT.json).

# Validation

Focused research tests: `30 passed`. Complete repository pytest: `1223 passed, 1 skipped`, with two existing deprecation warnings. Ruff check and format check passed for the added research code; the byte-identical archived candidate copy is excluded from formatting. Strict mypy passed for the eleven added typed source files. JSON files and the compressed structural trace parsed and passed the no-economic-field audit. The staged diff passed `git diff --cached --check`; the credential scan found no matching key material. No new CSV artifact was generated because valid cross-arm results do not exist.

# Production changes

`NONE`. No production strategy or configuration file was changed. `main` retains global cap eight and LIVE disabled. No PAPER or broker action was started.

# If proven: future frozen economic test

Not authorized. Materiality was not proven.

# If not proven: candidate remains closed

The valid-replay `MATERIALITY_NOT_PROVEN` classification does not apply because cap-eight integrity failed. Residual reversal remains closed under the parent review; no economic retest is authorized.

# Exact next action

`REPAIR_DEFERRED_CRYPTO_CAP_ADMISSION_AND_RERUN_FROZEN_STRUCTURAL_ARMS` on a new, pre-outcome review protocol. Recheck the crypto cap when deferred BUYs activate after a SELL order completes with remaining inventory; prove incumbent parity and rerun both six/eight arms twice before interpreting capacity. The research replay must not change this arm after observing its failure.

# Final branch SHA

The local evidence commit containing this report is the final branch SHA; resolve it with `git rev-parse HEAD`. No ref was pushed.
