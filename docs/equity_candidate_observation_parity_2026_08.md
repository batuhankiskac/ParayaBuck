# Candidate-Owned Observation-Basis Parity Receipt (OCP-1)

Date: 2026-08-31
Scope: research-only observation-basis correctness and provenance.  No
candidate economics, promotion, M3, M8, PEAD, composite, or new alpha search
was run.

## 1. Gate result

`OCP_GATE_PASS — READY_FOR_M3_DATA_GATE`

The configured V3 observation basis remained unchanged, an explicit fresh V3
ranker reproduced the frozen R1 universe component, and the synthetic changed
ranker changed only the streamed observation basis while preserving scanner
eligibility and policy metadata.

## 2. Repository receipt

| Field | Value |
| --- | --- |
| Required starting HEAD | `9cfe1bd053aa545a18d81384558edb2f2c78539e` |
| Implementation / Commit A | `352b6a0bc1568ce840b9c8cf7ccf7eeb9d2975f6` — `research: add candidate observation parity harness` |
| Receipt / Commit B | The commit containing this file; its authoritative SHA is the final `git rev-parse HEAD` handoff value (a commit cannot embed its own object ID without a self-reference). |
| Final tracked worktree | Clean: `git status --short` reported no paths after Commit B; final branch was `main`. |
| Remote publication | This historical receipt was written before publication. The connected remote later resolved `origin/main` to Commit B `677274a29b882d8b60d63083370d89df0ecd2776`, then to the corrective receipt commit `37ed1882f833dd6313f00dd33aa165038f6811b3`. The newer provenance-hardening commits remain local until explicitly published. |

The starting pin and clean tracked worktree were verified before editing.

## 3. Problem confirmed

Before OCP-1, `build_trailing_point_in_time_universe()` accepted an explicit
`observation_strategy`, but `build_product_universe()` always constructed the
configured product ranker and `research_v3_equity_roadmap.prepare_window()` had
no candidate-owned hook. A changed-ranker candidate could therefore be run as
`X|C` only, not as the required deployment-parity `X|X` basis.

## 4. Implementation

- `src/parayabuck/backtest/product.py`: added the backward-compatible
  `observation_strategy_override: PortfolioStrategy | None = None` keyword.
  `None` is the unchanged configured-product path; an override is validated for
  type, market, and selected timeframe and is passed to the existing PIT
  observation builder. Scanner, pins, capacity, risk, execution, and terminal
  behavior remain untouched.
- `src/parayabuck/backtest/observation_parity.py`: added deterministic
  strategy identity/fingerprint helpers and a pure comparator that aligns by
  `effective_at`, fails closed on boundary drift, audits scanner/pool/metadata/
  pins/capacity contracts, and reports observation-set changes and Jaccard
  statistics.
- `scripts/research_v3_equity_roadmap.py`: added fresh observer/execution
  factories, candidate-owned basis labeling, identity/fingerprint assertions,
  and receipt fields while preserving all existing callers by default.
- `scripts/research_candidate_observation_parity.py`: added the clean,
  research-only E1/E2 runner. It refuses dirty worktrees, records source/data
  provenance, writes only ignored raw JSON, and never touches runtime/config/
  registry artifacts.
- `data/research_manifests/equity_candidate_observation_parity_2026_08.json`:
  predeclared the mission, frozen R1 hashes, parity contract, hard stops, and
  no-candidate-economics policy before the canonical run.
- `tests/test_candidate_observation_parity.py`: added focused coverage for
  default/explicit parity, changed-ranker isolation, PIT/future-bar safety,
  fail-closed override validation, boundary/contract checks, deterministic
  identities, and fresh factory instances.
- `src/parayabuck/backtest/__init__.py`: exported the research helpers.

Public API addition:

```python
build_product_universe(
    config,
    bars,
    *,
    market,
    end=None,
    timeframe=None,
    observation_strategy_override=None,
)
```

## 5. Default-path R1 no-drift evidence

The existing clean control runner was run from Commit A `352b6a0` with empty
tracked paths before and after execution. All frozen values remained exact.

| Metric / count | Observed |
| --- | ---: |
| Total return | `76.4538%` |
| CAGR | `5.5197%` |
| Sharpe | `0.571342` |
| Max drawdown | `20.3809%` |
| Turnover | `149.724817` |
| Rebalance decisions | `495` |
| Fills | `1,890` |
| Terminal events | `12` |
| Incomplete strategy batches | `0` |

