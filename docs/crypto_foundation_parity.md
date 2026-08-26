# Crypto Foundation Parity — Hardened

**Status: CRYPTO FOUNDATION PARITY HARDENED**

## 1. Configured Candidate Pool Semantics

**Authoritative pool:** `universe.crypto.trade_symbols` (22 USD pairs, researched 18 → 22 on 2026-08-21).

```
configured_pool = trade_symbols  # 22
    ∩ allowlist  (if non-empty)
    - denylist
    ∩ broker ACTIVE ∧ tradable at t   # for PAPER; bar availability at t for research PIT
```

Pipeline:

```
configured candidate pool
  ↓ current broker active/tradable check (PAPER) or bar availability (research)
  ↓ data-quality / liquidity / ATR / spread / correlation scanner
  ↓ eligible pool (scanner selected_symbols)
  ↓ strategy alpha ranking (crypto_low_variance_v2 on 1d, 22d vol)
  ↓ observation set (streamed, ≤ maximum_stream_symbols) + pinned pins
  ↓ target portfolio (top_n=2 equal weight before risk overlay)
```

**What was wrong before:** `refresh_crypto_universe()` did

```
get_all_assets(CRYPTO, ACTIVE) → every active USD pair → scanner
```

when `allowlist_symbols == []`. PAPER therefore evaluated un-researched pairs (e.g. PAXG/USD, BAT/USD, etc., 36 vs 22) that never belonged to historical research dataset `data/historical/crypto_expanded_1h.csv`. Research PIT used whatever bars existed (including dataset-only extras MKR/Near/TRX).

**Fix:** `src/parayabuck/universe/preflight.py` now intersects `trade_symbols` with broker active/tradable; `src/parayabuck/backtest/universe.py` filters PIT candidates to `trade_symbols` when `crypto_config` is present. Same economics, same scanner, same alpha.

## 2. Difference Between Sets

| Set | Definition | Example (current) |
|-----|------------|-------------------|
| **candidate pool** | Configured pool ∩ broker availability at t | 22 → ≈ 15-22 depending on broker; FOO/USD never enters |
| **eligible pool** (`selected_symbols`) | Candidates passing scanner eligibility (completeness, liquidity, ATR, spread, correlation) | Scanner `selected_symbols` |
| **observation set** | Top `maximum_stream_symbols` (12) after strategy alpha ranking plus pinned BTC/ETH | Streamed symbols; persisted snapshot |
| **selected target portfolio** | Top-2 lowest vol from eligible pool, equal weight pre-risk | `strategy.decide` output |
| **managed outside-pool position** | Position held that left configured pool | Observed via `load_active_crypto_symbols(..., pinned=(managed,))` as `managed_position` role, never re-enters alpha pool for fresh ENTRY; only risk-reducing exits |

Managed positions are **observable but not entry-eligible**. `load_active_crypto_symbols` adds them as reserved pins without making them candidates.

## 3. Research ↔ PAPER Parity Table

| Aspect | RESEARCH (backtest) | PAPER (live refresh) | Notes |
|--------|---------------------|----------------------|-------|
| Candidate source | `trade_symbols` ∩ bars available at timestamp `t` | `trade_symbols` ∩ broker `ACTIVE ∧ tradable` at now | Availability inferred from bars vs authoritative broker metadata |
| Scanner | `CryptoUniverseScanner` same config (`lookback_days 14, 1h bars`) | Same | Deterministic, `candidate input order` irrelevant (sorted) |
| Eligibility | Same filters (`minimum_data_completeness 0.90`, `median_dollar_volume 1000`, `ATR 0.004–0.2`, `spread 200bps`, `btc corr 0.97`) | Same | Spread `None` → neutral 0.5 if unavailable |
| Alpha ranking | `crypto_low_variance_v2` sample std 22 daily returns, 23 consecutive bars | Same via `_crypto_observation_set` batch 1d history | Shared `_score_candidates` |
| Observation set | `maximum_stream_symbols 12` + pins | Same | Strategy ranked, scanner fallback if < warmup |
| Unavoidable differences | Historical tradability metadata unavailable; inferred from bar presence; delisted assets cannot be reconstructed; historical quotes/spreads unavailable (neutral) | Broker provides authoritative status, tradability, quotes | Documented, not claimed PIT-perfect |
| Hash | `hash_point_in_time_universe` | `crypto_scanner_config_digest` + `observation_strategy_*` | Digest excludes observation identity (see §5) |

