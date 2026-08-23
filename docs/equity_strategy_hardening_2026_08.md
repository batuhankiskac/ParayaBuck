# Equity Strategy Hardening — 2026-08

Mission: deep correctness/parity/quant-research pass on the EQUITY side of
ParayaBuck. Correctness first; research second; promotion last.

## 1. Executive summary

Three correctness bugs were confirmed at HEAD and fixed:

1. **Split-adjustment parity** — PAPER warmup seeded the strategy's 1d signal
   history on RAW prices while research data is SPLIT-adjusted and the
   observation-set ranker fetched SPLITS: three different bases for the same
   alpha. Live continuation had no corporate-action handling at all, so any
   split welded a fake −50%/−75% "return" into the trailing 273-bar window
   (~13 months of corrupted scores per event). Follow-up hardening replaced
   the cumulative-factor bridge with PROVIDER-AUTHORITATIVE signal history,
   verified by one batched raw+splits reconciliation before any finalized
   equity decision; unverifiable basis fails closed for new equity entries.
2. **top_n / max_positions conflict** — the risk overlay enforced its
   position count by sorting TARGET WEIGHTS (ties → symbol order). After
   inverse-vol sizing and the 10% cap pinned several names at exactly 10%,
   symbol ordering — not strategy alpha — decided which positions survived.
3. **ivol quantile small-n bias** — `int(q*n)` indexing kept q·n+1 names of n;
   for n≤3 it rejected NOBODY. At the production pool sizes it over-retained
   ~1 borderline name every decision.

Consequences quantified on the canonical window (2016-01→2026-07, split-
adjusted CSV + explicit dividends):

| metric | pre-fix HEAD | corrected baseline |
|---|---|---|
| return | +129.3% | **+125.3%** |
| Sharpe | 0.679 | **0.658** |
| maxDD | 22.2% | **27.4%** |
| closed trades | 762 | **643** |

The old numbers were flattered by the overlay silently keeping low-vol names
over alpha leaders. All prior top_n research is confounded and was re-run.

Research verdicts (full tables below): standardized residual momentum
(A-family) improves risk profile but not fold distribution → RESEARCH PASS;
longer beta windows hurt → REJECT; total-return signaling is measurably
WORSE (−9.7pp, folds −2.6pp median) → KEEP price-signal; top_n 6/8/9
differences are noise-level →
KEEP top_n=8; production drawdown policy costs between −5pp (sensible reset)
and −106pp (never reset) depending entirely on operator reset discipline →
report, no limit change. Survivorship bias remains UNSOLVED (data-provider
limitation), documented with numbers.

## 2. Baseline identity

| item | value |
|---|---|
| HEAD audited | `036abbb1de9b6a732d96bade4b6f91288e634533` |
| Python | 3.14.7 (`.venv`) |
| equity strategy | `equity_residual_momentum_v2` (weekly) |
| params | formation 252 / skip 21 / beta 273 / rvol 63 / top_n 8 / cap 0.10 / ivol q 0.70 / alloc 0.60 |
| risk | pos 10%, max_pos 6, gross 60%, cash buffer 40%, daily loss 0.5%, dd halt 5% |
| dataset | `equity_1200_2016_2026.csv` sha256 `3baec12e…e903a`, 1,738,468 bars, 1,194 symbols |
| dividends | `equity_1200_dividends.csv` sha256 `7d79025d…73e1e0`, 16,878 actions, 619 symbols |
| quality gates at HEAD | pytest 606 passed · ruff clean · format clean · mypy clean |

Canonical reproduction used the production harness:
`parayabuck backtest --market equity --data … --corporate-actions …`
(artifact `data/research/equity_hardening_2026_08/phase0_baseline_head/`).

## 3. Split-adjustment parity (FIXED)

### Findings at HEAD (path → effective basis)

