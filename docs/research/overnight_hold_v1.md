# overnight_hold_v1 (2026-10-14)

Verdict: **FAIL** (N1 and N2 fail all gates; production E2 unchanged).

## Method
C0 = production E2 (ewma harness). N1 = E2 weight overnight only; N2 = overnight full
weight + half intraday. Cost s per side evaluated at 1/2/4/8 bps; gate at s=4 (m=2 and m=1).
Overnight benchmark N3 (report-only) = unconditional QQQ 1x overnight.

## Main window (m=2, base financing)

| Arm | s=1bps CAGR | s=4bps CAGR | max DD @4bps | turn/yr |
|---|---:|---:|---:|---:|
| C0 | 14.67% | 14.67% | 49.56% | 5.97 |
| N1 | 0.16% | -18.09% | 99.50% | 335.07 |
| N2 | 2.65% | -7.29% | 87.10% | 169.73 |

## Bootstrap (main window, s=4bps)
N1 median (arm-C0) = -12.71 pp, share beating C0 = 7.4%.
N2 median = -8.76 pp, share beating C0 = 10.4%.

## Raw anomaly (N3)
QQQ overnight-only: 10.47%/yr gross; -9.66%/yr at 4bps (500 round trips).
Year-by-year 2017-2026 overnight returns were positive 9/10 years, but intraday
was also positive in 2019, 2020, 2023, 2025; split unstable.

## OOS
SPY/DIA/IWM N1 all deeply negative (-16.9% to -22.5% CAGR) with near-total
drawdowns.

## Conclusion
Overnight exposure cannot be harvested after auction costs and financing at these
turnover rates. Production E2 next-open fills remain preferred.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
