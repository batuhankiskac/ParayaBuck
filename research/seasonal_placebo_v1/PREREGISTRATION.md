# seasonal_placebo_v1 — pre-registration (2026-10-14)

Written and committed with `screen.py` and `DATA_MANIFEST.json` before any non-control arm
is simulated on real data. The only computation on real data before this commit is
`screen.py --harness` (C0 = production E2 only). Run path smoke-tested on synthetic prices.

## Question

Is S3 of seasonal_overlay_v1 ("cash during September") genuinely special, or a lucky draw /
selection-biased September effect? Skeptical placebo / event-decomposition robustness check.
No new candidate may be selected (report-only).

## Arms (all parameters fixed)

C0 = production E2 exactly (control). Non-control arms are CASH_m for m = 1..12:
target weight x 0.00 (cash) for sessions in calendar month m; x 1.00 otherwise, with the E2
target and cap otherwise unchanged (vol20, 0.35, cap 0.98, majority 2-of-3, 0.5 pp band,
cost 4 bps x m). Month flags from the session's calendar date (resampled bootstrap keeps
the original session's month). CASH_9 is S3.

Additionally (report-only, derived):
- 1,000 random-month arms: each calendar year, cash in one uniformly random month
  (seed 20261014); distribution of Δ CAGR / Δ max DD / Δ Calmar vs C0.
- Event dependence: S3 except single year y stays invested, for every year with a September
  in the main window.

## Data

Reuses ewma_band_v1 data byte-for-byte (stress_robustness_v1 daily/dtb3; ewma_band_v1
gspc/index/tb3ms; E2 deviation). No network data added. Main window 2000-03-09 -> 2026-09-29.
OOS windows as ewma_band_v1 (GSPC 1957-99 + M1..M5). Bootstrap block-20 seed 20261011.

Restricted analysis: M3/M4/M5 measurement window restricted to 2000-01-02..2026 for a
post-1999 non-US metric (M1/M2/GSPC unchanged).

## Gates

G1-G9 exactly as ewma_band_v1 (G1 base/cal, G2 base/cal, G3 base/cal, G4 ≥2 of 3 subperiods,
G5 base/cal bootstrap, G6 base/cal, G7, G8, G9 turnover ≤ 2x). Each CASH_m evaluated.

## Decision rule (fixed)

S3 ("CASH_9") is "credible" only if ALL of:
1. September ranks 1st or 2nd of the 12 on the pooled OOS bootstrap Δ;
2. September ranks 1st or 2nd of the 12 on the post-1999 non-US Δ
   (M3, M4, M5 restricted to 2000-2026);
3. S3's Δ Calmar is at/above the 95th percentile of the 1,000 random-month placebo;
4. S3's DD gain stays ≥ 3 pp when the single best September is kept invested.
Otherwise "not credible".

No new candidate selected; report-only.
