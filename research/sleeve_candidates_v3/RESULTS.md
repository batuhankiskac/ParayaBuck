# Sleeve candidates v3 — results (2026-09-25)

Verdict: **STAGE_B_PASS for `dividend_growth_v1` and `gold_v1b`** (declared
post-hoc structural variant of `gold_v1`); every other candidate fails. Per
`PREREGISTRATION.md` a pass authorizes a production-integration *design* only,
never a production change. Machine-readable outputs: `RESULTS_STAGE_A.json`,
`stage_b/RESULTS_STAGE_B.json`; Stage B runs in `stage_b/runs/` (harness:
`research/sleeve_candidates_v2/stage_b/`, `--campaign v3`).

## Stage A — summed sleeve screen (1x costs)

Control A (shipped): CAGR 6.71%, Sharpe 0.657, max DD 19.61%, 2022 −15.5%.
Cash control B: CAGR 5.10%, Sharpe 0.663.

| Candidate | CAGR | Sharpe | Max DD | 2022 | Holm p | Stage A |
|---|---:|---:|---:|---:|---:|---|
| all_weather_v1 | 6.29% | 0.753 | 17.85% | −13.7% | 0.060 | pass |
| balanced_60_40_v1 | 6.38% | 0.733 | 18.26% | −14.2% | 0.193 | FAIL (G8) |
| gold_v1 | 7.28% | 0.844 | 17.31% | −12.0% | 0.148 | pass |
| equal_weight_us_v1 | 6.84% | 0.720 | 18.88% | −14.1% | 0.710 | pass |
| dividend_growth_v1 | 7.45% | 0.794 | 17.37% | −12.7% | 0.148 | pass |
| hedged_equity_v1 | 6.13% | 0.738 | 15.64% | −11.9% | 0.172 | FAIL (G3, G8) |
| covered_call_v1 | 6.25% | 0.688 | 18.28% | −14.2% | 0.711 | FAIL (G2, G9a) |
| global_min_vol_v1 | 6.40% | 0.719 | 18.34% | −13.8% | 0.620 | pass |
| momentum_quality_v1 | 7.42% | 0.752 | 19.77% | −14.7% | 0.216 | FAIL (G2) |
| small_cap_v1 | 6.63% | 0.677 | 19.88% | −14.6% | 0.711 | FAIL (G2, G9a) |
| treasury_trend_v1 | 5.39% | 0.700 | 16.35% | −12.3% | 0.711 | FAIL (G2, G3, G4, G9a, G9c) |
| short_treasury_sweep_v1 (X) | 7.10% | 0.692 | 20.48% | −16.1% | 0.000 | FAIL (XG3, XG6: duration loss in 2022) |
| gold_v1b (post-hoc) | 7.28% | 0.845 | 17.30% | −12.0% | 0.149 | pass |

Pre-registered Stage B selection (top two S passers by Sharpe): `gold_v1`,
`dividend_growth_v1`.

## Stage B — shared ledger (1x costs)

Fresh arm A reproduced the v1 archive hashes; A: CAGR 6.69%, Sharpe 0.675,
max DD 19.41%, validation Sharpe 0.475, 2022 −15.27%, ending NAV 185.8k.

| Candidate | CAGR | Sharpe | Max DD | Calmar | Val Sharpe | 2022 | Ending NAV 1x/2x/3x | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|---|
| gold_v1 | 7.14% | 0.834 | 18.10% | 0.394 | 0.638 | −13.0% | 193.4k / 183.8k / 176.7k | FAIL (S1 only) |
| dividend_growth_v1 | 7.00% | 0.767 | 18.22% | 0.384 | 0.558 | −13.5% | 191.1k / 181.0k / 168.3k | **PASS** |
| gold_v1b | 7.28% | 0.858 | 18.10% | 0.403 | 0.732 | −13.0% | 196.0k / 186.4k / 174.6k | **PASS** |

Sleeve P&L per $100k (1x): dividend growth SCHD 11.9k, DGRO 8.9k, VIG 8.4k;
gold GLD 14.2k, OUNZ 14.3k.

### Why `gold_v1b` exists (declared post-hoc)

`gold_v1`'s invariance arm did not reproduce A (174.8k vs 185.8k): IAU is
inside V3's own observed universe (V3 saw it in 121 of 495 decisions), so
pinning it for the sleeve changes V3's inputs. `gold_v1b` replaces IAU with
OUNZ, a physically backed gold ETF V3 never observes; the economic exposure is
unchanged (Stage A results are identical to three decimals) and its
invariance arm reproduces A exactly. The variant was added after seeing the
S1 failure and is labelled accordingly.

## Year by year (shared ledger, 1x)

| Year | A | dividend_growth_v1 | gold_v1b |
|---|---:|---:|---:|
| 2017 | 12.8% | 12.8% | 10.6% |
| 2018 | −2.3% | −2.0% | −1.3% |
| 2019 | 10.8% | 12.2% | 10.7% |
| 2020 | 13.5% | 11.7% | 15.1% |
| 2021 | 9.8% | 11.1% | 6.5% |
| 2022 | −15.3% | −13.5% | −13.0% |
| 2023 | 13.3% | 12.7% | 12.8% |
| 2024 | 11.6% | 12.1% | 12.7% |
| 2025 | 12.9% | 8.9% | 19.6% |
| 2026 (to Jul) | 1.0% | 4.2% | 0.0% |

## Caveats — read before acting

- **Not statistically established.** Holm-adjusted bootstrap p ≈ 0.15 for
  both passers; across ETF diversifier v1, sleeve v2 and v3, about twenty
  sleeves were screened on the same 2017–2026 window, so some passes are
  expected by chance.
- **Gold's return edge is concentrated in 2025** (+6.7 pp that year during the
  gold rally); outside 2025 its CAGR is roughly equal to A. Its robust part is
  diversification: lower drawdown and a smaller 2022 loss. The whole sleeve is
  a single asset (G8 not applied).
- **Dividend growth** is broad US equity with a value/quality tilt; its gain
  is spread across years (7 of 10 years at or above A) but smaller.
- Single historical path; shared-ledger outcomes move by roughly ±$1–4k per
  run through daily-loss-latch path dependence. Both passers clear A by more
  than that (+5.3k and +10.2k), but not by a wide multiple.
- Broker cash interest is modeled as zero.
