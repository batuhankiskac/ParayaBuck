# Research register

This is the compact, durable index of completed equity research and governance
campaigns. The register records decisions and provenance; large JSON, replay
curves, compressed audits, runners, and rejected candidate implementations
remain outside production `main` behind the archive tags listed here.

No entry below authorizes automatic PAPER/LIVE promotion. Production changes
are listed explicitly and are separate from research conclusions.

## Account-capacity integration

Migration V3 (`9c5e61382e8cdb71fdd3442d945719024accedd9`) concluded
`ACCOUNT_ARCHITECTURE_MIGRATION_8_ACCEPTED`. Production parity V2
(`d20b70c0333b888b6d0e5438091ede540f3a27ee`) concluded
`CAP8_PRODUCTION_PARITY_READY_FOR_PAPER_CHANGE`; the subsequent correctness
campaign concluded `PRODUCTION_CORRECTNESS_FIXED_CAP8_READY_FOR_PAPER_CHANGE`.
The repository integration changes only `risk.max_positions` from 6 to 8.
Status: `CODE/CONFIG INTEGRATED; RUNTIME NOT STARTED IN THIS CAMPAIGN`.
LIVE: `DISABLED`. See [the integration record](cap8_main_integration_v1.md).

## Decision index

| Research ID | Original branch | Final recorded SHA / date | Validity | Verdict | Durable classification | Production changed? | Closed / superseded-by | Archive tag |
|---|---|---|---|---|---|---|---|---|
| `equity_short_term_residual_reversal_v1` | `research/residual-reversal-local-v1` (deleted before this audit) | `a3a54cff64a9312300ef700f2fe9409435a064d3` / 2026-09-16 | `CORRECTED_REPLAY_VALID`; valid capital-neutral portfolio test | `RESEARCH_REJECT` | `ALPHA_VALID_PORTFOLIO_REJECTED` | No | Closed; superseded the original invalid campaign; no resurrection | `archive/research/residual-reversal-local-v1-2026-09` |
| `equity_spy_defensive_overlay_v1` | `research/spy-defensive-overlay-v1` | `da9a99b6e23df880fb64afa78d1f38aa70edd5ec` / 2026-09-17 | Valid; independent verification passed | `RESEARCH_REJECT` | `PORTFOLIO_REJECTED` | No | Closed | `archive/research/spy-defensive-overlay-v1-2026-09` |
| `equity_v3_volatility_managed_v1` | `research/v3-volatility-managed-v1` | `9dbe4bd78b3276159dd089e4edf4e15b9ed1cb01` / 2026-09-17 | Valid; structural replay and verifier passed | `RESEARCH_REJECT` | `PORTFOLIO_REJECTED` | No | Closed | `archive/research/v3-volatility-managed-v1-2026-09` |
| `equity_overnight_intraday_design_v1` | `research/overnight-intraday-design-v1` | `7ec53efecc4d37cab17d0d96f194389b4513a69b` / 2026-09-17 | Design evidence valid; no economic result in this campaign | `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST` | Design only | No | Closed; superseded by the overnight economic result | `archive/research/overnight-intraday-design-v1-2026-09` |
| `equity_overnight_reversal_v1` | `research/overnight-reversal-v1` | `41e1c9026bb34bbe81f98577844c9eca0523da8a` / 2026-09-18 | `RESEARCH_RUN_VALID`; independent verification passed | `RESEARCH_REJECT` | `ALPHA_REJECTED` | No | Closed; supersedes the design-only decision | `archive/research/overnight-reversal-v1-2026-09` |
| `equity_universe_integrity_audit_v1` | `maintenance/equity-universe-integrity-v1` | `180fd12e7bafb5943856229e85e305d5d9906511` / 2026-09-18 | Structural audit; no economic backtest | `UNIVERSE_CONTRACT_AMBIGUOUS_OPERATOR_DECISION_REQUIRED` | Not an alpha test | No | Closed; superseded/refined by security-master source research and the limitations document | `archive/research/equity-universe-integrity-v1-2026-09` |
| `equity_security_master_source_v1` | `research/equity-security-master-source-v1` | `70738106760eec86908b846823b25bd6964b5c0c` / 2026-09-18 | Source study valid; historical PIT coverage not proven | `SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED` | Not an alpha test | No | Closed; informs future source acquisition, no production integration | `archive/research/equity-security-master-source-v1-2026-09` |
| `equity_v3_reserved_role_corrected_baseline_v1` | `research/v3-reserved-role-corrected-baseline-v1` | `d34efd3b57cc22c766c5351c12201089bdf55480` / 2026-09-18 | Corrected replay internally verified under original protocol | `CORRECTED_V3_BASELINE_FAILED` (superseded) | Baseline, not an alpha test | No; role fix was already on `main` | Closed; superseded by the repaired protocol decision below | `archive/research/v3-reserved-role-corrected-baseline-v1-2026-09` |
| `equity_v3_baseline_protocol_repair_v1` | `research/v3-baseline-protocol-repair-v1` | `96910de6a54a3d3154996cad865308c3fff127ed` / 2026-09-18 | V2 validity, parity, and independent verification passed | `CORRECTED_V3_BASELINE_VALID` | Baseline, not an alpha test | No runtime/configuration change | Closed; current canonical V3 baseline/control | `archive/research/v3-baseline-protocol-repair-v1-2026-09` |
| `equity_52week_high_close_v1` | design: `research/52week-high-design-v1`; readiness: `research/52week-high-readiness-v1`; economic: `research/52week-high-economic-v1` | `2d53ba1985a60c67bb727e1092124df08c220842` / 2026-09-21 | `RESEARCH_RUN_VALID`; readiness was `G1_READY_FOR_FROZEN_ECONOMIC_TEST` | `RESEARCH_REJECT` | `ALPHA_REJECTED` | Generic correctness fixes were already merged to `main`; no candidate code merged | Closed; no combination experiment warranted | design/readiness/economic tags listed below |
| `equity_information_discreteness_continuous_v1` | design: `research/information-discreteness-design-v1`; standalone: `research/information-discreteness-economic-v1`; combination: `research/v3-id-complementarity-v1` | `a5d355eb8b8045e0438d435312fcc23e59cb3936` / 2026-09-21 | standalone and combination runs valid | standalone `RESEARCH_REJECT`; combination `COMBINATION_REJECT` | `ALPHA_VALID_PORTFOLIO_REJECTED` | No | Closed; standalone alpha remains valid, but no canonical 5+1 slot | design/economic/combination tags listed below |
| `equity_abnormal_volume_v1` | design V1: `research/abnormal-volume-design-v1`; readiness V1: `research/abnormal-volume-readiness-v1`; design V2: `research/abnormal-volume-design-repair-v1`; readiness V2: `research/abnormal-volume-readiness-v2`; economic: `research/abnormal-volume-economic-v1` | `693b86a16bb7363e98be46aeb25d164c384fe9aa` / 2026-09-21 | `RESEARCH_RUN_VALID`; independent verification passed with zero issues | `RESEARCH_REJECT` | `ALPHA_REJECTED` | Generic scheduler primitive was already on `main`; no candidate code merged | Closed; no retuning or complementarity test | five archive tags listed below |
| `equity_max_lottery_design_v1` | `research/max-lottery-design-v1` | `405103908acd7b872df2d9663fa8166e6d793020` / 2026-09-21 | Pre-outcome design review; no economic candidate frozen or run | `DESIGN_BLOCKED_DATA_OR_EXECUTION` | Design-stage block, not an alpha classification | No | Closed; requires a new pre-outcome design campaign if blockers are resolved | `archive/research/max-lottery-design-v1-2026-09` |
| `equity_opportunistic_insider_purchases_design_v1` | design: `research/opportunistic-insider-purchases-design-v1`; source reassessment: `research/opportunistic-insider-source-reassessment-v1` | `e46e9ae0e1c124138c2143b30d93809a97f4282d` / 2026-09-21 | Two pre-outcome reviews; no local economic outcome inspected | original `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; reassessment `REASSESSMENT_CONFIRMS_DESIGN_REJECT` | Design-stage rejection; source/mapping feasibility improved | No | Closed; no retuning or economic experiment | two archive tags listed below |
| `equity_cash_based_operating_profitability_design_v1` | `research/cash-based-operating-profitability-design-v1` | `0f77021a2d6a0d08df76f0902da3354e90f95f65` / 2026-09-21 | Pre-outcome design review; no local forward returns | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | Design-stage rejection | No | Closed; exact accounting semantics fail closed | `archive/research/cash-based-operating-profitability-design-v1-2026-09` |
| `equity_positive_liquidity_shock_design_v1` | `research/positive-liquidity-shock-design-v1` | `15644b3c117535eb94a12eb5099d9d82d10cb2cf` / 2026-09-21 | Pre-outcome design and structural audit; no future-return study | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | Design-stage rejection | No | Closed; measurable but unsuitable for the concentrated architecture | `archive/research/positive-liquidity-shock-design-v1-2026-09` |
| `equity_schedule_13d_activism_design_v1` | `research/schedule-13d-activism-design-v1` | `c4e9e30039eb4bef8005fdb0700e15a99884070c` / 2026-09-22 | Pre-outcome design and SEC structural audit; no candidate returns | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | Design-stage rejection, not `ALPHA_REJECTED` | No | Closed; no candidate or economic protocol created | `archive/research/schedule-13d-activism-design-v1-2026-09` |
| `equity_share_repurchase_announcement_design_v1` | `research/share-repurchase-announcement-design-v1` | `73fe3e0065c46f813508a08013fb2198629d9bda` / 2026-09-22 | Pre-outcome design review; no local candidate economics | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | Design-stage rejection | No | Closed; no candidate or economic protocol created | `archive/research/share-repurchase-announcement-design-v1-2026-09` |
| `equity_management_guidance_drift_design_v1` | `research/management-guidance-drift-design-v1` | `9648192713c5d881bf033e4d505780e70273a98a` / 2026-09-22 | Pre-outcome design review; no local candidate economics | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | Design-stage rejection | No | Closed; evidence and concentration failed independently of the licensed-data block | `archive/research/management-guidance-drift-design-v1-2026-09` |
| `equity_earnings_announcement_return_design_v1` | `research/earnings-announcement-return-design-v1` | `1f7c461be32f11bb23a8c85a89f19621244a270f` / 2026-09-22 | Pre-outcome design review; no local EAR or future return | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | Design-stage rejection | No | Closed; no candidate or economic protocol created | `archive/research/earnings-announcement-return-design-v1-2026-09` |
| `public-data-alpha-discovery-v3` | `research/public-data-alpha-discovery-v3` | `f37820f11e35da1d360600d7adbf10f501e88f09` / 2026-09-22 | Public/free-data, pre-outcome screen; no candidate economics | `PUBLIC_DATA_DISCOVERY_V3_EXHAUSTED_AFTER_SINGLE_DESIGN_CANDIDATE` | Discovery campaign, not an alpha result | No | Closed; sole shortlist candidate received the design decision below; no untested shortlist candidate remains | `archive/research/public-data-alpha-discovery-v3-2026-09` |
| `equity_spinoff_child_drift_v1` | `research/spinoff-child-drift-design-v1` | `4b38d4db4a62459b4b60f271ce98beaf1e98f7f9` / 2026-09-22 | Primary-source, pre-outcome child-security design audit; no economic test | `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` | Design-stage rejection, not `ALPHA_REJECTED` | No | Closed; reopen only with materially new external evidence | `archive/research/spinoff-child-drift-design-v1-2026-09` |
| `account_capacity_migration_8_v1` | `research/account-capacity-migration-8-v1` | `ede3b3dfadfd5f3f0bdef040a14d20365c8df469` / 2026-09-22 | Stopped before either arm; no migration outcomes | `ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE` | Inconclusive migration attempt; not an economic result | No | Closed; frozen specification is provenance only; re-freeze from corrected main | `archive/research/account-capacity-migration-8-v1-inconclusive-2026-09` |
| `crypto_exit_giveback_audit_v3` | `research/crypto-exit-giveback-audit-v3` | `d46338454cb7f8d59525d88fd775f98e2b927d45` / 2026-09-24 | Causal canonical replay; independent episode verification PASS | `CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED` | `MIXED_PROFIT_RETENTION_BEHAVIOR` | No | Closed; V2 input authority superseded, conclusion reconfirmed | `archive/research/crypto-exit-giveback-audit-v3-2026-09` |

The branch `maintenance/reserved-equity-role-isolation-v1` was an exact alias
of current `main` (`bdfa55ea...`) with no unique commit or evidence. It is not
a research campaign and is represented in the archive inventory only.

## Durable classification vocabulary

These classifications keep standalone alpha evidence separate from portfolio
contribution evidence:

- `ALPHA_REJECTED`: standalone evidence is not strong enough to justify a
  portfolio-contribution test. Current examples are overnight reversal and
  52-week high.
- `ALPHA_VALID_PORTFOLIO_REJECTED`: standalone evidence is positive and
  credible, but a valid frozen portfolio-contribution test failed. Current
  examples are residual reversal and information discreteness.
- `PORTFOLIO_REJECTED`: the hypothesis is itself a portfolio transformation or
  overlay, rather than an alternative standalone alpha, and its portfolio test
  failed. Current examples are the SPY defensive overlay and volatility-managed
  V3.
- `ALPHA_VALID_PORTFOLIO_ACCEPTED`: reserved for a future candidate that passes
  both standalone validity and a frozen complementarity test. It does not by
  itself authorize PAPER or LIVE deployment.

## Completed campaign records

### Residual reversal — `equity_short_term_residual_reversal_v1`

The original campaign was invalidated and replaced by the corrected replay at
`a3a54cff64a9312300ef700f2fe9409435a064d3`. The corrected replay was valid,
but the final verdict was `RESEARCH_REJECT`.

The standalone reversal had positive economics at 1x (net return 88.49%, CAGR
6.84%, Sharpe 0.6483, max drawdown 17.78%), but its capital-neutral treatment
was a valid portfolio experiment and did not provide the required contribution:
CAGR, Sharpe, and Sortino did not improve within the frozen tolerance. The
six-position account constraint
created allocation-capacity shortfall on 495/495 treatment decisions, with
substantial unused sleeve capacity. The original campaign is not to be
resurrected; the corrected replay is the durable result and is preserved by
the archive tag above. Its durable classification is therefore
`ALPHA_VALID_PORTFOLIO_REJECTED`, not `ALPHA_REJECTED`. Production did not
change.

### SPY defensive overlay — `equity_spy_defensive_overlay_v1`

The frozen candidate used a positive/negative 253-close SPY regime signal and
either held the remainder in cash or allocated 10% actual SH in defensive
weeks. The evidence was independently verified and the run was valid, but the
verdict was `RESEARCH_REJECT`.

Treatment C reduced full-period 1x max drawdown from 20.44% for control A to
15.53%; SH added 2.46 percentage points of drawdown reduction beyond the cash
arm B. The contribution gate still failed: the best full-period risk-adjusted
gain was Calmar +0.096 versus the frozen +0.100 requirement. Deployability
also failed because defensive weeks left 10.21% average equity allocation
unused, above the 5% tolerance, under the real six-position account limit.
No parameter, instrument, PAPER, or LIVE setting changed.

### V3 volatility management — `equity_v3_volatility_managed_v1`

The slot-free hypothesis scaled V3 exposure using completed-bar SPY VOL21
observations without changing V3 selection or instruments. The research run
and structural replay were valid, but the mandatory gates failed and the
verdict was `RESEARCH_REJECT`.

At full period and 1x, the control produced 94.60% net return, 7.20% CAGR,
Sharpe 0.686, and 20.13% max drawdown. The treatment produced 72.44% net
return, 5.86% CAGR, Sharpe 0.670, and 19.33% max drawdown: only a small
drawdown improvement at a material CAGR drag. The static comparator also
failed the frozen risk-adjusted/timing-value/cost gates. No production change.

### Overnight/intraday design — `equity_overnight_intraday_design_v1`

The design campaign froze exactly one candidate,
`equity_overnight_reversal_v1`, with a one-session overnight-to-following-
session reversal signal, simple SPY excess adjustment, completed-bar
decision, next tradable daily-bar open execution, daily rebalance, at most six
long names, fixed `0.55 / 6` targets, and current cost/risk policies. Its
validity status was `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`; no economics were
inspected in that design campaign. It is closed and superseded by the economic
experiment below. Production did not change.

### Overnight reversal — `equity_overnight_reversal_v1`

The frozen one-session delayed implementation was an independently verified
valid research run (`RESEARCH_RUN_VALID`) but failed the conjunctive gates and
was rejected. Full-period 1x results were approximately net return -45.53%,
CAGR -6.15%, Sharpe -0.323, max drawdown 59.01%, and annualized one-way target
turnover 171.58x. The current daily execution latency did not preserve the
literature mechanism sufficiently: the signal is observed at the opening
component but the current engine decides only after the completed bar and
executes at the following tradable open. No rescue, tuning, registry change,
PAPER change, or LIVE authorization occurred.

### Equity-universe integrity — `equity_universe_integrity_audit_v1`

The structural audit concluded
`UNIVERSE_CONTRACT_AMBIGUOUS_OPERATOR_DECISION_REQUIRED`. Alpaca broad
`US_EQUITY` is not a common-stock taxonomy; current product-type fields do not
prove a complete historical ordinary-equity universe. `maximum_per_sector` is
fallback observation-set diversity, not a hard final portfolio constraint.
The finite static leveraged/inverse list is incomplete for a universal product
policy. No production fix or universe conversion was made. The audit is
superseded/refined by the source study and the durable limitations document.

### Security-master source study — `equity_security_master_source_v1`

The study concluded
`SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED`. Current public Nasdaq
directories and SEC/Cboe evidence can support a current classifier, but they do
not prove a 2016–2026 point-in-time taxonomy for all 9,010 historical symbols.
Nasdaq Daily List history and NYSE Security Master history require secured or
licensed access for the demonstrated fields. Unknowns must fail closed for
ordinary alpha rather than being silently mapped to common stock. The
recommendation is to retain the broad historical `US_EQUITY` universe while
pursuing an effective-dated licensed source; no production integration was
performed.

### Original reserved-role baseline — `equity_v3_reserved_role_corrected_baseline_v1`

The corrected replay branch proved the narrow reserved-role production fix and
then reported `CORRECTED_V3_BASELINE_FAILED` because its frozen G5 compared
offline alpha maximum drawdown (20.4359%) directly with the 5% production
drawdown latch. That result is prominently superseded: G5 was methodologically
misspecified. The production role fix itself was already integrated on `main`
at `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`; this branch did not contribute
additional production code.

### Repaired V3 baseline — `equity_v3_baseline_protocol_repair_v1`

The repaired V2 protocol classified the original G5 as `G5_MISSPECIFIED` and
reused the independently verified persisted replay without reopening economic
outputs before protocol freezing. The final decision is
`CORRECTED_V3_BASELINE_VALID`, with all V2 gates passing and anti-post-hoc
answer `NO`.

This is the current canonical V3 research baseline/control. Its 1x full-period
metrics are net return 97.5529%, CAGR 7.3714%, Sharpe 0.6880, Sortino 0.8841,
max drawdown 20.4359%, and Calmar 0.3607. Validation metrics are net return
22.7203%, CAGR 4.5736%, and Sharpe 0.4672. The 2x and 3x full-period runs
remained positive, and ordinary V3 target eligibility for SPY/SH/PSQ/DOG was
zero. The full protocol, role, dataset, and cost contract is recorded in
[`canonical_v3_baseline.md`](canonical_v3_baseline.md).

### 52-week high close — `equity_52week_high_close_v1`

The design campaign at
`72b51a0e5c03f2ce0a8790b197cf360b26b33cd3` initially concluded
`DESIGN_BLOCKED_DATA_OR_EXECUTION`. The blocking items were canonical data-loader
compatibility, corporate-action and lifetime normalization, month-end decision
semantics, broad-pool-through-t ranking, and all-in effective-dated cost
scaling. The generic fixes were integrated into production `main` at
`839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de` before economics. The separate
research-only timing/ranking harness then reached
`G1_READY_FOR_FROZEN_ECONOMIC_TEST` at
`339d62f404a0b924ec78e3a67d8cddad7cee24c7`.

The frozen economic run at
`2d53ba1985a60c67bb727e1092124df08c220842` was valid but rejected. Candidate B
returned approximately +0.65% full-period net at 1x and -12.93% in validation;
full-period net returns at 2x and 3x were approximately -2.28% and -5.12%.
Only G1 and G9 passed; G2–G8 failed. This is `ALPHA_REJECTED`: the candidate
failed its own standalone validation, cost, turnover, and concentration
evidence, not merely a comparison with V3. A portfolio-combination experiment
is not warranted and the candidate must not be retuned under this campaign.

Evidence is preserved by:

- `archive/research/52week-high-design-v1-2026-09`;
- `archive/research/52week-high-readiness-v1-2026-09`;
- `archive/research/52week-high-economic-v1-2026-09`.

### Information discreteness — `equity_information_discreteness_continuous_v1`

The frozen design at `a822083eef4123d960ee7886d52951bfb20b29bd`
reached `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`. It used raw split-adjusted
simple returns, a 252-return formation window, a 21-return skip, positive PRET,
ascending information-discreteness rank, monthly decisions, and six
equal-weight names.

The standalone run at `9dbc44ce9f62b6e2f1b5d233eee77a7fff3df9d3`
was valid. Its formal frozen displacement-protocol verdict was
`RESEARCH_REJECT`, but its own economics were positive: full-period 1x net
return was approximately +57.79%, CAGR 4.88%, and Sharpe 0.446; validation net
return was approximately +18.68%. It also remained positive at 2x and 3x.
Accordingly, the standalone alpha classification is `ALPHA_VALID`, not
`ALPHA_REJECTED`.

The subsequent canonical 5+1 experiment used full six-slot V3 as Control A, a
matched five-slot V3 core plus reserved satellite cash as Control B, and the
same five-slot core plus one ID satellite as Treatment C. The current branch tip
is `a5d355eb8b8045e0438d435312fcc23e59cb3936`; the economic evidence was
verified at `997fd9f592e1449aeeca6f45af7229cb24f23c11`. The run was
`COMBINATION_RUN_VALID` and its verdict was `COMBINATION_REJECT`.

At 1x full-period net return, A/B/C were respectively 0.975529, 0.553304, and
0.508135. The independently attributed satellite P&L was +4,844.72 full-period
and +1,358.82 in validation. G1, G4, G6, G7, G8, and G9 passed; G2, G3, and G5
failed. The alpha was real and positive standalone, but it did not earn a
complementary slot under the frozen shared-ledger architecture. Its durable
classification is `ALPHA_VALID_PORTFOLIO_REJECTED`.

Evidence is preserved by:

- `archive/research/information-discreteness-design-v1-2026-09`;
- `archive/research/information-discreteness-economic-v1-2026-09`;
- `archive/research/v3-id-complementarity-v1-2026-09`.

The candidate-independent architecture and governance rules extracted from the
campaign are documented in
[`core_satellite_combination_policy_v1.md`](core_satellite_combination_policy_v1.md).

### Abnormal volume — `equity_abnormal_volume_v1`

The original pre-outcome design at
`4f454f5deb4c77151b574f87c29228c859cfc884` froze
`AV49_D1_NORMALRET_M1_LONG` and returned
`DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST`. The intended candidate used a
split-consistent dollar-volume shock, 49 prior sessions, one-day formation,
middle-40% formation-return conditioning, a month-end decision, entry at the
next month's first tradable open, calendar-month holding, and at most six
equal-weight long names.

Readiness V1 at `93e85169b9e1417902d3c13ac91fc1c304dda4f8`
returned `G1_BLOCKED_DATA_OR_EXECUTION` before any economics were inspected.
It found two pre-outcome issues: `Q0.90(DV[t-49:t-1])` did not specify its
quantile estimator/interpolation convention
(`FROZEN_Q90_CONVENTION_UNSPECIFIED`), and the then-generic monthly path
decided on the first completed new-month bar and filled on the following
session, adding an unintended extra session.

The scheduler issue was fixed separately and generically on production `main`
at `980c6b90b7f316bcec6a9466dfc9b0e1756cc748`. The candidate-neutral
`month_end_first_open` contract now maps the completed final exchange session
of month M to the first tradable open of M+1, with no later-session fallback.
Fresh canonical V3 replay remained exactly equal to its pre-fix baseline, and
no production strategy or configuration changed. This correctness primitive
is not evidence for the rejected alpha and must not be attributed to it.

Design V2 at `a39cf6dfc72a4a4aa92d95b3f760626e2ce216c6`
was a pre-outcome specification repair; the original V1 commit stayed
immutable and no economics had been seen. For ordered reference values
`x_(1) <= ... <= x_(49)`, the repository convention is
`j = 1 + floor(9 * (n-1) / 10)`. With `n = 49`, `j = 44`, so
`Q0.90 = x_(44)`, zero-based index 43, with no interpolation and no
deduplication. The repaired identity is
`AV49_D1_NORMALRET_M1_LONG / DESIGN_V2`.

Readiness V2 at `ec37b0f1671191f1842f5f952977bb60c772fa4b`
returned `G1_READY_FOR_FROZEN_ECONOMIC_TEST`. It processed 11,681,230 causal
bars through 2026-07-30 and excluded 5,602 partial 2026-07-31 rows. Across 115
month-end boundaries it reconstructed 1,730 eligible signals and 595
structural targets; first-open availability was 595/595 and ordinary targets
for SPY, SH, PSQ, and DOG were all zero. The runner replay was deterministic,
the independent verifier agreed exactly, and no economic output was inspected.

The one authorized frozen economic experiment ended at
`693b86a16bb7363e98be46aeb25d164c384fe9aa`. It was
`RESEARCH_RUN_VALID`, independent verification passed with zero issues, its
formal result was `RESEARCH_REJECT`, and its durable classification is
`ALPHA_REJECTED`. Full-period 1x evidence was net return 41.72%, CAGR 3.70%,
Sharpe 0.402, and maximum drawdown 15.07%; cost robustness remained positive
through 3x. V3 economically outperformed the candidate, but that comparison
was diagnostic only and did not determine rejection.

Frozen gates G1-G7 passed. G8 failed substantively because only 74.78% of
decisions held at least four names versus the frozen 90% minimum. Widening the
middle-40% band, lowering the abnormal-volume threshold, changing `top_n`,
cadence, or eligibility would be prohibited post-outcome retuning. G9 also
failed under its frozen wording: realized gross reached 61.14% and realized
cash fell to 38.86%, versus limits of gross <=60% and cash >=40%. Later
candidate-neutral clarification of target-versus-realized exposure semantics
does not retroactively change that failure.

The candidate produced positive standalone economics but failed the complete
conjunctive G1-G9 acceptance contract. It is not eligible for the canonical
5+1 complementarity experiment, and no retuning is authorized. Evidence is
preserved by:

- `archive/research/abnormal-volume-design-v1-2026-09`;
- `archive/research/abnormal-volume-readiness-v1-2026-09`;
- `archive/research/abnormal-volume-design-repair-v1-2026-09`;
- `archive/research/abnormal-volume-readiness-v2-2026-09`;
- `archive/research/abnormal-volume-economic-v1-2026-09`.

### MAX / lottery preference — `equity_max_lottery_design_v1`

The pre-outcome MAX design review ended at
`405103908acd7b872df2d9663fa8166e6d793020` with
`DESIGN_BLOCKED_DATA_OR_EXECUTION`. No economic candidate was frozen or run,
so this is a design-stage block rather than `ALPHA_REJECTED`.

The principal blockers were: the broad historical `US_EQUITY` universe is not
a point-in-time common-stock-only taxonomy and could make LOW-MAX select bond
or defensive ETFs, ETNs, or preferred-like products; the then-generic monthly
scheduler had the extra-session behavior later fixed independently; published
support was stronger for long-short/high-MAX underperformance than for a
six-name long-only LOW-MAX alpha; MAX substantially overlaps V3's residual-
volatility filtering and sizing; and liquid/scaleable replication evidence was
weaker. Evidence is preserved by
`archive/research/max-lottery-design-v1-2026-09`.

### Opportunistic insider purchases — `equity_opportunistic_insider_purchases_design_v1`

The original pre-outcome design at
`d08c94cd93a1f71e9f9b8bf21e3c726b73953f50` studied publicly disclosed
opportunistic insider purchases using the Cohen, Malloy, and Pomorski
classification concept. It returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` without inspecting a local economic
outcome. The modern large/liquid buy-only evidence was insufficient; the
headline 82 bp result is buy-minus-sell rather than the buy-only long leg; the
literature did not validate a top-one/one-slot expression; historical security
mapping was initially judged blocked; and Form 4 Code P alone does not prove an
open-market conviction purchase.

