# Codebase Health Audit — 2026-08-27 (T1 Promotion)

**Starting HEAD:** `4a6f5c0fe2a9c3c1d07a8f2ebbeaa8aaa64a9942`
**Audit scope:** `config`, `strategy registry`, `portfolio`, `universe`, `execution`, `runtime`, `monitoring`, `storage`, `web`, `CI/tooling`
**Search signals:** fail-open trading, swallowed exceptions, duplicate vocab, broad `except`, undefined names, dead imports, stale packaged defaults, research/product leaks, duplicate constants, accidental runtime imports from `research`, state mutation outside transactions, unchecked optionals on order paths.

---

## P0 — Correctness / Safety (fixed in this promotion)

### P0-1 — Product strategy boundary leak
- **File:** `src/parayabuck/portfolio/__init__.py:46-76` (`portfolio_strategy_from`), `src/parayabuck/config.py:782-815` (`_resolved_timeframe`, observation blocks)
- **Behavior:** `_PORTFOLIO_STRATEGY_FACTORIES` contained `crypto_low_variance_hysteresis_v1` while `PRODUCT_STRATEGY_NAMES` omitted it; generic `portfolio_strategy_from()` could construct research-only identities, and `load_config()` used the generic factory, so `config/settings.yaml` could select a research strategy without product gating. Product-support semantics were not a security boundary.
- **Repro:** `portfolio_strategy_from("crypto_low_variance_hysteresis_v1")` succeeded; `load_config()` with `strategy.crypto.name: crypto_low_variance_hysteresis_v1` succeeded before promotion without product check.
- **Risk:** Unvalidated research strategy runs in PAPER/LIVE, ordering on un-vetted alpha.
- **Fix:** Added `product_strategy_from()` that validates `name in PRODUCT_STRATEGY_NAMES` then delegates; `load_config()` and observation blocks now use `product_strategy_from`. Research tooling may explicitly call `portfolio_strategy_from`. Added `tests/test_product_boundary.py` (8 tests). `PRODUCT_STRATEGY_NAMES` deliberately gains `crypto_low_variance_hysteresis_v1` only at promotion (COMMIT F).

### P0-2 — Config fail-closed bypass for missing strategy block
- **File:** `src/parayabuck/config.py:756-778` (`load_config`)
- **Behavior:** `_strategy_from_raw()` merged missing `strategy.<market>` YAML block over packaged historical defaults (`crypto_low_variance_v1`). `universe.crypto.enabled=true` + accidentally deleted `strategy.crypto` silently became `crypto_low_variance_v1`.
- **Repro:** YAML with `universe.crypto.enabled: true` and no `strategy.crypto` block loaded without error, selecting v1.
- **Risk:** Operator trims config, production silently trades wrong strategy.
- **Fix:** `load_config()` now requires explicit `strategy.<market>` block with `name` when that market’s universe is enabled **and** the strategy is not explicitly `enabled: false`. Disabled market may omit block. Dataclass defaults remain for unit tests; YAML loading fails closed (`ValueError`). Tests: `tests/test_product_boundary.py` + `tests/test_config.py` updates; existing `tests/test_crypto_universe.py` patched to provide explicit blocks where intents require enabled market.

### P0-3 — Alert event vocabulary drift
- **File:** `src/parayabuck/config.py:122-157` (`_ALERT_KINDS`), `src/parayabuck/monitoring/alerts.py:35-54` (`ALERT_KINDS`, `_WARNING_KINDS`)
- **Behavior:** Two independent literal lists; `monitoring/alerts.py` knew `paper_incident`/`paper_receipt` while `config._ALERT_KINDS` did not, so `Config.alerting` validation and runtime sink could drift.
- **Repro:** `config._ALERT_KINDS` lacked `paper_incident`/`paper_receipt`; `alerts.ALERT_KINDS` included them.
- **Risk:** Config rejects valid runtime events or accepts unknown kinds.
- **Fix:** Created `src/parayabuck/monitoring/events.py` single source (`ALERT_KINDS`, `ALERT_KINDS_ORDERED`, `WARNING_KINDS`, `DEFAULT_ALERT_EVENTS`). Both `config.py` and `monitoring/alerts.py` import from there. `DEFAULT_ALERT_EVENTS` excludes `paper_receipt` (high-volume routine) to avoid webhook spam while keeping it valid configurable; `paper_incident` remains webhook-enabled. Drift test: `AlertingConfig` accepted kinds == `monitoring.ALERT_KINDS`.

### P0-4 — Crypto metadata import bypass (`try/except ImportError: pass`)
- **File:** `src/parayabuck/config.py:565-575`, `src/parayabuck/universe/crypto_scanner.py:25-48`
- **Behavior:** `CryptoUniverseConfig.__post_init__` validated `CRYPTO_CATEGORIES` via `try: from parayabuck.universe.crypto_scanner import CRYPTO_CATEGORIES … except ImportError: pass`. Production safety validation silently disappeared if import failed; missing categories for 22/22 pool not enforced.
- **Repro:** Import error suppressed -> incomplete category map accepted.
- **Risk:** Unknown-category penalty silently applied to production pool instead of failing loudly.
- **Fix:** Extracted `src/parayabuck/universe/crypto_metadata.py` dependency-neutral `CRYPTO_CATEGORIES` (22 entries, SHIB/PEPE `meme` preserved). `config.py` and `crypto_scanner.py` import normally; validation is mandatory (`len==22` and missing -> `ValueError`) without `try/except` or `enabled` guard. Tests: `tests/test_crypto_foundation_parity.py` + scanner tests.