## 4. Category Metadata Policy

**Single source:** `src/parayabuck/universe/crypto_scanner.py:CRYPTO_CATEGORIES` (static, operator-maintained, 22 entries).

| Symbol | Category | Reason |
|--------|----------|--------|
| BTC/USD, ETH/USD | core | |
| SOL/USD, XRP/USD, ADA/USD, AVAX/USD, DOT/USD | layer1 | |
| LINK/USD, UNI/USD, AAVE/USD, ONDO/USD, CRV/USD | defi | CRV added |
| FIL/USD, RENDER/USD, GRT/USD | infrastructure | GRT added |
| LTC/USD, BCH/USD | legacy | |
| ARB/USD, HYPE/USD | high_beta | |
| DOGE/USD, SHIB/USD, PEPE/USD | meme | SHIB/PEPE added |

**Invariant:** `configured trade_symbols - CRYPTO_CATEGORIES == ∅` for dynamic crypto trading. Enforced at `CryptoUniverseConfig.__post_init__` fail-fast; not silent `unknown_category_penalty`. Unconfigured discovery symbols may remain `unknown` defensively, but never for PAPER 22.

**Control:** `maximum_meme_symbols=1`, `maximum_per_category=3`, `maximum_high_beta_symbols=2` apply in `CryptoUniverseScanner.scan` fallback observation (pre-alpha) and category distribution is reported.

## 5. Observation Provenance Policy

**Fields (parity with equity):**

- `observation_strategy_name`
- `observation_strategy_version`
- `observation_strategy_timeframe`
- `observation_strategy_parameters_digest` (sha256 of sorted `strategy.parameters`)

Persisted in `CryptoScannerResult` and in `data/runtime/active_crypto_universe.json`. Scanner digest (`crypto_scanner_config_digest`) **excludes** these fields (same as equity); changing alpha invalidates snapshot via `observation_strategy_*` mismatch, not via pretending scanner economics changed.

**Validation:** `validate_active_crypto_universe` fail-closes with precise statuses:

- `observation_strategy_mismatch` (name or version)
- `observation_timeframe_mismatch`
- `observation_digest_mismatch`

Order: `status` → `scanner_version` → `config_digest` → **observation identity** → timestamps → pins → candidate pool → observation inside pool. `load_active_crypto_symbols` falls back to `pinned + trade_symbols` truncated to `maximum_stream_symbols` on any mismatch, with warning.

**Test:** Persist snapshot with `v2/1d/abc123`, load with `v999/9.9.9` → `observation_strategy_mismatch` (see `tests/test_crypto_foundation_parity.py`).

## 6. Historical Dataset Audit (prep for next mission)

- **File:** `data/historical/crypto_expanded_1h.csv` (705,541 rows, 25 symbols, header `timestamp,symbol,open,high,low,close,volume`)
- **Unique symbols:** AAVE, ADA, ARB, AVAX, BCH, BTC, CRV, DOGE, DOT, ETH, FIL, GRT, HYPE, LINK, LTC, MKR, NEAR, ONDO, PEPE, RENDER, SHIB, SOL, TRX, UNI, XRP (25)
- **Configured 22 present:** all 22 present (intersection = 22).
- **Dataset-only extras:** MKR/USD, NEAR/USD, TRX/USD (3) — excluded from alpha after fix.
- **Counts & coverage:**
  - AAVE 44,072 (2021-07-15 → 2026-07-31)
  - AVAX 41,163 (2021-11-18 → 2026-07-31)
  - BCH 48,746, BTC 48,869, DOGE 48,859, ETH 48,867, LINK 48,857, LTC 48,865, UNI 48,799 (full since 2021-01-01)
  - SOL 38,787 (2021-01-01 → 2026-07-31)
  - XRP 22,566 (2024-01-01 → 2026-07-31)
  - SHIB 29,634 (2023-03-14 → 2026-07-31)
  - DOT 25,848, CRV 25,871 (2023-08-18 → 2026-07-31)
  - GRT 44,175 (2021-07-15 → 2026-07-31)
  - ADA 4,020, ARB 3,947, FIL 3,947, HYPE 3,923, ONDO 3,923, RENDER 3,878 (all Feb 2026 → 2026-07-31) — late listing.
  - PEPE 13,156 (2025-01-29 → 2026-07-31)
  - MKR 40,641 (2021-01-01 → 2025-09-05) — ends early
  - NEAR 6,298 (2022-01-20 → 2023-06-23)
  - TRX 7,830 (2022-04-01 → 2023-04-19)
