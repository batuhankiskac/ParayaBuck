# Seasonal placebo v1 (2026-10-14)

**Verdict: FAIL — S3 ("cash during September") is NOT credible under the pre-registered
decision rule.** September does rank near the top on the pooled OOS bootstrap (rank 2 of 12)
and has the highest bootstrap-median Δ among the 12 months (+1.059 pp), but it ranks 8th of 12
on the post-1999 non-US windows, its Calmar gain is only at the 86.9th percentile of the
1,000 random-month placebo (below the 95% threshold), and the entire 7.62 pp drawdown gain is
explained by September 2000 alone (keeping that single month invested removes the whole gain).
No new candidate is selected; this is a report-only study. Production E2
(`equity_leveraged_trend_v1` 1.1.0) is unchanged.

Pre-registration commit: 7464970.
Evidence: `research/seasonal_placebo_v1/` (`RESULTS.json`, `SUMMARY.txt`, `screen.py`,
`PREREGISTRATION.md`, `DATA_MANIFEST.json`). Report-only study; no selection.

## Decision rule applied (from PREREGISTRATION.md)

S3 is "credible" only if ALL of:

1. September ranks 1st or 2nd on the **pooled OOS bootstrap** Δ — **PASS** (rank 2).
2. September ranks 1st or 2nd on the **post-1999 non-US** Δ (M3/M4/M5) — **FAIL** (rank 8).
3. S3's Δ Calmar at/above the **95th percentile** of the random-month placebo — **FAIL** (86.9%).
4. S3's DD gain stays **≥ 3 pp** with the single best September kept invested — **FAIL** (0.00 pp).

Result: `credible = false`.

## Main window (2000-03-09 → 2026-09-29, m = 2)

C0 = production E2: CAGR 14.67% / max DD 49.56% / Calmar 0.296. S3 (cash in September):
CAGR 14.38% (−0.29 pp) / max DD 41.95% (−7.62 pp) / Calmar 0.343 / 2017+ CAGR 33.49%
(vs C0 32.44%, +1.05 pp). Base (DTB3) financing shown. Calibrated (DTB3 + 0.75%): C0 14.10% /
49.65% DD; S3 13.86% / 42.04% DD; S3 2017+ 32.80% vs 31.69%.

| month cashed | CAGR | max DD | Calmar | 2017+ CAGR | boot20 Δ vs C0 | pooled OOS Δ | post-1999 non-US Δ | turns/yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 (none) | 14.67% | 49.56% | 0.296 | 32.44% | 0.000 pp | 0.000 pp | 0.000 pp | 3.75 |
| Jan | 15.39% | 43.87% | 0.351 | 30.03% | +0.474 pp | −1.395 pp | +0.322 pp | 5.14 |
| Feb | 13.91% | 49.56% | 0.281 | 34.01% | +0.252 pp | −0.692 pp | −1.508 pp | 5.48 |
| Mar | 14.86% | 43.04% | 0.345 | 34.08% | −0.217 pp | −0.234 pp | −0.315 pp | 5.30 |
| Apr | 12.77% | 45.29% | 0.282 | 28.58% | −0.908 pp | −1.240 pp | +0.203 pp | 5.71 |
| May | 12.61% | 44.72% | 0.282 | 24.66% | −1.863 pp | −0.738 pp | +1.002 pp | 5.98 |
| Jun | 13.04% | 49.49% | 0.263 | 24.43% | −0.420 pp | −0.229 pp | +0.532 pp | 5.89 |
| Jul | 11.25% | 45.87% | 0.245 | 28.58% | −1.764 pp | −0.981 pp | −0.236 pp | 5.94 |
| Aug | 14.23% | 53.75% | 0.265 | 28.04% | −0.410 pp | **+1.019 pp** | **+1.039 pp** | 5.91 |
| **Sep (S3)** | **14.38%** | **41.95%** | **0.343** | **33.49%** | **+1.059 pp** | **+0.810 pp** | **−0.357 pp** | **5.18** |
| Oct | 11.90% | 49.56% | 0.240 | 30.71% | −2.361 pp | −0.385 pp | −0.621 pp | 5.72 |
| Nov | 11.19% | 49.56% | 0.226 | 26.60% | −2.854 pp | −1.182 pp | −1.752 pp | 5.89 |
| Dec | 13.70% | 41.41% | 0.331 | 28.04% | −0.721 pp | −2.156 pp | −2.522 pp | 5.47 |

