# Dip overlay v1 (2026-10-14)

**Verdict: FAIL. None of P1–P4 passes the pre-registered gates. Production E2
(`equity_leveraged_trend_v1` 1.1.0) stays unchanged.**

Idea tested: temporarily raise QLD exposure after sharp short-term drops in QQQ while
E2 is on (short-term reversal in an uptrend), plus a symmetric trim after sharp
gains. Four fixed arms: P1 (5-day drop < −5% → 1.5× production weight for 5 sessions),
P2 (RSI(2) < 10 → 0.98 for up to 10 sessions or until RSI(2) > 70), P3
(5-day gain > +5% → 0.67× production weight for 5 sessions), P4 = P1 and P3 together.
All use vol20, cap 0.98, the production E2 signal, and 4 bps × m cost (m = 2).

- **P1 / P2 (buy the dip):** raise CAGR slightly (+0.03 / +0.24 pp base) and 2017+
  CAGR (+0.50 / +4.22 pp), but worsen max DD (56.8% / 58.5% vs 49.6%) and fail G2
  (DD > C0 + 1 pp), and the main block-20 bootstrap median Δ is negative
  (−0.32 / −0.54 pp). OOS bootstrap and six OOS windows pass.
- **P3 (trim strength):** cuts max DD to 42.2% and makes G1/G2 but lowers CAGR by
  −0.21 pp and 2017+ CAGR by −1.33 pp; fails G3, G4, G7. Bootstrap barely positive
  (+0.07 pp).
- **P4 (both):** neither the dip boost nor the trim rescues the combination; it fails
  G1 and G5 on return, with no net improvement (−0.15 pp CAGR, bootstrap −0.21 pp).

The broader pattern: the dip overrides buy the most exposure at the moments that add
drawdown (2020-type crashes during trend), while the trim loses the strong-gain
sessions that recover them. The 4 bps round-trip cost is small relative to these effects.

Evidence: `research/dip_overlay_v1/` (untracked harness copied from `ewma_band_v1`,
left in place) and data copies `data/historical/ewma_band_v1_*` plus committed
`stress_robustness_v1_*`.

- `PREREGISTRATION.md`, `screen.py`, `DATA_MANIFEST.json`,
  `E2_INTEGRATION_DEVIATION.json` committed in `5d476e5`, before any non-control
  arm ran.
- Outputs `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`. Offline, ~50 s.
  Two runs gave byte-identical `RESULTS.json` (sha256
  `631c3c7e261bf5788379c8bfc97fadd9c598c4c3a2a9b31eb84330136f361d85`).

## Method

Same mechanics as `ewma_band_v1` / `trend_robustness_v2`: synthetic QLD* daily-reset 2×
QQQ total return, 0.95% + DTB3 (base) or +0.75% more (calibrated) financing, decide at
the close, fill next open, 0.5 pp resize band, 4 bps × m cost, idle cash zero; main
window 2000-03-09 → 2026-09-29.

- **Control C0.** Production E2 1.1.0 on QQQ price close, `vt = min(1, 0.35/vol20)`,
  total weight capped at 0.98.
- **Overlay.** Applied only when E2 is on, using QQQ price close (total-return price
  level in bootstrap). A 5-session return or 2-period RSI is computed at close t;
  the override affects targets for sessions t..t+4 (or up to 10 for P2). Trigger restarts
  the horizon. If E2 turns off, target becomes 0 regardless of overlay state.
- **Harness.** C0 reproduces `e2_integration_v1`'s production-E2 row to 0.000000 pp
  (CAGR 14.67% base / 14.10% calibrated at m = 2, 14.95% at m = 1, max DD 49.6%).
  Block-20 bootstrap (seed 20261011, 5,000 ten-year paths) and six OOS windows
  (^GSPC 1957–1999 plus M1–M5, pooled bootstrap 10,000 paths) identical to prior campaigns.