The dedicated source reassessment at
`e46e9ae0e1c124138c2143b30d93809a97f4282d` recovered compliant official SEC
access and obtained historical Form 4 data from 2013-Q1 through 2026-Q1. The
2026-Q2 archive remained unavailable in that run because of DNS/network access
and was recorded as missing. Contemporaneous SEC and repository identity
evidence made fail-closed historical security mapping more feasible than first
believed. From 2017-01 through 2026-03 the structural-only audit found 2,649
eligible transaction rows, 1,659 accessions, and 1,225 entity-month
opportunities. No subsequent return was calculated.

The reassessment returned `REASSESSMENT_CONFIRMS_DESIGN_REJECT`: the data-access
and mapping concern materially improved, but modern liquid-stock buy-only
evidence and a defensible top-one ranking rule still failed. The durable family
status remains `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`; no economic experiment
or retuning is authorized. Evidence is preserved by:

- `archive/research/opportunistic-insider-purchases-design-v1-2026-09`;
- `archive/research/opportunistic-insider-source-reassessment-v1-2026-09`.

#### Reusable SEC source-capability lesson

Future SEC ownership/event research may use the official historical Insider
Transactions datasets, accession identity, issuer and reporting-owner CIKs,
as-filed trading symbols, transaction fields, raw-filing provenance where
needed, and fail-closed historical mapping. This establishes a reusable source
capability, not that every Form 4 strategy is feasible.

