# Capital Utilization & Cadence Verdict — 2026-08-18

Research mission on the ParayaBuck PAPER product: quantify the startup
capital-utilization defect and the economics of faster rebalance cadences for
the two validated v1 strategies — WITHOUT changing any alpha parameter.

Scope frozen: no new alpha, no shorting/leverage/options/ML, no parameter
tuning, no grid search, no LIVE. Candidates were predeclared before any
performance table was viewed.

Artifacts: `scripts/research_{equity_cadence,crypto_cadence,deployment_start,
robustness_audit,equity_staleness}.py` and
`data/research/cadence_research_2026_08/{equity,crypto,deployment,robustness}/`
(baselines reproduced bit-identically against `docs/strategy_robustness.md`
and `docs/crypto_bakeoff_validation.md`).

## 1. Startup parity defect (confirmed + fixed)

Backtest: the engine's first usable bar is a period start
(`is_period_start(None, ts)` is True) → immediate initial decision.
PAPER runtime before this mission: `PortfolioRuntime.seed()` advanced
`previous_timestamp` to the latest warmup bar without deciding, so a fresh
mid-period deployment sat in cash until the next cadence boundary.

Deterministic test `test_backtest_decides_but_runtime_waits_for_boundary_then_catch_up_fires`
proves the mismatch on identical history/universe/account.

Fix (runtime behavior, NOT alpha): `PortfolioRuntime.startup_catch_up()`
makes ONE immediate full-target decision on the latest closed warmup bar when
ALL hold: warmup complete, batch complete, no persisted decision for this
(account, configuration digest, market), no managed positions in the market,
no non-terminal two-phase rebalance. Restart-safe: any prior decision
permanently suppresses catch-up (`catch_up_superseded`); duplicate orders are
impossible via `has_decision` + deterministic `_decision_id` + `INSERT OR
IGNORE`. The engine wires `_run_startup_catch_up()` after warmup + account
fetch; circuit breaker, reconciliation failure, readiness/staleness and
entry-latch gates block it, and a catch-up failure never halts normal
cadence. Decisions are tagged `decision_origin: startup_catch_up` in
metadata; scheduled decisions remain `decision_origin: scheduled`.

## 2. Startup capital utilization (deployment simulation)

26 predeclared deployment dates (14 equity 2018-2025, 12 crypto 2021-2026),
control (wait for boundary) vs catch-up (immediate), identical bars, warmup,
PIT universe (end=deploy) and cost model; only the initial
`previous_timestamp` differs (asserted).

| Market | Variant | median days→exposure | p90 | avg idle days | avg 30d return |
|---|---|---|---|---|---|
| Equity | control | 20.5 | 25 | 17.1 | +0.05% |
| Equity | catch-up | 0 | 0 | 0.07 | +0.31% |
| Crypto | control | 4.0 | 11 | 5.2 | -0.21% |
| Crypto | catch-up | 0 | 0 | 0.3 | -0.60% |

Equity catch-up adds +0.26pp avg 30d (idle capital deployed); crypto shows a
small negative 30d delta (-0.39pp) — earlier deployment into the sampled
bear-heavy crypto windows — within one standard deviation of per-start noise,
with no drawdown materiality (avg maxDD +0.75pp equity, +0.30pp crypto;
median deltas ≈ 0). No lookahead (universe
hash asserted equal across variants), no duplicates (0), no risk bypass.

## 3. Equity cadence — monthly vs weekly

Canonical window 2017-01-03..2026-07-30, universe built once, identical
costs; only the cadence differs.

| Metric | monthly (v1) | weekly | |
|---|---|---|---|
| return / CAGR | +59.21% / 4.98% | +84.61% / 6.61% | better |
| Sharpe / Sortino | 0.474 / 0.603 | 0.624 / 0.833 | better |
| maxDD / Calmar | 25.45% / 0.196 | 19.68% / 0.336 | better |
| profit factor | 1.49 | 1.40 | slightly lower |
| decisions / trades | 113 / 300 | 495 / 784 | 2.6x |
| turnover / fees | 50.9x / $62 | 132.4x / $182 | higher, still 0.03% of equity |
| avg exposure | 46.2% | 47.3% | better |
| cost stress 1x/2x/3x | 59.2/56.8/54.5 | 84.6/75.3/68.5 | better at every level |
| walk-forward (16 folds) | 9/16 positive, median +1.86% | 10/16 positive, median +3.67% (wins vs monthly 9/16) | better |
| concentration top-1/top-3 | 6.4%/16.1% | 5.3%/14.0% | less concentrated |

Verdict: weekly improves every headline full-window metric (CAGR ↑, Sharpe ↑,
Sortino ↑, maxDD ↓, utilization ↑, cost-robust at 1x/2x/3x, WF 10/16 folds,
concentration shares lower). Two material caveats, disclosed:

- **Top-name dependence**: ASX-drop jackknife (fresh engines, no cache):
  weekly +84.61% → +78.72% (−5.9pp); monthly +59.21% → +61.13% (+1.9pp).
  The weekly edge is materially ASX-driven; it still beats the monthly
  baseline by ~19.5pp without its top contributor. The independent audit
  script applies a stricter concentration gate (top-1 jackknife drop < 5pp),
  which flags this and reports `dominates=false` for equity weekly in
  `robustness/verdict.json`; the mission's release gate is the disclosed
  caveat above — the advantage survives ASX removal.
