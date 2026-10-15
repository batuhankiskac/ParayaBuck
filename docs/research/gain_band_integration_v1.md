# Gain-scaled downsize band integration v1 (2026-10-15)

**A1u is shipped in the shared execution policy** — `execution.downsize_gain_band`
in `config/settings.yaml`, consumed by backtest, PAPER and LIVE through
`PortfolioExecutionPolicy`. It is a **tax-timing device**: a discretionary
downward resize that would realise an embedded gain is postponed, so the number
of taxable FIFO realisations falls (research: ~31/yr → ~6-11/yr) while turnover
and exposure stay ~unchanged. **H2 ships with it** —
`execution.december_downsize_deferral_sessions` postpones a downsize whose fill
session falls in the last 10 NYSE sessions of December (see §H2). This doc
records what shipped, the lot interface, the fail-safe, where the production
rule differs from the research engine, and what is still missing (a Stage B
replay; nothing is deployed).

- **Rule.** Only a downward resize between two positive weights
  (`0 < target_weight < current_weight`) may be held: it fills only when
  `current_weight − target_weight` reaches `B_down(g_frac)` (the research arm
  used a strict `>` at the band edge — see "Differences"), where `g_frac` is
  the USD FIFO gain fraction of the units the sell would consume over the bot's
  own (managed) lots and

  | `g_frac` | `B_down` |
  |---|---|
  | `≤ 1 %` | 0.5 pp |
  | `> 1 %, ≤ 5 %` | 3 pp |
  | `> 5 %` | 8 pp |

  Entries, exits to zero and up-resizes keep the production 0.5 pp band
  (`execution.rebalance_band_pct`). The thresholds are inclusive (exactly 1 %
  selects the 0.5 pp tier), matching the research arm.
- **Shipped selection.** `config/settings.yaml` and the packaged default
  (`config.py::ExecutionConfig.downsize_gain_band = DownsizeGainBand()`) both
  carry `thresholds: [0.01, 0.05]`, `bands: [0.005, 0.03, 0.08]`. The packaged
  default mirrors the YAML for the same reason R2's `early_reentry_weight` does
  (commit `d5309d1f`): a deleted or renamed YAML key must not silently restore
  the constant band. Disabling is explicit — `downsize_gain_band: null` in the
  `execution` block — and then every decision is byte-identical to R2's.
- **Not yet Stage B replayed, not deployed.** The shipped rule has not been
  replayed on the production engine over 2017-2026, and no LIVE readiness
  review covers it (`docs/live_readiness_v1.md` pins the strategy version, not
  this policy). PAPER-only either way (`live.enabled: false`).

## Research basis

`docs/research/gain_band_v3.md` (arm **A1u**, control = production R2):
before tax essentially neutral (+0.001 / −0.005 pp bootstrap median vs a
−0.10 pp non-inferiority margin, Δ max DD +0.43 pp) and after Turkish tax
positive on every new stage — 21 ETFs under the real TRY calendar (median
+0.117 pp, 19/21 positive), 21 rolling January starts (+0.396 to +0.991 pp,
21/21), and the four fresh bootstraps (+0.029 to +0.265 pp). It nonetheless
**FAILS as pre-registered on one cell** (E3, seed 20261015, block-10 flat-tax:
53.7 % of paths positive against a 55 % floor, median +0.029 pp), so nothing
was selected by the v3 pass; A1u is the implementable simplification of A1
(`g_frac` in USD from the bot's own fill ledger, no FX calendar, no Yİ-ÜFE
indexation) and differs from A1 on 0.78 % of the main-path sessions.

## Implementation and the lot interface