### Cash-based operating profitability — `equity_cash_based_operating_profitability_design_v1`

The pre-outcome design at
`0f77021a2d6a0d08df76f0902da3354e90f95f65` returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`. No economic test or local forward
return was run. Modern large/liquid U.S. evidence for the exact CbOP long-only
effect was insufficient for a concentrated experiment, and diversified
academic portfolios do not validate six-stock or eventual one-stock
concentration.

SEC accession-vintage fundamentals are conceptually feasible, but exact
Compustat-to-XBRL accounting semantics create material fail-closed missingness.
The Apple raw-filing reconstruction reconciled value, context, sign, duplicate
handling, and arithmetic, yet exact CbOP remained `null`: the prepaid-expense
input was missing and the available accrued-liabilities concept was not proven
equivalent to `XACC`. Unknown components were not silently set to zero.

The reusable lesson is: SEC accession-vintage accounting panels are possible,
but Compustat factor definitions cannot be assumed to map one-to-one into SEC
XBRL. A missing tag is not automatically zero. The family is closed, with no
economics or retuning, and is preserved by
`archive/research/cash-based-operating-profitability-design-v1-2026-09`.

### Positive liquidity-shock continuation — `equity_positive_liquidity_shock_design_v1`

The pre-outcome design at
`15644b3c117535eb94a12eb5099d9d82d10cb2cf` returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`. No economic test or future-return
study was run. Modern large/liquid U.S. evidence was insufficient for a
concentrated long-only implementation; the fixed $20M liquidity floor removes
much of the small/illiquid population where the published anomaly is strongest;
the literature supports diversified portfolios rather than top-six/top-one
concentration; and broad historical `US_EQUITY` lacks comprehensive PIT product
classification.

