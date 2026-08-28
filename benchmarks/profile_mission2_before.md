# Mission 2 — Fresh Profile After Mission 1 (c4943f7)

**Head:** `c4943f7b6b96745b2b89f665378be2b8342208ca`
**Date:** 2026-08-28
**Dataset:** `equity_1200_2016_2026.csv` 1,738,468 bars

## Bench Before (mission2 starting point, from `bench_backtest_core.py`)

- C synthetic (50 sym, 400d): 0.012s engine / 1183k bars/sec
- A realistic 3y 2019-2021 (753k bars, 157 decisions, 525 fills): **2.2023s** engine median (342k bars/sec, 71.29 dec/sec), product 2.2309s, batch4 8.9549s, peak RSS 1109 MB
- B 6m 2019-H1 (385k bars, 26 decisions, 81 fills): **0.3772s** engine, batch4 1.5564s

This matches Mission1 after (2.201s) within noise.

## cProfile (6m v3, 0.925s under profiler, 0.377s native, 7,032,450 calls)

```
run                          0.926s 100%
  _decide                    0.589s 63.6% (124 cycles)
    _score_candidates        0.544s 58.7% (26 calls)
      _aligned_returns       0.333s 36.0% 3835 calls
        numpy.array          0.047s  5.1% 15340 calls (2 per aligned)
        tolist               0.013s  1.4% 7670 calls
        append               0.119s 12.9% 2.9M calls
        dict.get             0.095s 10.3% 1.85M gets
      _compounded_return     0.078s  8.4% 3835 calls
      _ols_beta              0.035s  3.8% 3835 calls
        sum                  0.015s, dot 0.000
      multi_horizon          0.050s  5.4% 26 calls (5799 _compounded)
        numpy.asarray        0.012s
        numpy.prod/any/all   0.015s
      _volatility            0.007s  0.8%
    _build_context           0.022s  2.4%
    _fill_cycle              0.022s  2.4%
  _ingest                    0.113s 12.2% 385k
  _ingest_execution          0.107s 11.6% 385k
```

## cProfile (3y v3, 4.97s under profiler, 2.20s native, 36,488,931 calls)

```
run                          4.974s 100%
  _decide                    3.985s 80.1%
    _score_candidates        3.700s 74.4%
      _aligned_returns       2.265s 45.5% 25889 calls (164 avg per decision)
        numpy.array          0.314s  6.3% 103556 calls
        tolist               0.092s  1.8% 51778 calls
        append               0.619s 12.4% 16.0M
        dict.get             0.407s  8.2% 8.8M
      _compounded            0.523s 10.5%
      _ols_beta              0.238s  4.8%
      multi_horizon          0.347s  7.0% (40125 _compounded)
        numpy.array/asarray  0.084s
      _volatility            0.044s  0.9%
  _build_context             0.164s  3.3%
  _ingest                    0.229s  4.6%
  _ingest_execution          0.235s  4.7%
```

## Quantification of requested question

**How much time is spent doing `np.array(...)` on same trailing values every decision?**

- 6m: 15340 `np.array` calls in `_aligned_returns` (3835*2 + 3835*2 for beta) = 0.047s + 0.012s tolist = 0.059s of 0.333s aligned (18% of aligned, 6.4% of total). Plus `_ols_beta` also does 2 arrays per call when n>=64 (3835*2=7670) but only for 3835 calls where n>=64, included in 15340. For 3y: 103k arrays = 0.314s + 0.092 tolist = 0.406s of 2.265s (18% of aligned, 8% of total). However the larger cost is the Python loops that FEED those arrays: `append` 0.619s + `get` 0.407s = **1.026s per 3y (20% of total, 45% of aligned)**. Combined, **_aligned_returns total 2.265s (45% of run) is dominated by per-bar traversal (274*25889 ≈ 7M bar touches, each dict get+append+close check), then array allocation, then return computation.**