| Component | SHA-256 |
| --- | --- |
| Universe | `d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011` |
| Target sequence | `b3b39c78d7731776d9dcc90a9eff2352b2e18007848bb14d23f68027d77f15c9` |
| Fill sequence | `dff4000e6c71074bcd7ffde96c98a209eb9b54c74233ddb264b7fb151d1b0b63` |
| Equity curve | `9d08f9c3b3a6959189fb0a482c7de6d294fd4a72e1cec3ae7fe7f79d6f2e02fb` |
| Terminal events | `8fd87474a7aaf9a7c5977dcb6c5d0cd02b09af0254d9fdefe81ba9ca0c766c95` |

The control receipt generated at this implementation commit had source
`352b6a0bc1568ce840b9c8cf7ccf7eeb9d2975f6`, config digest
`1833407d7c42fcef45b1f2aef200a3fd14e344d6cdcc9b03c578f8e494152a3e`, and the
canonical bars/lifetimes/corporate-actions SHAs
`bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`,
`ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`, and
`a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`.

## 6. Candidate-owned canary evidence

### E1 — canonical explicit V3 self-parity

- `2,656` control and candidate snapshots aligned exactly.
- Canonical universe component: control = explicit V3 = expected
  `d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011`.
- Every explicit snapshot identified `equity_residual_momentum_v3` / `3.0.0`.
- Scanner/pool/metadata/pin/capacity mismatch counts were all `0`; observation
  sequence and membership changes were both `0`; Jaccard mean/min/p50/p95 were
  `1.0 / 1.0 / 1.0 / 1.0`.
- The fresh observer and fresh execution identity matched on name, version,
  market, timeframe, parameters, and digest (`d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa`).

### E2 — synthetic changed-ranker canary

| Check | Result |
| --- | ---: |
| Control/candidate snapshots (aligned) | `359 / 359 / 359` |
| Market, methodology, eligible symbols, scanner version/digest | all mismatch counts `0` |
| Candidate count, score breakdown, eliminated map, sector coverage | all mismatch counts `0` |
| Configured pins / maximum stream capacity | mismatch counts `0 / 0` |
| Observation sequence changes | `86` |
| Observation membership changes | `86` |
| Jaccard mean / min / p50 / p95 | `0.8083565459610027 / 0.2 / 1.0 / 1.0` |
| Candidate latest observation set | `SPY, AAA, BBB` |
| Direct `build_observation_set()` result | `SPY, AAA, BBB` (exact match) |
| Ranker output outside eligible observation pool | `0` in the built basis |
| Future-bar leak at a pre-session probe | `false` |
| Candidate hash / repeated candidate hash | `8175f591f95e1aa943f314f59242e9642afbd9fa21f7f3d9b91150eb886f7af8` / exact same |

The candidate ranker was deliberately unregistered and only changed
observation ordering/membership. No synthetic return, Sharpe, or other
economic statistic was computed or interpreted.

## 7. Tests and quality gates

- `git diff --check`: PASS.
- `.venv/bin/python -m py_compile scripts/research_candidate_observation_parity.py`: PASS.
- `.venv/bin/ruff check .`: PASS.
- `.venv/bin/mypy src/parayabuck/backtest/observation_parity.py`: PASS.
- Focused OCP/product/universe/V3/terminal suite: `77 passed`.
- Full suite without exclusions: `1158 passed, 11 skipped, 6 failed`; all six
  failures were the documented sandbox localhost-bind restriction:
  `test_alerts_webhook_delivery`,
  `test_alerts_throttle_coalesces_repeat_failures`,
  `test_alerts_webhook_event_filtering_and_disable`,
  `test_probe_web_bind_succeeds_on_free_port`,
  `test_probe_web_bind_fails_fast_on_occupied_port`, and
  `test_probe_web_bind_resolves_localhost`.
- The exact narrow deselection of those six tests produced
  `1158 passed, 11 skipped, 6 deselected`.
