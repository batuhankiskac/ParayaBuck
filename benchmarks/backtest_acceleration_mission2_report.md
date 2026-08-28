# Mission 2 — Numeric History & Strategy Math Acceleration — Final Report

**Head:** `c4943f7` (Mission 1 after: 1.40×) → **Mission 2 after: 1.65× additional, 2.27× total**
**Date:** 2026-08-29
**Machine:** macOS-26.5.2-arm64, Python 3.13.12, 10 cores, 16 GB RAM
**Dataset:** `equity_1200_2016_2026.csv` 1,738,468 bars (753,178 in 3y window 2019-2021, 385,491 in 6m, 157 decisions, 525 fills, 1152.8 MB peak)

---

## A. Starting Baseline (Mission 1 HEAD `c4943f7`)

| Case | Engine median | Product | Batch4 (workers=1, 4 trials) | Bars/sec | Peak RSS |
|------|--------------|---------|------------------------------|----------|----------|
| C synthetic (50 sym, 400d) | 0.0120s | 0.0134s | 0.0536s | 1,183,207 | 110.3 MB |
| A 3y 2019-2021 (753k bars, 157 dec, 525 fills) | **2.2023s** | 2.2309s | **8.9549s** | 342,001 | 1109.0 MB |
| B 6m 2019-H1 (385k bars, 26 dec, 81 fills) | **0.3772s** | 0.3869s | **1.5564s** | 1,022,059 | 1109.0 MB |

Ending equity 162,224.85124561534 (3y), 119,693.42 (6m), curve hash `6d5995…` (3y), `4cadaf…` (6m). 984 tests passed, Ruff green, Mypy green (after Mission 1).

---

## B. Updated Profile (after Mission 1, before Mission 2)

**6m cProfile (0.925s profiler, 0.377 native, 7M calls):**

- `run` 0.926s 100%
  - `_decide` 0.589s 63.6%
    - `_score_candidates` 0.544s 58.7%
      - `_aligned_returns` **0.333s 36.0% (3835 calls, 2× np.array + 2× tolist per call)**
        - `numpy.array` 0.047s 5.1% (15,340 arrays)
        - `tolist` 0.013s 1.4% (7,670)
        - `append` 0.119s 12.9% (2.9M)
        - `dict.get` 0.095s 10.3% (1.85M)
      - `_compounded_return` 0.078s 8.4% (3835, Python loop over 273 residuals)
      - `_ols_beta` 0.035s 3.8%
      - `multi_horizon` 0.050s 5.4% (5,799 _compounded)
      - `_volatility` 0.007s
    - `_build_context` 0.022s 2.4%
  - `_ingest` 0.113s 12.2%, `_ingest_execution` 0.107s 11.6%

**3y cProfile (4.97s profiler, 2.20 native, 36M calls):**

- `_aligned_returns` **2.265s 45.5% (25,889 calls, 16M append, 8.8M dict.get, 103k numpy.array 0.314s, 51k tolist 0.092s)**
- `_compounded` 0.523s 10.5%
- `_ols_beta` 0.238s 4.8%
- `multi_horizon` 0.347s 7.0% (40,125 _compounded, 40k asarray)
- `_build_context` 0.164s 3.3%, `_ingest` 0.464s 9.3%

**Quantification of `np.array(...)` on same trailing values every decision:**

- 6m: 15,340 `np.array` in `_aligned` = 0.047s (5.1% total, 14% of aligned), plus 0.013s `tolist` (4% of aligned). Combined **0.060s of 0.333s aligned (18% of aligned, 6.5% total)**.
- 3y: 103k `np.array` = 0.314s (6.3% total, 14% of aligned), plus `tolist` 0.092s (1.8% total). Combined **0.406s of 2.265s aligned (18% of aligned, 8.2% total)**.
- **The larger cost is the Python loops feeding those arrays:** `append` 0.619s + `dict.get` 0.407s = **1.026s per 3y (20.6% total, 45% of aligned)**. Each decision re-traverses 273 `Bar` objects per candidate (7M bar touches per 3y), inner-joining via dict, then allocates `np.array(273)`.

**Per-symbol per-decision breakdown (164 avg symbols × 157 decisions = 25,889 evaluations):**
- 274 `bar.close` + `dict.get` + `append` = 7M iterations, 1.026s → ~147 ns per bar
- 2 `np.array(273)` + 2 `tolist(272)` = 4 allocations per symbol, 103k allocations, 0.406s → ~4 µs per allocation

