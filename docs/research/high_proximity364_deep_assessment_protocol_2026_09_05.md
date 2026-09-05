# high_proximity364 deep assessment protocol

**Date:** 2026-09-05  
**Status:** DRAFT — research design only; awaiting root scientific review  
**Scope:** assess `high_proximity364` as a possible complementary crypto sleeve. This document authorizes no production, PAPER/LIVE, configuration, trading, promotion, download, or new backtest.

## Question and fixed facts

The question is whether the frozen candidate adds useful, tradable information to the existing crypto sleeve at the same total crypto budget. The candidate is unchanged:

```text
score(symbol,t) = close[t] / max(last 364 complete daily closes through t)
rank descending, tie-break by symbol
HYST4 retention, top 2, equal weight, rebalance every 96 elapsed hours
current close is included; execution is the next later open
```

The screen implementation is the research adapter at `scripts/research/portfolio_expansion_crypto_screen_2026_09.py:152-167`. It requires 364 consecutive complete daily bars (`:87-97`), creates the one-day-later signal view (`:281-294`), and uses the native engine with the configured risk and execution policies (`:289-295`). The frozen screen and audit are retrospective through 2026-07-31, not an independent holdout. Their pinned evidence is:

* bars: `data/historical/crypto_expanded_1h.csv`, SHA-256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`;
* runner: SHA-256 `8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d`;
* raw frozen manifest: SHA-256 `b84d411e1edcf855afc1505098415e707b605a8517cacdbb4e3cb4b8df4140f2`;
* audit: `docs/research/portfolio_expansion_crypto_screen_final_audit_2026_09_05.md`.

The incumbent remains `crypto_low_variance_hysteresis_v1`, with the equity identity `equity_residual_momentum_v3` and its 0.55 ceiling untouched. The account contract remains crypto maximum 0.05, gross maximum 0.60, minimum cash 0.40, maximum two crypto positions and six total positions. These configured values are visible in `config/settings.yaml:141-184`.

Existing results motivate assessment, but do not answer the question. The frozen diagnostic reports high-proximity as a positive-return, lower-drawdown 50/50 initial-NAV curve blend, while the audit explicitly says that this is not a shared ledger, shared fills, order competition, or full V3 result. The exposure audit adds a decisive preliminary confounder: full-window 1x high-proximity was BTC-only, active only from 2022-06-25 to 2025-07-03, with 68 fills and 55.7% inactive hours (mean gross 2.22%, maximum marked exposure 6.22%). This makes causal BTC/cash controls and observer tracing the next gate; it is premature to test an ensemble.

## Data and provenance gate

Before any economic computation, freeze a manifest containing the exact input hashes, code hashes, UTC boundaries, and the candidate definition above. A run is blocked if any hash, boundary, timezone, or daily visibility rule differs.

The local inventory performed for this draft found no confirmed crypto price file extending beyond 2026-07-31. `crypto_expanded_1h.csv` is pinned to `as_of=2026-07-31T23:59:59Z`; `data/historical/crypto_18_extended_report.json` reports every listed symbol's `last_bar` as 2026-07-31. The other local crypto files (`crypto_18_1h_2024_2026.csv`, `crypto_18_1h_extended.csv`, and `crypto_18_1d_extended.csv`) are also historical artifacts whose report metadata ends at 2026-07-31. Their file mtimes in August and the runtime SQLite mtimes in September are storage metadata, not evidence of later market bars. No price values or strategy performance were inspected for this inventory, and no live database snapshot was taken.

The runtime databases may contain newer operational observations, but they are WAL-backed state, not a verified historical price dataset. If a later holdout is needed, use an existing static research file or a SQLite online-backup API snapshot only after confirming a coherent backup and recording its hash. Do not copy the main file while ignoring its WAL, and do not read its performance for this assessment. If no post-cutoff static input exists, the holdout portion remains `DATA_UNAVAILABLE`, not a reason to download or create a feed.

Minimum data requirements are 364 consecutive complete UTC daily closes plus a later executable 1h open for every candidate decision, with the daily bar visible only after its bucket has completed. The scanner also needs the configured 1h completeness, dollar-volume, ATR, spread, BTC-correlation, category, meme and high-beta gates. The repository has OHLCV and scanner proxies, but not a complete venue-PIT tradability history or historical bid/ask tape. Results must label that limitation; a 2 bps spread and 25 bps crypto taker fee are a model stress, not observed execution proof.

## Staged falsification campaign

The first campaign must not combine two independent top-2 target lists. Their union can contain three or four symbols, but the account contract permits only two crypto positions. The native risk overlay enforces that limit by retaining ranked symbols up to `maximum_crypto_positions` (`src/parayabuck/execution/portfolio.py:248-272`) and then can scale the managed sleeve to the allocation/cash limits (`:274-310`). Applying it to an independent union would silently drop targets or turn the remainder into cash; changing the cap or allowing four positions would test a different contract. Therefore the previously suggested shared 50/50 union is **not a valid first experiment** and is removed from the minimum campaign.

Run fresh standalone ledgers for the incumbent and unchanged candidate, plus static controls, using the same initial NAV, executable bars, readiness mask, timing, costs and configured risk policy. Preserve HYST4/top-2/96h, the daily shift, next-open fill, no-forced-liquidation terminal convention, rebalance band and minimum notional. Do not search parameters, blend weights, cadence, top-N, lookback, or thresholds.

All six audited high-proximity cells are now reported as BTC-only, with full-window active hours 44.293%, recent active hours 19.716%, and last activity 2025-07-03; terminal exposure is zero. Those facts make the first falsifier observational, not economic. Do not spend the planned control matrix until the source of this concentration and inactivity is proven.

### Stage 0 — one full-window 1x observational trace

Run exactly one research-only full-window 1x replay of the unchanged candidate, with a trace wrapper and the existing immutable input/runner/manifest. This replay must prove complete native parity with the audited candidate artifact for NAV/equity curve, timestamps, fills, fees, slippage, spread, terminal policy, and terminal positions. A parity failure blocks all economic interpretation and yields `TECHNICALLY_INVALID`.

The trace must cover the complete causal chain at every as-of snapshot: configured pool (22) → scanner eligible pool → streamed observation set (reported as 12 where applicable) → provided history → 364-complete score eligibility → score/rank order → HYST4 retention → top-2 target → native fills. It must also record engine preflight/decision skips, not only strategy `decide` calls. A missing trace event is a blocker, not evidence of no candidates.

The highest-priority structural hypothesis is a history-retention asymmetry, not an alpha conclusion. `HighProximity364Adapter` inherits `minimum_history_bars` from the low-variance base: `variance_lookback_days + 1`, normally 23 bars (`src/parayabuck/portfolio/crypto_low_variance.py:56-57`). Its adapter then requests a 364-bar daily window (`scripts/research/portfolio_expansion_crypto_screen_2026_09.py:156-164`) without overriding the declared minimum. The engine builds each context with `trim = max(strategy.minimum_history_bars, 274)` and trims every non-benchmark symbol to that bound, while explicitly exempting the crypto benchmark `BTC/USD` (`src/parayabuck/backtest/portfolio_engine.py:1088-1103,1121-1127`). Thus, if the adapter's daily signal history passes through this context, BTC can be the only symbol with 364 visible bars; BTC-only selection would then be a structural artifact. The trace must record requested window length, provided per-symbol history length, benchmark exemption, and the exact rejection reason before any score is accepted. This hypothesis is confirmed if non-BTC symbols have at most 274 provided bars while BTC has 364 or more at the same decision; rejected if non-BTC symbols receive 364+ causal daily bars and still fail only on their own data/continuity gates.

### Corrected research adapter after trace proof

Once the Stage 0 trace proves the 23-versus-364 asymmetry, the corrected candidate is the new research-only module `scripts/research/high_proximity364_history_fixed_2026_09_05.py`. `HighProximity364HistoryFixedAdapter` inherits the frozen screen adapter and overrides only `minimum_history_bars` to exactly 364. This is the correct off-by-one contract because `_daily_window(context, symbol, 364)` consumes 364 prices including the current close, hence 363 intervals; no 365th price is required. Its constructor freezes `top_n=2` and `rebalance_hours=96`, while inherited score, HYST4 retention, rank order, equal weighting and crypto identity remain unchanged.

The native engine then computes `trim=max(364,274)=364`, so ordinary symbols receive enough causal history; BTC's benchmark exemption remains harmless and is still traced. Existing runner warmup/prepared-input behavior supplies all bars before the window start and shifts complete daily signal bars by 24 hours (`scripts/research/portfolio_expansion_crypto_screen_2026_09.py:281-294`); the corrected adapter must use those inputs unchanged. It must not be added to the product strategy registry or YAML. The next economic step, only after the trace and adapter contract are independently reviewed, is six corrected candidate cells (full/recent × 1x/2x/3x), with the already audited six incumbent cells and analytical cash diagnostics reused. No wider control matrix is justified until corrected candidate activity is known.

The wrapper must preserve the strategy name, version, required-history declaration, parameters, state transitions and return values, and must pass through the native as-of timestamp, warmup, scanner and universe arguments unchanged. Use existing observation/universe APIs and research-only adapter hooks; do not patch globals, bypass source guards, or replace preflight behavior. The relevant seams are `src/parayabuck/universe/crypto_scanner.py:166-180,339-374`, `src/parayabuck/universe/preflight.py:650-711`, and `src/parayabuck/backtest/observation_parity.py:60-90`.

The tiny fixture test should assert trace ordering, causal timestamps, skip visibility, unchanged target/fill outputs, and the same 364-readiness behavior. The full replay should then assert exact parity before any trace is used to explain BTC concentration. This is trace validation, not a new strategy result.

Only after Stage 0 passes should root freeze the smallest causal controls. The candidate and incumbent native cells are reused; no new 18/30-cell matrix is currently authorized. The likely next matrix is the necessary subset of matched-mask incumbent and native BTC control at full/recent × 1x/3x, with cash as an analytical zero-crypto baseline rather than six redundant backtests.

The deferred retrospective matrix is **30 cells**, of which 12 are reused immutable results and 18 are new control cells:

| Dimension | Fixed cells |
|---|---:|
| windows | full 2022-01-01..2026-07-31; recent 2024-01-01..2026-07-31 |
| costs | 1x, 2x, 3x the frozen slippage/spread/taker-fee inputs |
| sleeves/controls | reused native incumbent; reused unchanged high-proximity364; new matched-mask incumbent; new native BTC control; new cash control |

The five rows are not an unconditional all-market winner tournament. The reused rows are accepted only after byte-for-byte policy, input, timing and accounting identity is confirmed against the audited 6-cell full/recent/1x-3x artifacts. The new matched incumbent isolates the readiness-mask effect; native BTC tests whether the candidate is simply BTC exposure; cash tests whether the result is explained by being inactive. All new rows use the same 5% crypto budget, executable bars, cost/timing and published as-of readiness mask. This matrix is contingent on Stage 0 and is not an integration result.

For each window, construct a **matched 364-readiness mask** before looking at returns: timestamps and symbols for which both incumbent and candidate have the required complete history and the same published scanner observation is available. Report the native incumbent on its ordinary mask and again on this matched mask. The candidate must be compared with the matched incumbent; otherwise a candidate with fewer eligible names can appear better because it avoids difficult periods. Report coverage, missing decisions, selected symbols, and turnover alongside returns. The mask is formed from as-of-valid history and scanner state before outcome inspection; it must not be inferred from the candidate's realized winning trades.

### New control recipes

* **Matched-mask incumbent:** instantiate the frozen `crypto_low_variance_hysteresis_v1` with its ordinary 22-day score, HYST4 retention rank 4, top-2 equal weights and 96h schedule. At each causal decision, restrict its eligible/observed symbols to the precomputed common incumbent/candidate mask. If a symbol or timestamp is absent under the mask, it is unavailable and the sleeve holds cash; do not backfill or replace it using later knowledge. Use the native next-later-open fill, configured 5% maximum allocation, 2 bps slippage, 2 bps spread and 25 bps taker fee multiplied by 1x/2x/3x.
* **Native BTC control:** one deterministic research-only strategy that targets exactly 0.05 account weight in `BTC/USD` at every scheduled 96h decision when BTC is present in the same causal observation/readiness mask; otherwise target zero and hold cash. It uses no high-proximity entry/exit dates, no candidate-selected dates, and no adaptive threshold. Orders are generated by the native rebalance band and filled at the next later BTC open with the same cost multipliers. The account still has at least 0.40 cash, at most 0.60 gross and at most two crypto positions. This is a fair simple BTC exposure control, not a market buy-and-hold benchmark.
* **Cash control:** target zero crypto weight throughout the same window, with the same starting NAV and cash-rate convention. It has no orders, no lookahead and no strategy selection. It is the clean inactivity baseline; compare it with native BTC to quantify whether high-proximity's inactive periods alone explain its curve.

An **ex-post exposure-matched BTC curve** may be reported as a descriptive diagnostic only: replay BTC over the candidate's realized exposure intervals after the fact, clearly labeled tautological and excluded from acceptance. It must never be used as the causal BTC control or as an implementable benchmark.

### Observer/rank trace required before economics

The current audit's report of six raw 364-ready coins versus an observer set of twelve among a configured pool of twenty-two is not yet a proven contradiction. The harness must emit, for every as-of snapshot, the scanner eligible pool, `selected_symbols`, `observation_set`, capacity and category eliminations, observation strategy name/version/parameter digest, and the candidate's complete 364-ready set before applying the observation filter. The crypto preflight path documents this split (`src/parayabuck/universe/preflight.py:650-711`), and the scanner records the fields (`src/parayabuck/universe/crypto_scanner.py:166-180`, `:339-374`).

The trace must then show, at each decision, which symbols were visible to the strategy, which had a valid 364-day window, their score/rank order, HYST4-retained names, selected top two, and the reason for every omission. The existing research adapter already records selected symbols and retained names (`scripts/research/portfolio_expansion_crypto_screen_2026_09.py:197-207`); the new research-only harness should extend that receipt without altering the adapter or frozen artifacts. This distinguishes “six coins are economically ready” from “twelve coins were streamed and only six were candidate-ready,” and can establish whether BTC was selected because it won the score or because other names were absent from observation.

An optional follow-up may test a **single equal-rank ensemble** only if the signal comparison justifies it. At each common decision, form one common eligible pool, combine the two frozen score ranks with equal rank weight, apply HYST4 retention once, and select exactly top 2 at equal weight. This is a new separately frozen research candidate, not two sleeves and not a union allocator. If the common pool has two or fewer eligible symbols on most decision timestamps, the combined rank contains no meaningful cross-sectional information; report `UNRESOLVED_NO_RANK_INFORMATION` and do not run an economic claim. If it has more than two often enough, root may freeze this optional arm with no parameter optimization. It still needs an explicit research-only strategy adapter and parity tests, but does not require increasing caps or silently dropping targets.

If a verified post-2026-07-31 file becomes available, add exactly one **unseen holdout** window after manifest freeze. It must have a separate input hash, no threshold or role selection after viewing it, and enough elapsed time to include at least 90 completed daily decisions after the 364-day warm-up (longer is preferable). Run the same five standalone rows at 1x and 3x only: 10 additional cells. The optional equal-rank ensemble, if frozen, is an additional separately counted row. If coverage or tradability gates fail, report the holdout as technically invalid or data-blocked rather than shortening the window.

## Required diagnostics and falsifiers

Every valid cell reports total return, CAGR, max drawdown and duration, daily and hourly net-PnL Sharpe with their annualization labels, turnover, fees, slippage, spread, average and maximum exposure, cash ratio, number of decisions, skipped decisions, filled orders, holding hours, selected-symbol counts, and final marked positions. Also report:

* **Incremental contribution:** candidate minus matched incumbent, native BTC and cash; include return, drawdown, Sharpe, turnover, active hours and cost deltas. Explicitly separate native BTC from any ex-post exposure-matched diagnostic.
* **Readiness/tradability:** per-timestamp common coverage, candidate-only and incumbent-only coverage, zero-volume/gap counts, scanner rejection reasons, and the fraction of decisions with two, one or zero eligible positions.
* **Concentration:** per-symbol contribution, selection frequency, maximum weight, BTC/ETH share, and leave-one-coin stress. The leave-one-coin stress is a predeclared counterfactual diagnostic: remove each selected symbol in turn, rerun the fixed rule with no replacement tuning, and report the range. It is not a selection mechanism.
* **Regimes:** fixed chronological folds (calendar years where available and equal-length half-years for the recent slice), plus BTC up/down and realized-volatility labels formed only from completed bars before each decision. Do not select a winner by regime or retroactively switch strategies. A fold with too few complete decisions is reported as insufficient sample.
* **Benchmarks:** native BTC and cash at the same budget. Static ETH is deferred until the BTC/readiness confound is resolved; it is not needed for the first falsification gate.

Falsifiers are declared before running:

1. **Technical invalidity:** any lookahead, incomplete daily window treated as valid, wrong next-open timing, unmatched cash/cap, non-coherent source, nonfinite accounting, or inability to reconcile cash + marked positions to equity invalidates the cell and yields `TECHNICALLY_INVALID`.
2. **No incremental benefit demonstrated:** on the matched retrospective sample, the candidate fails to beat the matched incumbent, native BTC and cash on the predeclared contribution measures at 1x and 3x, or its apparent advantage disappears after the BTC concentration and leave-one-coin stress. This outcome does not claim the factor is impossible; it says this evidence does not justify integration.
3. **Unresolved:** valid results are mixed across costs/windows/folds, the sample is too short, or tradability coverage is too weak to distinguish alpha from data quality.
4. **Supports further shadow validation:** the candidate shows a repeatable matched-mask contribution over the incumbent, native BTC and cash with no material drawdown worsening, survives concentration stress, and remains valid under 3x costs and a genuinely unseen holdout when available. This supports only more shadow/PAPER-observer validation, never automatic promotion.

For a finite predeclared readout, use the four anchor cells (full/recent × 1x/3x). A valid candidate may support further shadow validation only if its paired net-return delta versus the matched incumbent is positive and its max-drawdown delta is non-positive in all four anchors, while native BTC does not meet or exceed the candidate on both measures in at least three of four anchors. Failure in at least three anchors, or native BTC explaining the result in at least three anchors, is `NO_INCREMENTAL_BENEFIT_DEMONSTRATED`; all other valid mixtures are `UNRESOLVED`. These are contribution criteria, not a promotion gate, and they do not require every regime fold to be positive.

The protocol deliberately does not require every regime to be positive and does not apply the archived standalone R1–R5 rules. A valid portfolio contribution can be asymmetric across regimes; the predeclared decision is based on contribution, controls, cost robustness and data validity.

## Integration roles after evidence

The roles are ordered by operational risk and are evaluated only after the campaign:

* **Passive independent observer / shadow research:** lowest risk. Run the candidate with the same observations and produce decisions, coverage and hypothetical fills without affecting orders or account state. This is the default role if evidence is promising but holdout or venue-PIT evidence is incomplete.
* **Research-only same-budget crypto ensemble:** plausible next role if the shared-ledger result supports it. A coordinator would allocate at most 0.05 total crypto, at most two crypto positions, and retain 0.40 minimum cash and 0.60 gross. It needs a new research dispatcher, shared cash/position netting, deterministic collision handling, and receipts for every rejected request. It cannot be implemented by adding the synthetic strategy to `PRODUCT_STRATEGY_NAMES` or changing YAML.
* **Entry veto:** highest interpretive risk for this candidate. A veto can suppress an incumbent entry without demonstrating positive candidate alpha and can silently change exposure. It would require a separately frozen causal rule, explicit missed-entry accounting, and evidence that vetoing improves the shared incumbent path after costs. It is not recommended as the first integration role.

The native seams explain why this remains a harness proposal. `build_product_universe` permits an explicit observation-strategy override while keeping scanner, capacity, costs, risk and execution owned by the configured product (`src/parayabuck/backtest/product.py:116-205`). `run_product_backtest` constructs one strategy and one `PortfolioBacktestEngine` (`:208-320`), so it cannot express two competing crypto sleeves. The engine accepts a `PortfolioStrategy`, enforces market/timeframe identity, and records fills and equity (`src/parayabuck/backtest/portfolio_engine.py:227-345`, `:560-590`). The configured product factory intentionally exposes only validated identities (`src/parayabuck/portfolio/__init__.py:17-48`); the synthetic `screen_high_proximity364` must stay research-only. The incumbent's HYST4 retention and top-2/equal-weight behavior is frozen in `src/parayabuck/portfolio/crypto_low_variance_hysteresis.py:46-118`.

The smallest harness should therefore reuse the existing score adapter, product universe builder, engine accounting, fill model, portfolio report and hash/receipt helpers, while adding only a research dispatcher and tests for shared cash, collision/netting, two-position enforcement, matched masks and reconciliation. No production implementation is part of this draft.

## Provisional disposition

Current status is **UNRESOLVED / retrospective evidence only**. The frozen curve blend is a useful reason to test shared capital, but it cannot establish incremental benefit. There is no confirmed local post-2026-07-31 crypto holdout in the inventory. Root should freeze the exact campaign only after reviewing the other proximity and research inputs; until then this file remains a draft and all existing frozen/source results remain unchanged.
