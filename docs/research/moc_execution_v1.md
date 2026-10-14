# moc_execution_v1 (2026-10-14)

> Evidence for this report (`research/moc_execution_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/moc-execution-v1-2026-10`.

Verdict: **FAIL** (no candidate passes all gates). Same-close execution (X1) raises
max DD (~+5.4 pp) and lowers CAGR vs C0 (~-0.94 pp); mixed timing X2 is worse on
DD/return too. One-session delay X3 gains CAGR/DD but risks late fills. OOS is
split; pooled OOS bootstrap favors X1.

## Method
Reuses ewma_band_v1 harness (C0 = production E2). Arms fix execution timing:
C0 next-open control; X1 same-close fill; X1n same-close on pre-close price
(close × (1+e), e~N(0,0.25%), 20 seeds); X2 exits/down-resizes at close,
entries/up-resizes next open; X3 report-only one-day delay. OOS reduced to
four usable real-open windows (IXIC 1990, GDAXI 1997, N225 2017, FTSE 1985).

## Main window (2000-03-09 → 2026-09-29, m=2)
| Arm | CAGR base/cal | Max DD | 2017+ CAGR | Turn/yr | Boot median Δ |
|---|---|---|---|---|---|
| C0 | 14.67 / 14.10 | 49.56 / 49.65 | 32.44 / 31.69 | 5.97 | +0.000 pp |
| X1 | 13.73 / 13.16 | 54.96 / 55.04 | 31.72 / 30.98 | 5.93 | -0.222 pp |
| X1n (median) | 13.53 / 13.11 | 54.53 / 55.18 | 31.04 / 30.13 | 6.31 | -0.222 pp |
| X2 | 14.06 / 13.49 | 53.83 / 53.91 | 31.27 / 30.53 | 4.80 | -0.575 pp |
| X3 (delay) | 15.05 / 14.48 | 42.44 / 42.53 | 31.62 / 30.88 | 6.00 | +0.688 pp |

X1n worst seed CAGR 12.39% (base m=2).

## Gates
All candidates fail G1 (base/calibrated m=2 CAGR), G2 (DD), G5 (bootstrap median
Δ≥0), G6 (m=1) and G4 (subperiods). X1/X1n pass G3, G7, G8, G9. X2 fails G8.

## Decomposition (report-only)
Over 2000–2026 measured sessions, QQQ price-only overnight (close→open) CAGR
~9.78%/yr vs intraday (open→close) ~-2.17%/yr. C0's own PAPER NAV attributes
~19.19%/yr to overnight phases vs ~-3.31%/yr to intraday phases: most of E2's
return arrives overnight, so filling at the close (foregoing the overnight gap
on entries) does not help.

## Caveat
OOS opens: usable windows only; bootstrap noise for X1n approximated by X1.
X1n bootstrap cost 4 bps×m held constant.