Structural feasibility was not the blocker. The audit processed 11,681,230
canonical rows, and all 115/115 structural months had at least six
scanner-scoreable names. Those counts establish measurability and opportunity
supply, not alpha. The family is closed with no retuning and is preserved by
`archive/research/positive-liquidity-shock-design-v1-2026-09`.

### Second-wave pre-outcome lesson

Opportunistic insider purchases, cash-based operating profitability, and
positive liquidity shocks were all rejected before local outcomes were viewed.
This does not imply that the academic anomalies do not exist. The
repository-specific conclusion is that broad factor evidence often lacks the
alpha density per occupied slot required by ParayaBuck's concentrated
architecture. Recurring failure modes were diversified-only evidence,
small/illiquid concentration, weak modern liquid-stock long legs, no
literature-supported top-one/top-six expression, and substantial PIT semantic
burden for external data.

Future discovery should place greater weight on issuer-specific events,
naturally concentrated signals, slow-decaying public information, mechanisms
that identify few unusually strong opportunities, and daily next-open
compatibility. This record does not name or start a new candidate.

## Event-driven pre-outcome wave — 2026-09-22

### Schedule 13D activist stake disclosures — `equity_schedule_13d_activism_design_v1`

The pre-outcome design at
`c4e9e30039eb4bef8005fdb0700e15a99884070c` returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`. No candidate returns or other
economics were calculated, and no candidate or future economic protocol was
created. The short post-filing evidence was not sufficiently strong in a
modern, value-weighted, concentrated form; longer-run gains depended
materially on future outcomes, especially takeovers; and no filing-time
natural one-slot ranking rule was defensible.

The SEC structural audit covered 2017-01-03 through 2026-09-21 and counted
23,705 initial Schedule 13D rows and 82,369 amendment rows. These are filing
rows, not qualifying activist opportunities or alpha evidence. The 2024 regime
changed the initial deadline to five business days and the amendment deadline
to two business days; structured XML became mandatory on 2024-12-18. The
family remains a design-stage rejection, not `ALPHA_REJECTED`, and is preserved
by `archive/research/schedule-13d-activism-design-v1-2026-09`.

### Open-market share-repurchase announcements — `equity_share_repurchase_announcement_design_v1`

The pre-outcome design at
`73fe3e0065c46f813508a08013fb2198629d9bda` returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`. Modern large/liquid concentrated
long-only evidence was insufficient, no natural announcement-time top-one
ranking was established, and the long holding horizons in the supportive
literature implied substantial slot-time opportunity cost. A complete
historical first-public event corpus and security mapping were not established.
Future repurchase completion was not used to classify events. No candidate
economics or economic protocol was created. Evidence is preserved by
`archive/research/share-repurchase-announcement-design-v1-2026-09`.

