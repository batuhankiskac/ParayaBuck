# Equity M3 DATA GATE — 2026-09

## 1. Executive verdict

`M3_DATA_GATE_BLOCKED`

The gate infrastructure and hermetic tests are complete, but the required
operator-supplied IWM raw page bundle was not available. No economic phase was
run.

## 2. Snapshot and commit topology

- Repository: `https://github.com/batuhankiskac/ParayaBuck`
- Branch: `main`
- Snapshot date: `2026-09-01`
- Base: `0a8a726b4ce6d1dcf0d8591a860125e2fed875ba`
- Implementation A: `11ffe9ea5650bfbd3bcd1eda0787d50f255f6f9a`
- Pre-result manifest B: `e76bfa0e9f43a0ddac18e38f43e5dd226ea1516d`
- B is a direct child of A; A is a direct child of the pinned base.
- The result receipt is generated from B and is intended for the following
  receipt/docs commit.

## 3. Scope and non-goals

This work adds a fail-closed, research-only input, provenance, calendar,
coverage, role-separation, and numerical-feasibility gate. It does not run a
candidate backtest or economic analysis and does not alter strategy, risk,
execution, broker, registry, runtime, PAPER, or LIVE behavior.

## 4. Accepted OCP context

The repository’s accepted candidate-owned observation path remains the
`observation_strategy_factory` plus fresh identity check in
`scripts/research_v3_equity_roadmap.py:516-589` and
`src/parayabuck/backtest/observation_parity.py:60-86`. It is a conditional
prerequisite only; this mission did not run OCP or re-freeze V3.

## 5. Exact external inputs discovered

The following local external artifacts were supplied and verified by raw
SHA-256 and byte identity during the gate:

| Artifact | Path | Result |
|---|---|---|
| Canonical v5 bars | `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | present; hash and size match its manifest |
| v5 lifetimes | `data/historical/equity_entity_lifetimes_full_v5.csv` | present; supplied expected hash matches |
| v5 corporate actions | `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | present; hash and size match its manifest |
| IWM raw bundle | operator-supplied path | absent |

An ignored normalized-only IWM CSV was discoverable locally, but it was not
operator-supplied raw evidence and was deliberately not consumed.

## 6. IWM source-bundle status

`iwm_raw_source.status = BLOCKED` with category
`missing_iwm_bundle`. No provider request, download, cache substitution, or
IWM synthesis occurred. The accepted bundle contract is implemented in
`scripts/m3_data_gate_helpers.py:955-1129`: source receipt, provider identity,
SIP/split/1Day request, entitlement and retention attestation, ordered page
hashes, row counts, token chain, terminal page, and exact IWM symbol are all
required.

## 7. SPY/IWM/QQQ inventory

The gate uses NYSE regular sessions through the inclusive exchange-session end
`2026-07-30`.

| Symbol | Raw rows | Normalized rows | Unique sessions | Missing expected | Extra/off-calendar | Zero volume |
|---|---:|---:|---:|---:|---:|---:|
| SPY | 2,658 | 2,658 | 2,658 | 0 | 0 | 0 |
| IWM | 0 | 0 | 0 | 2,658 | 0 | 0 |
| QQQ | 2,658 | 2,658 | 2,658 | 0 | 0 | 0 |

The canonical file contains zero normalized IWM rows, matching the prior
static audit; this was recorded rather than silently inferred.

## 8. Raw provenance review

Canonical v5 metadata declares `feed=sip` and `adjustment=split`, and the gate
checks those declarations before parsing. The IWM source contract requires
`asset_class=us_equity`, `symbol=IWM`, `timeframe=1Day`, `feed=sip`,
`adjustment=split`, ascending order, and no fallback. Missing provenance is a
blocker, not an invitation to use another ETF or feed.

## 9. Normalization review