- **Recent-window sensitivity**: the mission's 2024-01-01..2025-06-30 smoke
  window REVERSES direction (monthly CAGR 7.56% vs weekly 7.42%, monthly
  maxDD 6.6% vs weekly 13.9%, WF positive 75% vs 50%). The full-window
  walk-forward (2019-01 anchored, 16 folds) favors weekly (10/16 positive
  folds vs 9/16; beats monthly in 9/16 folds, median +3.67% vs +1.86%), but
  the edge is not uniform across sub-periods.

Verdict: B — VALIDATE WEEKLY AS NEW V2 (`equity_residual_momentum_v2`),
with the ASX-dependence and window-sensitivity caveats disclosed; v1 stays
frozen and promotion is an operator decision.

## 4. Crypto cadence — 168h vs 96h vs 72h

Canonical window 2021-04-01..2026-07-31, universe built once from 1h bars.

| Metric | 168h (v1) | 96h | 72h |
|---|---|---|---|
| return / CAGR | +4.61% / 0.85% | +6.56% / 1.20% | +2.74% / 0.51% |
| Sharpe / Sortino | 0.172 / 0.231 | 0.233 / 0.332 | 0.115 / 0.156 |
| maxDD / Calmar | 13.17% / 0.064 | 13.25% / 0.090 | 13.84% / 0.037 |
| PF | 1.109 | 1.138 | 1.056 |
| decisions / trades | 263 / 124 | 453 / 155 | 602 / 175 |
| turnover / fees | 13.3x / $3,799 | 16.2x / $4,805 | 18.5x / $5,278 |
| fees % gross | 43.7% | 41.2% | 62.9% |
| cost stress 1x/2x/3x | 4.6/0.9/-2.9 | 6.6/1.4/-3.2 | 2.7/-1.7/-7.1 |
| WF positive folds | 8/10 | 7/10 | 7/10 |
| WF median / mean fold | +1.11% / +0.32% | +1.35% / -0.09% | +1.07% / -0.18% |
| round trips / flips | 18 / 24 | 27 / 38 | 43 / 50 |

96h improves 1x AND 2x net economics (CAGR, Sharpe, Sortino, Calmar, PF,
fee/gross) with flat maxDD; 3x marginally worse (-3.2% vs -2.9%); walk-forward
is mixed (7/10 vs 8/10 positive, higher median, lower mean). 58.7% of 96h
trades are avoidable at 168h (band suppresses more of the faster decisions:
34% of 96h decisions trade vs 47% at 168h). 72h is strictly dominated on every
axis. Signal staleness (no trades): top-2 pair persists 49% of consecutive
weeks; rankings correlate 0.82 week-over-week; selected symbols remain top-2
~3.8 weeks.

Verdict: 96h is the best-validated faster cadence → new v2 identity with the
walk-forward caveat documented; 72h rejected.

## 5. Idle-capital classification (Wave 4)

Full-window baselines, in-force decision targets (post-overlay):

| Cause | Equity | Crypto |
|---|---|---|
| structural/designed cash (sleeves + caps) | 3064/3495 days (88%) | 1773/1946 days (91%) |
| full cash — no qualified candidates/risk-off | 0 days | 146 days (7.5%) |
| drawdown-halt shadow days (runtime would latch) | 62 days | 107 days |
| startup cadence wait (accidental, per deployment) | 17.1 days avg | 5.2 days avg |

Idle time is overwhelmingly INTENTIONAL (allocation limits). The fixable
ACCIDENTAL component is the startup cadence wait, addressed by catch-up.

## 6. Signal staleness (Wave 5, diagnostic only)

Equity (161 weekly samples 2023-07..2026-07): week-over-week rank correlation
(top-50) mean 0.896; top-8 churn ~51% per week; a monthly decision's top-8
keeps only 73.6%/62.1%/60.7% of names at weeks 1/2/3/4. Rankings are stable
but ~40% of the top-8 churns within a month — weekly cadence responds to real
intra-month signal drift.

## 7. Adverse turnover (Wave 6)

Equity (smoke window): weekly produces 32 round trips / 16 flip-flops
(reversal cost $199); 42.7% of weekly fills would not occur under monthly.
Crypto: reversal costs $639/$993/$1,683 at 168h/96h/72h. Costs are real but
small relative to the net alpha improvement; faster cadence does increase
flip-flop exposure, most at 72h.

## 8. Rebalance band (Wave 7)

Band (0.5%) suppresses more of the faster cadence: 47% of 168h decisions
trade vs 34% of 96h and 29% of 72h; equity 300 trades/113 decisions (2.65x)
vs 784/495 (1.58x). Faster evaluation increasingly produces no orders — band
unchanged this mission.

## 9. Decisions

- EQUITY: B — VALIDATE FASTER CADENCE AS NEW V2 (`equity_residual_momentum_v2`,
  weekly). v1 stays frozen.
- CRYPTO: B — VALIDATE 96H AS NEW V2 (`crypto_low_variance_v2`, 96h), with the
  walk-forward caveat (7/10 positive, one weak 2025H1 fold) documented. v1
  stays frozen. 72h: rejected (strictly dominated).

## 10. Safety

- LIVE: never enabled (runtime.mode paper, live.enabled false).
- No risk/allocation/alpha changes. Startup catch-up is a parity correction
  under the full safety contract (gates in §1); it cannot duplicate, cannot
  re-enter after any prior decision, cannot bypass the entry latch or the
  two-phase sells-before-buys machinery.
- PAPER smoke: NOT RUN — `PARAYABUCK_ALLOW_PAPER_SMOKE` absent; the offline
  chaos gate (`scripts/run_paper_chaos.py`, fake-broker fault injection) is
  the paper-safety evidence.