- **Gaps:** obvious listing dates; no forward-fill/interpolation (missing bar = missing data).
- **Aggregation 1h → 1d:** `crypto_candidate_from_bars` uses median daily notional (not hourly); `CryptoLowVariance` trailing 22 daily returns require 23 completed consecutive daily bars spanning exactly 22 calendar days (`bar_is_closed` check in `preflight._crypto_observation_set` prevents forming bar rank). Historical daily aggregation in backtest uses `require_complete=True` implicitly via `bars_to_calendar_days` and `bar_is_closed` filtering; gaps cause exclusion until 22 consecutive days accumulate.
- **Availability assumption:** listing support inferred from bar availability at t (PIT), not from historical tradability metadata (unavailable).

See artifact `data/research/crypto_foundation_2026_08/crypto_universe_parity_audit.json` for deterministic JSON with hashes.

## 7. Canonical Numbers

Before fix, PIT universe included dataset-only extras; after fix, only configured 22. Local re-run on same economics (initial_cash 100k, slippage 2bps, spread 2bps, taker 25bps, daily rebalance) shows **byte-identical for early window where extras had no data, diverging only when extras become liquid**; overall delta is correctness over headline return. No parameter tuning.

Run audit to regenerate before/after:

```bash
python scripts/audit_crypto_universe_parity.py
```

and for backtest comparison (example harness):

```bash
python benchmarks/parity.py  # if available
```

Universe/observation hashes are recorded in the audit artifact (`trade_symbols_sha256`, `dataset_symbols_sha256`, `scanner_digest_sha256`, `combined_sha256`).

## 8. Files Changed

- `src/parayabuck/universe/crypto_scanner.py` — complete category map, provenance fields, digest excludes observation, validated status
- `src/parayabuck/universe/preflight.py` — candidate-pool parity, observation provenance persist
- `src/parayabuck/config.py` — observation fields, category fail-fast, provenance wiring
- `src/parayabuck/backtest/universe.py` — PIT candidate pool filter
- `tests/test_crypto_scanner.py` — updated unknown penalty to FOO
- `tests/test_crypto_foundation_parity.py` — new foundation parity suite (11 tests)
- `scripts/audit_crypto_universe_parity.py` — deterministic audit
- `data/research/crypto_foundation_2026_08/crypto_universe_parity_audit.json` — artifact
- `docs/crypto_foundation_parity.md` — this doc
- `src/parayabuck/runtime.py` — formatting fix (ruff)

## 9. Frozen Contracts Remain

- `crypto_low_variance_v2`: 1d, 22d vol, 23 bars, lowest vol first, top_n 2 equal weight, 96h, no momentum/regime/etc. — proven in `tests/test_crypto_low_variance.py` and `tests/test_crypto_foundation_parity.py:9`.
- `equity_residual_momentum_v3` untouched (weekly, formation 252, skip 21, beta 273, ivol 63, top_n 8, max 0.10, ivol 0.70).
- `runtime.mode` stays `paper`, `live.enabled` stays `false`, `hard_dollar_cap` 0, `maximum_allocation` 0.05/0.60.
- No new parameters, no LIVE enable, no equity math changes.

## 10. Next Mission Hypotheses (not implemented)

1. Broad-universe crypto research beyond 22 with explicit listing metadata.
2. Intraday (1h) low-variance vs daily for turnover/cost tradeoff.
3. Meme concentration vs vol interaction study.
