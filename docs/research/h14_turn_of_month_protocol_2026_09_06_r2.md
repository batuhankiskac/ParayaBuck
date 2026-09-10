# H14 turn-of-month protocol — 2026-09-06 revision 2

Status: `CA_MAPPING_PASS; PREFLIGHT_PENDING_INDEPENDENT_AUDIT`. This revision
preserves the original v1 preflight attempt and corrects its window labels and
corporate-action conclusion. It freezes a research-only SPY diagnostic; it is
not an economic result, product change, PAPER/LIVE instruction, or promotion.

## Frozen signal and execution mapping

* Candidate ID: `h14_spy_turn_of_month_lastday_first3_v2`.
* Symbol: `SPY` only. There is no ranking, fitted threshold, or extra signal.
* Calendar: NYSE regular sessions from `pandas-market-calendars==5.4.0`.
* For month `m`, `L0` is the last NYSE session in `m`; `F1`, `F2`, and `F3`
  are the first three NYSE sessions in `m+1`; `F4` is the next NYSE session
  after `F3`; `P0` is the NYSE session immediately before `L0`.
* After `P0` close, submit the entry and use only `L0` open. After `F3`
  close, submit the exit and use only `F4` open. The position therefore spans
  the four open-to-open intervals beginning at `L0`, `F1`, `F2`, and `F3`.
  Missing any tuple member excludes that event; no close fill, interpolation,
  or synthetic open is allowed.
* The mapping is an executable adaptation of the original four-session
  turn-of-month observation. The original paper does not establish this
  SPY-only next-open implementation.

## Corrected windows

The primary cells reuse the existing supported equity grids and stop at the
last proven equity session, 2026-07-30:

| Cell | Actual input start | Actual input end | Role |
|---|---|---|---|
| `full_2022` | 2022-01-03 | 2026-07-30 | primary full comparison cell |
| `recent_2024` | 2024-01-02 | 2026-07-30 | primary recent comparison cell |
| `long_2016` | 2016-01-04 | 2026-07-30 | optional prespecified context only |

`long_2016` cannot replace either primary cell. The source contains a July 31
row, but that row is outside every evaluation endpoint and is forbidden. The
existing V3 comparison is allowed only where the candidate and V3 share the
exact decision/fill date grid; otherwise it is `NOT_COMPARABLE`.

## Fixed controls and budget

Use an isolated NAV of `100000.00`, with exactly 5% (`5000.00`) reserved for
the candidate and 95% cash. This is a diagnostic sleeve and makes no claim on
protected equity capital. The passive control buys 5% of SPY at the first
supported open in the same cell and holds through the actual endpoint.

The exposure-matched neutral control is one deterministic calendar recipe:
for each candidate event, set `M0=F4` and `M1..M3` to the next three NYSE
sessions after `F4`; enter at `M0` open and exit at `M4` open, where `M4` is the
next NYSE session after `M3`. It uses the same 5% sleeve and four open-to-open
intervals. No alternate windows are selected after results.

The cost scenarios are fixed `1x`, `2x`, and `3x` multipliers of the current
equity execution-cost model, hash-pinned before any run. No cost, spread,
slippage, threshold, or window is fit to an outcome.

## Corporate-action and price basis

The canonical bars manifest identifies SIP 1d bars with `adjustment=split`,
2,658 complete SPY sessions, and source last timestamp 2026-07-31. This means
the adapter must pass split-adjusted OHLCV through unchanged and must not apply
split actions a second time.

