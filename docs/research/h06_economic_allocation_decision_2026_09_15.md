# H06 economic allocation decision — 2026-09-15

**Decision: `ECONOMIC_ALLOCATION_REJECT`.** Reject the proposed allocation of
95% accepted V3 plus 5% capacity-filtered H06 on the evidence currently
available. It has not established an executable improvement over V3.

This is an allocation decision requested after the original failed campaign.
It does not replace that campaign's immutable
`H06_FINAL_ONE_SHOT_EXECUTION_FAILED` record or certify its provisional
`H06_RESEARCH_REJECT_ECONOMIC` output as a valid complete experiment.

## Economic basis

The observed holdout overlap is 2026-01-02 through 2026-07-29, measured from
the 2025-12-31 closing NAV. The H06 component has no trades or open positions
throughout those 143 sessions. Its NAV remains positive cash in all three
cost cells. V3 earns 11.788854% over the same interval.

| Cost multiplier | Shared allocation return | Difference from V3 |
| --- | ---: | ---: |
| 1x | 11.278109% | -0.510745 percentage points |
| 2x | 11.278271% | -0.510584 percentage points |
| 3x | 11.278432% | -0.510422 percentage points |

The frozen verifier requires a nonnegative holdout return difference (gate 8).
The observed portion fails this economic condition. All 143 observed rows in
each cell satisfy the reported exposure and position limits: maximum measured
exposure is 52.25%, maximum positions is six, and violations are zero.

For a constant cash component, the difference follows directly:

`R_shared - R_V3 = -cash_fraction_at_window_start * R_V3`.

Both quantities on the right are positive before the minus sign. Therefore
the sign of the drag does not depend on the exact positive starting cash
left by the invalid 2024 execution. This observation does not establish how
a corrected strategy would have traded.

## What this decision does and does not establish

- The rejected object is the current proposed shared allocation. No capital
  allocation, research admission, PAPER/LIVE activation, or promotion follows.
- Standalone H06 is not shown to be economically unprofitable: the preserved
  runner reports 15.8737% full-window return and 5.3676% holdout return at 1x
  costs; 3x figures are 15.7866% and 5.3395%. These are reported diagnostics
  within an invalid full bundle, not a standalone promotion decision.
- The original shared ledger has 57 cap-violating rows in June 2024. Its
  independent verifier still fails with `overlay_cap_violation`. This review
  does not waive that failure, alter any frozen evidence, or rerun economics.
- The protocol ends on July 31, but the available V3 overlap ends on July 29.
  July 30–31 are absent. The observed-window rejection supports the present
  allocation decision; it is not certification of complete frozen-holdout
  coverage, nor proof that every possible H06 implementation must fail.

## Evidence and checks

The companion [review receipt](../../data/research_receipts/h06_economic_allocation_review_2026_09_15.json)
records exact metrics and SHA-256 identities. Read-only checks matched every
published result file against the run receipt, checked the pinned V3 source,
reconciled the candidate trade quantities to zero positions before holdout,
checked constant cash and every observed row's NAV chain/caps, and recomputed
the window returns and cash-drag identity from the preserved ledgers.

Original records:

- [Frozen protocol](h06_final_economic_protocol_2026_09_15.md)
- [Original final report](h06_final_result_2026_09_15.md)
- [Original verification failure](../../data/research_receipts/h06_final_verification_2026_09_15.json)

This addendum is deliberately separate from those immutable records.