| path | basis | consumer |
|---|---|---|
| CLI research download (`cli.py`) | splits | offline CSVs |
| runtime warmup initial/retry/hot-swap | **raw** | strategy 1d history + market state |
| live 1d bars (aggregated from 1m stream) | raw | strategy 1d continuation |
| gap repair `_repair_gap` | raw | execution-side market state |
| scanner eligibility metrics | raw | universe selection |
| observation-set ranking | splits | `rank_candidates` |

No corporate-action handling existed anywhere in the live path.

### Contract implemented (v3: FULL-WINDOW provider authority)

* **SIGNAL = provider split-adjusted daily history over the ENTIRE required
  strategy window.** Before any finalized equity 1d decision passes its
  gate, ONE batched splits request fetches `max(minimum_history_bars)+1`
  bars (+10-day margin) of calendar coverage for every active equity
  symbol. A short tail is NEVER used as authority for a long history:
  `merge_provider_authority` overrides overlap with the provider, retains
  strictly-newer provisional stream bars, and RETAINS out-of-scope older
  history — a successful verification can never shorten a healthy 274+ bar
  history (`test_short_provider_tail_never_truncates_long_history`: 300
  stored bars vs a 10-bar provider tail ⇒ 300 bars out, old prefix retained
  verbatim).
* **Decision-bar invariant.** The gate requires, for EVERY required symbol,
  `signal_verified_through_by_symbol[s] >= decision_timestamp`, where the
  decision timestamp is the newest finalized bar in the processed batch. If
  the provider has not published that bar yet, the decision runs latched as
  `signal_adjustment_unverified` — risk-reducing exits flow, NO new entries
  (`test_decision_requires_verified_through_decision_timestamp`,
  integration through `_process_portfolio_timestamp`;
  `test_provider_lag_on_split_day_blocks_new_entries`).
* **Fail closed on partial/empty responses.** Adequacy is enforced per
  symbol BEFORE anything is adopted; one missing/inadequate symbol fails
  the whole check and leaves every history untouched — no partial adoption,
  no cross-symbol watermark (`test_empty_provider_response_fails_closed`,
  `test_partial_symbol_response_fails_closed`,
  `test_per_symbol_verified_through_is_required`).
* **Adequacy vs the request boundary.** Coverage is measured against the
  STRATEGY-required oldest BAR (the oldest bar of each runtime's trailing
  window), never against the inclusive network `start`: a daily API returns
  its first bar at/after start and later still across weekends/holidays.
  The request itself goes out with the safety margin BEFORE that required
  bar. Regressions drive a window-honoring fake through intraday starts,
  weekend starts whose first returned bar is Monday, and genuinely
  insufficient archives (fail closed, one failure alert, no adoption).
* **Shutdown ordering.** `stop()` stores and JOINS the post-warmup
  `pb-signal-adjustment` thread before closing the historical data client;
  the regression proves the worker has exited before `_historical.close()`
  runs (`test_shutdown_joins_signal_adjustment_before_client_close`).
* **Alert semantics.** "Recovery" fires only for failed→healthy; the
  initial unknown→healthy transition is silent (RuntimeHealth already
  reports it), keeping exactly one recovery per failure episode.
* **Startup race eliminated.** `pb-signal-adjustment` no longer starts with
  the thread fan-out (it previously raced `_warmup()`); it launches only
  AFTER warmup completes, and every monitor pass refuses to run while
  `_warming_up`. The regression proves the hazard itself — monotonic
  `seed()` permanently drops warmup bars behind a pre-seeded short tail —
  and both guards
  (`test_signal_adjustment_does_not_race_startup_warmup`).
* **Exactly-once alert transitions.** An explicit healthy/failed state
  machine emits ONE failure alert per episode and ONE recovery alert per
  recovery; repeated failed or repeated healthy checks stay silent.
* **EXECUTION = raw**, unchanged everywhere.