- **Policy.** `src/parayabuck/execution/portfolio.py`: `PortfolioExecutionPolicy
  .downsize_gain_band: DownsizeGainBand | None` (code default `None`; the
  packaged *config* default is the schedule, mirroring R2's split between a
  strategy class default and the shipped config). `config.py::DownsizeGainBand`
  validates the table (one more band than thresholds, strictly increasing
  thresholds in (0, 1), non-decreasing bands in [0, 1)), and
  `portfolio_execution_policy_from_config` is the single config → policy
  mapping used by `runtime.py` (×2), `cli.py`, `live_authorization.py`,
  `backtest/product.py` and `web/managers/engine.py`. The schedule is therefore
  inside `configuration_digest` (live operator authorization) and inside the
  raw-config digest, so an approval for the constant band cannot authorize A1u.
- **Decision.** `downsize_gain_fraction(lots, units, price)` returns
  `max(0, Σ_take (take × price − take × lot_cost)) / (units × price)`; the
  planner (`TargetToOrderPlanner.plan`) and the backtest engine
  (`PortfolioBacktestEngine._apply_target`) both call
  `downsize_gain_band_verdict(...)`, so the order-existence decision for the
  same `(target, current, lots)` triple is identical in BACKTEST, PAPER and
  LIVE (`tests/test_execution_policy_parity.py`).
