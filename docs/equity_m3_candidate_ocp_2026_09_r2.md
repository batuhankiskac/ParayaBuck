# M3 Candidate OCP Remote Attestation R2

## C24 pre-CI state

This R2 attestation is a provenance correction for the historical D23/E23
remote-closure mismatch. It does not rerun the OCP, the M3 data gate, or any
economic mission.

The pre-CI R2 receipt is intentionally blocked:

- Receipt: `data/research_receipts/equity_m3_candidate_ocp_2026_09_r2.json`
- Verdict: `M3_CANDIDATE_OCP_ATTESTATION_R2_BLOCKED`
- Blocked reason: `corrective_remote_ci:NOT_RUN_BLOCKED`
- Corrective CI: not run; `local_result_commit` remains unset
- Next required mission: `M3_CANDIDATE_OCP_ATTESTATION_R2_REMOTE_CLOSURE`

## Historical supersession

The unversioned receipt and report remain immutable historical artifacts and are
classified `SUPERSEDED_INVALID_REMOTE_ATTESTATION_BINDING` in the R2 manifest.
The historical D23 commit was `3436c288fe7052cb98c73fe7713aef2daeb78af2`
(CI `33671110579`), and the historical E23 commit was
`ac442c9f0232711a9eef7ade7a076ed0cc8e5fb2` (CI `33672017181`). Their receipt
binding used the older A22/B22 chain and a non-R2 raw artifact; a CI run ID was
not sufficient to bind that receipt to its tested commit.

## Substantive C23 evidence

The substantive OCP evidence is reused exactly, without replay:

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
- Economic fields and execution: absent/not run

The C/X hash-parity evidence remains unchanged: snapshot count `2658`,
sequence changes `2384`, membership changes `2381`, scanner metadata
mismatches `0`, direct-builder mismatches `0`, and future-bar failures `0`.

## Corrective append-only chain

- Starting head: `ac442c9f0232711a9eef7ade7a076ed0cc8e5fb2`
- A24 implementation: `039591492d823997b48651986e2f9690e02087d7`; parent E23;
  implementation/test paths only; CI `33675436608` fully green.
- B24 initial declaration: `863dc020197ce895004063c0e26de52295e54643`; direct
  child of A24; manifest-only; CI `33676147349` fully green.
- Append-only repair: `6e8067dfa816d2de138004babf5d5f47ab327642`; direct child
  of B24; the R2 compactor source self-check was corrected; CI `33677087604`
  fully green.
- Replacement declaration: `4ebb5fa2353eba437f94b48fc5b00a6cfe0cd902`; direct
  child of the repair; manifest-only; CI `33677886242` fully green.

The replacement declaration binds the critical implementation, workflow, and
consumer blobs to the repaired implementation commit. No receipt or report is
included in this declaration commit.

## Scope boundary

No market-data access, credentials, IWM refetch, OCP replay, M3 data-gate
replay, economic backtest, P&L, optimization, promotion, PAPER/LIVE change,
strategy/risk/execution/config change, or production behavior change occurred.
All commits are append-only fast-forward commits; prior history and the
superseded artifacts were not rewritten.
