# credit_vote_v1 — pre-registration (2026-10-14)

Written, and committed with `screen.py`, `DATA_MANIFEST.json` and data copies,
**before any non-control arm was simulated on any real data**. The only
computation on real data before this commit is `screen.py --harness` (C0 only).

## Question

On top of production E2 (C0), do corporate-credit spreads or the 10y-3m yield
curve as additional risk-off signals improve the PAPER rule: vetoes, a 4-vote
majority, curve inversion veto, or replacing TSM252 with a credit vote?

## Data

Inputs in `DATA_MANIFEST.json`.

- Main: `stress_robustness_v1_daily.csv` (QQQ open/close/dividend), DTB3; credit
  from FRED: BAA10Y (daily 1986+), T10Y3M (daily 1982+). Daily series used
  with a 1-session lag.
- OOS (US windows only): ^GSPC 1957-1999 and M1 ^IXIC 1975-1999, M2 ^GSPC
  1935-1956. Credit: monthly BAA/AAA (spread) and monthly GS10/TB3MS (10y-3m),
  used only from the first session of the following month. Non-US windows
  (M3-M5) are not evaluated for credit arms.
- GSPC/OOS loaders and bootstrap mechanics copied from `ewma_band_v1`.

## Mechanics (unchanged)

Production E2 1.1.0: synthetic QLD*, expense 0.95%, DTB3 (base) or DTB3+0.75%
(calibrated), decide at close, fill next open, cap 0.98, costs 4 bps x m. Vol
weight `vt = min(1, 0.35/vol20)` with vol20 unchanged. All arms use the 0.5 pp
resize band.

## E2 price votes

SMA200 = close > 200-session SMA; TSM252 = 252-session price return > 0;
DON126 = close > midpoint of 126-session max/min. E2 on iff >= 2 of 3.

## Credit signals

- Daily (main): `credit_up = BAA10Y (1-session lagged) <= 200-session SMA of
  BAA10Y` (market on unless spread is above its own 200-session level).
- Curve: `curve_inv20 = (1-session lagged T10Y3M < 0) for 20+ consecutive
  sessions`.
- Monthly (OOS): `credit_up = (BAA-AAA) (monthly lagged) <= 12-month SMA`;
  `curve_inv20 = (GS10-TB3MS) (monthly lagged) < 0 for 20+ consecutive sessions`
  (curve condition neutral before data starts, e.g. pre-1953).

## Arms (five, no others)

- **C0**: production E2.
- **K1** (veto): market on iff E2 on AND credit_up.
- **K2** (4-vote majority): market on iff >= 3 of {SMA200, TSM252, DON126,
  credit_up}.
- **K3** (curve veto): market on iff E2 on AND NOT curve_inv20.
- **K4**: credit replaces TSM252: market on iff >= 2 of {SMA200, credit_up,
  DON126}.

## Gates

Reuse G1-G8 unchanged structurally, with G7/G8 reduced to the US windows
described above. G9 turnover <= 2 x C0 (credit overlays trade at most modestly
more when the credit flips). Reduced OOS gate:
- G7: median over the 3 US windows of (arm-C0) CAGR >= 0; CAGR >= C0-0.5pp in
  all 3; max DD <= C0+3pp in all 3 and never > C0+8pp.
- G8: pooled block-20 bootstrap over M1+M2 (4,000 paths) median (arm-C0) >=0.
OOS static windows: ^GSPC 1957-1999, M1, M2. Bootstrap monthly macro values are
sampled by day and condition recomputed on the sampled path.

## Smoke test

`screen.py --harness` verified C0 reproduction before this commit.

