# Static concentration analysis

This document uses only configured ceilings and hypothetical target weights. It does not use historical holdings, prices, or outcomes. Inputs: equity sleeve 0.55, crypto sleeve 0.05, gross 0.60, cash minimum 0.40, equity per-name 0.10, crypto per-name 0.05, equity `top_n: 8`, crypto `top_n: 2`, and shared count 6 (`config/settings.yaml:143-184`). See `STATIC_CAPACITY_GEOMETRY.csv` for caps 4, 5, 6, 7, 8, 10, and 12 under 0, 1, and 2 crypto names.

## Meaning of the figures

For global cap `C` and crypto names `K`, max possible equity names are `min(8, C-K)`. An **upper bound**, not a forecast or guaranteed target, is equity `min(0.55, 0.10 × equity_names)` plus crypto 0.05 if `K > 0`. The crypto 0.05 is split equally across two names in the two-name illustration. Count-only unused equity sleeve is `0.55 − equity_upper_bound`. The other 0.05 gross gap when no crypto is present reflects an absent crypto sleeve, not an effect of the count cap.

HHI is `Σ w_i²`, with weights measured against total account equity. Its equal-weight value at the specified maximal expressible sleeve weights is a **minimum conditional on those weights and active names**, by convexity. The account HHI can appear lower when gross is forcibly lower, so the table also shows invested-capital-normalized HHI, `Σ w_i² / gross²`. Neither number includes cash as a name, correlations, or realized holdings.

| Global cap; two crypto slots | Equity names | Equity upper bound | Gross upper bound | Equal-weight account HHI | Equal-weight invested HHI | Highest permitted single-name target |
|---:|---:|---:|---:|---:|---:|---:|
| 6 | 4 | 0.40 | 0.45 | 0.04125000 | 0.20370370 | 0.10 equity; 0.025 each crypto under equal two-name sizing |
| 8 | 6 | 0.55 | 0.60 | 0.05166667 | 0.14351852 | 0.10 equity; 0.025 each crypto under equal two-name sizing |
| 10 | 8 | 0.55 | 0.60 | 0.03906250 | 0.10850694 | 0.10 equity; 0.025 each crypto under equal two-name sizing |

The count ceiling does **not** guarantee the equal-weight illustration. A policy allowing up to eight equities also permits only six, five, or fewer selected names and may therefore have concentration near the six-name case. At full 0.55 equity deployment, six equal equities would each be 0.0916667; eight equal equities would each be 0.06875. The equity per-name ceiling remains 0.10 in every case. With two crypto names at 0.025 each, the minimum account HHI under full sleeve deployment is 0.05166667 for six equities and 0.03906250 for eight equities. The supremum HHI permitted by name caps at the same full sleeve exposure is approximately `5×0.10² + 0.05² + 2×0.025² = 0.05375`, approached if the remaining nominal names have arbitrarily small weights. Thus a larger cap reduces the *minimum possible* concentration, not the worst concentration permitted by the weight rules.

With no crypto, six equity names can express 0.55 (equal-weight account HHI 0.05041667), while eight can express the same 0.55 with equal-weight HHI 0.03781250. With two crypto names and cap six, four equity names at the per-name ceiling express only 0.40, and the combined target is at most 0.45. The lower account HHI in that case is a consequence of unused target exposure, not proof of safer diversification.

Actual V3 sizing is **inverse residual volatility**, normalized over selected names and capped at a raw 0.10 with no residual redistribution (`src/parayabuck/portfolio/equity_residual_momentum.py:563-569`; `src/parayabuck/portfolio/momentum.py:49-69`). Its target ranks decide which names survive a count trim (`src/parayabuck/execution/portfolio.py:312-335`); the overlay then enforces sleeve/gross limits. Unequal residual volatilities, eligibility, and other gates can produce weights below these ceilings. Crypto's two-name target is equal-weight before the 0.05 sleeve is applied (`src/parayabuck/portfolio/crypto_low_variance_hysteresis.py:58-85`; `src/parayabuck/execution/portfolio.py:343-375`). No inference about actual target distribution is made here.
