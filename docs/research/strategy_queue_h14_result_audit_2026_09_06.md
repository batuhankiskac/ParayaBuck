# H14 native result audit — 2026-09-06 r4

Retrieval/check date: 2026-09-07. This is an independent, read-only audit of
the completed r4 receipt. The parser did not execute the runner or engine.

## Receipt and source bounds

The completed receipt is
[`h14_native_batch_receipt.json`](../../data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_receipt.json),
SHA-256
`dd2de0467c2c7ddea0f506d4856fdcee66d7274e9547ee44a5dca70f2a74510f`.
The launch log is
[`h14_native_batch_launch.log`](../../data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_launch.log),
SHA-256
`acd2d94619d7de53b591c29c87dd66f923234cd2fcd752dcef9c64ba95a736fe`.
The frozen execution manifest is
[`h14_turn_of_month_protocol_2026_09_06_r4.json`](../../data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json),
SHA-256
`014c232e59bd3d6c19ffb00c39e5ad45fdb27d3c1e16c1804f4bd29c6c0bfd0a`.
The frozen runner SHA is
`1b5a33a90e34895f961ff81b9a62847f0e7a96a20d7d7273b0efd0690e287599`.

The source pins bind SPY canonical SIP split-adjusted 1d bars
(`bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`), the
v5 corporate-action ledger (`a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`),
the final settings hash
(`414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`), and
the frozen r2 protocol. The candidate is SPY-only; QQQ and IWM sidecars are
context and are not economic gates for this run.
The retained corporate-action source endpoint is
`https://data.alpaca.markets/v1/corporate-actions`; its ex-date entitlement
semantics are documented at
[`getcorporateannouncements`](https://docs.alpaca.markets/us/reference/getcorporateannouncements).

## Independent checks

The persisted machine result is
[`h14_native_batch_independent_audit.json`](../../data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_independent_audit.json).
It reports `status=PASS`, `economic_run=false`, and no errors.

The audit verified:

* exactly 18 unique cells: candidate, passive, and fixed-calendar control ×
  `full_2022` and `recent_2024` × 1x/2x/3x costs;
* 54 event tuples in full and 30 in recent, with `P0` close decisions and
  candidate fills at `L0` open followed by `F4` open exits;
* the fixed control uses `M0=F4` and the next three sessions, exiting at the
  fourth following open; candidate/control have 108 and 60 fills;
* fresh 100,000 initial NAV, 5% target sleeve, daily native session curves,
  finite values, no pending orders, and final cash equal to terminal NAV;
* split-adjusted prices with no second split adjustment, 40 supplied dividend
  ledger actions, and ex-date cash accrual. Candidate/control event windows receive no SPY
  dividend; passive dividends reconcile to 326.355619 full and 189.841177
  recent;
* passive's one initial buy uses the preceding real-session decision and the
  first in-window open. Its same-endpoint terminal liquidation row is a
  native engine convention; the gap from the prior mark equals the emitted
  terminal fee, spread, and slippage components. It is not an unrecorded
  pre-window buy or a missing NAV/cash balance. The passive terminal close is
  not represented by a `PortfolioFill` or decision timestamp, so passive's
  transaction ledger is incomplete at that terminal point;
* spread and slippage totals increase monotonically across 1x/2x/3x. Native
  fee totals are reported as emitted and are not assumed to scale linearly.

## Corporate-action coverage boundary

The supplied SPY corporate-action ledger has known missing quarters in 2016-Q1
and 2018-Q2. Both are outside the two tested windows (`full_2022` and
`recent_2024`); no quarterly gap was detected inside either used window. The
ledger shows four actions per year for 2019–2025 and two actions through
2026-06-18. Raw provider page/HTTP/per-symbol completion receipts are absent,
so this does not establish blanket dataset completeness or point-in-time
provenance. Passive total returns are conditional on the supplied corporate-
action ledger. The optional 2016 context window was not run.

## Full matrix

Returns are isolated 100,000-NAV descriptive diagnostics. Exposure is the
four-event-session holding fraction (`4 × event count / available sessions`)
for candidate/control and 1.0 for passive buy-and-hold after its first
in-window fill. `TR` is cumulative net return and `DD` is native maximum
drawdown. Fees, spread, and slippage are currency totals.

| Mode | Window | Cost | TR | DD | Fees | Spread | Slippage | Dividends | Events | Trades | Exposure |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| candidate | full_2022 | 1x | 0.4444% | 0.4836% | 4.8191 | 54.1714 | 108.3429 | 0.0000 | 54 | 108 | 18.8317% |
| candidate | full_2022 | 2x | 0.2817% | 0.5089% | 4.8142 | 108.2567 | 216.5134 | 0.0000 | 54 | 108 | 18.8317% |
| candidate | full_2022 | 3x | 0.1193% | 0.5341% | 4.8093 | 162.2559 | 324.5118 | 0.0000 | 54 | 108 | 18.8317% |
| passive | full_2022 | 1x | 3.1162% | 1.2274% | 0.1629 | 1.2822 | 2.5644 | 326.3556 | 54 | 1 | 100.0000% |
| passive | full_2022 | 2x | 3.1123% | 1.2274% | 0.1628 | 2.5644 | 5.1288 | 326.3556 | 54 | 1 | 100.0000% |
| passive | full_2022 | 3x | 3.1085% | 1.2275% | 0.1628 | 3.8466 | 7.6932 | 326.3556 | 54 | 1 | 100.0000% |
| calendar_control | full_2022 | 1x | 0.5617% | 0.9939% | 4.8054 | 54.0060 | 108.0120 | 0.0000 | 54 | 108 | 18.8317% |
| calendar_control | full_2022 | 2x | 0.3987% | 1.0204% | 4.8004 | 107.9259 | 215.8518 | 0.0000 | 54 | 108 | 18.8317% |
| calendar_control | full_2022 | 3x | 0.2359% | 1.0469% | 4.7955 | 161.7598 | 323.5196 | 0.0000 | 54 | 108 | 18.8317% |
| candidate | recent_2024 | 1x | 0.1667% | 0.4836% | 2.3690 | 30.0452 | 60.0904 | 0.0000 | 30 | 60 | 18.5759% |
| candidate | recent_2024 | 2x | 0.0764% | 0.5089% | 2.3675 | 60.0643 | 120.1285 | 0.0000 | 30 | 60 | 18.5759% |
| candidate | recent_2024 | 3x | -0.0137% | 0.5341% | 2.3660 | 90.0572 | 180.1143 | 0.0000 | 30 | 60 | 18.5759% |
| passive | recent_2024 | 1x | 3.0212% | 1.1887% | 0.1628 | 1.2769 | 2.5538 | 189.8412 | 30 | 1 | 100.0000% |
| passive | recent_2024 | 2x | 3.0173% | 1.1887% | 0.1627 | 2.5538 | 5.1076 | 189.8412 | 30 | 1 | 100.0000% |
| passive | recent_2024 | 3x | 3.0135% | 1.1887% | 0.1627 | 3.8307 | 7.6614 | 189.8412 | 30 | 1 | 100.0000% |
| calendar_control | recent_2024 | 1x | 0.8460% | 0.5640% | 2.3909 | 30.2414 | 60.4829 | 0.0000 | 30 | 60 | 18.5759% |
| calendar_control | recent_2024 | 2x | 0.7550% | 0.5788% | 2.3893 | 60.4564 | 120.9129 | 0.0000 | 30 | 60 | 18.5759% |
| calendar_control | recent_2024 | 3x | 0.6640% | 0.5936% | 2.3878 | 90.6450 | 181.2901 | 0.0000 | 30 | 60 | 18.5759% |

## Descriptive period and event evidence

At 1x costs, annual net returns for candidate/control were:

| Window | Mode | Annual net returns |
|---|---|---|
| full_2022 | candidate | 2022 +0.1483%; 2023 +0.2337%; 2024 -0.0155%; 2025 -0.1007%; 2026 partial +0.2305% |
| full_2022 | calendar_control | 2022 -0.7561%; 2023 +0.3725%; 2024 +0.5979%; 2025 +0.1607%; 2026 partial +0.1908% |
| recent_2024 | candidate | 2024 +0.0402%; 2025 -0.1007%; 2026 partial +0.2305% |
| recent_2024 | calendar_control | 2024 +0.4924%; 2025 +0.1607%; 2026 partial +0.1908% |

Native pair-level net event returns at 1x were:

The event calculation uses the native adverse `fill_price` cash flows plus
explicit fees. The receipt's spread/slippage fields reconcile the adverse
price movement and are not subtracted a second time.

| Window | Mode | Events | Mean | Median | Win fraction | Min | Max |
|---|---|---:|---:|---:|---:|---:|---:|
| full_2022 | candidate | 54 | +0.1667% | +0.5089% | 61.11% | -5.4804% | +4.5698% |
| full_2022 | calendar_control | 54 | +0.2069% | +0.5432% | 64.81% | -6.2939% | +4.1537% |
| recent_2024 | candidate | 30 | +0.1134% | +0.6331% | 63.33% | -5.4804% | +2.7080% |
| recent_2024 | calendar_control | 30 | +0.5627% | +0.6964% | 70.00% | -2.5482% | +4.1537% |

## Terminal policy and ledger limitation

The r2 protocol says passive buys at the first supported open and holds through
the actual endpoint (lines 43–46); the r4 manifest freezes the same one-buy
semantics. The pinned runner sets the passive decision to the preceding real
session and includes that session through the endpoint
([`scripts/research_h14_turn_of_month.py:517-524`](../../scripts/research_h14_turn_of_month.py)).
It calls the native engine with `liquidate_at_end=True`
([`scripts/research_h14_turn_of_month.py:531-552`](../../scripts/research_h14_turn_of_month.py)).
This flag is not a separate r2/r4 manifest field; it is frozen indirectly by
the r4 manifest's exact runner SHA. A future runner change would therefore
require a new source pin and receipt.

The engine then closes remaining positions against the last observed bar's
**close**, applies adverse slippage, spread, and the effective-dated equity
fee, appends a second NAV at the same endpoint timestamp, and clears holdings
([`portfolio_engine.py:526-557`](../../src/parayabuck/backtest/portfolio_engine.py));
the close records costs in liquidation totals but deliberately does not create a
synthetic `PortfolioFill` or decision timestamp
([`portfolio_engine.py:1364-1391`](../../src/parayabuck/backtest/portfolio_engine.py)).

This is consistent with “hold through endpoint” as a deterministic terminal
valuation convention. It is **not** an unsupported claim that a tradable
next-open exit exists after 2026-07-30. The audit therefore passes NAV/cash and
cost reconciliation, but records transaction-ledger completeness as
`PASS_CANDIDATE_CONTROL; PASSIVE_TERMINAL_LIQUIDATION_INFERRED_NOT_RECORDED_AS_FILL`.
Passive terminal close evidence remains inferred from the pinned engine rule,
not an observed fill.

The candidate has lower 1x drawdown than the fixed-calendar control in both
windows, while cumulative return is lower in both. That is descriptive timing
and risk evidence, not proof of a hedge: both use the same SPY and roughly the
same event exposure, and the passive comparison is dominated by its continuous
exposure. The candidate remains `RETROSPECTIVE_DIAGNOSTIC_COMPLETE` with
`PORTFOLIO_CONTRIBUTION_UNTESTED`; no optimized weight, period, or threshold
was selected.

## V3 and disposition

The current V3 control receipt still has no reusable daily NAV series. The
archived daily curves are V2, and M3 `active_wealth` is relative. H14 versus
current V3 therefore remains `NOT_COMPARABLE`; no correlation, regime overlap,
or portfolio-improvement claim is made. A real complementarity decision still
requires a shared ledger containing V3 daily NAV, common cash, fills, fees,
position overlap, drawdown overlap, and matched controls. This result does
not authorize allocation, PAPER, LIVE, or production changes.
