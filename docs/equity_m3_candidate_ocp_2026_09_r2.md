# M3 Candidate OCP Remote Attestation R2

## Final corrective verdict

The R2 receipt is closed and provenance-bound:

- Receipt: `data/research_receipts/equity_m3_candidate_ocp_2026_09_r2.json`
- Verdict: `M3_CANDIDATE_OCP_ATTESTATION_R2_PASS`
- Blocked reasons: `[]`
- Self-digest: `8a576e650060c27e7f4a0c6f0fd8a532bc2509df26fd58c0587656ad47f766b6`
- Next required mission: `M3_ECONOMIC_MISSION_PREDECLARATION`
- Candidate economics: `false`
- Economic metrics: `false`
- Economic mission authorized: `false`

The C24 pre-CI receipt was intentionally
`M3_CANDIDATE_OCP_ATTESTATION_R2_BLOCKED` with only
`corrective_remote_ci:NOT_RUN_BLOCKED`. After C24 CI was green, the final
receipt was compacted against C24 and its exact CI head.

## Accepted upstream R5 data gate

The accepted upstream result was independently checked from the immutable R5
receipt and manifest:

- Result commit: `3c2f67d31772f2fba118137ad262bd8ffdb737d8`
- Data verdict: `M3_DATA_GATE_PASS`
- R5 mandatory gates: all 38 `PASS`
- R5 receipt self-digest: `41afb58aca2fb38f4f0d301fc169938809b1cb9afcfa5c9855d97e292916abeb`
- Candidate economics: `false`; economic metrics: `false`; economic mission ready: `false`

The R5 receipt and manifest bytes are unchanged at the final repository head.

## Why D23/E23 were superseded

The unversioned receipt and report remain immutable historical artifacts and are
classified `SUPERSEDED_INVALID_REMOTE_ATTESTATION_BINDING`. D23 was
`3436c288fe7052cb98c73fe7713aef2daeb78af2` (CI `33671110579`), and E23 was
`ac442c9f0232711a9eef7ade7a076ed0cc8e5fb2` (CI `33672017181`). D23 attached
C23 CI `33665025444` to the wrong A22/B22 and non-R2 raw lineage; a green run
ID without a matching head binding could not repair that mismatch.

| Field | Expected C23/R2 value | Observed historical D23 value |
| --- | --- | --- |
| CI head | `cd812af9c958c4934ca3da1a23bae00ae58278a4` | `cd812af9c958c4934ca3da1a23bae00ae58278a4` |
| Receipt implementation | `32357c110c09f92b438ee188fac289da966bcd24` | `cae97cf9e059c5c03d79469b29e84d47533119d6` |
| Receipt manifest | `fe52e30e7cca36030621152f6f1bdbcc247f8e9c` | `4d612d9f8fc50a5d4911f2c37d74979c61af93be` |
| Raw artifact path | `data/research/generated/equity_m3_candidate_ocp_2026_09_r2/ocp_run.json` | `data/research/generated/equity_m3_candidate_ocp_2026_09/ocp_run.json` |
| C23 CI run | `33665025444` with head C23 | Run ID was attached without a receipt-compatible lineage proof |

## Substantive C23 evidence

The substantive OCP evidence was reused exactly; no OCP replay occurred:

- Implementation A23: `32357c110c09f92b438ee188fac289da966bcd24`
- Manifest B23: `fe52e30e7cca36030621152f6f1bdbcc247f8e9c`
- Local result C23: `cd812af9c958c4934ca3da1a23bae00ae58278a4`
- C23 CI: `33665025444`, head exactly C23, conclusion `success`
- Python matrix: 3.12 `PASS`, 3.13 `PASS`, 3.14 `PASS`
- Required CI stages: all `PASS`
- Raw artifact: `data/research/generated/equity_m3_candidate_ocp_2026_09_r2/ocp_run.json`
- Raw artifact SHA-256: `609fba66a8e909036b92e20f62dfbb68cfb2308d79e64100198fd2b6be61a05e`
- Raw payload digest: `e23b09537693f1bec8b0e54339821a687ab8bd533a5cee1e6c6aa37ddd43f486`
- Raw verdict: `M3_CANDIDATE_OCP_GATE_PASS`
- Mandatory OCP gates: 42/42 `PASS`