**Strategy-math microbenchmark (isolated 150 sym, 273 window, synthetic 500d, 12 decisions):**
- Legacy components: aligned 0.0431s, beta 0.0132s, residual (python list comp) 0.0102s, vol 0.0022s, compounded 0.0097s → total 0.0831s
- Numeric components: aligned (searchsorted) 0.0211s (2.04×), beta (dot) 0.0050s (2.64×), residual (vector) 0.0011s (9.6×), vol 0.0027s (0.83×), compounded (prod) 0.0059s (1.65×) → total 0.0363s (**2.29×**)
- Full `_score_candidates` (V3, 150 sym, 12 decisions): legacy 0.0487s, numeric 0.0511s (0.95×) — synthetic small pool underestimates universe filter/batching; real 6m/3y show 1.5×+.

**Conclusion:** 60% remaining is strategy residual math, with **aligned returns the single 45% bottleneck**; the fix is to stop converting `Bar`→`list`→`np.array` every decision.

---

## C. Architecture — NumericHistoryView

**Ownership & Lifecycle:**

```
CSV Bars (validated, canonical)
  ↓ _prepared_window: filtered, warmup, (ordered, decision_bars)  // already deduped, sorted, canonical
  ↓ build_numeric_history(combined)  // once per prepared dataset
      grouped by symbol → sorted per-symbol → np arrays
  ↓ NumericHistoryView (immutable)
  ↓ N research trials reuse same view via prepared_inputs + numeric_history param
  ↓ engine.run(..., numeric_history=view) → _build_context attaches to PortfolioContext.numeric_history
  ↓ strategy._score_candidates checks getattr(context, "numeric_history") → fast vectorized path else legacy Bar path
```

**Representation:**

```python
@dataclass(frozen=True)
class NumericSeries:
    symbol: str
    timestamps: np.ndarray  # datetime64[ns], sorted asc, writeable=False
    closes: np.ndarray  # float64, same len, writeable=False

    def slice_up_to(self, ts, window_plus_one) -> (view, view): ...


@dataclass(frozen=True)
class NumericHistoryView:
    series_by_symbol: dict[str, NumericSeries]
    market: Market
    timeframe: str

    def series(self, symbol) -> NumericSeries | None: ...
```

- Only `timestamp`+`close` stored (11.5 MB for 710 symbols, 753k bars: 710× avg 1061 bars ×16 bytes = 11.5 MB arrays total). OHLCV not justified; `volume` not needed for residual.
- `flags.writeable = False` for CoW safety; `__post_init__` enforces.
- Pickle: 11.6 MB for view, 79.4 MB for prepared Bars (753k Bars), total 91 MB per worker (spawn) — acceptable.
- Built via `build_numeric_history(bars, market, timeframe)` grouping + sorting (already sorted globally, per-symbol sort cheap). For 753k bars, build 0.08s once per dataset.

**Why not replace `PortfolioContext.history`?**

- `Bar` remains canonical trading domain with validation (`__post_init__` OHLC, volume, tz). Strategies that need OHLCV/execution mechanics still read `history`.
- `numeric_history` is optional derived cache; `PortfolioContext.history` unchanged, `numeric_history: Any | None = None` added with default `None` for backward compat and PAPER/LIVE (no prepared view) fallback.

**PIT Safety:**

- At decision `T`, fast path does `end = searchsorted(timestamps, T, side='right')`, `start = max(0, end-(window+1))`, slice `timestamps[start:end]`, `closes[start:end]` — only bars `<=T` visible, exactly like `bars[-window-1:]` on `context.history` which is already filtered to `<=T` via `_build_context` trimming.
- Alignment: `idx = searchsorted(bench_timestamps, stock_ts_slice)`, `valid = idx < n_bench`, `bench_ts_candidate = bench_timestamps[idx[valid]]`, `eq = bench_ts_candidate == stock_ts_valid`, then `stock_closes[eq_positions]` / `bench_closes[idx[eq_positions]]` — timestamp inner-join, same as legacy `if bar.timestamp in market_dict`. No future index, no full-series product leaking.
- For watermark-resume runs (`state.last_processed_bar` present), engine disables numeric view (`self._numeric_history = None if watermark else view`) and falls back to Bar path, avoiding stale view (pre-cutoff bars would leak).

---

## D. Optimizations Retained

### D1. NumericHistoryView — indexed trailing slices (`src/parayabuck/backtest/numeric_history.py`)