Tests: `tests/test_signal_adjustment_parity.py` carries 24 regressions:
the deterministic multi-split cumulative-basis case (4:1 → >14d → 2:1 ⇒
factor 8, not 2), forward+reverse splits, idempotent re-reconciliation, no
double-scaling of already-authoritative bars, restart and hot-swap between
splits, short-tail truncation, startup race, split-day gating, worker
failure blocking entries, recovery, provider lag, empty/partial responses
and per-symbol watermarks.

## 4. top_n / max_positions semantics (FIXED)

Mechanism reproduced: `PortfolioRiskOverlay` truncated `desired` by
`(−weight, symbol)` to `max_positions=6` while the strategy had selected the
alpha-top-8. Deterministic failure mode: ≥4 names tied at the 10% cap made
alphabetical order decide holdings.

Fix: `TargetWeight.rank` (1-based alpha rank, persisted round-trip) travels
from `decide()` through latch/intermediate-reduction rebuilds; the overlay
retains best-ranked names when ranks exist, else legacy weight order
(crypto unaffected). Count enforcement itself unchanged.

Deterministic tests: `tests/test_alpha_rank_preservation.py` (rank retention,
cap-tie scenario with reversed alphabet, unranked fallback, partial ranking,
serialization round-trip, negative-rank rejection).

## 5. IVol quantile (FIXED)

Old: threshold `sorted[int(q*n)]`, keep `<=` ⇒ keeps int(q·n)+1 of n
(n=10,q=.70 → 8 names = 80%; n=3 → all). New: `reject_quantile_threshold`
(`portfolio/momentum.py`) uses nearest-rank on index space [0,n−1]:
keeps floor(q·(n−1))+1 distinct-valued names, ties KEPT (deterministic,
documented). Edge tests n=1..10, identical values, boundaries, argument
validation. Two fixtures that encoded the old behavior were updated
consciously (`test_explanation_metadata`, `test_observation_set` — the latter
was literally demonstrating the n=3 keeps-everything bug).

## 6. Corrected baseline (Phase 4)

Artifact: `data/research/equity_hardening_2026_08/baseline_corrected/`
(identity.json + summary.json + full report artifacts). Headline (see §1
table): +125.3% / CAGR 8.2% / vol 12.8% / Sharpe 0.658 / Sortino 0.86 /
maxDD 27.4% / Calmar 0.367; 495 rebalances, 643 trades; avg exposure 50.7%;
max concentration 13.8%; worst year −7.3% (2021); benchmark SPY comparison
and per-symbol contributions in the artifact. Dividends are booked as
explicit cash flows (dividend_total in artifact); SIGNAL returns are
split-adjusted PRICE returns; TOTAL ACCOUNT return additionally includes
those dividends plus uninvested-cash drag (no interest modeled).

Pre-fix vs corrected delta: return −4.0pp, maxDD +5.2pp — the phase-2 fix
exposes the true risk profile the old overlay masked.

## 7. Production-policy simulation (Phase 5)