`m3-iwm-normalizer-v1` normalizes raw `1Day` only to canonical `1d`, derives
`session_date` in `America/New_York`, parses finite OHLCV as `Decimal`, and
rejects invalid OHLC ordering, non-positive prices, negative volume, duplicate
keys, mixed timeframe tokens, out-of-range rows, and non-IWM symbols. It does
not fill, interpolate, round, or synthesize values. Canonical row encoding is
versioned and LF-delimited in `scripts/m3_data_gate_helpers.py:398-430`.

## 10. Immutable merge review

The existing v5 CSV was never overwritten. The helper merge primitive verifies
duplicate/conflicting IWM keys and compares pre/post non-IWM semantic hashes in
`scripts/m3_data_gate_helpers.py:1228-1253`. Because no valid IWM bundle was
available, no merged CSV or merged manifest was created; the receipt records
`NOT_RUN_BLOCKED_BY_DATA_GATES`.

## 11. Calendar and coverage review

The gate uses `pandas-market-calendars==5.4.0`, calendar `NYSE`, rather than a
weekday or federal-holiday approximation (`scripts/m3_data_gate_helpers.py:427-447`).
There are 2,658 expected sessions and zero triple-common sessions because IWM
is absent. The full schedule contains 552 weekly decision sessions and the
recent schedule contains 239; all remain in initial warm-up because no common
reference window exists. No accepted weekly decision was silently skipped.

## 12. Evaluation-end and timezone review

The primary boundary is the exchange session date, not a UTC-midnight
timestamp. `2026-07-30` is inclusive; `2026-07-31` is outside evaluation.
`04:00Z` summer and `05:00Z` winter daily timestamps resolve to the intended
New York session date. The legacy UTC-midnight hazard is recorded at
`scripts/research_equity_m3_m4.py:60,264-266`.

## 13. Reference non-tradability review

The research overlay pins SPY, IWM, and QQQ as observation-only references,
reserves three of the configured 30 observation slots, and reports effective
capacity 27. Candidate, target, and executable sets exclude all three. The
overlay is pure and does not read runtime state (`scripts/m3_data_gate_helpers.py:844-927`).

## 14. Exact-consumption hash review

Every external input was copied to a content-addressed staging file, hashed
while copied, parsed only from that staged file, and re-stat'ed after copying
(`scripts/m3_data_gate_helpers.py:132-218`).

