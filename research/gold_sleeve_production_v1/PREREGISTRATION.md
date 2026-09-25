# Gold + dividend sleeve production validation v1 — pre-registration (2026-09-25)

Written before any run. Baseline P = shipped `equity_residual_momentum_v3_dividend_sleeve_v1`
(V3 core 0.40 + SCHD/VIG/DGRO 0.15), archived runs at tag
`archive/research/dividend-sleeve-production-v1-2026-09` (P 1x 7.22% CAGR, Sharpe 0.780,
MaxDD 17.2%, 2022 −12.5%, validation 2022–2026 CAGR 4.23%).

Arms (production code path, equal-weight five-ETF sleeve SCHD/VIG/DGRO/GLD/OUNZ, config-only change;
XLRE/XLC dropped from the unused static fallback list to keep the IEX 30 limit; V3 keeps 21 ranked slots):

| Arm | V3 core | Sleeve | Per ETF | Dividend | Gold |
|---|---:|---:|---:|---:|---:|
| G1 | 0.40 | 0.15 | 0.03 | 0.09 | 0.06 |
| G2 | 0.35 | 0.20 | 0.04 | 0.12 | 0.08 |
| G3 | 0.30 | 0.25 | 0.05 | 0.15 | 0.10 |

Gates (each against P at the same harness):
- H1 accounting invariants; H2 every stream ≤ 30; H3 non-sleeve slot claims ≤ 8.
- H4 full CAGR ≥ P; H5 full Sharpe ≥ P; H6 max drawdown ≤ P; H7 2022 return ≥ P.
- H8 CAGR ≥ P at 2x and 3x costs.
- H9 validation-window (2022–2026) CAGR ≥ P.
Selection: among arms passing every gate, the highest full-period Sharpe. If none passes,
production is unchanged.