New harness `scripts/research_production_policy.py`; engine gained two
additive hooks (`enforce_drawdown_halt` flag default False = today's shadow
semantics; `_risk_epoch_policy` no-op; `reset_peak_equity()` mirrors the
operator's verified epoch reset). Predeclared policies P0–P3 in
`predeclared.json`.

Results (identical inputs, only halt enforcement differs):

Latched-cycle metrics now include the still-open final episode (previously
P0's true cost was invisible). Cycle counts are of 2,440 daily cycles:

| curve | return | Δreturn | Sharpe | maxDD | avg expo | halt episodes | latched cycles | open episode |
|---|---|---|---|---|---|---|---|---|
| alpha (shadow, reference) | +125.3% | — | 0.658 | 27.4% | 0.50 | 1 decision-day breach | — | — |
| P0 never reset | +18.9% | **−106.4pp** | 0.420 | 9.8% | 0.10 | 1 (2018-06-27, permanent) | 2033 | yes, 2033 cyc |
| P1 reset after 10 up-or-flat sessions | +120.1% | −5.2pp | **0.721** | **21.2%** | 0.43 | 9 | 1209 | yes, tail 15 cyc |
| P2 reset at rebalance once dd<5% again | +24.9% | −100.4pp | 0.388 | 12.9% | 0.16 | 6 | 1663 | yes, 1607 cyc (deadlock) |
| P3 fixed 20-trading-day delay | +114.0% | −11.3pp | 0.644 | 25.9% | 0.48 | 26 | 515 | yes, tail 15 cyc |

Findings:

* The single most important operational variable is RESET DISCIPLINE, not
  the 5% threshold itself: P0 vs P1 differ by >100pp on identical alpha.
* P2 as declared DEADLOCKS: while entries are latched the portfolio cannot
  recover enough for "drawdown < 5%" to fire again (monotonic peak, flat-ish
  book). Shallow breaches reset; the COVID-depth breach (2020-03-09) never
  did. Any real reset policy must be time/stabilization-based or external-cash
  based, not recovery-condition based.
* P1 dominates on every risk metric (Sharpe 0.72, maxDD 21%) because halts
  de-risk precisely during drawdowns; its cost vs alpha is −5.2pp over 9.5y.
* The alpha curve's own shadow-event count badly underestimates operational
  halt frequency: one weekly-decision breach vs 9–26 cycle-level episodes.
* Recommendation: 5% remains unchanged; reset-policy sensitivity was
  demonstrated, but the threshold itself was NOT independently calibrated by
  this experiment (it was held fixed across all scenarios). A P1-style
  stabilization-reset runbook is the highest-value operational change;
  operator action stays manual and verified as today. Nothing changed
  automatically.

## 8. Standardized residual momentum (Candidate A)

Score = compounded residual return ÷ stdev(residuals, same formation+skip
window) — Blitz/Huij/Martens standardization; ablations predeclared.

| variant | return | Sharpe | maxDD | median fold | worst fold | folds won vs control | top-1 PnL share |
|---|---|---|---|---|---|---|---|
| control | +125.3% | 0.658 | 27.4% | +3.93% | −11.1% | — | 6.4% |
| A1 std + ivol + inv-vol | +124.0% | 0.716 | 23.0% | +3.23% | −8.4% | 9/20 | 6.5% |
| A2 std, no ivol filter | +180.4% | 0.723 | 31.8% | +3.44% | −12.7% | 10/20 | 7.7% |
| A3 std + ivol + equal size | +135.7% | 0.710 | 23.4% | +3.53% | −10.1% | 9/20 | 7.8% |

Cost stress (full window, 2x/3x) and jackknife artifacts per variant under
`data/research/equity_hardening_2026_08/{a1,a2,a3}.json`: no candidate shows
cost fragility (fee share of gross PnL scales ~linearly; ranking stable) and
dropping any of NVDA/AVGO/ASX still leaves >100% total return — no one-symbol
dependence (top-1 ≈ 6–8% of positive PnL).

Verdict: **RESEARCH PASS** (A1/A3 flavor). Risk-adjusted improvement is real
but fold wins sit at 45–50% — improvement is NOT uniformly distributed, which
fails the mission's own promotion bar. A2's extra return comes with the worst
tail. Nothing promoted; production config untouched.

### Cost stress across ALL variants (full window)

| variant | 1x return | fees % of gross PnL | 3x return | 3x fees % |
|---|---|---|---|---|
| control | +125.3% | 4.9% | +108.4% | 13.6% |
| a1 | +124.0% | 4.4% | +107.9% | 12.0% |
| a2 | +180.4% | 2.8% | +163.1% | 7.7% |
| a3 | +135.7% | 4.2% | +118.1% | 11.5% |
| b504 | +123.4% | 4.0% | +108.4% | 11.0% |
| b756 | +80.2% | 4.8% | +69.5% | 13.5% |
| c_tr | +115.6% | 4.7% | +99.7% | 12.9% |
| topn6 | +123.5% | 5.0% | +106.5% | 13.8% |
| topn9 | +124.6% | 4.8% | +108.6% | 13.2% |

No variant is transaction-cost fragile: ranking is unchanged at 3x costs and
fees stay ≤14% of gross PnL everywhere.

## 9. Beta stability (Candidate B)

| beta window | return | Sharpe | maxDD | median fold |
|---|---|---|---|---|
| 273 (control) | +125.3% | 0.658 | 27.4% | +3.93% |
| 504 | +123.4% | 0.648 | 26.1% | +2.82% |
| 756 | +80.2% | 0.511 | 28.6% | +0.62% |

Longer estimation windows do NOT stabilize performance here — they dilute the
formation-window information the beta feeds. Verdict: **REJECT** (504 =
neutral-to-worse; 756 clearly worse). Beta shrinkage toward 1.0 not pursued
(no supporting evidence from the simpler study).

## 10. Total-return signal (Candidate C)

Offline TR series built per predeclared formula
TR(t)=TR(t−1)·[P(t)/P(t−1)]·[1+D(t)/P(t−1)] (date-matched ex-dates; verified
KO ×1.38, JNJ ×1.33, XOM ×1.56 over 10y). Signal-only swap; executable bars
and dividend cash accounting unchanged (no double counting by construction).

Result with the corrected date matching (stock AND SPY benchmark both on
the TR basis inside the signal history; executable bars and dividend cash
accounting unchanged):

| variant | return | Sharpe | maxDD | median fold |
|---|---|---|---|---|
| price-signal control | +125.3% | 0.658 | 27.4% | +3.93% |
| total-return signal | +115.6% | 0.639 | 28.6% | +1.17% |

Total-return signaling is WORSE on every reported dimension (−9.7pp return,
median fold −2.8pp). Mechanism: reinvesting dividends mechanically lifts
formation-window scores of high-yield names, dragging selection toward slower
payers away from pure residual price momentum. Dividends belong in ACCOUNT
P&L, not alpha ranking. (An earlier run showing "identical to control" was a
plumbing bug — timestamp-granularity matching missed every action; caught by
verification, fixed, re-run.) Verdict: **KEEP** split-adjusted price signals.

## 11. top_n 6/8/9 re-run (post-fix)

| top_n | return | Sharpe | maxDD | median fold |
|---|---|---|---|---|
| 6 | +123.5% | 0.646 | 28.1% | +4.04% |
| 8 (=control) | +125.3% | 0.658 | 27.4% | +3.93% |
| 9 | +124.7% | 0.677 | 25.7% | +3.90% |

The documented prior claim ("top_n=9 materially better") does NOT survive the
semantics fix: differences are ≤0.8pp return and within fold noise. Verdict:
**KEEP top_n=8**; prior top_n evidence invalid (confounded by weight-order
eviction).

## 12. Survivorship bias (UNSOLVED — data provider limitation)

Mechanism (verified from data + code): dataset membership was frozen to the
download-day survivor list. 1,193 of 1,194 symbols trade through the final
bar (2026-07-30); only ATTO ends early (2023-07-21). Any name delisted
between 2016 and the re-download has zero rows in every period. PIT scanner/
universe logic (`backtest/universe.py` forward pass) is point-in-time correct
WITHIN the supplied bars — it cannot resurrect absent securities. Late
starters (801 symbols begin after 2016-03) are handled correctly by the
completeness gate and are not themselves a bias.

Quantification possible locally: earliest approx-PIT membership ≈ 2019-05-28
(and even then incomplete: ~100+ of ~1194 names plausibly delisted 2019→2026
are absent). Recent-window sensitivity: re-running control from 2022-01 cuts
the frozen-membership distortion window roughly in half (artifact:
`control.json` folds 2022+ show positive medians; treat absolute levels,
especially 2016-2021, as optimistic).

Classification: **UNSOLVED** without a delisting-inclusive historical
provider. Backtest documentation must carry this limitation (it does, via
`survivorship_bias_limitation` in every product report).

## 13. What should NOT change

* LIVE arming, circuit breakers, reconciliation, ownership protections,
  two-phase rebalance, entry latches — untouched, tests green.
* Frozen v1 strategy specs (freeze tests pass unchanged).
* Production config parameters — unchanged throughout this mission.
* Execution-domain raw pricing (stream/market state/gap repair).
* Scanner raw-price eligibility basis (deliberate: as-traded tradability),
  now documented against the splits-basis ranking domain.

## 14. Open risks before LIVE

1. Survivorship bias inflates absolute historical levels (§12) — size
   expectations accordingly.
2. Drawdown-halt operations: pick and rehearse a reset runbook (§7); P2-style
   rules deadlock.
3. Daily-loss latch between weekly decisions is evaluated only at decision
   points in both curves (documented approximation).
4. Cash yield: no interest on idle cash modeled in either curve.
5. Signal-basis verification adds two batched REST calls per decision day;
   monitor `signal_adjustment` in RuntimeHealth and the
   `signal_adjustment_failure`/`_recovery` alerts. Sustained provider
   outages now fail closed for NEW equity entries by design — exits are
   unaffected; verify this behavior in PAPER during a deliberate outage
   drill before considering LIVE.

## 15. Reproduction commands

Reproducibility contract (committed OUTSIDE the gitignored data/research/
tree): `data/research_manifests/equity_hardening_2026_08.json` carries the
audited HEAD, dataset SHA256s, the exact predeclared variants (base class +
parameter overrides), the production config digest, Python version, the exact
commands, and sha256 of every generated result artifact. The research runner
REFUSES to execute unless its built-in VARIANTS match that manifest exactly,
and every result artifact embeds full identity (commit, config digest,
dataset hashes, python). Refresh result hashes with
`--refresh-manifest-hashes` after intentional reruns.

```bash
# gates
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check . \
  && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy src

# canonical backtest (corrected baseline)
.venv/bin/python -m parayabuck.cli backtest --market equity \
  --data data/historical/equity_1200_2016_2026.csv \
  --corporate-actions data/historical/equity_1200_dividends.csv \
  --output-dir data/research/equity_hardening_2026_08/<dir>

# research candidates / production policy
.venv/bin/python scripts/research_equity_hardening.py --variants control,a1,a2,a3,b504,b756,c_tr,topn6,topn9
.venv/bin/python scripts/research_production_policy.py
```

## 16. Final decision table

| Item | Classification | Evidence | Recommendation |
|---|---|---|---|
| split parity | FIXED — multiple corporate actions, short-tail verification, startup races and provider failures included | §3 v3: full-window batched provider authority (300-bar proof), per-symbol verified_through >= decision timestamp through the real pipeline, fail-closed on lag/empty/partial, post-warmup-only verifier, exactly-once alerts | keep |
| top_n/max_positions conflict | FIXED | §4, deterministic tests, baseline delta | keep |
| ivol quantile | FIXED | §5, edge-case tests | keep |
| standardized score (A1/A3) | RESEARCH PASS | §8: Sharpe +0.06, maxDD −4pp, but 9/20 fold wins | separate promotion decision |
| A2 (std, no ivol) | RESEARCH PASS (flagged) | §8: best return, worst tail | do not promote |
| longer beta 504/756 | REJECT | §9: neutral/worse, 756 −45pp | keep 273 |
| total-return signal | KEEP (price signal) | §10: TR worse on every metric (−9.7pp) | keep price basis |
| top_n 6/8/9 | KEEP top_n=8 | §11: prior topn9 claim does not survive | keep 8 |
| 5% production halt | REPORTED (policy finding) | §7: cost ranges −11pp..−106pp incl. OPEN episodes; threshold not recalibrated | adopt P1-style runbook manually; calibrate threshold separately |
| survivorship bias | UNSOLVED (provider limitation) | §12: 1193/1194 trade through end | document; prefer recent windows |