| Consumed artifact | Raw SHA-256 | CRLF-normalized SHA-256 | Bytes |
|---|---|---|---:|
| Canonical v5 bars | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` | `df42733b640e6dea17e1ed11115846933a86d2c525810dd3f473acc32e6d0db3` | 882,354,549 |
| v5 lifetimes | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` | `fe998bf81af1f705accf461f13a6e71fdcedb2d68a317c5aa2152833b4cad4f0` | 2,329,186 |
| v5 corporate actions | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` | `5776de7b91a1e0fd32b0070bfefae4f65d6bb6e4a068201f8d1cb7e6552e991d` | 14,713,370 |

Manifest SHA: `9ae0bd74262e52bba4d794618188e9044b72bdd0ced0743a61cd09ca32141053`.
Verifier SHA: `21fd8a5eb1e7b381211e73285b78d1d9281902bce0dbf3eb8ba13944280667c5`.
Normalizer SHA: `21ee8f9fc49351835b0d48441a6e9783de9025b32edf7e75250de84a6d308601`.
Receipt self-digest: `2a77b2d7094945b9b525a6efae979ef24b70a171cce38962c075876430bfe1c6`.

## 15. Numerical feasibility review

The thresholds were frozen in the pre-result manifest: 273 rows, 3 columns,
rank 3, singular-value ratio at least `1e-6`, condition number at most
`1e6`, finite inputs/coefficients/residuals, residual standard deviation above
`1e-12`, exactly 274 closes and 273 returns. The real-data matrix phase was
`NOT_RUN_BLOCKED_BY_DATA_GATES` with zero matrices because IWM and a frozen
candidate-symbol input were unavailable. Hermetic tests cover full-rank,
rank-deficient, ill-conditioned, nonfinite, and degenerate cases. The frozen
residual convention is demeaned fit followed by non-demeaned application with
no applied intercept; the latest 21 residual returns are omitted only by the
declared momentum skip convention. No economic metric was produced.

## 16. M3 candidate-owned observation parity prerequisites

Not run. The data gate is blocked before OCP. The accepted future path must use
the candidate-owned observation override, fresh observer and execution
instances, exact identity checks, no future bars, and no outside-pool
candidates. No candidate was promoted or registered.

## 17. Same-run V3-control readiness

Not ready. Same-run V3 control remains conditional on a valid IWM-inclusive
data vintage, the same evaluation sessions, scanner policy, terminal inputs,
and reference-reservation contract. The frozen V3 control and production
configuration were not changed.

## 18. Test and quality-gate results

- `python scripts/research_equity_m3_data_gate.py --help`: unavailable because
  this host has no `python` executable (`zsh:1: command not found: python`).
- `.venv/bin/python -m py_compile ...`: passed for both gate modules and the
  new test module.
- `.venv/bin/pytest -q tests/test_research_equity_m3_data_gate.py`: 58 passed.
- Required focused regression group: 104 passed.
- Full `.venv/bin/pytest`: 1,237 passed, 11 skipped, 6 environment failures;
  all six failures were sandbox-denied localhost binds in existing webhook/web
  tests, not M3 failures.
- Targeted `ruff check`: passed.
- Targeted `ruff format --check`: passed.
- `.venv/bin/mypy src`: passed, 147 source files.
- `.venv/bin/python -m build`: environment-blocked because isolated build
  dependency installation could not resolve PyPI DNS; `--no-isolation` was also
  blocked because `setuptools.build_meta` is unavailable in the environment.
- `python3 scripts/check_production_isolation.py --record /tmp/m3_data_gate_before.json`:
  recorded 10 protected paths.
- `python3 scripts/check_production_isolation.py --verify /tmp/m3_data_gate_before.json`:
  `Production isolation OK — no protected files mutated`.
- Final gate invocation from clean B exited `20` and wrote this blocked
  receipt. No network or broker call was made.

## 19. All stop conditions

The active stop conditions are missing IWM raw evidence, zero common reference
sessions, no accepted weekly window, no real-data numerical matrix audit, and
no candidate-owned OCP/same-run control. Canonical hash, source feed and
adjustment declarations, duplicate/quality checks on available references,
receipt economic-field scan, and production isolation passed. The blocked
conditions are sufficient to prevent PASS.

## 20. Remaining UNKNOWN facts

- IWM provider/API product identity, acquisition bytes, entitlement, and page
  token chain.
- IWM complete NYSE session coverage and normalized/panel digests.
- IWM-specific corporate-action inventory.
- Real candidate panel membership and per-decision numerical feasibility.
- Candidate-owned observer/execution identity parity and same-run V3 control.

## 21. Exact files changed

- `scripts/m3_data_gate_helpers.py`
- `scripts/research_equity_m3_data_gate.py`
- `scripts/research_equity_m3_m4.py` — receipt consumption only
- `tests/test_research_equity_m3_data_gate.py`
- `tests/fixtures/m3_data_gate/README.md`
- `data/research_manifests/equity_m3_data_gate_2026_09.json`
- `data/research_receipts/equity_m3_data_gate_2026_09.json`
- `docs/equity_m3_data_gate_2026_09.md`

## 22. Explicit do-not list

- No fabricated IWM.
- No fetched IWM.
- No ETF/index substitution, forward-fill, interpolation, or unpinned cache.
- No canonical v5 overwrite or large-data commit.
- No economic P&L/return analysis, optimization, ranking, or promotion.
- No production strategy, risk, execution, broker, registry, runtime,
  configuration, PAPER, or LIVE change.
- No V3 re-freeze and no claim that unavailable external data exists.

## 23. Final verdict

`M3_DATA_GATE_BLOCKED`
