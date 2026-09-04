# Equity V3 profit-protection research — 2026-09

**Mission:** `equity_profit_protection_2026_09`  
**Scope:** research only; no production or configuration changes  
**Pre-result implementation commit:** `aacde25b7230a418c6c9e709d49d59cc2b888a30`  
**Decision:** **KEEP_CURRENT_V3**

## 1. Executive verdict

The repository snapshot cannot support a valid empirical verdict: the declared canonical bars file, `data/historical/equity_1200_2016_2026.csv` (SHA-256 `3baec12e…903a`), is absent. The newer symmetric-PIT manifest points to an absent developer-machine path and its 882 MB CSV is also absent. Stage One therefore produced no complete batch and no candidate is eligible for robustness or PAPER review. The only research-integrity-safe decision is to keep the current exit policy unchanged. No headline, matched-episode estimate, or old result is substituted.

If a future, newly predeclared mission is funded after restoring canonical data, prioritize **ex-ante tail-risk control** (then position sizing/volatility scaling), not another exit-threshold sweep: prior stop evidence found diffuse rather than single-name-dominated losses, while repeated threshold testing creates selection risk.

## 2. Exact baseline provenance

| Item | Frozen value |
|---|---|
| Required base | `9578d5be695a00316e13e4a5ec9646ba3bc79a1c` (verified at start) |
| Tracked worktree at start | clean (`git status --porcelain --untracked-files=no` returned empty) |
| Strategy | `equity_residual_momentum_v3`, version 3.0.0 |
| Allocation | 0.55 |
| Signal | formation 252, skip 21, beta 273, residual-vol 63, top 8, single-name cap 0.10, ivol rejection 0.70 |
| Schedule | weekly normal decisions; daily completed bars only for research protection |
| Costs | repository effective-dated equity fees, 2 bp slippage, 2 bp spread |
| Intended period | 2017-01-03 through 2026-07-30; recent starts 2022-01-01 |
| Intended bars | `equity_1200_2016_2026.csv`, declared hash `3baec12e…903a` |
| Dividends | declared hash `7d79025…73e1e0` |

The manifest freezes the exact four policies, thresholds, 20 folds, cost multiples, and bootstrap seed. `no_results_yet` remains true because the precondition failed. The attempted runner fails closed instead of downloading data or contacting Alpaca.

## 3. Current equity exit call graph

```text
weekly scheduler / completed daily batch
  -> PortfolioRuntime._decide
  -> EquityResidualMomentum1dV3.decide
       _score_candidates: positive 12-1 residual gate, PIT membership,
       6/9/12 residual-rank consensus, ivol rejection, top-8 sizing
  -> PortfolioRiskOverlay.apply
       maximum allocation/position/count/cash limits;
       daily-loss and drawdown mechanisms block increases, not force a
       per-position profit/loss exit
  -> TargetToOrderPlanner.plan
       rebalance band + minimum notional; managed ownership only
  -> submit portfolio market order
  -> next broker execution/fill and portfolio ownership reconciliation
```

V3 does not override the base intermediate-risk hook. Consequently, between weekly decisions there is no P&L-triggered equity decision, bracket, take-profit, or trailing stop. A normal signal exit occurs when the scheduled V3 target omits a holding; risk overlays may reduce targets but do not implement this mission's position-level protection. Backtests create orders after a completed bar and fill them at the next tradable open.

## 4. Prior-art matrix

| Prior research | Mechanism | Result | Relevance now |
|---|---|---|---|
| Exit latency E1 | unconditional daily unqualified/rank > 8 | rejected | Daily alpha revalidation was not an improvement. |
| Exit latency E2 | unconditional daily unqualified/rank > 12 | rejected | Hold-band variant was rejected. |
| Exit latency E3 | unconditional breakdown twice, rank > 12 | rejected | Direct comparator for new E3, but new E3 is conditional on first reaching +5%. |
| Tail loss S1/S2 | fixed 8% / fixed 10% loss stops | rejected | Loss caps, not profit protection. |
| Tail loss S3 | fixed entry minus 3×ATR20 | rejected | Volatility-scaled loss stop, not gain-activated. |
| Tail loss S4 | 3×ATR20 trail after +1R | rejected | Intraday OHLC stop with ATR/R activation, mechanically unlike close-based 50% profit floor. |

