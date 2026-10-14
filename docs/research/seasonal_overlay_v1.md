# Seasonal overlay v1 (2026-10-14)

> Evidence for this report (`research/seasonal_overlay_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/seasonal-overlay-v1-2026-10`.

**Verdict: PASS — S3 (cash during September only).** S3 passes all pre-registered gates
(G1–G9, base and calibrated, m = 1 and m = 2) on the 2000–2026 main window and on the
six OOS windows, and is the selected arm. Production code/config are unchanged; S3 is
research evidence pending a Stage B replay.

Gates, arms:
- C0 = production E2 exactly (control).
- S1 = weight × 0.50 in May–Oct (fails).
- S2 = vol target 0.45 (Nov–Apr) / 0.25 (May–Oct) (fails).
- S3 = cash in September only (passes all).
- S4 = full weight only in the last session + first 3 sessions of each month, 0.50 otherwise (fails).

Main window (m = 2, base financing = DTB3):

| arm | CAGR | max DD | Calmar | 2017+ CAGR | boot20 median Δ | OOS pooled boot Δ | turn/yr |
|---|---:|---:|---:|---:|---:|---:|---:|
| C0 | 14.67% | 49.56% | 0.296 | 32.44% | 0.000 pp | 0.000 pp | 5.97 |
| S1 | 10.22% | 43.61% | 0.234 | 21.43% | −1.902 pp | +0.649 pp | 5.08 |
| S2 | 11.96% | 53.34% | 0.224 | 27.85% | −1.027 pp | +0.420 pp | 6.31 |
| S3 | 14.38% | 41.95% | 0.343 | 33.49% | +1.059 pp | +0.810 pp | 6.65 |
| S4 | 7.83% | 34.87% | 0.224 | 17.74% | −2.436 pp | +0.652 pp | 11.56 |

Calibrated financing (DTB3 + 0.75%) m = 2: C0 14.10% / 49.65% DD; S3 13.86% / 42.04% DD;
S3 2017+ 32.80% vs C0 31.69% (+1.11 pp); S3 boot median +1.094 pp.

Gates (each must PASS, both financings; numbers for the failing arm stand):

| arm | G1 base/cal | G2 base/cal | G3 base/cal | G4 | G5 base/cal | G6 base/cal | G7 | G8 | G9 | result |
|---|---|---|---|---|---|---|---|---|---|---|
| S1 | F/F | P/P | F/F | F | F/F | F/F | F | P | P | FAIL |
| S2 | F/F | F/F | F/F | F | F/F | F/F | P | P | P | FAIL |
| S3 | P/P | P/P | P/P | P | P/P | P/P | P | P | P | PASS |
| S4 | F/F | P/P | F/F | F | F/F | F/F | F | P | P | PASS |

S3 is the only arm passing every gate. S3 gives up 0.29 pp CAGR (base) and 0.24 pp (calibrated)
versus C0 but cuts max DD from 49.6% to 42.0% (base) / 42.0% (calibrated), raises 2017+
CAGR by 1.05 pp (base) / 1.11 pp (calibrated), and the block-20 bootstrap median is +1.06 pp.
OOS: S3 passes G7 (median Δ CAGR across six windows ≥ 0) and the pooled OOS bootstrap +0.81 pp.
Across OOS windows S3 wins on M1 (+0.98 pp), M4 (+0.29 pp), M5 (+0.97 pp), GSPC (+0.52 pp)
and M3 loses (−1.04 pp, Japanese market); M2 is roughly flat (−0.15 pp).

Why only S3: the other seasonal tilts are pro-cyclical with the drawdowns.
- S1 (Sell in May) misses the summer rallies and rebalances into the Halloween low; it loses
  4.45 pp CAGR and runs a −1.9 pp bootstrap. Classic calendar effect does not survive
  transaction costs / overlap with trend.
- S2 (seasonal vol target) hits the 2017+ / max-DD tests (G3, G2 fail): lower target in
  May–Oct sizes down into rallies and the higher Nov–Apr target sizes up into crashes.
- S4 (turn-of-month) cuts DD the most (34.9%) but destroys CAGR (−6.84 pp): a 0.5 pp trend
  strategy is already sized by vol; turning it into a monthly market-timing trade costs 11.6 turn/yr.

Cost note: S3's turnover (6.65/yr) is slightly above C0 (5.97/yr); G9 still passes at the 2x
limit. At 2x cost (8 bps/side) S3 CAGR falls ~0.1 pp less than C0, so the advantage holds.

Evidence: `research/seasonal_overlay_v1/` (RESULTS.json, SUMMARY.txt, screen.py,
PREREGISTRATION.md, DATA_MANIFEST.json). Inputs are byte-identical to ewma_band_v1's
stress_robustness and OOS files. Harness PASS (C0 reproduced to 0.000000 pp, band simulator
bit-equal). Two `--run` invocations gave byte-identical RESULTS.json.

Pre-registration commit: 9d5f178.
