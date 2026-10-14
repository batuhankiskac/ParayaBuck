# Fast re-entry integration v1 (2026-10-14)

**R2 is shipped as `equity_leveraged_trend_v1` version 1.2.0**
(`early_reentry_weight: 0.5` on top of the E2 trend vote). The owner asked for
the rule to be implemented and pushed ahead of a Stage B replay: the research
pass is parameter-specific, its own confirmation study **failed** the
pre-registered plateau gate, and the three largest early episodes produce
95.3% of the measured gain. This doc records what shipped, the replay window
and its parity evidence, and what is still missing (a Stage B replay).

- **Rule.** While the E2 vote is off, the strategy may hold
  `early_reentry_weight` of its production weight in the hold symbol when the
  signal index's completed closes are above their 50-session average, their
  20-session annualized sample volatility is below that series' 252-session
  median, and the close is above the close 20 sessions earlier. The path ends
  when the close falls back below the 50-session average, and E2's own vote
  overrides it whenever it is on (`docs/research/fast_reentry_v1.md`,
  "R2: R1 at **half** the production weight until E2 turns on").
- **Shipped selection.** `config/settings.yaml` and the packaged default
  (`src/parayabuck/config.py::_default_equity_strategy`) add
  `early_reentry_weight: 0.5`. Parameters without the key keep E2's exact
  tuple, version 1.1.0, metadata and decisions; with the key the strategy
  reports 1.2.0.
- **Minimum history** becomes `max(253, 397) = 397` completed sessions: the
  replay starts 125 sessions back and needs that session's 252-session vol
  median, which is defined from index 271. Below 397 closes the early path is
  simply unavailable (inactive, `downtrend`); the 253-session gate for the vote
  itself is unchanged.
