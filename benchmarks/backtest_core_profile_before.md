# Backtest Core Profiling — Before Optimization (HEAD 63b7c72)

Generated 2026-08-28, machine macOS-26.5.2-arm64, Python 3.13.12

## Benchmark Context

- Dataset: `data/historical/equity_1200_2016_2026.csv` (1_738_468 bars, 123 MB)
- Window: 2019-01-01 → 2021-12-31 (3y, 753_178 bars including warmup, 877 universe snapshots)
- Strategy: `equity_residual_momentum_v3` (production MH_RAW, monthly, top_n=8)
- Prepared-window reuse (universe + dedup/sort reused), engine-only median **3.0868s** (product wrapper 3.1102s)
- 6m window 2019-01-01 → 2019-06-30: engine median **0.575s**, 124 bar cycles, 26 decide calls, 3_835 symbol-decision evaluations

cProfile overhead ~2.7x; native timing used for speedup claims, cProfile for distribution.

## Main cProfile (6m window, 1.627s under profiler, 0.575s native)

```
  12751801 calls in 1.537s (v2) / 13890324 calls in 1.652s (v3)
  run                          1.627s 100%
  _decide                      1.009s 62.0%   124 calls (every bar cycle, early return for non-scheduled)
    _score_candidates          0.964s 59.2%   26 scheduled decisions
      _aligned_returns         0.486s 29.9%   3_835 calls (per-symbol timestamp join, 273 bars window)
      _ols_beta                0.196s 12.0%   3_835 calls
      _compounded_return       0.080s 4.9%    3_835 calls
      multi_horizon_consensus  0.096s 5.9%    v3 only, 26 calls (includes 5_799 _compounded)
  _ingest                      0.204s 12.5%   385_491 calls
  _ingest_execution            0.196s 12.0%   385_491 calls
  _key                         0.133s 8.2%    831_802 calls
  canonical_symbol             0.066s 4.0%    1_043_080 calls
  sorted (overall)             0.076s 4.7%    4_179 calls (context keys, rank sorting)
  _fill_cycle                  0.037s 2.3%    124 calls
  _fill_side                   0.027s 1.7%    121_640 calls? grouped
  _build_context               0.022s 1.35%   26 calls
  in_universe / contains       0.040s 2.5%    12_921 calls each (bisect_right O(log n) already)
  _equity                      0.001s 0.06%   478 calls (8 holdings avg, sum over positions)
  _known_average_volume        0.002s 0.12%   66-81 calls (window 20 scan, linear but tiny)
  _record_holdings_snapshot    0.002s 0.12%   124 calls
  _update_risk_state           0.001s 0.06%   124 calls
```

Scaled to 3y (157 decisions, 525 fills, 753k bars):
- estimated _decide ~6.0x larger → ~6.1s profiler / ~2.2s native
- ingest scales with bars: 753k vs 385k → ~0.78s native
- strategy remains ~60% of total

## Ranked Bottlenecks (native 3y extrapolated)

1. **Strategy scoring — 58-62%** — `_aligned_returns` timestamp JOIN + `_ols_beta` OLS + residual compounding + ivol. Per-symbol Python loops over 273-bar windows, `statistics.fmean`, generator `sum()`, `math.isfinite` checks. 3_835 evaluations per 6m → ~23k per 3y.
2. **History ingestion — 24-26%** — `_ingest` + `_ingest_execution` per bar (385k-753k calls) with `canonical_symbol` + `dict.setdefault` + `list.append`. `_key` helper adds 8% extra.
3. **Canonical symbol normalization — 4%** — 1M calls per 6m, cached dict but still function call + tuple key overhead.
4. **Sorting / ranking — 4-5%** — `sorted(context.history)` per decision (26×1200 keys), `sorted(scored)` for rank, percentile rank sorts.
5. **Fill processing — 2-3%** — `_fill_cycle` scans pending orders per bar (twice per cycle, SELL then BUY), `_fill_side` linear pending scan, `_known_average_volume` limited.
6. **Universe membership — 2.5%** — `in_universe` → `PointInTimeUniverse.contains` with `bisect_right` O(log 877) already optimal; engine helper `_universe_symbols` linear scan still present for history but negligible.
7. **Context construction — 1.3%** — Already optimized via trailing-window trim (`_MAX_CONTEXT_HISTORY_BARS=274`) and fast-path tail check; dictionary/tuple allocation per decision still ~172k Bar references but cheap.
8. **Equity / risk / holdings — <0.3% combined** — `_equity` iterates 5-6 positions, `_record_holdings_snapshot` sums active positions, `_update_risk_state` per cycle.

