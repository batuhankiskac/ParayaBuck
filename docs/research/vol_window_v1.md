# vol_window_v1 — volatility window & asymmetric estimator screen

Verdict: **FAIL** (no arm passes all gates; production E2 (C0) stays unchanged).

Idea: production sizes by vol20 (20-session realized vol). ewma_band_v1 tested RiskMetrics EWMA and failed G3/G5. This screen tested the vol window length (10/40/60), an asymmetric max(vol20,vol60) estimator, and a downside semi-deviation estimator on top of E2. Arms: C0 (control), V10, V40, V60, VMAX, SEMI. Resize band left at production 0.5 pp throughout; signal and engine unchanged.

## Method

- Control C0 reproduces ewma_band_v1's control row: 14.67% base / 14.10% calibrated CAGR (m=2), 14.95% (m=1), 49.56% max DD (harness PASS).
- All estimators computed on the instrument's (QLD*) daily simple returns, annualized by sqrt(252), as `wants_for` and vt(v)=min(1,0.35/v).
- VMAX = elementwise max(vol20, vol60). SEMI = sqrt(2 * 252 / 20 * sum min(r,0)^2) over 20 returns.
- Gates G1-G8 reused from ewma_band_v1; G9 turnover <= 2 x C0.
- Main window 2000-03-09 -> 2026-09-29; block-20 bootstrap (seed 20261011, 5,000 paths); OOS six windows + pooled bootstrap (10,000 paths) as in ewma_band_v1.

## Main window (base m=2)

| Arm | CAGR | max DD | Calmar | 2017+ CAGR | turn/yr | boot20 median (arm-C0) |
|---|---|---|---|---|---|---|
| C0 | 14.67% | 49.56% | 0.296 | 32.44% | 5.97 | +0.000 pp |
| V10 | 14.70% | 46.84% | 0.314 | 32.95% | 7.66 | +0.384 pp |
| V40 | 14.82% | 47.21% | 0.314 | 31.15% | 5.08 | -0.652 pp |
| V60 | 14.06% | 44.29% | 0.317 | 28.83% | 4.90 | -1.132 pp |
| VMAX | 13.84% | 43.44% | 0.319 | 28.65% | 5.34 | -0.550 pp |
| SEMI | 13.38% | 53.60% | 0.250 | 30.44% | 6.18 | -0.619 pp |

Calibrated-financing and m=1 rows are in `RESULTS.json` / `SUMMARY.txt`. V10 alone passes G1/G2/G3/G4/G6/G9 on the main window; V40 raises base CAGR but fails G3 and G5; V60/VMAX/SEMI fail G1/G3/G5; SEMI also fails G2 (max DD 53.6% vs 49.6%).

## OOS (calibrated m=2)

Median arm-C0 CAGR over six windows: V10 -0.026 pp, V40 +0.130 pp, V60 -0.021 pp, VMAX -0.181 pp, SEMI +0.049 pp. DD differences are all within the gate's 3 pp in >=5/6. Pooled bootstrap medians: V10 -0.019 pp, V40 +0.115 pp, V60 +0.176 pp, VMAX -0.036 pp, SEMI -0.001 pp.

## Gates

| Arm | G1base | G1cal | G2 | G3 | G4 | G5base | G5cal | G6 | G7 | G8 | G9 | pass_all |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V10 | P | P | P | P | P | P | P | P | F (median -0.026 pp, 0/..? median below 0) | F | P | FAIL |
| V40 | P | P | P | F | P | F | F | P | P | P | P | FAIL |
| V60 | F | F | P | F | F | F | F | F | F | P | P | FAIL |
| VMAX | F | F | P | F | F | F | F | F | F | F | P | FAIL |
| SEMI | F | F | F | F | F | F | F | F | F | F | P | FAIL |

V10 fails G7/G8 by a hair (OOS median -0.026 pp, pooled bootstrap -0.019 pp). V40 fails G3 (2017+ CAGR 31.15% vs C0 32.44%, -1.29 pp) and G5 (bootstrap median -0.65 pp on base, -0.65 calibrated). No arm passes all gates; selection = none.

## Conclusion

Shorter vol (10-session) is in-sample and bootstrap positive on the main window with modestly lower drawdown, but its edge does not survive the OOS / pooled-bootstrap median tests. Longer windows and asymmetric estimators do not beat production E2 either. Keep production E2 unchanged.
PY
echo done