- **Research numbers (not a production proposal by themselves).** Main window
  2000-03-09 → 2026-09-29 at m = 2: C0 (production E2) 14.67% CAGR / 49.6% max
  DD / 5.97 turnover per year; R2 15.48% / 46.8% / 5.99, bootstrap median
  Δ +0.683 pp, 2017+ 32.71% vs 32.44%, and the six out-of-sample windows at
  Δ CAGR −0.09/+0.50/+0.44/+0.07/+0.06/**−0.54** pp and Δ DD
  +0.63/+2.36/−4.19/−2.13/−0.51/**+2.71** pp — i.e. G7 passes with 0.04 pp of
  CAGR margin and 0.29 pp of drawdown margin left.
- **Not yet Stage B replayed.** v1's own decision section says the pass "makes
  a Stage B replay the next step"; this integration ships the rule without it.

## Replay window

Production is stateless: `decide()` recomputes everything from history, so the
early path's state machine (E2 resets, SMA50 closes, the vol/momentum filter
opens) is replayed over the last **126** completed sessions, starting inactive
125 sessions back. The window is a fidelity choice, not a rule parameter:

- The state machine's active episodes are short (164 active sessions in 32
  episodes over 1999-2026 on QQQ, 2.46% of sessions), so a 126-session replay
  reproduces the full-history state.
- Verified on the production price basis (split-adjusted QQQ closes from
  `data/historical/stress_robustness_v1_daily.csv`): replay windows of 63, 126,
  189, 252 and 378 sessions reproduce the full-history state with **0
  mismatches** every session from the first decidable index to 2026 (6,679
  sessions, 164 active days, first episode 2001-02-01; the first vol median is
  defined at index 271 = 2000-04-04). Longer windows add no fidelity; a shorter
  one would trade margin for no benefit.
- The same replay is pinned in `tests/test_equity_leveraged_trend_r2.py` on a
  synthetic crash/recovery fixture (700 sessions, 3 entries, 2 E2 takeovers,
  1 SMA50 exit): production `decide()` at every session from 396 equals a
  full-history reference implementation of the state machine.

## Implementation

- **Strategy.** `src/parayabuck/portfolio/equity_leveraged_trend.py`: new
  optional parameter `early_reentry_weight` (a number in (0, 1]), accepted only
  with `trend_rule: majority_sma_tsm_don` (the SMA rule rejects it like
  `tsm_bars`/`donchian_bars`). The fixed research parameters are module
  constants (SMA 50, momentum 20, vol window 20, median window 252, replay
  126). `early_reentry_state()` precomputes the vol20 series once for the
  indices the replay reads, then the 126 medians and the replay loop; it reuses
  `tsm_vote`/`donchian_vote` on each session's prefix (the production vote is
  stateless, so the prefix is what the vote saw then). State `early_reentry`
  emits a `TargetWeight` of `production weight × early_reentry_weight` with
  reason `early_reentry` and `RebalanceReason.SCHEDULED`; every other state,
  the tradable check and the vol-target check are unchanged.
- **Metadata.** Only with the parameter: `early_reentry` with `weight`,
  `active`, `sma50`, `above_sma50`, `vol20`, `vol20_median252`, `momentum20`
  and `replay_bars`. E2's and VT35's metadata (and every receipt or hash built
  from it) is byte-identical without it.
- **Config.** `config/settings.yaml` adds the key with a dated comment
  pointing at `fast_reentry_v1.md`/`v2.md` and this doc; the packaged default
  mirrors it so a deleted YAML key cannot silently drop back to 1.1.0. Because
  the packaged default now carries a majority-only key, the loader
  (`config.py::_inheritable_default_parameters`) drops rule-specific packaged
  defaults when a block selects another rule — `trend_rule: sma` still restores
  VT35 (version 1.0.0, 200-session minimum history) exactly.
- **Observability.** The daily summary appends `early re-entry 0.5x` when the
  state is `early_reentry` (and a structured `early_reentry` field);
  `scripts/backtest_live_comparison.py` records the new metadata next to the
  vote and re-decides every session with its recorded parameters, so pre-R2
  sessions are not compared against the early path. `dashboard.js`'s strategy
  description mentions the half-weight path (`ASSET_VERSION` bumped).
- **Warmup / readiness.** Nothing else needed to change: every warm-up,
  readiness, catch-up, doctor and verifier path reads
  `strategy.minimum_history_bars`, so the warm-up fetch becomes 402 bars
  (`minimum_history_bars + 5`). Checked: `runtime.py` warm-up fetch and engine
  readiness, `portfolio/runtime_service.py` startup catch-up and scheduled
  decision gates, `backtest/portfolio_engine.py` LIVE-parity warm-up,
  `backtest/product.py` evaluation start, `cli.py` doctor/warm-up window and
  `signal_cross_check.py` / `intent_watchdog.py` (both treat `trend_state` as
  an opaque string, so `early_reentry` needs no branch).
- **Tests.** `tests/test_equity_leveraged_trend_r2.py` (absent-parameter
  contract, validation, minimum history, replay parity, sizing, precedence,
  JSON round-trip) plus re-pinned shipped-config tests
  (`test_strategy_spec_freeze`, `test_combined_account_risk`,
  `test_equity_paper_startup`, `test_audit2_20261011_w3`, `test_config`,
  `test_product_backtest`, `test_vt35_golden`, `test_equity_leveraged_trend_e2`)
  and the VT35 golden left unchanged.

## Caveats

- **No Stage B replay, and no LIVE readiness review.** The shipped rule has not
  been replayed on the production engine for 2017-2026 (E2's Stage B row was),
  and `docs/live_readiness_v1.md` pins 1.1.0; both would be needed before any
  LIVE promotion. PAPER-only either way (`live.enabled: false`).
- **The pass is narrow.** 0.81 pp CAGR built from 32 episodes, three of which
  are 95.3% of the relative gain (2001-10-31, 2002-11-04, 2023-01-20); the
  motivating case (2020) produced **no** early episode because the fast signals
  lagged E2 there. G7 passes at its margins (M5 Δ CAGR −0.54 pp, Δ DD
  +2.71 pp).
- **The confirmation study failed its plateau gate.** `fast_reentry_v2.md`:
  all six one-parameter neighbours beat C0 on CAGR, but the SMA-40 neighbour
  deepens max DD to C0 + 1.77 pp (limit +1 pp) → gate P FAIL. Gate I passed on
  21 independent ETFs (median +0.299 pp, 15/21 positive, pooled bootstrap
  +0.234 pp over 42,000 paths, Δ DD ≤ +3 pp on 17/21 = 81.0% against an 80%
  bar). The owner's decision to ship is an exception to that failed gate.
- **After tax** (report only, `vt_level_tax_v1` engine, $100k): R2 stays ahead
  in every window, by +0.63 pp (2000), +0.21 pp (2010) and +0.20 pp (2017).
- **Parameter-specific evidence.** The instrument sample is independent by
  instrument but overlapping in calendar time, gate I's drawdown clause passes
  by one instrument, and the out-of-sample windows are price-only with some
  fills at the previous close (identically for every arm).

## Evidence

- Research: `docs/research/fast_reentry_v1.md` (screen, gates G1-G9, arm
  tables), `docs/research/fast_reentry_v2.md` (neighbour plateau, 21-instrument
  test, after tax, mechanics audit); inputs/scripts in tags
  `archive/research/fast-reentry-v1-2026-10` and `-v2-2026-10`. Reproducibility:
  v2 recomputes v1's C0 and R2 rows on all 20 checks to 0.01 pp, and the audit's
  64 truncation probes and 64 trailing-window recomputations show 0 mismatches.
- Parity: the replay-window check above (0 mismatches for 63-378 sessions on
  QQQ 2000-2026, 164 active sessions) and the fixture parity test in
  `tests/test_equity_leveraged_trend_r2.py`.
- Engine: `tests/test_vt35_golden.py::test_e2_golden_backtest_is_unchanged`
  pins the shipped 1.2.0 behaviour on the committed QQQ/QLD/SPY fixture
  (2020-07-29 → 2022-12-30: 612 rebalances, 187 fills, 17.99% CAGR, 25.38% max
  DD); the VT35 golden is unchanged.

## Decision

R2 ships as `equity_leveraged_trend_v1` 1.2.0 with `early_reentry_weight: 0.5`
in the PAPER identity, on the owner's instruction, ahead of a Stage B replay.
The parameter is optional and off by default, so removing the key restores E2
(1.1.0) exactly. Next step if the owner wants the candidate taken further: a
Stage B replay on the production engine over 2017-2026 (and the LIVE readiness
review) before any LIVE promotion.