Hash parity is unchanged: C universe
`e1f0e2ee7420a9d43a8fe69e2048e4a5a3e135f3d3ffc5d2bb8b425fdb487eb1`, C
Merkle `d6ec0d61b100ab527ee2978ddcd6210c09f866b03cc62637525c9ac4f2d6509a`, X
universe `eb9b3df623a2fdcb5b02c9c174fbf629a355f4faf018df9989de8703f8907f6f`,
X Merkle `cee257f5053570c2b5957adf5b6b4b8b2c0943273790b20135a24ed5ffbe568f`,
snapshots `2658`, sequence changes `2384`, membership changes `2381`, scanner
mismatches `0`, direct-builder parity `9/9`, and future-bar checks `4/4`.

## Corrective append-only chain

- Required starting head: `ac442c9f0232711a9eef7ade7a076ed0cc8e5fb2`
- A24 implementation: `039591492d823997b48651986e2f9690e02087d7`; parent E23;
  CI `33675436608` fully green.
- B24 initial declaration: `863dc020197ce895004063c0e26de52295e54643`; direct
  child of A24; manifest-only; CI `33676147349` fully green.
- Append-only compactor repair: `6e8067dfa816d2de138004babf5d5f47ab327642`; direct
  child of B24; CI `33677087604` fully green.
- Replacement R2 declaration: `4ebb5fa2353eba437f94b48fc5b00a6cfe0cd902`; direct
  child of the repair; manifest-only; CI `33677886242` fully green.
- C24 local result: `154ffd6925327ea6283903f3f1c1098992efef3e`; direct child of
  the replacement declaration; receipt/report only; C24 CI `33678608824` fully
  green.
- Post-C24 test-only maintenance: `75d439e3968366c071b7283e8e3163855b447c91`;
  CI `33679478221` fully green. It changed only the R2 regression test and no
  critical provenance blob.

The replacement R2 manifest changes exactly one path:
`data/research_manifests/equity_m3_candidate_ocp_remote_attestation_2026_09_r2.json`.
The final receipt binds the corrective implementation, replacement manifest,
C24 result, C24 CI run, and C24 CI head; every critical blob is identical at
each declared commit and head.

## Corrective CI binding

- Corrective CI run: `33678608824`
- CI head SHA: `154ffd6925327ea6283903f3f1c1098992efef3e`
- Conclusion: `success`
- Python 3.12: `PASS`
- Python 3.13: `PASS`
- Python 3.14: `PASS`
- Required stages: lint, format, mypy, focused OCP, focused M3 data gate,
  acquisition/PIT, full pytest, production isolation, build, wheel smoke,
  dependency audit, and secret scan — all `PASS`

## Mandatory corrective gates

All mandatory corrective gates are `PASS`: starting head; clean worktree;
historical D23/E23 topology; defect reproduction; old receipt supersession;
C23 raw identity; C23 raw payload digest; C23 local receipt identity;
C23 implementation identity; C23 manifest identity; C23 result topology; C23
CI run identity; C23 CI head binding; C23 CI matrix; corrective implementation
identity; corrective manifest-only commit; corrective result topology;
corrective CI head binding; critical blob identity; all 42 substantive OCP
gates; substantive hash parity; no economic execution; no market-data network;
no credentials; forbidden-field check; receipt self-digest; and R2-only
economic consumer validation.

## Scope boundary and consumer validation

The economic consumer accepts the completed R2 receipt and rejects the old
unversioned receipt when explicitly pointed at it. It requires the R2 PASS,
empty blocked reasons, A23/B23/C23 substantive identities, exact raw hashes,
C23 CI head binding, corrective CI PASS, topology, all substantive gates, and
the receipt self-digest.

No market-data access, credentials, IWM refetch, OCP economic replay, M3 data
gate replay, economic backtest, P&L, optimization, promotion, PAPER/LIVE
change, strategy/risk/execution/config change, or production behavior change
occurred. All commits are append-only fast-forward commits; no prior history
or superseded artifact was rewritten.

## E24 final remote closure

- D24 full SHA: `f05056198f2519cfd396b1b05d3779bd7e7c2ace`
- D24 CI run: `33680287825`
- D24 CI head: `f05056198f2519cfd396b1b05d3779bd7e7c2ace`
- Python 3.12: `PASS`
- Python 3.13: `PASS`
- Python 3.14: `PASS`
- D24 required stages: all `PASS`
- Final candidate-OCP remote closure: complete
