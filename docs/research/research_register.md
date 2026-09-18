# Research register

This is the compact, durable index of completed equity research and governance
campaigns. The register records decisions and provenance; large JSON, replay
curves, compressed audits, runners, and rejected candidate implementations
remain outside production `main` behind the archive tags listed here.

No entry below authorizes automatic PAPER/LIVE promotion. Production changes
are listed explicitly and are separate from research conclusions.

## Decision index

| Research ID | Original branch | Final recorded SHA / date | Validity | Verdict | Production changed? | Closed / superseded-by | Archive tag |
|---|---|---|---|---|---|---|---|
| `equity_short_term_residual_reversal_v1` | `research/residual-reversal-local-v1` (deleted before this audit) | `a3a54cff64a9312300ef700f2fe9409435a064d3` / 2026-09-16 | `CORRECTED_REPLAY_VALID` | `RESEARCH_REJECT` | No | Closed; superseded the original invalid campaign; no resurrection | `archive/research/residual-reversal-local-v1-2026-09` |
| `equity_spy_defensive_overlay_v1` | `research/spy-defensive-overlay-v1` | `da9a99b6e23df880fb64afa78d1f38aa70edd5ec` / 2026-09-17 | Valid; independent verification passed | `RESEARCH_REJECT` | No | Closed | `archive/research/spy-defensive-overlay-v1-2026-09` |
| `equity_v3_volatility_managed_v1` | `research/v3-volatility-managed-v1` | `9dbe4bd78b3276159dd089e4edf4e15b9ed1cb01` / 2026-09-17 | Valid; structural replay and verifier passed | `RESEARCH_REJECT` | No | Closed | `archive/research/v3-volatility-managed-v1-2026-09` |
| `equity_overnight_intraday_design_v1` | `research/overnight-intraday-design-v1` | `7ec53efecc4d37cab17d0d96f194389b4513a69b` / 2026-09-17 | Design evidence valid; no economic result in this campaign | `DESIGN_READY_FOR_FROZEN_ECONOMIC_TEST` | No | Closed; superseded by the overnight economic result | `archive/research/overnight-intraday-design-v1-2026-09` |
| `equity_overnight_reversal_v1` | `research/overnight-reversal-v1` | `41e1c9026bb34bbe81f98577844c9eca0523da8a` / 2026-09-18 | `RESEARCH_RUN_VALID`; independent verification passed | `RESEARCH_REJECT` | No | Closed; supersedes the design-only decision | `archive/research/overnight-reversal-v1-2026-09` |
| `equity_universe_integrity_audit_v1` | `maintenance/equity-universe-integrity-v1` | `180fd12e7bafb5943856229e85e305d5d9906511` / 2026-09-18 | Structural audit; no economic backtest | `UNIVERSE_CONTRACT_AMBIGUOUS_OPERATOR_DECISION_REQUIRED` | No | Closed; superseded/refined by security-master source research and the limitations document | `archive/research/equity-universe-integrity-v1-2026-09` |
| `equity_security_master_source_v1` | `research/equity-security-master-source-v1` | `70738106760eec86908b846823b25bd6964b5c0c` / 2026-09-18 | Source study valid; historical PIT coverage not proven | `SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED` | No | Closed; informs future source acquisition, no production integration | `archive/research/equity-security-master-source-v1-2026-09` |
| `equity_v3_reserved_role_corrected_baseline_v1` | `research/v3-reserved-role-corrected-baseline-v1` | `d34efd3b57cc22c766c5351c12201089bdf55480` / 2026-09-18 | Corrected replay internally verified under original protocol | `CORRECTED_V3_BASELINE_FAILED` (superseded) | No; role fix was already on `main` | Closed; superseded by the repaired protocol decision below | `archive/research/v3-reserved-role-corrected-baseline-v1-2026-09` |
| `equity_v3_baseline_protocol_repair_v1` | `research/v3-baseline-protocol-repair-v1` | `96910de6a54a3d3154996cad865308c3fff127ed` / 2026-09-18 | V2 validity, parity, and independent verification passed | `CORRECTED_V3_BASELINE_VALID` | No runtime/configuration change | Closed; current canonical V3 baseline/control | `archive/research/v3-baseline-protocol-repair-v1-2026-09` |

The branch `maintenance/reserved-equity-role-isolation-v1` was an exact alias
of current `main` (`bdfa55ea...`) with no unique commit or evidence. It is not
a research campaign and is represented in the archive inventory only.

## Completed campaign records

### Residual reversal — `equity_short_term_residual_reversal_v1`

The original campaign was invalidated and replaced by the corrected replay at
`a3a54cff64a9312300ef700f2fe9409435a064d3`. The corrected replay was valid,
but the final verdict was `RESEARCH_REJECT`.

The standalone reversal had positive economics at 1x (net return 88.49%, CAGR
6.84%, Sharpe 0.6483, max drawdown 17.78%), but its capital-neutral treatment
did not provide the required contribution: CAGR, Sharpe, and Sortino did not
improve within the frozen tolerance. The six-position account constraint
created allocation-capacity shortfall on 495/495 treatment decisions, with
substantial unused sleeve capacity. The original campaign is not to be
resurrected; the corrected replay is the durable result and is preserved by
the archive tag above. Production did not change.

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

## Production boundary

The cleanup merged no production code. Current `main` remains
`equity_residual_momentum_v3` in PAPER with LIVE disabled, and no research
campaign above is registered as a production strategy. The current broad
historical universe and role isolation are documented separately in
[`equity_universe_limitations.md`](equity_universe_limitations.md).