### Positive management-guidance surprise — `equity_management_guidance_drift_design_v1`

The pre-outcome design at
`9648192713c5d881bf033e4d505780e70273a98a` returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`. A historical positive good-news long
leg exists, but a literature-faithful forecast surprise requires point-in-time
analyst consensus. No entitled immutable consensus panel was supplied, and no
honest free substitute was accepted. Independently of that data block, modern
U.S. liquid/concentrated evidence was insufficient and diversified evidence did
not establish within-tail top-one concentration. No local economic result or
future protocol was created. This is not merely "blocked by analyst data."
Evidence is preserved by
`archive/research/management-guidance-drift-design-v1-2026-09`.

### Earnings Announcement Return continuation — `equity_earnings_announcement_return_design_v1`

The pre-outcome design at
`1f7c461be32f11bb23a8c85a89f19621244a270f` returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`. Historical positive EAR long-leg
evidence exists, but the exact Brandt et al. benchmark requires historical
CRSP/Compustat-style size and book-to-market membership. Current canonical data
cannot reproduce it. An independently validated simpler historical definition
was not substituted for the materially different primary benchmark. Modern
U.S. evidence and top-six/top-one concentration
support were insufficient. The campaign resolved the 2007/2008 working-paper
discrepancy and pinned claims to the 22 January 2008 version. No local EAR,
future return, economics, candidate design, or future protocol was created.
Evidence is preserved by
`archive/research/earnings-announcement-return-design-v1-2026-09`.

