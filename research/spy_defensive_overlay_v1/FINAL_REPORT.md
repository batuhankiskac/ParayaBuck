# ParayaBuck cleanup and SPY defensive-overlay research report

## Part I — Repository cleanup

### Starting branches

The repository was `/Users/batuhankiskac/Desktop/ParayaBuck`. The initial
checkout was clean on `research/residual-reversal-local-v1`; local and remote
`main` both pointed to `8123694a71d8fbb0e520f13b4e1692e932754e97` after a
fetch/prune.

| Branch | Tip | Relative to main | Classification | Action |
|---|---|---:|---|---|
| `main` | `8123694a` | baseline | production | retained and repaired |
| `research/residual-reversal-local-v1` | `a3a54cff` | 8 ahead | rejected residual strategy, runners, and large result artifacts | inspected, production defects independently reproduced, then deleted locally/remotely |

No other local or remote research branch remained after pruning.

### Production fixes extracted

The old research branch was not merged. Two defects were reproduced on main
and reimplemented narrowly on a temporary maintenance branch:

1. terminal/stale managed symbols could wedge weekly completeness and leave
   holdings unresolved; typed lifecycle evidence now closes at the real final
   bar, cancels pending terminal orders, rejects overdue inventory, and records
   terminal audit rows;
2. a requested V3 run could build its observation universe using the configured
   V1 identity; universe construction and optimizer reuse now receive the
   resolved requested strategy.

Focused regressions cover both paths. No strategy parameter, allocation, PAPER
selection, or LIVE setting changed.

### Research artifacts removed

No ambiguous production file was deleted. Residual-specific strategy code,
runners, gate tables, JSON outcomes, and multi-million-line artifacts were
kept out of main by deleting the closed branch rather than merging it. The
canonical `docs/web_route_contract_before.json` fixture, accidentally absent
from the starting main commit, was restored because the normal suite requires
it. Five existing Python files were mechanically Ruff-formatted.

### Branches deleted

`research/residual-reversal-local-v1` was deleted locally and from origin only
after recording its final rejected tip `a3a54cff64a9312300ef700f2fe9409435a064d3`
and confirming no production fix remained stranded. Recovery is possible by
the recorded SHA while the object remains available in Git storage/backups;
the remote branch ref itself is gone.

### Main verification

| Check | Result |
|---|---|
| Ruff lint | pass |
| Ruff format check | pass |
| mypy | pass, 131 source files |
| complete pytest suite | 1,071 passed, 1 skipped, 2 warnings |
| working tree / remote parity | clean; `main == origin/main` |
| rejected strategy registered | no |
| PAPER / LIVE changes | none |

### Cleaned main SHA

`5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`, pushed to `origin/main` by a
normal fast-forward workflow.

## Part II — SPY defensive overlay research

### Decision

`RESEARCH_REJECT`

The evidence is valid, and SH materially reduced drawdown, but the frozen
candidate failed Gate 3 by narrowly missing the required 0.10 risk-adjusted
improvement and failed Gate 8 because the real six-position limit left too much
of the equity sleeve unused. No parameter or instrument was retuned.

### Research branch SHA

- Branch: `research/spy-defensive-overlay-v1`
- Corrected replay source SHA: `ab36d4fb6c719495d44bbcb16f916edbc0bd20ab`
- Clean production base: `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1`

The final evidence commit is the descendant containing this report and the
machine-readable replay artifacts.

### Frozen hypothesis

At the existing weekly V3 decision, use exactly 253 completed SPY observations
to compute `SPY_close[t] / SPY_close[t-252] - 1`. Positive is NORMAL; zero or
negative is DEFENSIVE. A always retains V3 at 0.55. In DEFENSIVE weeks, B caps
V3 at 0.45 and holds the remainder in cash, while C caps V3 at 0.45 and targets
0.10 actual SH. SH consumes a real position; execution is next tradable bar.

The frozen protocol SHA-256 is
`7c2be80ea9be03e2a75dedaa233ecdb5856bba333d11d41739dd44805d0b44ea`.
An initial replay was explicitly invalidated before decision because a wrapper
bug allowed B to exceed its frozen 0.45 V3 cap. The documented correction only
enforces that pre-existing cap; signal, dates, instrument, costs, and gates were
unchanged.

### Literature / mechanism basis

The mechanism is grounded in time-series momentum and long-horizon trend
evidence (Moskowitz, Ooi & Pedersen; Hurst, Ooi & Pedersen; Faber), with
published counterevidence retained rather than treated as a parameter-selection
guide. SH evidence comes from the issuer and SEC filings: it targets the inverse
of the S&P 500's **daily** result, so multi-day outcomes are path-dependent and
must use actual SH bars rather than synthetic negative SPY returns. The exact
ParayaBuck result below is implementation evidence, distinct from that general
literature.

Primary references are listed in `LITERATURE_AND_PRODUCT_BASIS.md`, including
the SH prospectus, issuer product page, SEC inverse-ETF bulletin, and the
Avellaneda–Zhang path-dependence analysis.

### Data identity