Those studies used an earlier commit/config with `maximum_allocation=0.60` and a different historical provenance. They are evidence against casual exit overlays, not proof for the current 0.55 baseline. None is rerun here. E1 directly falsifies a fixed +5% realization premise; E2 is gain-activated and retains half of the highest completed-close gain; E3 conditions the formerly rejected rank-12/two-close rule on an established winner. All use close signals and next-open fills, unlike prior intrabar stops.

## 5. Primary-literature matrix

| Source | Market/model | Exit/risk mechanism | Main finding | Direct applicability to V3 | Does **not** prove |
|---|---|---|---|---|---|
| Jegadeesh & Titman (1993), *Journal of Finance*, “Returns to Buying Winners and Selling Losers” ([DOI](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x)) | US cross-sectional equities | 3–12 month formation/holding momentum portfolios | Intermediate-horizon winner continuation is empirically positive. | High-level rationale for avoiding premature winner liquidation. | No optimal +5% threshold, next-open rule, or V3-specific exit. |
| Moskowitz, Ooi & Pedersen (2012), *Journal of Financial Economics*, “Time Series Momentum” ([DOI](https://doi.org/10.1016/j.jfineco.2011.11.003)) | 58 liquid futures/forwards | Own-past-return directional signals and volatility scaling | Return persistence appears across asset classes, then partially reverses at long horizons. | Supports persistence as an opportunity-cost concern only. | Not cross-sectional single-stock V3 evidence and not a profit-lock test. |
| Kaminski & Lo (2014), *Journal of Financial Markets*, “When Do Stop-Loss Rules Stop Losses?” ([DOI](https://doi.org/10.1016/j.finmar.2013.10.001)) | Analytical return processes plus US equity evidence | Stop-loss switches risky asset to cash | Stop-loss value depends on serial dependence; under random walk it generally reduces expected return. | Frames why momentum persistence may change exit economics. | Does not validate take profits or this portfolio/state/execution contract. |
| Novy-Marx & Velikov (2016), *Review of Financial Studies*, “A Taxonomy of Anomalies and Their Trading Costs” ([DOI](https://doi.org/10.1093/rfs/hhw054)) | US equity anomalies | Effective bid-ask-spread transaction-cost modeling | Costs materially weaken many anomaly implementations; trading design matters. | Direct warning to evaluate turnover and multi-level costs independently. | Does not show any protective exit improves momentum. |
| Odean (1998), *Journal of Finance*, “Are Investors Reluctant to Realize Their Losses?” ([DOI](https://doi.org/10.1111/0022-1082.00072)) | Individual brokerage accounts | Realization behavior/disposition effect | Investors realize gains more readily than losses, inconsistent with several rational explanations. | Warns that “bank +5%” can encode behavioral bias. | Does not establish optimal systematic selling or causal portfolio improvement. |
| Leung & Zhang (2019), *International Journal of Theoretical and Applied Finance*, “Optimal Trading with a Trailing Stop” ([DOI](https://doi.org/10.1142/S0219024919500219)) | Optimal stopping under diffusion/mean-reverting models | Entry/liquidation with trailing-stop constraint | Derives model-conditional optimal policies and value effects. | Useful implementation vocabulary for path-dependent state. | OU/diffusion theory is not direct empirical evidence for long-only cross-sectional V3. |

No blog evidence is used. Internet retrieval was attempted but unavailable (HTTP 401); stable journal DOI landing pages are supplied for traceability. The literature motivates hypotheses and safeguards, not candidate promotion.

## 6. Frozen candidates

* **E0_CONTROL:** exact weekly V3; no daily profit protection.
* **E1_TP5_CLOSE:** completed close at least 5% above authoritative managed weighted-average basis; close all managed quantity next open; cash to weekly rebalance.
* **E2_PROFIT_LOCK_50:** arm at a completed +5% close without exiting on that bar; subsequently maintain completed-close high and close next open when close is at/below basis plus 50% of peak gain.
* **E3_PROFIT_ALPHA_CONFIRMED:** arm at completed +5%; after activation require two consecutive completed closes where exact V3 qualification fails or rank exceeds 12; close next open.

The implementation emits only zero targets for triggers and current-weight targets for survivors. The engine's intermediate-rebalance clamp independently prevents any increase or replacement.

## 7. Position state and timing

Research state begins only from engine-owned managed positions and its authoritative `avg_price`. Zero-to-positive creates state; a net increase adopts the engine's fill-derived weighted basis and resets activation/high-water/breakdown; trims retain state; zero removes it; re-entry starts fresh. Pending state suppresses duplicates and remains through partial fills. State has a JSON-safe snapshot/restore representation.

Completed closes alone trigger. `PortfolioBacktestEngine` places a resulting order after that close and uses the actual next tradable open plus its cost/participation model. It therefore naturally models overnight gaps rather than threshold fills. Scheduled V3 decisions run through the normal path and take precedence; an intermediate decision cannot increase exposure. Split-adjusted data requires a correspondingly split-adjusted basis—the intended canonical series is adjusted, but empirical corporate-action parity could not be run without the file.

## 8. Integrity tests

Nine focused state tests passed: below/equal/above activation; activation-bar prohibition; E1 boundary; later-close E2 trigger; two-close E3 confirmation; BUY basis/reset; trim/full-exit/re-entry; pending partial-fill restart; split-adjusted representation; managed-quantity/duplicate protection. Existing engine tests remain the source of truth for next-open order timing, actual open gaps, reduction-only intermediate targets, and production accounting.

The attempted independent Stage One run failed before loading a bar. This is an **incomplete comparability batch**, so the universal gate fails. No curve or partial metric was written.

## 9. Stage One results

| Window | E0 | E1 | E2 | E3 |
|---|---:|---:|---:|---:|
| Full canonical | not run | not run | not run | not run |
| 2022-present | not run | not run | not run | not run |

Reason: `FileNotFoundError: data/historical/equity_1200_2016_2026.csv`. There are no valid return, risk, cost, exposure, exit, episode, tail, or re-entry metrics. No candidate can satisfy the Stage One screen.

## 10. Robustness

Correctly **not run**. Stage One produced zero survivors. Running folds, annual windows, cost stress, bootstrap, exclusions, or jackknives would violate the survivor-only rule and could not repair missing baseline comparability.

## 11. Position/re-entry diagnostics and opportunity cost

Not estimable without independent paths and authoritative fills. In particular, matched-episode arithmetic was not used as a substitute. Candidate exit count, trigger/fill profit, gap, activation/holding durations, MFE/MAE, giveback, forward 5/20-session return, return-to-control-exit, missed upside, avoided loss, incremental costs, net protection value, re-entry timing/price/P&L, worst-account episode, concentration, and winners-becoming-losers are all explicitly **not available**.

## 12. Implementation-risk assessment

| Area | Risk before any future PAPER design |
|---|---|
| State | Persistent per-account/per-strategy/per-symbol episode identity is required. |
| Partial fills | Pending intent and armed/high-water state must survive while managed remainder exists. |
| Restart | State and order intent need atomic persistence and deterministic replay. |
| Ownership | Manual/external shares must never enter basis or sell quantity. |
| Duplicate exits | Client-order idempotency plus pending-order reconciliation is mandatory. |
| Corporate actions | Split quantity/basis/high-water/floor atomically; dividends should not silently alter price-return triggers. |
| Broker execution | Next-open research is not a fill guarantee; rejects, partials, halts and extended closures need policy. |
| Observability | Record episode, basis provenance, activation, high/floor, rank evidence, trigger close, order/fill, attribution and suppression reason. |

## 13. Final decision

# KEEP_CURRENT_V3

This is a failure to establish evidence, **not evidence that protection cannot work**. With no complete control/candidate batch, positive net protection, cost durability, fold breadth, symbol/year independence, exposure retention, and re-entry friction are unproven. No candidate deserves a separate PAPER architecture review.

### Not authorized

* No production exit policy or code/config/registration change.
* No PAPER or LIVE enablement, runtime start, Alpaca contact, credential use, or order placement.
* No promotion of E1, E2, or E3.
* No threshold change, replacement candidate, sweep, optimization, or result extrapolation.
* No use of old headline results as current-baseline evidence.
* No robustness run until a complete, independently rerun Stage One produces a predeclared survivor.