### Event-wave lesson

Issuer specificity did not by itself establish post-disclosure alpha. Across
these four families, public events often realized much of their information
immediately; longer-run returns could depend on future outcomes unavailable at
entry; academic group-level effects did not establish top-one alpha density;
licensed expectation or benchmark data could be essential; and natural event
strength/ranking was critical. Future discovery should require meaningful
post-publication persistence, modern liquid-stock long-leg evidence, a natural
pre-existing concentration variable, manageable slot-time, and point-in-time
source feasibility. This cleanup does not propose a new candidate.

## Canonical crypto baseline maintenance

The crypto replay authority was consolidated on 2026-09-22 from
`maintenance/crypto-replay-parity-v1` at
`4b7eb4020896453a599c876092ff550216183d07`. Its evidence is preserved by the
annotated archive tag `archive/maintenance/crypto-replay-parity-v1-2026-09`.
Only durable documentation was consolidated; no runtime, strategy, allocation,
risk, PAPER or LIVE configuration changed.

The 455-decision / 388-fill control was a valid historical V2 replay but is
superseded as the current diagnostic input. The accepted causal execution
repair at `b135d73743ec2652f79b8c840df50bd3f67166e8` is now authoritative:
454 decisions, 406 fills, the same pinned source and universe, and independent
causal verification. Its physical order timing uses the first observed hourly
open strictly after order creation. See [canonical crypto baseline](canonical_crypto_baseline.md)
for source/data/config identity, hashes, costs, execution contract, limitations
and archived evidence. The repair did not change strategy parameters or
production PAPER/LIVE behavior. Its giveback interpretation is recorded below
under the corrected V3 audit; neither baseline maintenance nor this register
authorizes an exit change.