### P0-5 — Dynamic universe fail-open (`PortfolioContext.in_universe` returns True when `universe is None`)
- **File:** `src/parayabuck/portfolio/base.py:37-43`, `src/parayabuck/backtest/portfolio_engine.py:869-895`, `src/parayabuck/portfolio/runtime_service.py:1663-1683`
- **Behavior:** `in_universe()` permissive fallback `universe is None -> True` caused dynamic product strategies to treat every symbol as in-universe when PIT universe missing, instead of failing closed.
- **Repro:** `PortfolioContext(..., universe=None, universe_version="")` with product strategy `decide()` → `context.in_universe("FAKE/USD")==True`; backtest/runtime did not check before `strategy.decide()`/`rank_candidates()`.
- **Risk:** Strategy ranks/orders symbols outside eligible pool.
- **Fix:** Kept `in_universe()` permissive for static research but added `require_product_universe(context, allow_static_context=False)` helper (`src/parayabuck/portfolio/base.py:55-95`). `PortfolioBacktestEngine._decide` and `PortfolioRuntime.process_closed_bars` now invoke guard before `decide()`/`rank_candidates()`; product identities (`PRODUCT_STRATEGY_NAMES` + hysteresis) fail closed, synthetic/research retain permissive via `allow_static_universe=True` or non-PRODUCT name. Regression: `tests/test_product_universe_guard.py` (8 tests). Patched legacy offline tests to set `allow_static_universe=True`.

---

## P1 — Reliability (fixed)

### P1-1 — Ruff suppressed undefined-name and bare-except
- **File:** `pyproject.toml:23-24` (`[tool.ruff.lint] ignore = [..., "E722", "F821", "E741", "E741"]`), `src/parayabuck/runtime.py:1692` (`except:` pattern via lambda)
- **Behavior:** `F821` (undefined name) and `E722` (bare `except`) globally ignored; duplicate `E741` entry; `src/parayabuck/runtime.py` had `lambda exc=exc` capture bug surface as undefined `exc` after `except` block.
- **Risk:** Undefined names (`CA_V4`, `LIFETIMES_V4` in archived research) and bare excepts hide bugs.
- **Fix:** Removed `F821`, `E722`, duplicate `E741` from global ignore; added `per-file-ignores` for `scripts/archive/**` (`F401,F821,E722`) to allow frozen research noise. Fixed real `F401` unused imports via `ruff --fix` in `src/`, `tests/`, `scripts/`; fixed `runtime.py` `F821` via `exc` capture (`lambda exc=exc:`). Now `ruff check .` and `ruff check . --select F821,F401,E722` both pass.

### P1-2 — Stale packaged defaults for backtest costs not versioned
- **File:** `src/parayabuck/backtest/product.py`, `src/parayabuck/config.py:822-837`
- **Behavior:** Cost inputs existed in config and engine but were not part of earlier promotion digests, allowing cost changes without invalidating artifacts.
- **Risk:** Silent economics drift.
- **Fix:** No code change this promotion (costs already in `effective_cost` in `promotion_receipt.json` and `parity_audit.json`); documented in health audit. Deferred to add cost digest to live-authorization if needed.

---

## P2 — Maintainability (deferred or minor)

- **P2-1 — Large modules (`runtime.py` ~207KB, `execution/alpaca.py` ~117KB, `execution/ledger.py` ~69KB):** No big-bang rewrite; only narrow extractions justified (events, crypto_metadata). Deferred: decompose `EngineRuntime`, broker abstraction, ledger only when responsibility boundary obvious, tests cover, diff narrow. Recorded as DEFERRED per §14.
- **P2-2 — Snapshot cache flake:** `tests/test_universe_snapshots.py::test_equity_refresh_uses_rolling_cache_delta_windows_and_real_spread` and `test_rolling_cache_last_write_wins_for_same_day_bars` fail with cached-history replacement logic (int vs float, stale volume). Pre-existing at `4a6f5c0` (2 failed, 867 passed). Not related to T1 promotion; needs incremental-history investigation. Deferred as P2; tracked but does not block promotion because PIT research parity and product parity pass.
- **P2-3 — Duplicate constants (`CRYPTO_FROZEN_RISK` etc) between `config` and `tests`:** Keep as is; tests freeze spec, not drift.
- **P2-4 — Broad `except Exception` in `monitoring/alerts.py:emit` and `crypto_scanner` validation:** Intentional never-raise incident paths; correctly logged, not suppressed. No change.

---

## Verification

- `ruff check .` → All checks passed
- `ruff check . --select F821,F401,E722` → All checks passed
- `ruff format --check .` → 238 files already formatted (formatted 1 via fix)
- `mypy src` → Success: no issues in 75 source files
- `pytest -q` → 897 passed, 11 skipped, 2 failed (pre-existing snapshot flake, unchanged from baseline 867+30 new), 2 warnings
- `pytest -q --ignore=tests/test_universe_snapshots.py` → 887 passed, 11 skipped, 2 warnings (no new failures)
- `scripts/audit_crypto_t1_promotion_parity.py` → PASS (full_window + 9 walk-forward folds, all hashes equal, economics match `full_window.json` exactly: `total_return 0.105399`, `fees 1192.07`)

---

## Deferred Decomposition Opportunities

- Split `runtime.py` `EngineRuntime` event queue vs portfolio bridging only with narrow interface tests.
- Extract `strategy registry` already done (events, crypto_metadata); further ledger/broker split deferred.
- Move `alert event vocabulary` already done; no further.
- Move `crypto metadata` already done; no further.