September is 3rd of 12 on main-window Δ CAGR and on main-window Calmar; 1st on the block-20
bootstrap median; 2nd on pooled OOS bootstrap; 8th on post-1999 non-US. August outranks
September on both pooled OOS (+1.019 pp) and post-1999 non-US (+1.039 pp).

Gate count (top-level, per arm): S3 passes 9/9 gates on the main window, both financings,
m = 1 and m = 2, including G7 on all six OOS windows and the pooled OOS bootstrap. Several
other months also pass most gates numerically, but no arm is selected in this study.

## Event dependence (September)

Per pre-registration: S3 except that a single September y stays invested, for every year
2000–2026 with a September session, with B = 0.5 pp and cost 4 bps.

- S3 max DD 41.95% vs C0 49.56% (gain 7.62 pp). Keeping September **2000** invested (cashing
  all other Septembers) reproduces the C0 level exactly (max DD 49.56%, Calmar 0.278): a
  **+7.62 pp** DD increase versus S3. Every other year shows 0.00 pp. September 2000 is
  therefore the sole source of the drawdown gain; 1999–2026 contribute nothing.
- Cumulative (keep the top-k "event" Septembers invested): top-1 (2000) max DD 49.56%
  (gain **0.00 pp** vs C0); top-2 (+1999) 49.56%; top-3 (+2001) 49.56%. The reported 0.00 pp
  gain is the expected consequence of 2000 being the sole driver, not an inconsistency: with 2000
  invested the strategy reverts to C0, so C0 − itself = 0. C0 − S3-without-2000 = 0 as well.
- Calmar: S3 0.343; keeping 2000 invested 0.278 (loss 0.065). Other years 0.000 loss.

This independence on a single month (September 2000, inside the 2000–2002 drawdown) is the
reason credibility criterion 4 fails.

## Random-month placebo (1,000 arms, seed 20261014)

Each synthetic arm, in each calendar year 2000–2026, cashes one uniformly random month
instead of September (same mechanics, vol20, cap 0.98, B = 0.5 pp, 4 bps). Versus C0:

- Δ CAGR: p5 −3.70 pp, p25 −2.02 pp, p50 −1.31 pp, p75 −0.65 pp, p95 +0.69 pp.
  S3 Δ −0.29 pp is at the **73.3rd percentile** (better than a typical random month on CAGR).
- Δ max DD: p5 −1.18 pp, p25 −2.24 pp, p50 −2.96 pp, p75 −4.46 pp, p95 −7.74 pp.
  S3 Δ −7.62 pp is at the **13.8th percentile** (a deeper drawdown cut than ~86% of random months).
- Δ Calmar: p5 −0.071, p25 −0.040, p50 −0.011, p75 +0.024, p95 +0.075.
  S3 Δ +0.047 is at the **86.9th percentile** (just below the ≥95% credibility threshold).

S3 does move DD and Calmar more favorably than most random month choices, but not at the
pre-registered 95% level, and the gain is driven by one (September 2000) rather than by the
calendar month.

## Conclusion

Despite passing every standard gate (G1–G9) on the main and OOS windows, S3 fails the
seasonal-placebo credibility criteria: post-1999 non-US rank 8, random-placebo Calmar
percentile 86.9% (< 95%), and a single-September (2000) dependence that gives 0.00 pp
of drawdown gain when that month is kept invested. The September effect is therefore not
judged credible on this evidence: it looks like a capture of the Sep 2000 → Oct 2002
drawdown rather than a stable calendar edge. Production E2 remains unchanged. No arm is
selected.

## Amendment (post-pre-registration code change)

After the pre-registration commit (7464970), `screen.py` was extended (uncommitted edit
committed with this report) to add a per-year Calmar loss field
(`calmar_gain_vs_removed_pp_notional`) to the event-dependence output and to coerce the year
key to `str` (a no-op: `month_year_arrays` already returns years as strings). The added metric
reports the same September-2000-only pattern (loss 0.065 vs. 0.000 for all other years) and
does not change any CAGR, drawdown, Calmar, gate, bootstrap, OOS, or random-placebo result;
byte-identical reproducibility was re-confirmed with two full `--run` invocations.

## Verification

- `screen.py --harness`: PASS (C0 reproduced to 0.000000 pp: 14.6749% m=2 base, 14.1043%
  calibrated; band simulator equals `simulate_multi` bit for bit).
- `screen.py --run`: exit 0; two runs produced byte-identical `RESULTS.json`
  (SHA-256 92e0d2bab18393fc16de8e44a98329d04ef09ce8c813c1d56dd886493bcf5dfe).
- Runtime ~2.5–3 minutes per full run; no network access except none (reuses ewma_band_v1 data).

PY
echo written