- **Gates.** G1–G8 unchanged from `ewma_band_v1`. G9 allows up to 2× C0 turnover for
  these arms because the idea is inherently about trading more; cost already stressed
  at m = 2. Pass required on every gate.

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD | Calmar | 2017+ | P1 / P2 / P3 (2000–08 / 09–16 / 17→end) | m = 1 CAGR | Boot. median Δ | Turnover/yr |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| C0 | 14.67% | 14.10% | 49.56% | 0.296 | 32.44% | −1.10 / 13.26 / 32.44 | 14.95% | 0.000 pp | 5.97 |
| P1 | 14.70% | 14.12% | 56.80% | 0.259 | 32.94% | −1.49 / 13.31 / 32.94 | 14.99% | −0.322 pp | 6.33 |
| P2 | 14.91% | 14.32% | 58.49% | 0.255 | 36.66% | −2.10 / 10.98 / 36.66 | 15.30% | −0.537 pp | 8.58 |
| P3 | 14.46% | 13.91% | 42.20% | 0.343 | 31.11% | 0.04 / 12.53 / 31.11 | 14.78% | +0.068 pp | 6.84 |
| P4 | 14.52% | 13.96% | 49.84% | 0.291 | 31.63% | −0.28 / 12.57 / 31.63 | 14.85% | −0.212 pp | 7.20 |

Bootstrap medians (base m = 2) are 7.16% for C0, 6.87% (P1), 6.75% (P2), 7.30% (P3),
6.89% (P4); each arm beats C0 on 35–46% of paths. Calibrated m = 2 values move by
the same ~0.5 pp (P3 −0.5 pp G3 miss is similar under both financings).

## Out of sample (calibrated, m = 2)

| Window | C0 CAGR / DD | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 9.98% / 39.0% | 10.10% / 39.0% | 10.22% / 39.3% | 9.55% / 39.3% | 9.66% / 39.3% |
| M1 ^IXIC 1975–1999 | 20.68% / 38.6% | 21.02% / 38.6% | 21.56% / 38.6% | 19.06% / 38.6% | 19.44% / 38.6% |
| M2 ^GSPC 1935–1956 | 11.26% / 62.5% | 12.63% / 59.2% | 13.10% / 57.8% | 11.49% / 62.4% | 12.83% / 59.2% |
| M3 ^N225 1966–2026 | 6.33% / 67.2% | 6.62% / 64.0% | 7.47% / 59.6% | 6.04% / 69.0% | 6.33% / 65.9% |
| M4 ^GDAXI 1989–2026 | 9.41% / 58.6% | 9.90% / 59.2% | 9.66% / 63.3% | 9.57% / 54.2% | 10.02% / 55.1% |
| M5 ^FTSE 1985–2026 | −0.05% / 82.7% | −0.04% / 82.7% | 0.00% / 82.9% | −0.03% / 83.0% | −0.03% / 83.0% |
| Median Δ CAGR | — | +0.31 pp | +0.57 pp | −0.30 pp | −0.01 pp |
| Pooled bootstrap median Δ | — | +0.075 pp | +0.267 pp | +0.015 pp | +0.098 pp |

P1/P2/P4 pass G7. P3 fails G7 (median −0.13 pp across the six, two windows worse than
−0.5 pp). Max DD never exceeds C0 + 8 pp; P2's worst DD improvement is M3.

## Gates (m = 2, base / calibrated)

| Gate | P1 | P2 | P3 | P4 |
|---|---|---|---|---|
| G1 return | pass / pass | pass / pass | pass / pass | fail / fail |
| G2 DD ≤ C0 + 1 pp | **fail** / **fail** | **fail** / **fail** | pass / pass | pass / pass |
| G3 2017+ ≥ C0 − 1 pp | pass / pass | pass / pass | **fail** / **fail** | pass / pass |
| G4 ≥2 of 3 sub-periods | pass | fail | fail | fail |
| G5 main bootstrap Δ ≥ 0 | **fail** / **fail** | **fail** / **fail** | pass / pass | **fail** / **fail** |
| G6 G1 at m = 1 | pass / pass | pass / pass | pass / pass | fail / fail |
| G7 OOS windows | pass | pass | **fail** | pass |
| G8 pooled OOS bootstrap | pass | pass | pass | pass |
| G9 turnover ≤ 2× C0 | pass | pass | pass | pass |
| **Result** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |

P1: G2 (DD +7.2 pp) and G5 (−0.32 pp median) fail.
P2: G2 (DD +8.9 pp), G4 (only P3 later 36.66 > 32.44; first two negative), and G5 (−0.54) fail.
P3: G3 (−1.33 pp), G4, G7 fail.
P4: G1 (−0.15 pp / −0.14 pp, Calmar 0.291 vs required 0.312), G5 fail (−0.21 pp).

## Selection

No eligible arm. Verdict: FAIL. Production E2 unchanged.
