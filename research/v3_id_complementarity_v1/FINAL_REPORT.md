# Final combination decision

Decision: `COMBINATION_REJECT`
Run validity: `COMBINATION_RUN_VALID`

## Why standalone RESEARCH_REJECT was not dispositive

The prior standalone test answered replacement of V3's six slots. This campaign answers whether one frozen ID slot adds value beside V3 under a matched reserved-slot cash control.

## Frozen 5+1 combination policy

Policy and hash were committed before B/C economics. B and C use a 5-slot inverse-volatility V3 core at 0.4583333333333333 and a 0.0916666666666667 reserved satellite slot; C owns the slot and B holds it in cash.

## Production/control identity

Control A is canonical `equity_residual_momentum_v3`; main and PAPER remain unchanged and LIVE remains disabled.

## Control A

1x full: net 0.975529; CAGR 0.073693; Sharpe 0.687928; Sortino 0.883963; max DD 0.204359; Calmar 0.360606.
1x development: net 0.609781; CAGR 0.100015; Sharpe 0.876975; Sortino 1.068102; max DD 0.155323; Calmar 0.643918.
1x validation: net 0.240146; CAGR 0.048196; Sharpe 0.489019; Sortino 0.670635; max DD 0.187039; Calmar 0.257678.

## Control B

1x full: net 0.553304; CAGR 0.047066; Sharpe 0.495623; Sortino 0.586194; max DD 0.184392; Calmar 0.255250.
1x development: net 0.327764; CAGR 0.058403; Sharpe 0.607941; Sortino 0.671768; max DD 0.176576; Calmar 0.330755.
1x validation: net 0.183501; CAGR 0.037533; Sharpe 0.402003; Sortino 0.511509; max DD 0.136704; Calmar 0.274558.
B is the matched five-slot V3 core plus the frozen reservation held in cash.

## Treatment C

1x full: net 0.508135; CAGR 0.043844; Sharpe 0.414914; Sortino 0.500248; max DD 0.228519; Calmar 0.191861.
1x development: net 0.278315; CAGR 0.050391; Sharpe 0.465940; Sortino 0.525477; max DD 0.228519; Calmar 0.220511.
1x validation: net 0.201325; CAGR 0.040930; Sharpe 0.392561; Sortino 0.511889; max DD 0.148756; Calmar 0.275151.
C appends exactly the frozen 0.0916666666666667 satellite allocation when the reservation is expressible.

## B/C V3-core parity

The independent parity artifact reports `595` union-event comparisons with `passed=True`. B and C core target maps are identical before C's satellite is appended.

## ID incremental value versus B

Full 1x C versus B net return: 0.508135 versus 0.553304; validation: 0.201325 versus 0.183501.
Independently attributed ID net P&L is 4844.72 full and 1358.82 validation, but the full-period portfolio comparison fails G2.

## Combined contribution versus A

C has 0 full-period risk-adjusted improvements versus A under the frozen tolerances; max-drawdown and the remaining risk-adjusted measures are recorded in METRICS.json. G3 therefore fails.

## Development

A 1x: net 0.609781; CAGR 0.100015; Sharpe 0.876975; Sortino 1.068102; max DD 0.155323; Calmar 0.643918.
B 1x: net 0.327764; CAGR 0.058403; Sharpe 0.607941; Sortino 0.671768; max DD 0.176576; Calmar 0.330755.
C 1x: net 0.278315; CAGR 0.050391; Sharpe 0.465940; Sortino 0.525477; max DD 0.228519; Calmar 0.220511.

## Validation

A 1x: net 0.240146; CAGR 0.048196; Sharpe 0.489019; Sortino 0.670635; max DD 0.187039; Calmar 0.257678.
B 1x: net 0.183501; CAGR 0.037533; Sharpe 0.402003; Sortino 0.511509; max DD 0.136704; Calmar 0.274558.
C 1x: net 0.201325; CAGR 0.040930; Sharpe 0.392561; Sortino 0.511889; max DD 0.148756; Calmar 0.275151.
G4 passes: C is positive, beats B on validation net return and CAGR, and improves at least one risk-adjusted measure versus A within the frozen drawdown tolerance.

## 1x/2x/3x cost stress

1x: A net 0.975529, B net 0.553304, C net 0.508135, C Sharpe 0.414914, ID net P&L 4844.72.
2x: A net 0.875323, B net 0.483176, C net 0.449476, C Sharpe 0.381027, ID net P&L 4894.15.
3x: A net 0.780506, B net 0.415528, C net 0.382036, C Sharpe 0.339883, ID net P&L 4798.13.
G5 fails because the stressed full-period C-versus-B comparisons do not satisfy the frozen requirements, even though the attributed satellite P&L remains positive.

## Satellite exposure and utilization

The candidate produced 114 valid reservations; 103 obtained a nonzero C target and 11 were documented risk-overlay zeros. Unexplained zeros: 0. G6 passes.

## Source P&L attribution

At 1x C, V3-core net P&L is 45968.77 after 5552.49 costs; ID-satellite net P&L is 4844.72 after 199.09 costs. Attribution is from the shared account ledger, not a blended NAV sleeve.

## Turnover economics

A one-way annualized turnover is 17.499789; C is 16.263590. Satellite full/validation/2x costs are below the corresponding gross contributions, so G7 passes.

## Concentration

The satellite spans 32 symbols; the largest absolute-symbol P&L fraction is 0.332964; leave-one-largest net P&L is 2701.81. G8 passes.

## Diversification diagnostics

The descriptive A/C daily-return correlation is 0.778175 over 2657 common observations; 54 reservation symbols occurred. These diagnostics are not acceptance gates.

## Frozen G1–G9 table

* G1_validity: PASS
* G2_id_incremental_value_vs_B: FAIL
* G3_full_portfolio_contribution_vs_A: FAIL
* G4_validation_contribution: PASS
* G5_cost_robustness: FAIL
* G6_satellite_deployability: PASS
* G7_turnover_economics: PASS
* G8_concentration: PASS
* G9_architecture_production_safety: PASS

## Final interpretation

`ALPHA_VALID_PORTFOLIO_REJECTED`

## Production state

No production, PAPER, LIVE, configuration, crypto, or broker-order state changed.

## Files

All raw arm results, source attribution, parity, exposure, diagnostics, verification, and gate inputs are in this directory.

## Final research branch SHA

`cb1b554a0d835e4664a1cd18de09a2319c696442`