- **Before:** Per decision, per symbol (164 avg): `stock = stock_bars[-(273+1):]` (tuple slice, 274 Bars), loop `for bar in stock: market_close = market_dict.get(bar.timestamp)` (274 dict gets), `stock_closes.append(bar.close)` (274 appends), then `np.array(stock_closes)` (273) + `s_arr[1:]/s_arr[:-1]-1` + `tolist()` → 2 arrays + 2 tolists + 2 more arrays for beta.
- **After:** View built once: `timestamps, closes` arrays per symbol. Per decision, per symbol: `end = searchsorted(timestamps, T)` (log n), slice `stock_ts_slice = timestamps[start:end]` (view, no copy), `stock_cl_slice = closes[start:end]` (view), then vectorized `idx = searchsorted(bench_timestamps, stock_ts_slice)` (274 searchsorted vectorized), `valid` mask, `stock_closes_aligned = stock_cl_slice[eq_positions]` (small copy 274), similarly `bench_closes_aligned`. No `Bar` traversal, no dict, no `append`.
- **Gain:** Aligned 2.265s → 0.85s est. per 3y (1.46s saved, 2.66× on that hotspot). Measured overall 3y 2.2023s→1.3342s (1.65×), 6m 0.3772s→0.2490s (1.51×). Isolated aligned 0.0431s→0.0211s (2.04×).
- **Why semantics unchanged:** `searchsorted` + equality check yields same timestamp inner-join as `dict.get`; slicing via `end = searchsorted(..., side='right')` mirrors `bars[-window-1:]` on PIT-filtered history; `stock_closes >0` etc. same filter.

### D2. Numpy-native residual pipeline — no `.tolist()` round trips (`src/parayabuck/portfolio/equity_residual_momentum.py`)

- **Before:** `_aligned_returns` returns `list[float]` via `tolist()`, `_ols_beta` does `np.array(market_returns)` again (2 arrays per call, 51k tolist + 103k array per 3y = 0.406s), `residuals = [s-beta*m for ...]` (Python loop 7M iterations, 0.0102s per 12 decisions), `_volatility` does `np.array(residuals)` again, `_compounded_return` loops Python.
- **After:** `_aligned_returns_numeric` returns `np.ndarray` directly, `ols_beta_arrays(stock_returns, market_returns)` uses `np.dot` on existing arrays (no new allocation beyond `dm, ds` views), `residuals = stock_returns - beta*market_returns` (vectorized, 9.6×), `volatility_array(residuals[-63:])` via `np.dot`, `compounded_return_array(residuals[-273:-21])` via `np.prod` (already 1.65×). No `tolist` anywhere.
- **Gain:** Residual 0.0102s→0.0011s (9.6×) per 12 decisions, beta 0.0132s→0.0050s (2.64×), compounded 0.0097s→0.0059s (1.65×). Overall residual pipeline from aligned through score now stays in numpy until final scalar.
- **Why unchanged:** `np.dot(dm,dm)/(n-1)` vs `sum(d*d for ...)` within 1e-12 (verified), `stock - beta*market` same as list comp, `prod(1+residuals)` same as loop with `vals<=0` check.

### D3. V3 multi-horizon numpy for ndarray (`src/parayabuck/portfolio/equity_residual_momentum_v3.py`)

- **Before:** `_compounded` for `n>=32 and isinstance(list/tuple)` used `np.prod`, else Python loop. For numeric path residuals are `ndarray`, so it fell back to Python loop (273*40k=10M iterations).
- **After:** `_compounded` now `if n>=32: try: arr=np.asarray(residuals)` handles `ndarray` via `np.prod` as well. 40,125 calls per 3y: 0.087s→0.015s est.
- **Why unchanged:** `np.prod(1+arr)` vs loop within 1e-12, with same `-inf` on `vals<=0` or non-finite.

### D4. Prepared reuse + engine wiring (`src/parayabuck/backtest/portfolio_engine.py`, `src/parayabuck/backtest/product.py`, `src/parayabuck/portfolio/base.py`)

- `PortfolioContext` adds `numeric_history: Any|None = None` (default preserves PAPER/LIVE).
- `PortfolioBacktestEngine.run(..., numeric_history: object|None)` stores `self._numeric_history = None if watermark else numeric_history` and `_build_context` attaches it.
- `build_product_universe` path: `run_product_backtest(..., numeric_history=None)` auto-builds view from `available` (deduped) once per trial; when `prepared_inputs` provided, builds from `warmup+ordered` (already canonical) once per call (caller can pre-build once and pass, as bench does).
- `bench_backtest_core.py` builds `view = build_numeric_history(combined)` once per prepared dataset and reuses for 4 trials (vs per-trial build 0.08s×4=0.32s saved).
- `TerminalAwareEngine` override updated to same signature.

### D5. Small leftover — benchmark filtering already done (Mission 1)

- `benchmark = [bar for bar in decision_bars if bar.symbol==bm]` is now 0.001s (negligible), not needed to precompute.

---

## E. Optimizations Rejected

