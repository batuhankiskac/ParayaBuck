# relstrength_v1 (relative-strength rotation) (2026-10-14)

> Evidence for this report (`research/relstrength_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/relstrength-v1-2026-10`.

**No arm passes the pre-registered gate; production E2
(`equity_leveraged_trend_v1` 1.1.0) stays unchanged.** All four rotation arms
lose 1.2–4.1 pp CAGR on 2000–2026 under both financing models, fail the main
bootstrap, and lose in both OOS pairs.

## Method

Control C0 is production E2 1.1.0 (hold synthetic QLD* while E2 is on).
QLD* is daily-reset 2x QQQ total return (expense 0.95% + DTB3); SSO* is the
same construction on SPY total return (expense 0.89%). Vol sizing
`vt = min(1, 0.35 / vol20)` of the held instrument; total weight capped at 0.98.
Decide at close, fill at next open, 0.5 pp resize band, 4 bps x m cost.

Arms: Q1 (QQQ E2 on; hold QLD* if QQQ 126-session return ≥ SPY's else SSO*,
2 pp hysteresis), Q2 (252-session relative), Q3 (each index own E2 vote; hold
stronger 126-day among those on; cash otherwise), Q4 (Q3 but 50/50 split when
both on and gap < 5 pp).

OOS reduced gate uses two aligned pairs: ^IXIC vs ^GSPC 1975-01-02 → 1999-12-31
and ^GDAXI vs ^FTSE 1989-01-11 → 2026-09-29. Block-20 bootstrap (seed
20261011) with 5,000 main-window paths and 2,000 per OOS pair.

## Main window (2000-03-09 → 2026-09-29)

| arm | CAGR base m=2 | maxDD base | 2017+ CAGR | boot median Δ base | G pass |
|---|---|---|---|---|---|
| C0 | 14.67% | 49.56% | 32.44% | +0.00 | (control) |
| Q1 | 13.44% | 48.72% | 29.66% | −0.89 | no |
| Q2 | 12.99% | 45.75% | 28.53% | −0.83 | no |
| Q3 | 11.97% | 55.85% | 23.59% | −1.49 | no |
| Q4 | 10.55% | 56.25% | 23.89% | −1.51 | no |

Calibrated financing: C0 14.10%, Q1 12.86%, Q2 12.42%, Q3 11.37%, Q4 9.96%.
All fail G1 (return), G3 (2017+), G5 (main bootstrap), G6 (m=1 return), G4
(sub-periods), G7 (OOS), G8 (OOS bootstrap) except Q1/Q2 pass G2 (max DD) and
G9 (turnover). Q3/Q4 also fail G2 and G9.

## Out of sample

IXIC vs GSPC 1975 → 1999: C0 20.68% CAGR / 38.62% DD; Q1 16.90% / 39.95%;
Q2 17.60% / 41.83%; Q3 18.94% / 36.50%; Q4 18.44% / 36.89%.
GDAXI vs FTSE 1989 → 2026: C0 8.34% / 58.28%; Q1 4.57% / 62.70%;
Q2 4.85% / 66.16%; Q3 7.68% / 56.31%; Q4 5.98% / 59.98%.
Pooled OOS bootstrap median delta vs C0: Q1 −3.05 pp, Q2 −2.88 pp, Q3 −3.42 pp,
Q4 −3.59 pp.

## Verdict

All four arms fail every substantive gate. Dynamic relative-strength rotation
does not improve on production E2; the simpler Nasdaq-only rule with QQQ E2
votes remains. Relative switching adds cost/whipsaw and gives up return
consistently.

## Amendments

- The GDAXI vs FTSE measured window starts 1989-01-11 rather than the
  pre-registered 1989-01-02, because at least 253 common trading days are
  required to compute the 252-session relative return and E2 vote; 1989-01-02
  would have left 247. The change affects only the first five plotting sessions.