- **Lot source, backtest.** `_Position.lots` is a FIFO `[quantity, unit cost]`
  book maintained at exactly the position's mutation sites: a BUY appends
  `(notional + fee) / qty` (the research engine's fee-inclusive basis), a SELL
  consumes the oldest lots, a split rescales every lot (more shares, same
  cost), and the book dies with the position.
- **Lot source, PAPER/LIVE.** `PortfolioStateRepository.owned_fifo_lots(account,
  symbol, market)` replays the bot's own persisted `portfolio_fills`
  (BUY price + that fill's fee per unit) and outstanding ownership adjustments
  through the same pure replay that already produced the position's cost bases
  (`portfolio/ownership_rebase.py::open_position_lots`). The book is therefore
  managed-only by construction: only this ledger's own fills and audited
  adjustments are events, a manually bought unit never becomes a lot, and the
  replay's own epoch boundary is the one the repository already uses for
  ownership — the lots are discarded whenever the position goes flat and
  rescaled by a booked split rebase. The query runs once per decision and only
  when the band is configured.
- **Fail-safe.** Any of: no lot book, a lot book that does not add up to
  `managed_qty` (`max(1e-6 × managed_qty, 1e-9)`), or lots that do not cover
  the sale → the resize is decided by `rebalance_band_pct`, i.e. exactly R2's
  behaviour, and the reason is logged at INFO (once per symbol per decision)
  (`lots_unavailable`, `lot_book_mismatch`, `lot_book_too_small`). Synthetic
  strategies, tests that build a `_Position` without fills, and any future
  mutation site that forgets the lot book therefore degrade to R2 rather than
  to a wrong band.
- **Observability.** A held resize is recorded per decision: the planner
  appends a `GainBandHold(symbol, g_frac, band, gap)` to the caller's sink, the
  runtime service writes it into the decision metadata
  (`metadata["gain_band"]`, persisted in `portfolio_decisions.target_json`),
  the backtest engine attaches it to the `RebalanceRecord`
  (`gain_band_holds`), and the daily summary appends
  `gain band held (g 7.2%, band 8pp)`. Decisions the band does not touch carry
  no `gain_band` key at all. The mirror-image record —
  `metadata["gain_band_bypass"]`, the symbols whose target the risk layer
  lowered — is written only when there is one (see "Review fixes").
- **Tests.** `tests/test_gain_band_a1u.py` (tiers and inclusive boundaries,
  FIFO consumption and a loss lot offsetting a gain lot, entries/exits/
  up-resizes unaffected, every fail-safe reason, engine lot book vs its own
  fills, split rescaling, runtime lot reconstruction from a synthetic fill
  ledger in `tmp_path`, runtime decision → persisted metadata, config
  validation, digest) plus the parity and digest additions in
  `tests/test_execution_policy_parity.py`, the shipped-config pins in
  `tests/test_config.py`, and the product-backtest goldens
  (`tests/test_vt35_golden.py`: every identity is pinned three times — band and
  deferral off (the pre-A1u numbers, unchanged), band only, and the shipped
  policy with both rules).

## Differences from the research engine

- **`g_frac` is read at the decision price**, not at the fill price. The
  research engine decides and trades at the same session open; production
  decides on the completed close and fills at the next open (research's own
  known "some fills at the previous close" caveat). The band can therefore bind
  on a marginally different `g_frac`, and its tier boundary can differ.
- **No sell-side cost in the fraction.** A1u nets the trade's cost out of the
  proceeds (`per_unit = (units × price − fee) / units`, 4 bps × m × Δw); the
  production planner has no cost model, so proceeds are gross of fees. The
  basis *does* include buy-side fees on both sides. Both effects are ≤ a few
  basis points of `g_frac`.
- **Boundary convention.** The research arm fills when the drift is strictly
  greater than the band (`> B_down`); production keeps R2's existing convention
  (an order exists when `gap >= band`, including at the 0.5 pp edge it already
  had), so the tier-1 behaviour is identical to R2 by construction.
- **Band-vs-`g_frac` floor.** A1u floors the aggregated gain at 0 and applies
  no per-lot floor (PREREGISTRATION §4.1), which is what shipped: a loss lot
  offsets a gain lot, and a net-loss sale reports `g_frac = 0` (tier 1).
- **The research numbers are not production numbers.** v3 ran on the research
  tax engine with a synthetic QLD leg, TRY/INDEXED gains for A1 and 4 bps × m
  costs; the integration's own evidence is the parity/golden suite above.

## Goldens (QQQ/QLD/SPY fixture, 2019-01-02 → 2022-12-30)

The band removes many small drift trims (turnover barely moves, fill count
falls), which is why the goldens change:

| Identity | Band | Fills | Held | CAGR | Max DD |
|---|---|---:|---:|---:|---:|
| VT35 (`trend_rule: sma`) | off / on | 228 / 123 | 0 / 137 | 26.07 % / 26.17 % | 27.79 % / 28.22 % |
| E2 1.1.0 (no early path) | off / on | 230 / 126 | 0 / 134 | 20.37 % / 20.70 % | 27.81 % / 27.81 % |
| shipped 1.2.0 (E2 + R2) | off / on | 187 / 93 | 0 / 124 | 17.99 % / 17.75 % | 25.38 % / 25.54 % |

Only VT35 moves under the review fix below: its 98 % managed sleeve is binding
almost every session while the account is fully invested, so the 0.5–0.6 pp
trims that keep the drift above that cap are risk enforcement and trade again
(122 → 123 fills, 149 → 137 holds). The band-off goldens are byte-identical, and
the two lower-exposure identities never reach their caps, so their numbers stand
unchanged. The 2017–2026 Stage B replay is still missing, and the band-on VT35
path diverges from the pre-fix one on 2020-02-04 onward, so no band-on golden is
a research number.

H2 is pinned on the same fixture as a third variant per identity (the shipped
policy: band and deferral together). The fixture spans four Decembers
(2019–2022), so the window postpones 9 QLD downsizes per identity; those
downsizes stop counting as band holds (`suppressed` falls by the same order),
because the window is tested first — the research's own `blocked_band` /
`deferred` counter split:

| Identity | Policy | Fills | Held | Deferred | CAGR | Max DD |
|---|---|---:|---:|---:|---:|---:|
| VT35 (`trend_rule: sma`) | shipped (band + H2) | 119 | 132 | 9 | 26.28 % | 28.22 % |
| E2 1.1.0 (no early path) | shipped (band + H2) | 122 | 129 | 9 | 20.81 % | 27.81 % |
| shipped 1.2.0 (E2 + R2) | shipped (band + H2) | 89 | 119 | 9 | 17.88 % | 25.34 % |

## Review fixes (2026-10-15)

The orchestrator's review of the first commit found two behavioural defects.
Both are fixed here; nothing else about the band changed.

- **False alarms.** The band deliberately leaves a position ABOVE its target
  by up to 8 pp, which the intent watchdog read as a failed order:
  `_over_target` alarmed at `held > target + max(0.01, 0.10 × target)` — target
  0.40/held 0.47 arose `equity_position_over_target`, which is DEGRADED health,
  an e-mail (`intent_alarm` ∈ `EMAIL_ALERT_KINDS`) and HTTP 503 from `/readyz`.
  The check now reads the latest decision's own `metadata["gain_band"]`
  (`runtime.py::_intent_watchdog_pass` →
  `PortfolioStateRepository.latest_decision_gain_band`, the new single reader
  that also serves `latest_decision_cross_check`) and widens the tolerance by
  the recorded band plus the same relative drift slack the target gets
  (`over_target_tolerance(target, held_band)`). An exit to zero keeps the
  constant tolerance (`held_band` is applied only when `target > 0`), the band
  can never hold an exit, and a symbol the record does not name is unchanged —
  a failed cash DAY SELL still alarms. A decision whose only intended order was
  the held downsize also stops reporting `NO_TARGET_CHANGE` in the decision
  summary: it reports `GAIN_BAND_HOLD`.
- **Risk reductions bypass the band.** The band is a tax-timing device for the
  STRATEGY's own drift trims. `PortfolioRiskOverlay.apply` now compares each of
  its output weights with the strategy's own weight for that symbol and writes
  `metadata["gain_band_bypass"]` (canonical symbols) whenever it lowered one —
  per-asset cap, sleeve/`maximum_allocation` scaling, gross/cash capacity, and,
  since a halt is exactly when the runtime must be able to de-risk, every
  target symbol while `halted`. (A position-count eviction removes the symbol
  from the target altogether, i.e. an exit, which the band never holds.) The
  entry-latch and intermediate-risk constructions (`min(weight, current)`,
  "risk reduction only") mark their symbols through
  `gain_band_bypass_metadata`. Both
  order-existence sites read the key (`TargetToOrderPlanner.plan` and
  `PortfolioBacktestEngine._apply_target`) and fall back to the constant band
  for those symbols, so parity holds and a risk-driven sell is never
  postponed for tax. Symbols the overlay did not lower — including a
  strategy trim that a cap above it left alone — stay band-eligible.

Audit of every other target-vs-held comparison (all verified against the tree,
none of them act differently for a band-held position):

| Site | Verdict |
|---|---|
| `intent_watchdog._under_target` | ok — a hold above target cannot read as under-target |
| `intent_watchdog.evaluate_equity` signal-cross-check branch | ok — replaces both checks; its target is the held weight |
| `daily_summary.build_equity_daily_summary` | ok — prints, and already annotates `gain band held (…)` |
| `runtime_service._with_paper_observability` | ok — records `target_weight`/`current_position`/`planned_order_delta`, no flag |
| `runtime_service._emit_decision_summary_safe` | **fixed** — was `NO_TARGET_CHANGE`, now `GAIN_BAND_HOLD` |
| `runtime_repository.latest_decision_target` / `latest_decision_cross_check` | **fixed** — `gain_band` was dropped; new reader |
| startup catch-up `already_invested` | ok — tests `managed_qty > 0`, never the target |
| rebalance recovery / `sell_pending` / `rebalance_in_progress` | ok — order status and phase, no target comparison |
| reconciliation (`alpaca_reconciliation`, `alpaca_account`, `paper_incident`) | ok — repository-own vs broker quantities; a held trim places no order |
| web dashboard / `/readyz` / ops / CLI status | ok — side-by-side display; `/readyz` only relays watchdog codes (now correct) |
| backtest `_enrich_rebalances` `target_vs_actual_drift` / `unfilled_notional` | ok — no consumer; the deliberate gap reads as drift there |
| `shadow_comparator`, `telemetry.expected_vs_actual` | ok — per-order quantity vs fill, not account vs target |

Coverage added with the fixes: `tests/test_intent_watchdog.py` (the widened
tolerance, a record for another symbol / a malformed record / a foreign
symbol changing nothing, an unfilled exit still alarming, the repository
reader through the runtime pass), `tests/test_gain_band_a1u.py` (the real
overlay's per-asset cap, the halt, the risk-reduction-only marking, the
engine's bypass, the PAPER path end to end including the persisted
`gain_band_bypass` and the `GAIN_BAND_HOLD` summary reason) and
`tests/test_execution_policy_parity.py` (planner vs backtest for a marked
target, with the unmarked control held by both).

## H2: the December downsize deferral (2026-10-15)

**H2 ships together with the band**, as the research decision
`band_h2_combo_v1` adopted the pair `K = A1u + H2` (`docs/research/band_h2_combo_v1.md`,
evidence in tag `archive/research/band-h2-combo-v1-2026-10`): on top of the band
the December deferral keeps its standalone contribution (+0.139 / +0.133 /
+0.271 pp after tax on the 2000 / 2010 / 2017 windows; bootstrap **+0.042 pp on
61.0 %** of B1-recent and **+0.025 pp on 59.5 %** of B2-flat paths) and repairs
the one cell that killed the A1u-only campaign (E3 block-10 flat: 58.3 % of
paths positive vs the 55 % floor, where A1u scored 53.7 %). It is a *tax-timing*
device for the same object as the band — which downward resizes fill — so it is
implemented in the same shared execution policy and the two are composed exactly
as pre-registered: **the December window is tested first, the band decides
otherwise**.

- **Rule.** A downward resize between two positive weights
  (`0 < target_weight < current_weight`) whose FILL session is one of the last
  `execution.december_downsize_deferral_sessions` (shipped: **10**) NYSE
  sessions of December is postponed; the ordinary rule — including the band —
  is re-evaluated at every later decision, so the resize executes from the
  first January decision on. Exits to zero, entries and up-resizes are never
  deferred, and neither is any reduction the risk layer produced
  (`metadata["gain_band_bypass"]`): a halt or a cap is exactly when the runtime
  must be able to de-risk. The rule carries no state between sessions — the
  held weight simply keeps drifting above the target and the next decision
  re-plans it.
- **Research convention (fill session, exchange calendar).** The research arm
  tests the **fill** session: `december_tail(days, i0, i1, tail)`
  (`research/band_h2_combo_v1/screen.py:436-450`) keeps the last
  `DEC_TAIL = 10` December sessions of the price calendar per year
  (`screen.py:165`), the arm's window is built at `screen.py:589`, and the
  branch that defers is
  `if spec["defer"] and i in dec_tail and tgt < w` (`screen.py:663`), tested
  *before* the band as the K composition requires (`screen.py:662-668`; the
  smoke identity `simulate_band_overlay` uses the same test at
  `screen.py:474-475`). The
  research engine decides on the completed close of session `i-1` and trades at
  session `i`'s open, so `i` is the fill session; production and the backtest
  decide on the completed close and fill at the next open, so the shared
  predicate maps the decision session to its successor — on the one NYSE
  calendar the engine already uses (`parayabuck.markets.nyse_calendar`) — and
  tests that successor:
  `execution.portfolio.in_december_deferral_window(decision_timestamp, sessions)`.
  Both order-existence sites call it with the decision timestamp
  (`TargetToOrderPlanner.plan`, `PortfolioBacktestEngine._apply_target`), so the
  window is identical in BACKTEST, PAPER and LIVE. Two consequences worth
  stating: a decision taken on the **last December session is not deferred**
  (its fill is already in January, so deferring it would only delay the sell to
  the second January session), and the window holds exactly `sessions`
  decisions, all of them in December. "Last 10" counts **NYSE sessions** — a
  13:00 ET early close (24 December 2024) is a session, an observed holiday
  (24 December 2021, 25 December 2024) is not — and an unavailable calendar
  yields **no** deferral, i.e. the ordinary rule executes
  (`_december_season_sessions`). One convention difference is worth naming: the
  window comes from the exchange calendar, not from the bars present, so on a
  dataset that is missing a December session the research arm's own price
  calendar would classify that decision differently from production (the
  committed fixtures and every daily provider series are session-complete).
- **Shipped selection.** `config/settings.yaml` and the packaged default
  (`config.py::ExecutionConfig.december_downsize_deferral_sessions = 10`) both
  carry 10, for the same reason as the band's schedule: a deleted or renamed
  YAML key must not silently drop a shipped order-existence rule. `0` disables
  the deferral (and every decision is then byte-identical to the band-only
  policy). `portfolio_execution_policy_from_config` is again the single mapping,
  so the window is inside `configuration_digest` (live operator authorization)
  and the raw-config digest.
- **Observability.** A postponed resize is recorded per decision exactly like a
  band hold, on its own key: the planner appends a
  `DecemberDeferralHold(symbol, target_weight, current_weight, sessions)` to the
  caller's sink, the runtime service writes `metadata["december_deferral"]`
  (persisted in `portfolio_decisions.target_json`), the backtest engine attaches
  it to the `RebalanceRecord` (`december_deferral_holds`), and the daily summary
  appends `december deferral held (88.0% -> 90.0%, 10 sessions)`. The paper
  observability receipt marks the symbol
  (`symbols[symbol]["december_deferral_sessions"]`, printed by the PAPER
  telemetry line), and a decision whose only intended order was the postponed
  resize reports `DECEMBER_DEFERRAL_HOLD` instead of `NO_TARGET_CHANGE`
  (`GAIN_BAND_HOLD` still wins if both records are present; the metadata carries
  both either way). Decisions the window does not touch carry no key at all.
- **Watchdog.** The deferral holds the *whole* resize, so no gap is evidence of
  a failed order while it stands: `_over_target` skips a symbol named by the
  latest decision's `metadata["december_deferral"]` whenever its target is
  positive (`PortfolioStateRepository.latest_decision_december_deferral` →
  `runtime.py::_intent_watchdog_pass`). A target of zero keeps the constant
  tolerance, so an unfilled exit still alarms and still emails; once the first
  January decision re-plans the sell, the record is gone and a missed fill
  alarms again. That is the review fix from the band applied to a device with an
  unbounded hold.
- **Tests.** `tests/test_december_deferral_h2.py` (the window against the real
  NYSE calendar for 2019/2020/2021/2022/2024/2025 — exactly 10 decisions per
  year, holidays skipped, the 2024 early close counted, the last December
  session excluded — deferral of a downsize, no deferral of exits / entries /
  up-resizes / risk reductions, execution from the first January decision,
  composition with the band inside and outside the window, the counter split,
  config validation and digest, metadata/summary/receipt, and the PAPER path
  end to end including a restart-replay ledger), plus
  `tests/test_execution_policy_parity.py` (planner vs backtest inside and
  outside the window, exits/up-resizes/risk reductions, the shared hold sink,
  digest) and `tests/test_intent_watchdog.py` (the deferral tolerance, a
  malformed/foreign record, an unfilled exit still alarming, band and deferral
  records side by side, the repository reader through the runtime pass).

## Decision

A1u ships as an execution-policy option, enabled in the shipped configuration,
on the owner's instruction and after the v3 evidence described above; H2 ships
with it as the adopted composition `K = A1u + H2` (`docs/research/band_h2_combo_v1.md`).
Both are fail-safe (A1u to the constant band, H2 to the ordinary rule), part of
both digests, and observable per decision. The review fixes above are part of
this revision: a downsize the risk layer produced keeps the constant band and is
never deferred, and a held position no longer reads as a failed order. Next
steps if the owner takes it further: a Stage B replay on the production engine
over 2017-2026 (the goldens above are the only production-engine evidence so
far), and the LIVE readiness review.