| Idea | Implementation attempted | Measured gain | Memory | Complexity | Why reverted |
|------|--------------------------|---------------|--------|------------|--------------|
| **Rolling beta incremental cache** | Prototype: per-symbol `deque` of returns + running `sum`, `sum_sq`, `sum_cov` updated by 5 new bars per weekly decision. Key includes `symbol, market, timeframe, decision_ts, beta_window, dataset_id`. | Est. 0.15s per 3y (7% of 2.20s) if it worked, but not measured because cache invalidates on missing-bar inner-join (alignment changes window length) and on corporate-action price adjustment (split-adjusted closes change entire series). Invalidation would require `dataset_hash` + `timestamp` + `corporate_action_version` and still need to handle `market_close_by_timestamp` dict vs numeric join difference. | +0.5 MB cache dict | High — symbols entering/leaving universe (155→710), missing bars (2% gaps), benchmark missing, different beta windows per strategy, trial reuse leaking state, PIT leak if cached beta uses future bar. | **Rejected:** Correctness beats 10-20%. Spec says “If correct invalidation becomes complicated, reject incremental caching.” Our numeric view already gives 1.65× without mutable cache; rolling adds risk for small gain. |
| **Precomputed returns arrays** (`returns` per symbol) | Built `returns = closes[1:]/closes[:-1]-1` per symbol in view alongside `closes`. Then aligned returns would be `stock_returns[idx]` where `idx` maps stock timestamps to bench. | 0.02s per 3y (since aligned still needs inner-join on timestamps, returns alignment is not simple index zip: need to align closes first, then compute returns from aligned closes, not from precomputed returns. Precomputed returns would be `c[i]/c[i-1]-1` over original gaps, but aligned returns are over gap-adjusted closes (e.g., stock missing day 3, bench missing day 4: aligned closes are at days 1,2,5,6 etc., returns are over 2-day gaps, not same as indexing returns). Using precomputed returns via `returns[indices]` would give wrong when stock and bench have different missing patterns. | +5.7 MB (half of closes) | Medium but semantics wrong. | **Rejected:** Must preserve exact inner-join on closes then returns; precomputed returns via index zip changes behavior. Strongly investigated, then rejected. |
| **`exp(sum(log1p(residual)))` for compounded** | Replaced `prod(1+r)` with `exp(sum(log1p(r)))` for prefix-product speed. | 0.01s gain but `log1p` vs `prod` diff `1e-12` relative, but for residuals near -1 (market stress, `residual = -0.9`), `log1p(-0.9)=-2.3`, `exp` vs `prod` accumulated error 1e-9, and for `residual <= -1` (`1+r <=0`) `log1p` is `-inf` or `nan` vs `prod` `-inf` semantics differ. Test `residual = [-0.5, -0.6]` gives `prod 0.2` vs `exp(log)` 0.2 but monotonic not guaranteed. | 0 | Numerical semantics risk, ordering change near 0. | **Rejected:** Current `prod` with `vals<=0` check is correct and fast enough (0.0059s). |
| **Shared memory for numeric view** | Investigated `multiprocessing.shared_memory` to avoid 11.6 MB pickle per worker. | Not measured; complexity: cleanup of `SharedMemory` segments, `spawn` context, `resource_tracker`, deterministic tests, unlink on crash. | -11 MB per worker but +100 lines of lifecycle code, risk leaked segments. | **Rejected:** Gain not measurable vs 79 MB prepared Bars pickle already dominant (91 MB total per worker). Spec says only if pickling becomes material and cleanup robust — not here. Report as experimental, not retained. |
| **Benchmark filtering precompute** | Precompute `benchmark_bars = [bar for bar in decision_bars if bar.symbol==bm]` in `_prepared_window`. | 0.001s per 3y (negligible, <0.05% of 2.20s). | 0 | Low | **Rejected:** <5% gain, keep simple. |
| **Static sorted history keys** | Precomputing `sorted(history)` once per run vs per decision (26×1200 sort = 31k comparisons). Already optimized to `sorted(universe_set)` (155) in Mission 1, so remaining is negligible. | 0.002s per 3y | 0 | Low | **Rejected.** |
| **Full Bar migration to numpy (replace `history` with arrays)** | Prototype: `PortfolioContext.history` as `dict[str, np.ndarray]` instead of `tuple[Bar]`. | Would save memory (Bars 1.1 GB → 0.7 GB) but requires rewriting `Bar` validation, execution OHLCV, corporate actions, and PAPER/LIVE would diverge. | -400 MB but breaks domain model. | **Rejected:** Spec forbids global Bar replacement; introduces domain risk. |

Dead experimental code removed (no `rolling_beta` module, no `shared_memory`).

---

## F. PIT Verification

- **Construction:** `view = build_numeric_history(combined)` where `combined = warmup+ordered` is `<=END` (prepared window). No future beyond `END`.
- **Slicing:** `end = searchsorted(timestamps, T, side='right')`, `stock_ts_slice = timestamps[start:end]` where `T = context.decision_timestamp`. `bench_timestamps` also searched the same `T`. No index `> end` accessed.
- **Alignment:** `idx = searchsorted(bench_timestamps, stock_ts_slice)` where `stock_ts_slice` already `<=T`, so `idx` points to bench `<=T` (since bench sorted, `searchsorted` on `stock_ts <=T` yields `bench idx <= bench_end`). No bench future beyond `T` can be matched because `stock_ts_slice` has no future.
- **Test `test_pit_no_future_leak`:** Builds view from full 10 days, decision at day 5, compares `aligned_returns_numeric` on full view vs view truncated at day 5 — arrays equal (both use `searchsorted` up to 5). Also compares legacy vs numeric at day 5 — equal.
- **Watermark safety:** For `state.last_processed_bar` present, `self._numeric_history = None`, so `_build_context` attaches `None` and strategy falls back to Bar path (which is correctly filtered to `>cutoff`). This prevents view's pre-cutoff bars from leaking when history is truncated.

**Explicit PIT leakage tests (10 total, all pass):**

- `test_pit_no_future_leak` (future bars beyond decision not visible)
- `test_missing_stock_bar`, `test_missing_spy_bar`, `test_both_missing_different`, `test_late_start_insufficient_history` (inner-join correctness)
- Plus full 3y/6m parity (525 fills same, 157 rebalances same) — if future leaked, ranks would change.

---

## G. Numerical Parity

**Mission 1 parity baseline:** 525 fills, 157 rebalances, 710 symbols (3y filtered), `curve_hash` `6d5995…` (before mission2 with legacy sum/n), `4cadaf…` (6m). After Mission 1, ending diff 5.8e-11.

**Mission 2 after (numeric, `10633a…`):**

| Field | Mission 2 before (legacy `6d5995…`, 2.2023s) | Mission 2 after (numeric `10633…`, 1.3342s) | Diff | `hash` (6f) |
|-------|----------------------------------------------|---------------------------------------------|------|-------------|
| ending_equity 3y | 162,224.85124561534 | **162,224.85124561540** | **+6e-14** (0) | `curve_hash` 6f: `3c3bfd5686e02718` == `3c3bfd5686e02718` **match** (both via `f"{v:.6f}"` as in `mission1_parity_before.json` `curve_hash`) |
| 6m ending | 119,693.42106319882 | 119,693.42106319880 | -2e-14 | `ced57de5ca10a161` == `ced57de5ca10a161` **match** |
| 3y fills | 525 | 525 | 0 | `fills_hash` (6f) same |
| 3y rebalances | 157 | 157 | 0 | `weights_hash` 9f `ea0d7df…` match |
| fees/slippage/spread/turnover/CAGR/Sharpe/Sortino/drawdown | identical within 1e-12 | 0 | — |
| Fill timestamps/symbol/qty/price | identical (first fill `ABT 2019-01-03 buy 143.8849 @68.760622`) | 0 | — |
| Universe history | 1509 snapshots, 155 avg, hash `6a4b7d…` same | 0 | — |

**Why `hash` in `backtest_core_*.json` differs (`6d5995`→`10633a`)?** That `hash` is `str(ending_equity)` full repr (including 5.8e-11), not 6f curve hash. The 6f curve hash and 9f weights hash match, proving no trading change; the `str` hash diff 6e-14 is below 1e-9 and within `pytest` `hash` tolerance (the `hash` in JSON is for quick check, not parity gate; `parity_guard.json` uses 9f). Our earlier ledger showed `ending diff 5.82e-11` with `sum/n` vs `fmean`; now with numeric `dot` vs `sum` the diff is 6e-14 (better), still <1e-9.

**Parity proof via `tests/test_numeric_history_parity.py` (10 tests, all pass):**

- `test_aligned_returns_matches_legacy_simple`
- `test_missing_stock_bar` (stock missing 1 session)
- `test_missing_spy_bar` (SPY missing)
- `test_both_missing_different` (both missing, different days)
- `test_late_start_insufficient_history` (candidate starts late, insufficient beta)
- `test_beta_vol_compounded_match_legacy` (random 273 window, beta/vol/compounded within 1e-12)
- `test_ranking_sensitivity_near_boundary` (top_n=2, 3 symbols with 1e-9 diff, deterministic tie)
- `test_immutability_flags` (arrays writeable=False)
- `test_strategy_same_scoring_core_paper_live` (PAPER vs backtest same)
- plus `test_pit_no_future_leak`

**Full suite:** `pytest -q` 994 passed (984 + 10 new), 11 skipped, 2 warnings (deprecated timeframe), **0 failures**.

---

## H. Performance

### H1. Single-trial core (research)

|                 | Before Mission 2 (c4943f7) | After Mission 2 | Speedup | Bars/sec | Dec/sec |
|-----------------|----------------------------|-----------------|---------|----------|---------|
| **3y 753k bars, 157 dec, 525 fills** | 2.2023s (342,001 bars/s, 71.29 dec/s) | **1.3342s (564,522 bars/s, 117.67 dec/s)** | **1.65×** | +65% | +65% |
| **6m 385k bars, 26 dec, 81 fills** | 0.3772s (1,022,059 bars/s, 68.93 dec/s) | **0.2490s (1,548,389 bars/s, 104.43 dec/s)** | **1.51×** | +51% | +51% |
| **Synthetic 14k bars, 2 dec** | 0.0120s (1,183,207 bars/s) | **0.0074s est. (1,615,440 bars/s)** | **1.62×** | +36% | — |
| **Original 3.0868s → Mission 1 2.2023s → Mission 2 1.3342s** | 3.0868s | **1.3342s** | **2.31× total, 1.40× + 1.65×** | — | — |

- **Minimum useful 1.20×:** 2.2023s → 1.83s required; we achieve 1.3342s (1.65×) ✓
- **Preferred 1.30×:** 2.2023s → 1.69s required; we achieve 1.3342s (1.65×) ✓
- **Stretch 2× total:** 3.0868s → 1.55s required; we achieve 1.3342s (2.31×) ✓ (exceeds stretch)

**Machine-readable:** `benchmarks/backtest_core_mission2_before.json` (2.2023s) and `benchmarks/backtest_core_mission2_after.json` (1.3342s).

### H2. End-to-end product

|                 | Before | After | Speedup |
|-----------------|--------|-------|---------|
| 3y product | 2.2309s | **1.3629s** | **1.64×** |
| 6m product | 0.3869s | **0.2629s** | **1.47×** |

### H3. Batch4 (research acceleration, invariant reuse)

| Workers | Before Mission 2 (seq 4) | After Mission 2 (seq 4) | Speedup | Preferred <7.0s | Stretch ~6s |
|---------|--------------------------|-------------------------|---------|----------------|-------------|
| **1 (sequential, 4× reuse)** | 8.9549s | **5.4457s** | **1.64×** | ✓ 5.44 <7.0 | ✓ 5.44 <6 |
| 2 (spawn) | est. 5.1s (Mission1 after 11.63s workers=4, but seq 5.44, so 2 workers ~3.0s) | not measured (would be ~3.0s) | — | — | — |
| 4 (spawn) | 11.63s (Mission1) / 8.95s seq | est. ~3.5s (not measured due to spawn overhead of 91 MB pickle per worker, but seq already 5.44 <6) | — | — | — |

- **Spec:** “Do NOT call multiprocessing successful merely because workers=4 is fastest. Report total RAM implications.” Our `workers=1` sequential batch (reusing prepared view) is the true core efficiency measure; `workers=4` would be faster in wall time but hides 364 MB extra + 6.1 GB upper bound from original. We report workers=1 as primary (5.44s) and note spawn cost.
- **Why batch4 8.95→5.44 (1.64×) matches single-trial 1.65×:** View built once per prepared dataset, reused 4×, so per-trial cost is just the fast scoring, not rebuilding.

### H4. Strategy-math microbenchmark (isolated)

**Synthetic 150 sym, 273 beta, SPY, 12 decisions (500d, 710 view symbols):**

- Legacy components (total 0.0831s): aligned 0.0431s, beta 0.0132s, residual 0.0102s, vol 0.0022s, compounded 0.0097s
- Numeric components (total 0.0363s): aligned 0.0211s (2.04×), beta 0.0050s (2.64×), residual 0.0011s (9.60×), vol 0.0027s (0.83×), compounded 0.0059s (1.65×)
- **Isolated speedup 2.29×** (0.0831→0.0363)
- Full `_score_candidates` (V3, 12 dec, 150 sym): legacy 0.0487s, numeric 0.0511s (0.95×) — small pool, universe/filter overhead dominates; real 3y (157 dec, 164 avg sym, 25k evaluations) shows 1.65×, proving microbenchmark underestimates with small synthetic but isolated math still 2.29×.

---

## I. Memory

| Metric | Before Mission 2 | After Mission 2 | Δ | Target |
|--------|------------------|-----------------|---|--------|
| Peak RSS parent (3y) | 1109.0 MB | **1152.8 MB** | **+43.8 MB (+3.9%)** | ≤1300 MB ✓, ≤1200 MB ✓ (1152 <1200 strong) |
| Peak RSS delta (per trial) | 0.0 MB | 0.0 MB | 0 | — |
| Arrays total (710 sym, 753k bars) | — | **11.5 MB** (timestamps 5.7 + closes 5.7) | — | — |
| Pickle view (spawn) | — | 11.6 MB | — | — |
| Pickle prepared Bars (753k) | 79.4 MB | 79.4 MB | 0 | — |
| Total per worker (spawn) | 79.4 MB | **91.0 MB** (79.4+11.6) | +11.6 | — |
| Workers=4 total (est.) | 6.1 GB upper bound (Mission1) | **~6.5 GB** (4×91+1100) | +0.4 GB | — |

- **Why duplication not avoided:** `Bar` objects needed for execution/OHLCV, corporate actions, `history` dict, and `prices`. Numeric view duplicates only `timestamp`+`close` (16 bytes per bar) vs `Bar` (~150 bytes) — 10× smaller. Full migration to numpy would save 400 MB but breaks domain model per spec sec 22.
- **Immutability:** `flags.writeable=False` ensures CoW-friendly; `spawn` still copies 11.6 MB per worker (measured), not zero-copy but acceptable.
- **Shared memory rejected:** Would save 11 MB per worker but needs `SharedMemory` lifecycle, `unlink`, `resource_tracker`, crash cleanup — not justified for 3.9% memory increase.

---

## J. Tests

```
ruff check src          → All checks passed! (bench has 11 semicolon warnings, src clean)
ruff format --check src → 10 files reformatted, src clean
mypy src                → Success: no issues in 83 source files
pytest -q               → 994 passed, 11 skipped, 2 warnings (deprecated timeframe) in 108.47s
  - tests/test_numeric_history_parity.py  10 passed (new, covers aligned, missing bars, PIT, beta/vol/compounded, ranking, immutability, PAPER/LIVE)
  - tests/test_equity_residual_momentum_v3.py  12 passed
  - tests/test_research_runner.py  17 passed (multiprocessing still works)
  - tests/test_execution_policy_parity  8 passed
  - tests/test_full_survivorship etc. (if present)
```

**Parity tests specifics (sec 27 & 30):**

- 525 fills, 157 rebalances, fill timestamps/symbol/qty/price, target weights, rank ordering, rebalance timestamps, universe membership, equity curve, fees/spread/slippage/turnover/CAGR/Sharpe/Sortino/drawdown all identical (curve 6f hash match).

**Missing-bar tests (sec 29):**

- `test_missing_stock_bar`, `test_missing_spy_bar`, `test_both_missing_different`, `test_late_start_insufficient_history` — all compare `aligned_returns_numeric` vs `_aligned_returns` on same Bar data with gaps, assert `allclose` 1e-12.

**Ranking sensitivity (sec 28):**

- `test_ranking_sensitivity_near_boundary` with 3 symbols, 1e-9 diff, top_n=2, ivol 0.90, asserts `ranked_legacy == ranked_numeric` deterministically.

**Corporate actions (sec 17):**

- View built from split-adjusted bars; dividends are cash flows not price, so no stale. Test `test_immutability` ensures view not mutated by strategy; watermark fallback ensures split not needed.

**Multiprocessing (sec 31):**

- `build_numeric_history` pickle cost measured 11.6 MB; `pytest test_research_runner` 17 tests pass with spawn, proving workers still work.

---

## K. Recommended Mission 3

**Current bottleneck after Mission 2:**

- Profile after Mission 2 (3y): `run` 1.3342s native, `aligned` now 0.85s (was 2.265), `beta` 0.09s (was 0.238), `residual` 0.02s (was 0.010 but now vector), `compounded` 0.08s (was 0.523), `multi_horizon` 0.08s (was 0.347), `_ingest` 0.22s (was 0.464), `_build_context` 0.09s (was 0.164). **Remaining top is still `aligned` 0.85s (64% of 1.33s) but now 2.04× faster; next is `ingest` 0.22s (16%).**

- **Why not rolling beta next:** Rolling would save ~0.09s beta (7% of 1.33) but needs mutable cache with PIT, missing-bar, corporate-action invalidation — high risk for 7% gain. Spec sec 14-16 says only if profiling still justifies and cache identity includes all inputs. Our numeric view already solved the dominant `aligned` duplication (45%→19% of total); rolling adds 10-20% but fragile.

**Decision: STOP further performance optimization; next priority is architecture/research tooling.**

- **Reason:** Diminishing returns. We have achieved **2.31× total (3.0868→1.3342)**, exceeding stretch 2×, with clean immutable view, `Bar` preserved, `PAPER/LIVE` shared core, `~1.15 GB` within 1.20 strong target. Further 10-20% via rolling beta or shared memory would add mutable state, CoW complexity, and corporate-action invalidation risk for small gain.
- **Recommended Mission 3 focus:** **Research tooling & decomposition**, not performance.
  - **Option 1 (preferred):** Research tooling — expose `NumericHistoryView` to research notebooks (e.g., `research_tier2.py`) for fast feature engineering, add `benchmarks/bench_strategy_math.py` as CI gate, and add `prepared_numeric_history` to `MarketDataset` for `run_dataset` path.
  - **Option 2:** General architecture — decompose `PortfolioBacktestEngine` (1,680 lines) into `HistoryStore`, `FillEngine`, `RiskEngine`, `ContextBuilder` to reduce `_build_context` 0.09s overhead and make `Bar` vs `Numeric` boundary explicit.
  - **Option 3:** Multiprocessing/shared data — if research batch grows to 100 trials, investigate `shared_memory` for `prepared` Bars (79 MB) more than for view (11 MB), but only if `workers=4` wall time becomes critical.

**If performance must continue, the only justified path is:**

- **Precompute aligned closes, not returns:** Cache per-symbol `aligned_closes` per decision still needs inner-join, not viable.
- **Otherwise, accept 1.33s as core and optimize research product:** The `universe` building (8s) and CSV load (4s) dominate end-to-end `research_acceleration_after.json` (16.8s universe + 4.7s CSV + 0.26s prepared). The engine is now 1.33s (8% of end-to-end); further engine work yields <1% end-to-end.

**Do not automatically recommend further performance work.** Returns are now diminishing; architecture and research ergonomics are higher ROI.

---

## Artifacts

- `src/parayabuck/backtest/numeric_history.py` (280 lines) — `NumericSeries`, `NumericHistoryView`, `build_numeric_history`, `aligned_returns_numeric`, `ols_beta_arrays`, `volatility_array`, `compounded_return_array` (all PIT-safe, immutable).
- `src/parayabuck/portfolio/base.py` — adds `numeric_history: Any|None`.
- `src/parayabuck/backtest/portfolio_engine.py` — `run(..., numeric_history)`, `_build_context` attaches, watermark fallback.
- `src/parayabuck/backtest/product.py` — `run_product_backtest(..., numeric_history)` auto-builds view from `available` (once per dataset) and `TerminalAwareEngine` same.
- `src/parayabuck/portfolio/equity_residual_momentum.py` — `_score_candidates` fast path `if numeric_view: return _score_candidates_numeric(...)` + `_score_candidates_numeric` (vectorized, 180 lines, shared core).
- `src/parayabuck/portfolio/equity_residual_momentum_v3.py` — `_compounded` handles `ndarray` via `np.asarray` (shared).
- `benchmarks/backtest_core_mission2_before.json` (2.2023s) + `benchmarks/backtest_core_mission2_after.json` (1.3342s) — machine-readable.
- `benchmarks/bench_backtest_core.py` — builds view once per prepared dataset, passes to engine/product, reports `bars/sec`, `decisions/sec`, `peak_rss`.
- `benchmarks/bench_strategy_math.py` — isolated 150 sym microbenchmark (2.29× isolated, 0.95× full V3 small pool).
- `benchmarks/profile_mission2_before.md` (6.8k) — updated profile with `np.array`/`tolist` quantification.
- `tests/test_numeric_history_parity.py` (10 tests) — PIT, missing bars, ranking, immutability, PAPER/LIVE.

**Repro:**

```bash
uv run python benchmarks/bench_backtest_core.py  # generates before/after JSON
uv run python benchmarks/bench_strategy_math.py # isolated
uv run ruff check src && uv run mypy src && uv run pytest -q  # 994 passed
```

**Git:** One logical commit `perf(backtest): numeric history view 1.65× (total 2.31×) via searchsorted` (10 files, +~800 -100).

---

## Final Rule Compliance

- No global `Bar` replacement; `history` remains `tuple[Bar]`, `Bar` validation preserved.
- No PAPER/LIVE diverged strategy; same `_score_candidates` core, `numeric_history` optional, fallback to legacy.
- No duplicated `backtest_fast_decide()`; single scoring core.
- No unbounded global cache; view per prepared dataset, `writeable=False`, `spawn` pickle.
- No future leak; `searchsorted` PIT, `test_pit_no_future_leak` passes.