## Exit-audit closure — 2026-09-22

The equity audit and both canonical crypto audits concluded
`MIXED_PROFIT_RETENTION_BEHAVIOR`.

### Equity

Branch `research/position-exit-giveback-audit-v1`, final SHA
`4444147ae8e53a2b33b0648077ee2589f4920e5d`, is preserved by annotated tag
`archive/research/position-exit-giveback-audit-v1-2026-09`.
The persisted independent verification covers 908 equity episodes. Among
the 103 naturally closed normalized episodes reaching +10%, median profit retention was approximately
78.8%, and approximately 9.7% eventually closed negative. There was no evidence
that large equity winners systematically evaporate before exit.
Durable conclusion: `EQUITY_EXIT_RESEARCH_NOT_CURRENTLY_JUSTIFIED`.
Evidence: `research/position_exit_giveback_audit_v1/` at that tag, especially
`EQUITY_GIVEBACK_REPORT.md`, `EQUITY_INTERPRETATION.md`, and
`INDEPENDENT_VERIFICATION.json`.

The same V1 campaign's crypto conclusion remains
`AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`. That historical finding is preserved,
not rewritten; its crypto interpretation is superseded by canonical V2 below.

### Crypto canonical-baseline V2

Branch `research/crypto-exit-giveback-audit-v2`, final SHA
`4dfd63c80f5b79f14dccf3e9d7aa8890be6cd959`, is preserved by annotated tag
`archive/research/crypto-exit-giveback-audit-v2-2026-09`.
Canonical replay contained 455 decisions / 388 fills, with 57 independently
verified position episodes. All 15 naturally closed episodes reaching >=30%
MFE ended profitable; their median retention was approximately 87.15%.
Of 50 naturally closed positive-MFE episodes, 20 eventually closed negative. Higher giveback
was associated with hysteresis behavior; causality was not established.
No alternative exit was tested.
Its historical decision was `CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED`.
Evidence: `research/crypto_exit_giveback_audit_v2/` at that tag, especially
`AUDIT_DECISION.json`, `SUMMARY.json`, `INDEPENDENT_VERIFICATION.json`, and
`HYSTERESIS_GIVEBACK_AUDIT.md`. **V2 is superseded as input authority because
its canonical replay used the older non-causal execution path; V3 below
independently reconfirms the research conclusion.** V2 history remains archived.

### Crypto corrected causal V3 — 2026-09-24

Branch `research/crypto-exit-giveback-audit-v3`, final SHA
`d46338454cb7f8d59525d88fd775f98e2b927d45`, is preserved by annotated
`archive/research/crypto-exit-giveback-audit-v3-2026-09`. It uses only the
accepted causal canonical baseline at
`b135d73743ec2652f79b8c840df50bd3f67166e8` (454 decisions, 406 fills).
Independent verification passed for 56 position episodes: 54 natural closures
and two endpoint-open positions settled by precommitted liquidation.

Of 51 naturally closed episodes with positive MFE, 21 ultimately closed
negative. This remains a real descriptive concern. Fifteen naturally closed
episodes reached >=30% MFE; all 15/15 closed profitable, with median profit
retention **87.4143415252469%**. The corrected audit therefore did not find a
systematic collapse of larger marked winners. Behavior is
`MIXED_PROFIT_RETENTION_BEHAVIOR`; durable decision:
`CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED`.

MFE is hindsight diagnostic information, **not an executable exit signal**;
no peak was treated as capturable. Alternative exits tested: `NONE`—no
take-profit, trailing-stop, stop-loss or time-stop treatment was run. Keep exit
research closed unless future live/PAPER behavior or materially new evidence
justifies a separately frozen investigation. Evidence remains in
`research/crypto_exit_giveback_audit_v3/` behind the tag, especially
`FINAL_GIVEBACK_REPORT.md`, `AUDIT_DECISION.json`,
`GIVEBACK_DIAGNOSTICS.json`, `INDEPENDENT_EPISODE_VERIFICATION.json`, and
`OLD_VS_NEW_COMPARISON.md`. No production code or configuration changed.

### Governance

Current evidence does not justify changing V3 exits, crypto hysteresis,
take-profit behavior, or trailing stops. Reopen only if future live/PAPER
evidence materially changes, strategy semantics change, or a new pre-outcome
exit hypothesis is independently justified. MFE is hindsight, not capturable
profit. These are diagnostic conclusions, not exit-treatment experiments.
No strategy, risk, persistence, allocation, PAPER, or LIVE behavior changes.
Large audit artifacts and audit-specific tests remain on archive tags only.

## Security-master procurement closure — 2026-09-22

The three-branch commercial security-master investigation is closed by operator
governance decision, not by a validity finding. Durable classification:
`DEFERRED_EXTERNAL_DATA_NOT_WORTH_PROCUREMENT_BURDEN`. This is neither
`SOURCE_INVALID` nor `SECURITY_MASTER_PROBLEM_SOLVED`.

### Campaign chain

