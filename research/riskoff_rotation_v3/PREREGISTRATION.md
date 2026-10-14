# riskoff_rotation_v3 pre-registration (low-turnover defensive legs)

## Question
Can a low-turnover version of R4 keep R4's benefit? Fixed arms: L1, L2, L3 on v2 audited code.

## Arms
C0 = production E2 exactly.
L1 = R4 but defensive leg (bond/gold) is resized only when |target - held| > 10 pp (forced entries/exits/switches still trade in full).
L2 = L1 plus defensive asset choice (bond/gold/cash) is re-evaluated only on first session of each month (E2 switch stays daily; if E2 turns off mid-month, choice made that day and held to month end).
L3 = L2 but defensive leg held at fixed weight 0.5 (no vol sizing).

Data, E2 signal, QLD*, BOND10*, GOLD, bills, costs, bootstrap (seed 20261011, block-20), OOS windows same as v2.

## Gates
G1-G8 as v2 (main window, subperiods, bootstrap, OOS). G9 = same doubled-cost stress as v2: each arm's main-window base+calibrated m=2 CAGR must beat C0 at doubled cost.

Selection: among passing arms, highest pooled OOS bootstrap median Δ. Report turnover and sells/yr.
