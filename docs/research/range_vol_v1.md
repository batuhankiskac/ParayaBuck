# range_vol_v1 — Range-based volatility on production E2

## Verdict

**PASS — arm `G3x` (Parkinson 20-session vol, expanding-scaled) passes every
gate (G1–G9) on the pre-registered run, both base (DTB3) and calibrated
(DTB3 + 0.75%) financing, m = 1 and m = 2, main block-20 bootstrap,
ETF OOS (SPY/DIA/IWM) and pooled OOS bootstrap.** The other three range arms
(`G1x` Yang-Zhang, `G2x` Garman-Klass + gap, `G4x` average of vol20 and YZ)
also pass every gate; `G3x` is selected by highest base m = 2 CAGR
(14.98% vs 14.80%).

Quick headline (base, m = 2, 2000-03-09 → 2026-09-29):

| Arm | CAGR | max DD | 2017+ CAGR | boot20 med Δ | OOS med Δ |
|---|---:|---:|---:|---:|---:|
| C0 | 14.67% | 49.56% | 32.44% | 0.000 pp | — |
| G1x (YZ) | 14.80% | 46.84% | 31.66% | +0.411 pp | +0.280 pp |
| G2x (GK+gap) | 14.80% | 46.95% | 31.74% | +0.460 pp | +0.264 pp |
| **G3x (Parkinson scaled)** | **14.98%** | **48.50%** | **32.72%** | **+0.275 pp** | **+0.306 pp** |
| G4x (avg) | 14.80% | 47.98% | 32.22% | +0.256 pp | +0.158 pp |

All range arms beat C0 by 0.13–0.31 pp CAGR, cut max DD 1.1–2.7 pp and pass
the bootstrap (median Δ +0.25 to +0.46 pp main, +0.16 to +0.31 pp OOS).
`G3x` has the highest base CAGR and the best 2017+ CAGR (+0.28 pp vs C0).

## Method

- Reused `research/ewma_band_v1` harness helpers (imported; folder left unmodified).
- Control C0 reproduces `e2_integration_v1` production-E2 deviation to 0.000000 pp.
- Main: production E2 on QQQ price close; volatility on QQQ adjusted OHLC
  (`open/high/low × adjclose/close`, close = adjclose), converted to the QLD
  basis as 2 × QQQ vol. Arms use the production 0.5 pp resize band.
- Estimates: Parkinson 20-day; GK 20-day + overnight gap variance; Yang-Zhang
  20-day; average of close-to-close (×2) and YZ; plus Parkinson 20-day scaled
  by the expanding (pre-date, no look-ahead) close-to-close/Parkinson ratio.
- Main bootstrap: stationary block-20, seed 20261011, 5,000 paths, m = 2, base
  and calibrated financing, all arms on identical paths.
- OOS: index OHLC failed the pre-registered data-quality test (high/low or
  open stale), so only quality-passing instruments were used: SPY (1993+),
  DIA (1998+), IWM (2000+). HTMS/indices excluded. Pooled bootstrap over the
  3 ETF markets, 2,000 paths each (6,000 total).

## Gates

Every arm passes G1–G9. Key gate values (`G3x`):

- G1 (return, m = 2, both): CAGR > C0 (base 14.98% vs 14.67%; cal 14.40%
  vs 14.10%).
- G2 (max DD, m = 2, both): 48.50% / 48.58% vs 49.6% cap.
- G3 (2017+ CAGR): 32.72% / 31.95% vs 32.44% / 31.69%.
- G4 (sub-periods): wins all 3 (P1 −1.00, but C0 −1.10; P2 +13.86 vs 13.26;
  P3 32.72 vs 32.44).
- G5 (main block-20 median Δ): +0.275 / +0.271 pp ≥ 0.
- G6 (m = 1, both): 15.23% / 14.65% vs 14.95% / 14.38%.
- G7 (OOS, 3 ETF windows): median Δ +0.306 pp ≥ 0, all windows ≥ −0.5 pp.
- G8 (pooled OOS bootstrap): +0.306 pp ≥ 0.
- G9 (turnover, base): 5.39 ≤ 2 × 5.97.

## Caveats

- OOS historical indices were excluded by the pre-registered data-quality test
  (high/low and opens stale in older data). Only SPY/DIA/IWM ETFs remain, so
  the OOS evidence rests on 3 instruments, 1993–2026, not on the longer
  price-history windows. Earlier `^IXIC`/`^GSPC`/`^N225`/`^GDAXI`/`^FTSE`
  windows would be needed to reproduce the full e2_confirmation_v1 OOS.
- All four range arms pass; the differences among them are small
  (0.00–0.18 pp CAGR). `G3x` selected by the pre-registered highest-CAGR rule.
- Production is unchanged; this is research evidence only (no src/ or config/
  edits). Stage B / integration specification would be the next step if the
  owner approves.

## Reproducibility

- `RESULTS.json` and `SUMMARY.txt` written by `research/range_vol_v1/screen.py
  --run`; run twice byte-identical. Commit hashes recorded in the final answer.
