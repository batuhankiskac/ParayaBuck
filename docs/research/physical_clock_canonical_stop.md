# Physical-clock canonical baseline stop — 2026-09-23

The research clock audit at `377d260835c5a5f8ea23b61e16bf8103a842b0e0`
ended with `PHYSICAL_CLOCK_REPAIR_EXPOSED_CANONICAL_BASELINE_CORRECTNESS_ISSUE`.
Its local annotated archive tag is
`archive/research/physical-availability-clock-v1-canonical-stop-2026-09`.
The research-only timing helper and audit are provenance, not validated
combined replay infrastructure.

The frozen crypto control consumes the completed 2021-04-01 daily BTC/USD
and ETH/USD bars and records two nonzero buys at the 2021-04-02 00:00 UTC
daily opening prints. The final 2021-04-01 hourly components complete at that
same boundary. An order created from the completed daily signal cannot be
assumed to obtain an opening print at the same instant. The clock campaign
stopped before a corrected crypto replay, new combined cap-six result, or any
cap-eight migration experiment. `SHARED_ACCOUNT_REPLAY_CLOCK_NOT_READY`
continues to apply.

The affected historical crypto baseline needs a separate causal-execution
maintenance campaign. Until that repair passes, existing baseline economics
remain historical provenance rather than a physically validated execution
control. Production strategy, configuration, PAPER, and LIVE behavior were
not changed by the clock audit.

Later resolution: the corrected causal crypto control and physical-clock V2
research infrastructure are recorded in
`docs/research/physical_availability_clock_v2_closure.md`. The V1 stop above
remains historical provenance, not the current migration-readiness decision.