| Step | Branch | Final SHA | Outcome |
|---|---|---|---|
| Source audit | `research/security-master-source-audit-v1` | `7366ea51ac819de7772ef67cc650869248ea7d07` | `SECURITY_MASTER_SOURCE_LEAD_VALID_BUT_ENTITLEMENT_REQUIRED`; NYSE Group Security Master lead; vintage `VINTAGE_SEMANTICS_UNCLEAR`, price `COMMERCIAL_QUOTE_REQUIRED` |
| Entitlement prep | `research/security-master-entitlement-prep-v1` | `951e609042431cfbaad13cc590db4479151f290a` | `READY_TO_CONTACT_NYSE`; spec v4.1 revalidated, samples parse; vintage immutability, correction arrival, backtest/retention rights, pricing vendor-answer-only |
| Vendor outreach prep | `research/security-master-vendor-outreach-v1` | `2d5b2b3d36e84851182da7a8f7208be51138d697` | `OUTREACH_READY_AWAITING_OPERATOR_SEND_APPROVAL`; send-ready 12-question inquiry drafted |

NYSE Group Security Master was technically promising (documented history from
2015-12-16, daily full/delta files, non-reused Stock Number, broad taxonomy),
but original-vintage semantics stayed unproven from public documentation and
further proof required vendor contact, commercial negotiation, and waiting on
external responses. The operator intentionally declined that procurement burden.

### Outreach facts

`NO_VENDOR_CONTACT_MADE`. `NO_PURCHASE`. `NO_CONTRACT_ACCEPTED`.
`NO_TRIAL_STARTED`. No inquiry was sent; no external response exists or is
pending. No vendor inquiry is to be sent now.

### Standing limitation

Historical `US_EQUITY` remains unproven PIT common-stock-only; contamination by
ETFs, ETNs, preferreds, warrants, rights, units, and structured products is
possible. See [`equity_universe_limitations.md`](equity_universe_limitations.md).
Future candidates depending critically on unavailable historical taxonomy must
be penalized, restricted to an issuer-linked subset, or rejected/deferred — not
rescued by reopening commercial procurement.

Evidence is preserved by:

- `archive/research/security-master-source-audit-v1-2026-09`;
- `archive/research/security-master-entitlement-prep-v1-2026-09`;
- `archive/research/security-master-vendor-outreach-v1-2026-09`.

### Policy

Research now follows
[`public_data_research_policy_v1.md`](public_data_research_policy_v1.md):
public/free/easy-access sources only, until the documented exception rule is
explicitly satisfied.

## Production boundary

The cleanup merged no production code. Current `main` remains
`equity_residual_momentum_v3` in PAPER with LIVE disabled, and no research
campaign above is registered as a production strategy. The current broad
historical universe and role isolation are documented separately in
[`equity_universe_limitations.md`](equity_universe_limitations.md).

## Public-data discovery V3 and spin-off design closure — 2026-09-22

`public-data-alpha-discovery-v3` reported screening 32 families under the
[public/free-data policy](public_data_research_policy_v1.md) without an economic
peek. It shortlisted only `equity_spinoff_child_drift_v1`; the second slot was
intentionally withheld. Patent efficiency remained
`DEFER_EXTERNAL_DATA_BURDEN` because an adequate free point-in-time
assignee-to-ticker map was not established. Public FINRA short-interest
surprise was `REJECT_EVIDENCE`: value-weighted long-side support was weak, with
stability, publication-cadence and momentum-overlap concerns. No other family
cleared the joint evidence, concentration, execution and provenance screen.
Count caveat: the archived `LONG_LIST.md` actually contains 33 rows, while its
printed disposition counts sum to 34; five rows, rather than its stated six,
have `REJECT_CONCENTRATION`. The original 32-family headline is therefore a
campaign-reported count, **not independently reconciled**. No candidate was
added to the shortlist by this discrepancy. This is
`PUBLIC_DATA_DISCOVERY_V3_EXHAUSTED_AFTER_SINGLE_DESIGN_CANDIDATE`:
the campaign's one nominee has now completed design review. It is not a claim
that future public-data hypotheses cannot exist. Its detailed evidence is at
`research/public_data_alpha_discovery_v3/` behind
`archive/research/public-data-alpha-discovery-v3-2026-09`.

The ensuing `equity_spinoff_child_drift_v1` review returned
`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS` **before economics**. Original studies
support a historical diversified long leg in newly traded child shares, but
do not establish a modern liquid-stock top-six or top-one expression or a
natural, pre-entry rank. The literature-supported holding horizon remains
long; shortening it solely to free one of six shared account slots is not
justified. Issuer/SEC filings plausibly identify individual child securities,
but complete historical mapping, causal regular-way availability, liquid
six-slot supply and scanner attrition were not established. No local forward
return, CAR, P&L, Sharpe, CAGR, IC, or candidate backtest was calculated.
Evidence is at `research/spinoff_child_drift_design_v1/` behind
`archive/research/spinoff-child-drift-design-v1-2026-09`.

Do not reopen this candidate using `recency × liquidity`, an arbitrary shorter
hold, a cherry-picked subtype or local outcome-driven ranking. Reopening
requires materially new **external** child-long, liquid, post-entry and
concentration evidence before any economics. The durable lesson is that
issuer-specific public event records can solve part of the historical
security-taxonomy problem, but source feasibility alone is insufficient:
diversified event alpha does not imply six-name alpha density, long holds use
scarce slots, and a natural rank must exist before a backtest. Free/public
sources are workable, yet they narrow the evidence-qualified opportunity set;
data availability alone never warrants testing a candidate.

After this closure, `equity_residual_momentum_v3` and
`crypto_low_variance_hysteresis_v1` remain the equity and crypto incumbents;
no additional equity alpha is accepted. Short-term residual reversal and
information discreteness remain standalone-valid but portfolio-rejected.
Overnight reversal, 52-week high and abnormal volume remain economic rejects.
MAX/lottery, opportunistic insider purchases, cash-based operating
profitability (CbOP), positive liquidity shock, Schedule 13D activism, share
repurchase announcements, management guidance, earnings-announcement return
and spin-off child drift remain design rejects or blocks, not negative
economic findings. Commercial security-master procurement stays
`DEFERRED_EXTERNAL_DATA_NOT_WORTH_PROCUREMENT_BURDEN`.

`NO_UNTESTED_SHORTLIST_CANDIDATE_REMAINS_FROM_PUBLIC_DATA_DISCOVERY_V3`.
