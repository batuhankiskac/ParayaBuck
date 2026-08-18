# ParayaBuck Optimization Tournament Report — 2026-08-18

PAPER product, `equity_residual_momentum_v2` (weekly) + `crypto_low_variance_v2` (96h).
Every Tier-1 candidate: baselined → implemented in isolation → benchmarked → correctness-checked → accepted/rejected.

## 1. Baseline

| | HEAD BEFORE | HEAD AFTER |
|---|---|---|
| commit | `9158382` | `e76e87f` |
| branch | main (clean) | main (clean) |
| Python | 3.14.7 | 3.14.7 |
| OS / CPU / RAM | Darwin arm64, 10 cores, 17 GB | same |
| ruff / format / mypy | clean | clean |
| pytest | 594 passed | 599 passed |
| paper chaos | 94 passed | 94 passed |
| build / pip-audit | ok / clean | ok / clean |

Baseline gate first, before any optimization: ruff, format, mypy, pytest, build, chaos, pip-audit all green at HEAD `9158382`.
Deterministic harness committed (`benchmarks/`): strategy, canonical backtest, startup, DB, memory; golden-parity hash locked at `46e257615b0cb924b15ad20857c444d0efbb3d7ea20055b60bc552df9269f748`.

## 2. Candidates tested

ID | candidate | before | after | delta | correctness | verdict
---|---|---|---|---|---|---
A1 | cache `canonical_symbol` (bounded exact-key) | 5-6 calls/bar hot loop | 1 lookup | micro | parity MATCH | ACCEPT
A2 | backtest: skip context build on non-scheduled cycles | full context/bar | gated | structural | parity MATCH | ACCEPT
A3 | universe: drop redundant per-period sorts, all-bar max | 2 sorts/period | 1 pass | micro | parity MATCH | ACCEPT
A4 | backtest: trim context history to strategy trailing window | O(B²) tuple copies | O(B·W) | major | parity MATCH | ACCEPT
A5 | equity: SPY close lookup once per decision | 300×274 dict builds | 1 build | ~2 ms/decision | bit-identical | ACCEPT
A6 | equity: idio-vol percentiles via bisect | O(n²) | O(n log n) | micro | parity MATCH | ACCEPT
A7 | universe: bisect snapshot membership | linear scan | bisect | micro | parity MATCH | ACCEPT
A8 | optimize_grid: hoist per-trial preprocessing | re-sort/dedup/trial | once/window | research | parity MATCH | ACCEPT
B1 | share one AccountSnapshot per processing cycle (30s bound) | 1.77 account()/cycle | 0 | -100% calls | parity+chaos | ACCEPT
B2 | hoist not-scheduled gate before high-water/context work | 301 µs/batch | 162 µs | -46% | parity+chaos | ACCEPT
B4 | RuntimeHealth once per batch | 2-4 builds/cycle | 1 | -50% | parity+chaos | ACCEPT
C1 | dashboard config parse cached by file mtime | parse/request | cached | structural | parity+chaos | ACCEPT
C2 | display-only dashboard snapshot TTL cache | 479 ms/payload | ~1 ms | -99.7% | parity+chaos | ACCEPT
C3 | parsed universe JSON cached by mtime | parse/request | cached | structural | parity+chaos | ACCEPT
C4 | one shared read-only connection per payload build | 12 conns/build | 2 | -83% | parity+chaos | ACCEPT
C5 | 6 additive indexes on account-scoped hot queries | full scans | index SEARCH | 0.36→0.03 ms | parity+chaos | ACCEPT
D1 | crypto scanner downloads only cache tail | 6526 rows | 123 | -98.1% | determinism test | ACCEPT
D2 | equity refresh reuses observation cache, same-slot skip | 23147 rows | 790 | -96.6% | determinism test | ACCEPT
D3 | runtime warmup reuses warmup cache with tail fetches | 21394 rows | 132 | -99.4% | determinism test | ACCEPT
E1 | trade-update reuses fresh account snapshot | 2 REST calls/fill | 0 | -33% REST/min | byte-identical reconcile | ACCEPT
E2 | batch open-orders fetch across protection checks | 4 get_orders/min | 1 | -75% | chaos | ACCEPT
E3 | incremental fill-activity sync with persisted watermark | 5 pages | 1 | reconcile 3.1→0.7 ms | determinism test | ACCEPT