The v5 corporate-action file is sourced by
`scripts/fetch_corporate_actions_v5.py` from Alpaca's
`GET /v1/corporate-actions` endpoint (`2016-01-01` through `2026-08-01`) and
retains the source fields `action_type,symbol,ex_date,record_date,payable_date,
rate,old_rate,new_rate,source_symbol,target_symbol,raw_id`. The official API
schema defines a cash-dividend `rate` as cash paid per share; the provider also
defines `ex_date` as the first date a purchase no longer receives entitlement.
See the [Alpaca corporate-action schema](https://github.com/alpacahq/alpaca-docs/blob/master/cli/api/specs/market-data-api.json)
and [date semantics](https://docs.alpaca.markets/us/reference/getcorporateannouncements).

For this adapter, only `action_type=cash_dividends` is admitted:

```text
timestamp = ex_date at 00:00:00 UTC
symbol    = upper(symbol)
value     = decimal(rate)  # cash per share
```

The existing research loader already performs this mapping, filters by the
evaluation end, and emits the native `CorporateAction(timestamp, symbol,
DIVIDEND, value)` object. No production loader or source file is changed. The
generic product loader expects the native four-column schema, so a run must use
the existing research mapping or a separately hashed research adapter; it must
not silently reinterpret the raw CSV.

Local audit evidence is 40 SPY rows, all `cash_dividends`, with no SPY split
rows; all have an ex-date and positive rate. Fourteen lack record or payable
dates. Missing payable metadata is retained and reported, never invented. It
does not change the entitlement timestamp: a position held before the ex-date
receives the dividend, a purchase at the ex-date open does not. The engine's
corporate-action-before-fill ordering and next-bar fill rule implement that
boundary. This is retrospective realized-dividend/NAV accounting only; the
calendar signal does not forecast or consume future corporate-action fields.
The 95% cash sleeve means this accounting does not require dividend funding
from protected capital. Live-feed/PIT quote truth and the actual broker
payment-date posting remain unproved. The adapter must reject a cash row with missing/invalid ex-date or
rate, and must reject any non-cash SPY action rather than treating it as a
dividend.

This is a source-level split gate, not proof that an external provider can
never revise history. The retained manifest proves the local source has split
rows globally but zero for SPY. A provider revision, missing raw response
receipt, or disagreement about the rate unit is a data gate failure and must
produce `DATA_BLOCKED`.

## Required preflight gates

1. Verify the companion input hashes and repository commit before any run.
2. Verify SPY session identity against the NYSE calendar: no missing,
   duplicate, off-calendar, zero-volume, or post-2026-07-30 rows.
3. Verify every event has the exact `P0,L0,F1,F2,F3,F4` tuple and that events
   do not overlap. Exclude terminal events with no supported `F4` open.
4. Verify the raw corporate-action hash, source endpoint/provenance, 40-row
   SPY audit, zero SPY split rows, positive per-share rates, and preserved
   missing-date fields. Confirm split-adjusted bars are not transformed.
5. Verify entry and exit are next-supported-open fills, then record session
   labels, source prices, dividend cash, fees, duration, and NAV reconciliation.
6. Obtain the independent baseline protocol/CA audit and freeze the complete
   source/input manifest. Until that independent PASS exists, do not run the
   canonical backtest; any substantive failure is `DATA_BLOCKED`.

## Evidence boundary

McConnell and Xu report the turn-of-month effect using historical CRSP daily
returns; that evidence supports the economic hypothesis only. It does not
support a performance claim for this SPY, four-session, open-to-open,
split-adjusted, explicit-dividend adaptation. Source:
<https://doi.org/10.2469/faj.v64.n2.11>.

No H14 returns, Sharpe, complementarity, or allocation claim is made here.

## Native research harness

`scripts/research_h14_turn_of_month.py` is the research-only adapter and batch
runner. `validate_manifest` verifies the full input pin set; `load_spy_bars`
and `load_native_spy_bars` enforce the actual SPY session grid; and
`load_spy_dividend_actions` emits native `CorporateAction` values without
changing production loaders. `H14TurnOfMonthStrategy` and
`H14SchedulePolicy` submit only the frozen event-date targets to
`PortfolioBacktestEngine`; they do not hand-roll fills or returns.

`run_batch` is bounded to exactly 18 cells: candidate, one initial
buy-and-hold passive control, and the fixed four-session calendar control,
each across `full_2022` and `recent_2024` at `1x/2x/3x` costs. It emits native
NAV curves, fills, rebalances, marked-position weights, final state, corporate
action receipts, and event traces. The CLI does preflight by default; `--run`
is available only after the independent strict PASS and source/input manifest
freeze.