- `.venv/bin/ruff format --check .`: the changed files are formatted; the repo
  baseline still reports nine unrelated pre-existing files
  (`scripts/check_production_isolation.py`, `scripts/repair_local_state.py`,
  `src/parayabuck/data/cache.py`, `src/parayabuck/research/runner.py`,
  `src/parayabuck/web/server_legacy.py`, `tests/conftest.py`,
  `tests/test_combined_account_risk.py`, `tests/test_research_runner.py`, and
  `tests/test_scanner_cache_determinism.py`).
- `.venv/bin/mypy src`: the existing baseline has two unused-ignore errors at
  `src/parayabuck/monitoring/snapshot.py:70,72`; no OCP module error remains.
- `.venv/bin/python -m build` is unavailable in this environment because the
  venv lacks the `build` module and the ignored repository `build/` directory
  shadows that import. The equivalent project backend check
  `uv build --offline` passed and produced the expected sdist/wheel.

The OCP runner was executed twice from clean Commit A outputs
`parity_receipt_r1.json` and `parity_receipt_r2.json`; they were byte-identical:

`c59857597cbd49c57aa5089c616ba6ef3f3abc5a708d264aad55e24ae367d0cd`

The internal canonical receipt digest in both files was
`1e0855e8ef8eacc09a8673f1baf94b3786a007d8b158e6e76b5811d8d4da903f`.

Exact reproducibility commands:

```text
.venv/bin/python scripts/research_v3_current_parity_control.py --output data/research/equity_candidate_observation_parity_2026_08/control_r1.json
.venv/bin/python scripts/research_candidate_observation_parity.py --output data/research/equity_candidate_observation_parity_2026_08/parity_receipt_r1.json
.venv/bin/python scripts/research_candidate_observation_parity.py --output data/research/equity_candidate_observation_parity_2026_08/parity_receipt_r2.json
```

The full-suite clean-environment rerun used this exact narrow exclusion for
the six socket-bind tests listed above:

```text
.venv/bin/python -m pytest -q -k 'not test_alerts_webhook_delivery and not test_alerts_throttle_coalesces_repeat_failures and not test_alerts_webhook_event_filtering_and_disable and not test_probe_web_bind_succeeds_on_free_port and not test_probe_web_bind_fails_fast_on_occupied_port and not test_probe_web_bind_resolves_localhost'
```

## 8. Invariant audit

1. V3 identity, parameters, registry, and PAPER selection unchanged — PASS.
2. Shared `PortfolioStrategy` semantics and scoring hooks retained — PASS.
3. `rank_candidates()` remains the strategy-owned shared scoring path — PASS.
4. Completed-bar / next-bar execution and strictly-prior observation history — PASS.
5. Existing risk overlay remains the sole exposure/order-risk authority — PASS.
6. Long-only bounded weights and no derivative/short/leverage path added — PASS.
7. Scanner eligibility remains separate from candidate alpha — PASS (`0` pool/metadata mismatches).
8. Runtime does not read research artifacts; no auto-promotion — PASS.
9. Candidate ranker remains outside the product registry — PASS.
10. Candidate basis changes only observation membership/identity/hash — PASS (all disallowed mismatch counts `0`).
11. No economic inference from the synthetic canary — PASS (`candidate_economics_run=false`).

## 9. Evidence limitations

- This mission intentionally stops before candidate economics; no candidate
  return forecast or promotion recommendation is supplied.
- The six localhost-bind tests require socket capability unavailable in the
  sandbox; the exact six-test deselection is recorded above.
- Full-repo format and strict mypy remain limited by the unrelated baseline
  findings listed above; changed-file Ruff/compile/mypy checks pass.
- The original raw receipts remain ignored by design. A subsequent corrective
  receipt is tracked at
  `data/research_receipts/equity_candidate_observation_parity_corrective_2026_08.json`.

## 10. Decision

`OCP_GATE_PASS — READY_FOR_M3_DATA_GATE`

## 11. Commits

- `352b6a0bc1568ce840b9c8cf7ccf7eeb9d2975f6` —
  `research: add candidate observation parity harness` (implementation and
  predeclaration).
- Commit B — this tracked receipt document, to be recorded immediately after
  verification; the final handoff reports its exact `git rev-parse HEAD`.

At the time of the original Commit B handoff no remote push was observed; the
connected remote was later updated to Commit B. Corrective commits are kept
local until explicitly published.
