# Crossasset retrospective diagnostic amendment V2

Status: frozen before outcomes on 2026-09-09. This is a research-only
amendment for X01-X06. It does not authorize production, configuration,
registry, PAPER, LIVE, trading, purchase, subscription, or promotion.

The target cutoff is `2026-07-31T23:59:59Z`. A signal may use only the
candidate's declared causal field when that field is present in the pinned
evidence. The only permitted retrospective lagged views are 1, 3, and 7
calendar days. Dimensionless transforms are allowed only when their inputs
are same-unit fields from one source row. A missing `available_at`, revision
vintage, identity, borrow, margin, settlement, or executable fill field keeps
the branch diagnostic-only and prevents admission.

The frozen diagnostic formulas are:

- X01: Treasury 10-year minus 2-year par-yield slope divided by the absolute
  2-year yield, with a `1e-9` denominator floor. This is a curve-shape
  diagnostic and is not an ETF return, duration, carry, or distribution proxy.
- X04: FINRA short-volume fraction minus `0.5`, retained as a lower-bound
  short-volume diagnostic. It is explicitly not borrow, locate, recall,
  financing, or a synchronized two-leg execution observation.
- X06: mean Binance USD-M funding rate per instrument and UTC day. Funding is
  a dimensionless interval rate; missing availability, OI, liquidation,
  margin, and revision metadata keep it retrospective and non-admissible.

X02 (CME settlement/roll), X03 (PIT country valuation and membership), and
X05 (continuous PIT open interest, dated curve, margin and settlement) have no
defensible diagnostic arm under the frozen evidence because their required
causal fields are absent. Their evidence and terminal public-route blockers
remain in the current matrix/gate package.

For each descriptive branch, the runner records full/recent/holdout windows,
1/3/7-day lag counts, and a standalone-return disposition. Turnover and cost scenarios (`1x`, `2x`, `3x`) are recorded as
`NOT_RUN_MISSING_EXECUTABLE_RETURN`, drawdown/tail and correlation as
`NOT_RUN_MISSING_CANDIDATE_RETURN`, and bootstrap/permutation/placebo as
`NOT_RUN_MISSING_OUTCOME`; regime analysis is `NOT_RUN_MISSING_DECLARED_REGIME_LABELS` because no regime labels are frozen in this amendment. No metric is fabricated from OHLCV or a current
snapshot. Two-leg feasibility is evaluated against the existing research-only
ledger and remains blocked whenever borrow, locate, recall, financing,
collateral, margin, or synchronized execution is absent.

A current-V3 overlay would be exactly `95%` V3 plus `5%` candidate, with
equity `0.55`, crypto `0.05`, gross `0.60`, cash `0.40`, at most two crypto
positions and six total positions. It is not run unless a candidate first
passes its complete causal/economic input gate. This package therefore writes
`overlay_status=NOT_RUN_NO_ELIGIBLE_CANDIDATE`, `economic_run=false`, and
`admitted_rows=0` for every X01-X06 branch.

The machine-readable protocol and source hashes are pinned in
`data/research_manifests/crossasset_protocol_amendment_2026_09_09_v2.json`.