Rejected / no-change (with evidence): event-queue coalesce (O(1) steady-state already), 3× bar enqueue (2 events/5m real, us-scale), reconcile lock (uncontended by construction), crypto to_thread batching (load-bearing timing), telemetry query bounds (semantics need full history), ledger batch commits (restart safety), canonical_symbol micro-opt in buffers, memory/retry audits (bounds enforced, no leaks/busy loops).

## 3. Accepted

candidate | impact | why safe | commit
---|---|---|---
A4 context trim + A2 skip | equity backtest -42% | trailing-window-only consumers; parity hash exact | `97f66e1`, `0fa371c`
C2 dashboard snapshot cache | payload 479→1 ms | display-only, keyed by engine state+config mtime, no mutation | `de08f1c`
B1 account snapshot sharing | -100% decision-path account() | freshness keyed on snapshot's own timestamp ≤30s | `32bf522`, `c8338fc`
D1/D2/D3 scanner+warmup caches | rows -97..-99%, warmup -40% | fail-closed digest/head/freshness gates | `6e2b253`..`9816faa`
E3 fill-activity watermark | reconcile 5 pages→1 | byte-identical seeded reconcile, idempotent | `75427f2`
C5 indexes | 0.36→0.03 ms lookups | additive CREATE INDEX IF NOT EXISTS | `a8b5c6e`
E1/E2 trade-update + open-orders | REST/min -33% | snapshot reuse within poll interval; batch merge verified | `8a8197a`, `ddee3ce`
A5/A6/A8 strategy+grid | decide -5%, grid faster | bit-identical outputs; `optimize_grid` timeframe-resolution fix | `8d7f528`, `1101d78`, `58479fe`, `e606c68`

## 4. Rejected

candidate | reason
---|---
W2 event-queue coalesce index | steady-state put already O(1); full-queue path unreachable in healthy operation
W3 bar enqueue dedupe | distinct consumers, distinct overflow semantics, us-scale at 0.3 events/s
W5 reconcile snapshot-under-lock | lock uncontended by construction; added concurrency surface for zero benefit
W6 crypto protection batching | 59 µs/bar = 1.8 ms per 5m cycle; batching changes load-bearing timing
C6 telemetry date bounds | report semantics require full history (INFEASIBLE)
C7 ledger batch commits | per-event commit required for restart safety
C6 buffers canonical_symbol | 0.6 µs/bar; complexity > benefit

## 5. Strategy compute

equity decide: 26.0 ms → 24.8 ms (300-symbol synthetic, idle). crypto: ~0.1 ms unchanged (already negligible). Outputs bit-identical (repr-hash equality on synthetic contexts; parity hash exact).

## 6. Database

queries before/after: 66 → 49 per dashboard payload build; decision_orders lookup 0.36→0.03 ms; operations last-decision 0.20→0.003 ms (seeded 1.2k decisions/12k orders/36k fills). indexes: +6 additive. write reduction: none removed (all proven semantically load-bearing); insert cost unchanged within noise.

## 7. Network

REST calls: 21 → 14 per simulated minute (-33%); account() 7-12/min → structural reuse within 30s boundary; reconcile fill pages 5 → 1. streaming: unchanged (set-diff convergence, no duplicate subscriptions verified). reconnections: bounded 2^n ≤16s, limit 5, verified.

## 8. Startup

config load 3.7 ms, DB open 0.2 ms, runtime construction 4.7 ms (idle; faked discovery) — already sub-20 ms. Dominant cost was warmup downloads: warmup wall -40% (2.91→1.74 s under load), rows -99.4% via fail-closed cache.

## 9. Backtest

