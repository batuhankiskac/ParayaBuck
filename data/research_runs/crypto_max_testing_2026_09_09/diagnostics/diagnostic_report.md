# Crypto maximum-testing retrospective diagnostics — 2026-09-09

This is a deterministic research-only diagnostic. The Coin Metrics reference response and every native feature row lack row-level `available_at`; lagged reconstructions are therefore not causal PIT evidence. No result admits, promotes, or makes an economic eligibility decision.

Protocol SHA: `0c779d78d593f769b6e9d1065dff8f6086e2a2fa9f6f470047ef87e6d78a0823`  
Results SHA: `b001fc2a5961f5ff73e27061a7dc0ccab85f801b7a0c40e2fc7719dc7b1e289d`  
Diagnostic arms: `162` (lags 1/3/7 × costs 1x/2x/3x × full/recent/holdout).

| Card | Status | Signal rows | Arms | Economic/admitted |
|---|---|---:|---:|---|
| K01 | RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT | 5538 | 27 | false / 0 |
| K02 | NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC | 0 | 0 | false / 0 |
| K03 | RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT | 5478 | 27 | false / 0 |
| K04 | RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT | 5536 | 27 | false / 0 |
| K05 | NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC | 0 | 0 | false / 0 |
| K06 | NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC | 0 | 0 | false / 0 |
| K07 | NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC | 0 | 0 | false / 0 |
| K08 | NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC | 0 | 0 | false / 0 |
| K09 | RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT | 2444 | 27 | false / 0 |
| K10 | NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC | 0 | 0 | false / 0 |
| K11 | RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT | 1 | 27 | false / 0 |
| K12 | NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC | 0 | 0 | false / 0 |

Representative full-window result (lag=1, cost=1x; all other grid arms remain machine-readable in `diagnostic_results.json`):
- K01: cagr=0.11895233167084451, max_drawdown=-0.40842506003355905, tail_p05=-0.02394254832246312, turnover=46.0, positive_day_fraction=0.13525305410122165, bootstrap_ci=(-0.0007955760883827188,0.0015776937685330363), placebo_p=0.3798449612403101, v3_down_mean=0.000929470056296451, overlay_minus_v3_cagr=0.006012761362992336, cap_violating_days=297
- K03: cagr=-0.43609892371637127, max_drawdown=-0.8423588548289731, tail_p05=-0.042262938039630216, turnover=529.0, positive_day_fraction=0.3394415357766143, bootstrap_ci=(-0.002780583306295216,0.000387745276564368), placebo_p=0.627906976744186, v3_down_mean=-0.0003174787107019719, overlay_minus_v3_cagr=-0.028367792616124543, cap_violating_days=821
- K04: cagr=-0.19356348826922143, max_drawdown=-0.6782935955018496, tail_p05=-0.04522464803415921, turnover=2.0, positive_day_fraction=0.4973821989528796, bootstrap_ci=(-0.0018526574607671234,0.0013758267415302544), placebo_p=0.43410852713178294, v3_down_mean=0.0007435145856762363, overlay_minus_v3_cagr=-0.007618299260511385, cap_violating_days=1146
- K09: cagr=-0.1378602456528112, max_drawdown=-0.42654762288458803, tail_p05=-0.007217513882598502, turnover=219.0, positive_day_fraction=0.05759162303664921, bootstrap_ci=(-0.0008382910729053483,0.00011805435647248462), placebo_p=0.7441860465116279, v3_down_mean=-7.633848086843651e-05, overlay_minus_v3_cagr=-0.011090323905442512, cap_violating_days=151
- K11: cagr=0.0, max_drawdown=0.0, tail_p05=0.0, turnover=0.0, positive_day_fraction=0.0, bootstrap_ci=(0.0,0.0), placebo_p=None, v3_down_mean=0.0, overlay_minus_v3_cagr=-0.004076310612631362, cap_violating_days=0
- K09 funding-short-history: signal_rows=300, cagr=-0.03196342529088836, max_drawdown=-0.15198152306851354, active_days=48, cap_violating_days=48

K09 has a separate short-funding-history branch; it is not merged with the basis/OI branch. K06, K10, and K12 have no defensible retrospective arm because the exact causal fields or PIT join are absent. K02, K05, K07, and K08 remain blocked by the frozen source gates.

The overlay is fixed at 95% current V3 daily NAV plus 5% candidate diagnostic return. Shared-capital checks conservatively assume six incumbent V3 holdings, so any active candidate position is reported as a position-cap violation; this is a feasibility diagnostic, not an allocation recommendation.
