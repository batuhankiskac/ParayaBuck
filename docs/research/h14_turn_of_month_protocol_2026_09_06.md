# H14 turn-of-month protocol — 2026-09-06

Status: `DATA_BLOCKED` preflight; protocol frozen for review, no runner and no
economic run. This artifact defines one candidate only. It does not claim a
close fill, future data, joint-capital performance, or PAPER/LIVE eligibility.

## Frozen candidate

* ID: `h14_spy_turn_of_month_lastday_first3_v1`.
* Symbol: `SPY` only; no cross-sectional ranking and no additional signal.
* Calendar: NYSE regular sessions, `pandas-market-calendars==5.4.0`.
* For each calendar month `m`, define `L0(m)` as the last NYSE session in `m`,
  `F1..F3(m)` as the first three NYSE sessions in `m+1`, `F4(m)` as the next
  NYSE session after `F3`, and `P0(m)` as the NYSE session immediately before
  `L0`.
* Signal is deterministic calendar exposure over `L0, F1, F2, F3`. The
  decision is made after the closed `P0` bar and the entry order is filled at
  the next supported tradable open, `L0` open. The exit decision is made after
  the closed `F3` bar and the exit order is filled at the next supported
  tradable open, `F4` open. No close execution is assumed.
* The product fill convention is therefore an open-to-open adaptation of the
  original four-trading-day turn-of-month observation. A missing `P0`, `F1`,
  `F2`, `F3`, or `F4` excludes that event from a realized run; it is never
  filled, interpolated, or marked as a close execution.
* Full supported input window: `2016-01-04` through `2026-07-30` inclusive.
  Recent supported input window: `2022-01-03` through `2026-07-30` inclusive.
  These are actual local endpoints; no August 2026 bars or synthetic future
  rows may be created. The July 31 source snapshot is outside the evaluation
  endpoint and must not be consumed.

## Price and corporate-action basis

The canonical bar manifest declares `feed=sip`, `adjustment=split`, 1d bars,
and a last source timestamp of 2026-07-31. Product code documents the equity
model as split-adjusted signal/execution prices plus explicit dividend cash
flows, with next-tradable-open fills. The local canonical CSV contains SPY
OHLCV for all 2,658 supported sessions and the local corporate-action CSV has
40 SPY dividend rows.

The current corporate-action source schema is
`action_type,symbol,ex_date,record_date,payable_date,rate,old_rate,new_rate,
source_symbol,target_symbol,raw_id`; it is not the product loader's required
`timestamp,symbol,action_type,value` schema. Consequently the action input is
not yet directly executable. A future preflight must define and hash a
lossless ex-date/value normalization, prove split-adjusted prices are not
double-adjusted, and state whether dividend cash is credited on ex-date or the
payable date. Until that gate passes, the correct status is `DATA_BLOCKED`, not
an economic failure.

The daily bars' `open` field is the only local candidate entry price supported
by the existing engine convention. The bar timestamp is a session label; it is
not evidence of an intraday auction timestamp. A run must therefore use the
next session's stored open and record the session label and fill-price source.

## Proposed isolated diagnostic design

* Initial NAV: `100000.00`; candidate diagnostic budget: fixed 5% (`5000.00`)
  of this isolated NAV, with 95% cash. This is a test sleeve convention only;
  it does not draw from protected equity cash and makes no joint-account claim.
* Entry: buy SPY at `L0.open`; exit: sell at `F4.open`; one position at a time.
  If an event overlaps due to malformed calendar data, fail closed rather than
  netting or extending the position.
* Cost scenarios: fixed `1x`, `2x`, and `3x` multipliers of the current equity
  execution-cost model, read and hash-pinned before any run. No cost, spread,
  slippage, or threshold may be fitted to results.
* Controls: (1) same-SPY 5% passive/cash from the first supported open through
  the actual evaluation end; (2) an exposure-matched neutral calendar control
  that invests 5% at the open of the fourth NYSE session after each `P0` and
  exits at the open after its fourth session, using the same four-session
  duration and cost model. The control's dates are fixed by the manifest, not
  selected after results.
* Existing V3 comparison is optional and only allowed on exact shared SPY
  decision/fill timestamps with the same data endpoint. No interpolation,
  rebasing, or comparison to a different terminal grid is allowed. If exact
  grids do not align, report `comparison=NOT_COMPARABLE`.
* Required output is event-level entry/exit timestamps and prices, dividend
  cash, fees, exposure duration, cash/NAV reconciliation, and candidate versus
  both controls. No economic verdict is permitted until the source, CA, and
  execution gates pass.

## Preflight gates and stop conditions

1. Verify every input hash in the companion manifest and the repository commit.
2. Verify NYSE sessions exactly, with no off-calendar, duplicate, missing, or
   post-end rows; reject the July 31 source row for these windows.
3. Verify SPY open/high/low/close/volume are finite and positive where required,
   and that every event has the exact `P0,L0,F1,F2,F3,F4` session tuple.
4. Normalize the corporate-action source losslessly to the product contract,
   preserve its provenance/raw IDs, and prove dividend timing and split basis.
   Any failure is `DATA_BLOCKED`.
5. Verify the next-open engine path and record the session-label/fill-price
   mapping. Do not substitute close prices or synthetic opens.
6. Freeze the candidate, controls, 5% isolated budget, windows, costs, and
   endpoint before reading any result. No runner is part of this mission.

## Original evidence and adaptation boundary

McConnell and Xu's original study reports a four-day turn-of-month pattern using
CRSP daily returns over 1926–2005; it does not validate this SPY-only,
open-to-open, split-adjusted, dividend-cash, fixed-5% adaptation. Source:
<https://doi.org/10.2469/faj.v64.n2.11>.

The artifact is therefore a frozen data/execution protocol. Its current verdict
is `DATA_BLOCKED` because the available corporate-action source is not yet in
the product input schema and executable dividend timing is unresolved. No
performance or complementarity claim has been made.