equity v2 (2019-2021, 1200 symbols): 36.9 s → 21.4 s median, -42%. crypto v2: 1.81 s → 1.05 s, -42%. Combined parity run: ~71 s → 28 s.

## 10. Memory

idle 101 MB; equity backtest peak 1190 MB (1.7M Bar objects inherent); no leaks found (bounds: queue 1024, BarStore 5000, processed_batches 256).

## 11. Concurrency

locks: no new locks in trading paths; thread-local dashboard connection; snapshot-under-lock pattern unchanged. parallel work: none added (rejected with evidence). race tests: chaos gate 94 + restart suites 56.

## 12. Tier 2 economic research

rebalance band: 0.5% control beats 0.75%/1.0% on net return+Sharpe (equity 0.340 vs 0.336/0.325) — REJECTED. minimum order: no effect at $10/25/50 (band gate dominates) — INCONCLUSIVE. execution style: INFEASIBLE (daily data cannot model fills). crypto cost: $5,495 fees / 100k, 30.6% fill reversals, 44.3% fills <$500; band/notional levers do not improve net economics — research-only, not promoted. timing: INFEASIBLE (completed-bar/T+1 authoritative).

## 13. Golden parity

universe/ranking/target/risk/orders/fills/PnL/equity curve: PARITY MATCH (exact hash) before and after integration and after the three P3 fixes.

## 14. Test gate

ruff: clean (145 files). format: clean. mypy: clean (67 files, strict). pytest: 599 passed. build: wheel+tar ok. paper chaos: 94 passed. pip-audit: no known vulnerabilities.

## 15. Safety

LIVE: untouched (disabled). risk: unchanged. alpha: unchanged. allocations: unchanged. lookahead: none (PIT/completed-bar/T+1 preserved). restart parity: restart recovery/chaos/hardening suites + chaos gate pass; reconcile determinism byte-identical.

## 16. Top 10 optimization opportunities

rank | opportunity | impact | confidence | complexity | status
---|---|---|---|---|---
1 | backtest context trim + scheduled-skip (A4/A2) | equity backtest -42% | high | low | ACCEPTED
2 | dashboard snapshot TTL cache (C2) | 479→1 ms payload | high | low | ACCEPTED
3 | per-cycle account snapshot sharing (B1/E1) | -100% decision account(), -33% REST/min | high | medium | ACCEPTED
4 | scanner/warmup incremental caches (D1-D3) | rows -97..-99%, warmup -40% | high | medium | ACCEPTED
5 | incremental fill-activity sync (E3) | reconcile 5→1 pages | high | medium | ACCEPTED
6 | additive hot-query indexes (C5) | 0.36→0.03 ms lookups | medium | low | ACCEPTED
7 | open-orders batch + health once (E2/B4) | -75% get_orders, -50% health | medium | low | ACCEPTED
8 | equity scoring hoist + bisect (A5/A6) | decide -5% | medium | low | ACCEPTED
9 | optimize_grid preprocessing hoist (A8) | research speedup | medium | low | ACCEPTED
10 | Tier-2 band/notional levers | no net improvement | low | - | REJECTED

## 17. Final product impact

startup: warmup -40% (network-bound dominant cost). runtime CPU: decision batch -46% non-scheduled; health -50%. memory: no regression, no leak. API reduction: -33% REST/min, -100% decision-path account(). DB latency: -83% connections, 49 vs 66 queries, index-driven hot paths. backtest speed: -42% equity, -42% crypto. economic improvement: none promoted (Tier 2 control remains best).

## 18. Final verdict

**A. MATERIAL ENGINEERING OPTIMIZATION ACHIEVED** — 22 accepted Tier-1 candidates with measured, isolated, parity-verified benefit; 13 rejected/infeasible/no-change with evidence; 3 P3 findings from the adversarial audit fixed. Same strategy, same risk, same decisions, same safety — with ~42% faster backtests, ~100x faster dashboard, -97..-99% scanner rows, -33% API calls, and zero economic change.