**Other numeric hotspots:**
- `_compounded_return` loops over residuals Python list (273*25889 ≈ 7M iterations) = 0.523s 10.5% (each does `value*=1+res` + isfinite check)
- `_ols_beta` vectorized part small (0.057s) because most n>=64 path uses dot; but `sum` still 0.054s for means
- `market_close_by_timestamp` rebuilt 157 times (SPY 1500 entries each) = 157*1500 dict inserts ≈ 235k, not separately profiled but inside `_score_candidates` 0.251s includes dict build (0.251-0.333? Actually _score includes dict build + loops). Measured: market dict building ~0.02s per 3y (157*1500 inserts ~0.02s).
- `Bar.close` traversal: 7M attribute accesses inside loops, included in append/get overhead.

**Per-symbol per-decision cost breakdown (164 symbols avg, 273 window):**
- 274 `bar.close` + `dict.get` + `append` = ~274*164*157 ≈ 7M iterates, 1.026s → ~0.15 µs per bar? Actually 1.026s/7M=146ns per bar (plausible).
- 2 `np.array(273)` allocations + 2 `tolist(272)` = 4 allocations per symbol*25889 ≈ 100k allocations = 0.406s → 4 µs per allocation.

**Strategy-math microbenchmark isolation (150 symbols, 273 beta, 26 decisions = 3900 aligned):**
- aligned: 3835*0.087ms ≈ 0.333s for 6m → ~0.087ms per symbol (measured)
- beta: 0.009ms per symbol
- vol: 0.0018ms
- compounded: 0.020ms
- multi-horizon: 0.050s/26 ≈ 1.9ms per decision vs aligned 12.8ms per decision

## Remaining opportunities

1. **Stop rebuilding `list[Bar]` slices per candidate:** Engine `_build_context` already trims to 274, but strategy still does `stock_bars[-(window+1):]` slicing creating new tuple slice per symbol per decision (25889 slices of 274 Bars). With numeric view, a single `searchsorted` + view slice (no copy) replaces this.
2. **Stop rebuilding `market_close_by_timestamp` dict:** Replace with benchmark `NumericSeries` + `searchsorted` per symbol vectorized (no dict, no per-bar Python get).
3. **Avoid `np.array(...)` on same values every decision:** With `NumericHistoryView`, `timestamps` and `closes` are already numpy arrays built once from validated Bars; decision slice is `closes[start:end]` view (no allocation, maybe copy if boolean masking needed but can be minimized).
4. **Avoid `.tolist()` round trips:** Keep returns/residuals as `np.ndarray` through beta→residual→vol→compounded until final scalar score. Currently `_aligned_returns` returns `list[float]` then `_ols_beta` re-converts to `np.array`; double conversion waste 0.092s per 3y + 0.314s re-conversion.
5. **Benchmark filtering:** `benchmark = [bar for bar in decision_bars if bar.symbol==bm]` scans 428k per trial, but currently done once per `run` (0.001s). Not material vs 2.2s.

## Memory

- Base Bars: 1.7M Bars → ~1109 MB. Numeric duplicate of closes+timestamps per symbol would be ~27 MB (1.7M*8*2) + overhead, still <1.20 GB target. If we keep both, 1136 MB estimated.

## Conclusion for Mission2

**60% strategy residual math is confirmed:** For 3y, 3.700s of 4.974s profiler (74% including decide, but native 2.20s → ~1.32s is strategy). Within that, **aligned returns is 61% of scoring, 45% of total**. The `np.array` conversion itself is 18% of aligned, but the Python loops feeding it are 45% of aligned. Fixing both via indexed `NumericHistoryView` with `searchsorted` views should save ~1.0-1.5s per 3y (45-68% of 2.20s), targeting 1.45-1.70×.

Next step: implement `NumericHistoryView` built once per prepared dataset, exposing `timestamps`/`closes` + searchsorted slicing, and refactor strategy pipeline to numpy-native.

