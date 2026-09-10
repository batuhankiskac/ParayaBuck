# Zero-wait defensive exposure overlay results — 2026-09-10

Economic execution completed after freeze pin validation. Five formulas, full/recent/single-use holdout windows, and 1x/2x/3x cost cells were evaluated with the exact V3 target path. No production, PAPER, or LIVE mutation occurred.

The machine-readable output is `data/research_runs/zero_wait_overlays_2026_09_10/results.json`. Daily NAV is `data/research_runs/zero_wait_overlays_2026_09_10/daily_nav.csv` and the exposure ledger is `data/research_runs/zero_wait_overlays_2026_09_10/exposure_ledger.jsonl`.

The primary inferential cell is the single-use true holdout at 2x. It reports paired V3-down-day loss reduction, drawdown, daily CVaR, opportunity cost, turnover, bootstrap, circular permutation placebo, and native one-decision schedule placebo for every candidate. Holm correction is applied to the predeclared 20-cell family.

No best arm is selected automatically. Any gate failure, capacity failure, or source drift blocks promotion.
