# Final design decision

`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` for the requested modern, concentrated, long-only EAR strategy.

The historical phenomenon is real enough to investigate: the 22 January 2008 Brandt et al. working paper reports a positive and significant highest-EAR quintile, and Chan et al. independently support a simpler market-adjusted announcement-return signal. The requested design nevertheless fails two explicit decision gates: modern U.S. evidence for the exact executable long leg is insufficient, and diversified quintile/decile evidence does not defend top-six or one-name concentration. This is a scoped design rejection, not a local negative-alpha result.

# Starting repository state

Local `main` and `origin/main` were verified at `16db2eaad9061a1669166ef9ab6717853337fafc`. This branch was created cleanly from `main`. The buyback and management-guidance research branches were not merged or modified. The required governance documents and relevant V3, scanner, lifecycle, OHLCV, execution and account paths were inspected; see [CURRENT_CAPABILITY_AUDIT.md](CURRENT_CAPABILITY_AUDIT.md).

# Primary EAR literature

The primary Brandt, Kishore, Santa-Clara and Venkatachalam source is a dated working paper rather than a journal publication. This campaign locks the latest located author-hosted version, 22 January 2008, SHA-256 `d55a0f2cc116437957f282345e31699a0e50598256e53fca12d1d3fc5b9e5b10`. Its 1987–2004 sample uses CRSP and Compustat Industrial Quarterly. The older June 2007 draft has materially different tables and is not mixed into the results. See [PRIMARY_LITERATURE_REVIEW.md](PRIMARY_LITERATURE_REVIEW.md).

# Exact EAR definition

For Compustat announcement date `t`, exact Brandt EAR is the compounded firm return over trading days `t−1,t,t+1` minus the compounded return over the same sessions of the firm's matched Fama–French 2×3 size/book-to-market portfolio. Current-quarter observations use quintile cutoffs estimated from prior-quarter EAR observations. Minimum sample, ties and several operational membership details are unstated. No SPY, raw return, market model or V3 residual is silently substituted. See [EAR_FORMULA_SPEC.md](EAR_FORMULA_SPEC.md).

# Benchmark feasibility

`PRIMARY_EAR_BENCHMARK_REQUIRES_LICENSED_DATA`.

Kenneth French publishes the six daily portfolio return series, but those files do not reveal which portfolio contains a particular security on a historical event date. Exact assignment requires historical CRSP returns/PERMNO and market equity, Compustat book equity with an acceptable PIT/restatement policy, dated CCM links and the NYSE breakpoint population. No such entitlement or extract was supplied. See [BENCHMARK_DATA_AUDIT.md](BENCHMARK_DATA_AUDIT.md).

# Alternative EAR definitions

Chan, Jegadeesh and Lakonishok independently validate an additive market-adjusted announcement return over `[-2,+1]` against the CRSP equal-weighted market index. Hou, Xue and Zhang later implement the same family through 2016. This proves a simpler alternative is not merely a convenience proxy, so the campaign is not rejected solely because Brandt's benchmark is licensed. It is still a different window, arithmetic, benchmark and monthly formation rule; a Brandt/Chan hybrid would be untested. Raw, SPY-adjusted, market-model and V3-residual variants were not independently validated for this exact task.

# Earnings event source

`SEC_ITEM_2_02_EVENT_IDENTIFICATION_FEASIBLE_BUT_RDQ_REPLACEMENT_NOT_YET_VALIDATED`.

Item 2.02 filings, exhibits and raw EDGAR acceptance headers provide causal document provenance. They do not reproduce Compustat RDQ or necessarily the first public release: filings may lag, 10-Q/10-K and 6-K paths differ, and amendments can duplicate an episode. Eight purposive SEC specimens demonstrate these cases without estimating market coverage. See [EARNINGS_EVENT_SOURCE_AUDIT.md](EARNINGS_EVENT_SOURCE_AUDIT.md) and [PRIMARY_EARNINGS_EVENT_SAMPLE.json](PRIMARY_EARNINGS_EVENT_SAMPLE.json).

# Event-session alignment

A verified pre-open SEC event maps to that session; a during-session event maps to the active session; an after-close/weekend/holiday event maps to the next exchange session. A filing whose stated release precedes acceptance fails closed until the earlier public timestamp is proven. Same-day acceptance alone does not prove the issuer release minute. See [EVENT_TIMING_AUDIT.md](EVENT_TIMING_AUDIT.md).

# Signal availability / t+2 timing

`EAR_NEXT_OPEN_IMPLEMENTATION_CAUSALLY_COMPATIBLE`.

The full `t−1,t,t+1` statistic is unavailable until the `t+1` bar completes. The earliest compatible daily order is therefore after that close for a `t+2` open fill. Brandt's post-window calculation begins at `t+2`, but the paper uses daily close-to-close returns and does not test an opening-price execution. Compatibility means no lookahead; it does not prove that modern net drift remains after the executable open. See [CAUSALITY_AUDIT.md](CAUSALITY_AUDIT.md).

# Long-only evidence

`POSITIVE_EAR_LONG_LEG_SUPPORTED` for the historical Brandt sample.

The 2008 version reports EAR5 abnormal returns of 1.08%, 1.99%, 2.42%, 2.62%, 3.56% and 4.61% at labeled 30/60/90/120/180/240-trading-day horizons, each marked significant at 1%. These are long-quintile values, not the EAR5−EAR1 spread. No t-statistics, value-weighted counterpart or explicit constituent-weighting rule is reported. Large-stock conditional results are supportive but weaker and diversified. See [LONG_ONLY_EVIDENCE_REVIEW.md](LONG_ONLY_EVIDENCE_REVIEW.md).