| Input | SHA-256 |
|---|---|
| split-adjusted SIP equity panel | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` |
| PIT entity lifetimes | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` |
| corporate actions | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` |

The full input contains 11,686,832 bars and 9,010 lifetime identities. SPY has
2,658 real bars and SH 2,606 real bars from 2016-01-04 through 2026-07-30;
both are active Tier-A PIT identities. SH cash distributions are applied once,
and split actions are not re-applied to split-adjusted prices. The replay used
448 streamed symbols, 1,087,280 engine bars, and 2,658 PIT snapshots.

All nine runs account for 500 scheduled weeks as 495 decisions plus 5 explained
warmup/completeness skips. There are 256 development and 239 validation
decisions; the longest decision gap is eight days.

### Regime statistics

| Statistic | Value |
|---|---:|
| NORMAL decisions | 424 (85.66%) |
| DEFENSIVE decisions | 71 (14.34%) |
| Defensive episodes | 4 |
| Average episode length | 17.75 decisions |
| Regime transitions | 8 |
| NORMAL compounded return / vol / Sharpe (C, 1x) | 107.55% / 10.97% / 0.876 |
| DEFENSIVE compounded return / vol / Sharpe (C, 1x) | -6.91% / 8.19% / -0.604 |

### Control A — V3 0.55

### Control B — defensive cash

### Treatment C — defensive SH

One-times-cost primary results:

| Window | Arm | Net return | CAGR | Vol | Sharpe | Sortino | Max DD | Calmar | Worst month | Turnover | Costs | Avg exposure | Max conc. | Fills | Rebalances |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Full | A | 98.21% | 7.41% | 11.26% | 0.691 | 0.888 | 20.44% | 0.362 | -7.94% | 169.01 | $7,529.78 | 51.94% | 10.00% | 2,054 | 495 |
| Full | B | 98.60% | 7.43% | 10.93% | 0.711 | 0.909 | 17.99% | 0.413 | -7.94% | 165.50 | $7,354.05 | 50.68% | 10.00% | 2,071 | 495 |
| Full | C | 93.22% | 7.12% | 10.56% | 0.705 | 0.887 | 15.53% | 0.458 | -8.83% | 163.98 | $7,103.28 | 51.09% | 10.00% | 2,049 | 495 |
| Development | A | 60.98% | 10.00% | 11.65% | 0.877 | 1.068 | 15.53% | 0.644 | -7.94% | 82.54 | $3,243.15 | 51.38% | 10.00% | 1,015 | 256 |
| Development | B | 58.16% | 9.61% | 11.46% | 0.859 | 1.038 | 15.53% | 0.619 | -7.94% | 81.88 | $3,195.69 | 50.83% | 10.00% | 1,021 | 256 |
| Development | C | 50.30% | 8.50% | 11.25% | 0.782 | 0.933 | 15.53% | 0.547 | -8.83% | 81.87 | $3,125.78 | 50.95% | 10.00% | 1,021 | 256 |
| Validation | A | 23.13% | 4.65% | 10.84% | 0.474 | 0.650 | 18.96% | 0.246 | -4.63% | 85.16 | $4,286.62 | 52.54% | 9.91% | 1,039 | 239 |
| Validation | B | 25.57% | 5.11% | 10.32% | 0.534 | 0.737 | 16.47% | 0.310 | -4.63% | 82.55 | $4,158.35 | 50.51% | 9.92% | 1,050 | 239 |
| Validation | C | 28.55% | 5.65% | 9.76% | 0.612 | 0.834 | 13.09% | 0.431 | -4.63% | 81.24 | $3,977.50 | 51.24% | 10.00% | 1,028 | 239 |

### SH incremental value versus cash

At 1x full period, C versus B changed CAGR by -0.31 percentage points,
Sharpe by -0.006, Sortino by -0.022, Calmar by +0.046, and reduced max
drawdown by 2.46 points. Turnover was 1.52 lower and costs $250.77 lower.

In validation, C versus B improved CAGR by 0.54 points, Sharpe by 0.078,
Sortino by 0.097, Calmar by 0.121, and max drawdown by 3.38 points, while
reducing turnover by 1.31 and costs by $180.86. This passed frozen Gate 5,
but does not override the failed all-gates decision rule.

### Development vs validation

Development did not show the benefit: C matched A's 15.53% max drawdown but
trailed A by 1.50 CAGR points, 0.095 Sharpe, 0.135 Sortino, and 0.097 Calmar.
Validation was much stronger: C beat A by 0.99 CAGR points, 0.138 Sharpe,
0.183 Sortino, and 0.186 Calmar while reducing drawdown by 5.87 points.
Frozen Gate 6 passed because validation independently met its thresholds and
development drawdown was neutral rather than oppositely signed.

### Cost stress

Full-period results:

| Cost | Arm | CAGR | Sharpe | Sortino | Max DD | Calmar | Costs |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1x | A | 7.41% | 0.691 | 0.888 | 20.44% | 0.362 | $7,529.78 |
| 1x | B | 7.43% | 0.711 | 0.909 | 17.99% | 0.413 | $7,354.05 |
| 1x | C | 7.12% | 0.705 | 0.887 | 15.53% | 0.458 | $7,103.28 |
| 2x | A | 6.84% | 0.644 | 0.827 | 21.31% | 0.321 | $14,431.35 |
| 2x | B | 6.88% | 0.664 | 0.849 | 18.83% | 0.365 | $14,105.61 |
| 2x | C | 6.58% | 0.656 | 0.826 | 15.57% | 0.423 | $13,629.05 |
| 3x | A | 6.28% | 0.597 | 0.768 | 22.13% | 0.284 | $20,961.11 |
| 3x | B | 6.33% | 0.616 | 0.789 | 19.66% | 0.322 | $20,495.80 |
| 3x | C | 6.03% | 0.608 | 0.766 | 16.32% | 0.370 | $19,809.50 |

Gate 7 passed: the 2x core gates survived and the 3x run remained bounded.

### Drawdown analysis

C reduced 1x full-period max drawdown from 20.44% to 15.53%: 4.90 percentage
points, or 23.99% relatively. B alone reduced it by 2.44 points, while SH added
another 2.46 points versus B. The drawdown objective passed, but the full-period
risk-adjusted hurdle did not: the largest C-minus-A gain was Calmar +0.096,
short of the frozen +0.100 requirement. Sharpe gained only 0.014 and Sortino
was essentially flat (-0.001).

### Capacity / six-position impact

SH was meaningfully present: median realized/intended SH was 1.00, and 68 of
71 defensive decisions (95.77%) realized at least 7.5% SH. Every allocation
respected six positions, the 10% per-name cap, 55% equity sleeve, 60% gross
cap, and 40% cash floor.

Deployability nevertheless failed. SH displaced a V3 slot on all 71 defensive
decisions. Average realized V3 was 35.22% (range 14.53%–37.50%); average total
defensive equity exposure was 44.79%, leaving 10.21% unused versus the 55%
sleeve, above the frozen 5% tolerance. Seven decisions also had an SH overlap
shortfall. No unused amount was reallocated.

The secondary structural whole-account check is worse when unchanged crypto
occupies slots: one crypto position caps equity at 50% (5% under-use), while
two crypto positions leave four equity slots, cap equity at 40%, and imply 15%
under-use. This is structural feasibility evidence, not a crypto backtest.

### Independent verification

`independent_verification.json` reports `PASS`: 279,316 checks passed and zero
failed. The verifier independently read CSV/JSON/Git inputs and reconstructed
the 253-close signal; it checked hashes, completed-bar/no-lookahead semantics,
next-bar real-open fills, actual SH bars, lifecycle events, coverage, V3 source
identity/derating, B/C allocation caps, positions/gross/cash, costs, dividends,
final liquidation, and NAV/cash reconciliation.

PAPER configuration, the production strategy registry, V3 source, crypto, risk
limits, and LIVE state were not modified by the research branch.

### Frozen gate table

| Gate | Result | Evidence |
|---:|---|---|
| 1 Replay/data validity | PASS | exact hashes; 9/9 coverage valid; independent verifier PASS |
| 2 Drawdown objective | PASS | -4.90 pp, -23.99% relative C vs A |
| 3 Risk-adjusted improvement | **FAIL** | best gain Calmar +0.096 < +0.100 |
| 4 Return cost | PASS | full CAGR drag 0.29 pp; validation improved 0.99 pp |
| 5 SH incremental value | PASS | -2.46 pp DD C vs B; validation confirms -3.38 pp |
| 6 Validation | PASS | validation DD and all three risk metrics improved materially |
| 7 Cost robustness | PASS | 2x core and 3x bounds pass |
| 8 Deployability | **FAIL** | 10.21% average unused allocation > 5% limit |

### Final reasoning

The mechanism is credible and the validation drawdown result is substantial,
but the candidate is conjunctively gated. It misses the precommitted risk-
adjusted threshold and, more importantly, cannot deploy the intended sleeve
under the real six-position constraint without pathological under-utilization.
The correct decision is therefore `RESEARCH_REJECT`. It is not registered,
promoted, or enabled in PAPER/LIVE, and this task stops without retuning.

### Files created

- `FROZEN_PROTOCOL.md` — pre-outcome hypothesis, data, chronology, and gates.
- `LITERATURE_AND_PRODUCT_BASIS.md` — primary literature and SH product basis.
- `IMPLEMENTATION_CORRECTION.md` — explicit invalidation/correction audit trail.
- `candidate.py`, `data.py`, `replay.py` — research-only implementation.
- `verify_results.py`, `evaluate.py` — independent verifier and frozen evaluator.
- `test_candidate.py`, `test_data.py`, `test_replay_invariants.py`,
  `test_verifier.py`, `test_evaluate.py` — deterministic research tests.
- `campaign_results.json`, `execution_audit.json`, `run_manifest.json`,
  `independent_verification.json`, `gate_table.json`, `decision.json` — frozen
  machine-readable evidence.
- `FINAL_REPORT.md` — this two-part handoff.