## Detailed Observations

### Context construction
Previously O(B) full history copy per bar cycle (O(B²)). Current code trims to `max(minimum_history_bars, 274)` and uses fast-path `bars[-1].timestamp <= decision_timestamp` → all eligible slice without scanning. Profiler confirms 1.35% cost (0.022s for 26 decisions). Allocation pressure from `tuple(eligible)` + `dict[str, tuple]` per decision remains but not dominant. Benchmark spill from full-history misuse eliminated.

### Strategy calculations
- `_aligned_returns`: builds `pairs` list via `market_close_by_timestamp.get` per bar (274 dict gets per symbol), then loops again to compute returns. Two Python loops per symbol.
- `_ols_beta`: `statistics.fmean` uses `math.fsum` per symbol (2 calls), plus two generator `sum()` for variance/covariance with per-element Python overhead. `1075802` `dict.get` etc.
- `realized_volatility` / `_volatility`: similar `fmean` + `sum` generator.
- `multi_horizon_consensus` (v3): extra 3× `_compounded` per symbol adds 0.088s for 26 decisions.

All are CPU-bound pure Python, no I/O.

### Repeated historical scans
Per-symbol per-decision full-window scan (273) repeated 3_835 times = ~1M bar touches per 6m. No incremental caching, no precomputed return arrays. Market `spy_bars` full history kept untrimmed for JOIN correctness (good) but rebuilt dict per decision.

### `_known_average_volume`
Scans `execution_history` list filter `item.timestamp <= order.created_at` for window 20. For 6m, 81 fills → 81 linear scans over ~300-600 length lists, 0.002s negligible. Not a bottleneck.

### `_equity()`
Sums `qty * price` over 5-6 positions per call, 478 calls per 6m → 0.001s negligible. Caching equity would save <0.1% but risk parity bugs; not justified.

### Canonical symbol
Called 1_043_080 times per 6m (per bar ingest + per symbol strategy loop + universe checks). Cache bounded 65k, hit rate high, still 4% of runtime due to Python function call + tuple key construction. Pre-canonicalizing bars once could halve calls.

### List / tuple / dict construction
Per-decision `history` dict with 1200 entries, each `tuple(eligible)` of 274 Bars → ~8.5M Bar references copied per 6m but only 0.022s. Not bottleneck after trim optimization.

### Sorting
`sorted(context.history)` per decision sorts 1200 strings; 26 sorts per 6m → 31k comparisons, 0.065s including 4_179 total sorts. Minor.

### Cross-sectional ranking
`percentile_ranks` sorts scores dict (size ~147 per decision) and handles ties; 26 calls per 6m, part of 0.096s consensus cost.

### Bar object overhead
`Bar` is frozen dataclass with validation (`__post_init__` checks finite, OHLC ordering). 1.7M bars in memory ≈ 1.1 GB peak RSS (1114 MB). Per-bar attribute access `bar.close`, `bar.timestamp` dominates strategy loops. Compact representation (slots, numpy) would reduce memory and attribute overhead but requires migration; current overhead shows as `math.isfinite` 30k calls but not dominant.

## Measurements

- 3y engine median 3.0868s, product 3.1102s, batch4 12.4679s, bars/sec 244k, decisions/sec 50.9, peak RSS 1114 MB
- 6m engine median 0.575s, bars/sec 670k
- Synthetic CI fixture 14.5k bars, 2 decisions, engine 0.0169s, 840k bars/sec

## Conclusions for Optimization

Target 1.5x single-trial speedup = need to cut 3.08s → ≤2.05s (save ~1.03s). Strategy scoring alone is ~1.8s of that, ingestion ~0.78s. Optimizing strategy math with numpy/vectorization expected to save ~0.7-0.9s (40% of strategy). Ingestion micro-opts expected ~0.15-0.25s. Combined aim → 2.0-2.2s (1.4-1.5x). To reach 2x, need additional ingest + canonical + sorting micro-opts or more aggressive numpy adoption.

Next steps: vectorize `_aligned_returns` + `_ols_beta` + `_volatility` + `_compounded`, pre-canonicalize bars, inline `_key`, cache sorted symbols, index universe snapshots (already bisected).