# Modern/post-publication evidence

`MODERN_US_EAR_EVIDENCE_INSUFFICIENT`.

Hou, Xue and Zhang provide a market-adjusted alternative through pooled 1972–2016 data, not an isolated modern exact-Brandt long leg. No qualifying post-2010 or 2020s U.S. replication was verified for the exact characteristic-adjusted three-day EAR, causal t+2-open entry, current liquid universe and concentrated positive leg. Recent SUE/PEAD and immediate after-hours evidence raises concerns about faster price incorporation but is not falsely presented as a direct EAR null. See [MODERN_EVIDENCE_REVIEW.md](MODERN_EVIDENCE_REVIEW.md).

# V3 distinctness

`EAR_INFORMATION_DISTINCT_ENOUGH_FROM_V3` at the signal-identity level. Brandt double sorts and Chan regressions retain announcement-return information after conventional momentum controls; Brandt reports less than 35% overlap between top EAR and momentum quintiles. V3 is a 6/9/12-month residual-rank consensus with a positive 12–1 gate and IVOL filter, so it is not the same signal. A positive EAR can mechanically enter later momentum, and no local outcome correlation or portfolio complementarity was calculated. See [V3_DISTINCTNESS_AUDIT.md](V3_DISTINCTNESS_AUDIT.md).

# SUE/PEAD distinction

EAR measures the price response to the complete announcement bundle. It is not standardized unexpected earnings. Brandt's independent two-way sorts and non-overlap test support incremental information, but the first hypothesis must remain EAR-only; no SUE, analyst forecast or guidance score is added.

# Security/universe implications

CIK and issuer-linked filings reduce ETF/ETN contamination but do not identify a unique historical tradable class. Mapping must use CIK, as-filed symbol, event date, security description and a valid lifetime interval, excluding collisions and ambiguity. The repository's broad historical `US_EQUITY` corpus is not common-stock-only and lacks a universal CIK bridge. The paper's financial/utilities inclusion robustness is for a combined SUE/EAR strategy, not an unconditional EAR5 long leg, so it does not fully remove the missing PIT industry problem.

# Structural opportunity supply

No future returns were calculated. The purposive source sample contains eight records: seven initial filings and one amendment; all have Item 2.02, an exhibit and a raw acceptance timestamp. Acceptance classes are two pre-open, one during-session and five after-close. Zero canonical security joins or EAR scores were adjudicated. Population event counts, scoreable events, extreme positives, simultaneity, top-six availability and occupancy remain `null`, meaning unknown rather than zero. See [STRUCTURAL_OPPORTUNITY_AUDIT.json](STRUCTURAL_OPPORTUNITY_AUDIT.json).

# Top-six feasibility

`EAR_PREMIUM_REQUIRES_DIVERSIFICATION` describes the demonstrated evidence scope. Brandt's monotone-looking diversified quintiles and HXZ deciles support broad rank direction, but neither source measures the six most extreme contemporaneous events or within-tail order statistics. Measurement error and extraordinary corporate news can dominate the largest observations. Top-six expression is not defensible from the reviewed evidence. See [CONCENTRATION_FEASIBILITY.md](CONCENTRATION_FEASIBILITY.md).

# One-slot feasibility

`ONE_SLOT_EAR_ALPHA_DENSITY_UNSUPPORTED`. Issuer specificity and a continuous signal make EAR naturally rankable, but they do not prove that the maximum observation has the highest expected net continuation. No top-one evidence was verified.

# Holding-period decision

Sixty trading days is the sole literature-motivated horizon prior considered: the 2008 paper reports a 1.99% EAR5 abnormal return at that horizon, it roughly spans one quarter, and it avoids choosing among local outcomes. Because the design is rejected, 60 days is not frozen as a candidate holding rule. Repeat-event replacement/refresh behavior also remains unfrozen.

# Red-team findings

All 24 requested challenges are answered in [DESIGN_RED_TEAM.md](DESIGN_RED_TEAM.md). The decisive failures are modern exact-signal evidence and concentrated alpha density. Separate unresolved data issues include benchmark membership, complete release-time/RDQ coverage, security joins and structural opportunity supply.

# Exact candidate

Not created. The decision is not READY.

# Future standalone protocol

Not created. G1–G9 are not frozen and no economic experiment is authorized.

# No-economic-peek verification

No candidate EAR, future return, P&L, NAV, CAGR, Sharpe, drawdown, IC, hit rate, return bucket, economic parameter comparison or V3 outcome correlation was computed. Permitted work was limited to published results, source specimens, structural counts and read-only repository inspection. See [NO_ECONOMIC_PEEK_RECEIPT.json](NO_ECONOMIC_PEEK_RECEIPT.json) and the independent verification artifact.

# Production state

The complete normal suite passed outside the socket-restricted sandbox: 1138 passed, 1 skipped, 2 existing deprecation warnings in 101.44 seconds. The protected-path verifier confirmed ten production cache/runtime paths unchanged. The initial sandbox run had six localhost-bind `PermissionError` failures and 1132 passes; this was an environment restriction, not a product failure, and the unrestricted rerun passed. No production code, configuration, strategy registry, scanner, PAPER/LIVE state, account limit or broker state changed.

# Files

All 16 mandatory artifacts are present. `PRIMARY_EARNINGS_EVENT_SAMPLE.json` and the independent design verification are supplemental. `CANDIDATE_DESIGN.md` and `FUTURE_TEST_PROTOCOL.md` are intentionally absent because the decision is not READY.

# Final branch SHA

Branch: `research/earnings-announcement-return-design-v1`. The final immutable commit SHA is reported after commit; embedding a commit's own hash inside its contents would be self-referential.
