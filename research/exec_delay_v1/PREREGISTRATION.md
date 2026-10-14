# exec_delay_v1 — pre-registration (2026-10-14)

## Question
Does a different fill timing (later/outside the signal session) improve on
production E2 beyond next-open fills, given the short-term reversal after
signals seen in moc_execution_v1?

## Control C0
Production E2 exactly (ewma_band_v1 C0): decide at close t, fill at next
open (open t+1), cost 4 bps x m. C0 row must reproduce 14.67% / 14.10% at
m=2, 14.95% at m=1, max DD 49.6%.

## Arms (fixed)
- **C0**: fill at open t+1 (production).
- **D1**: fill at close t+1 (= moc X3 one-day delay).
- **D2**: fill at open t+2.
- **D3**: half the trade at open t+1, half at close t+1.
- **D4**: exits/downward resizes at open t+1, entries/upward resizes at
  close t+1.
- **D4r**: report-only mirror of D4 (entries/upward at open t+1, exits/downward
  at close t+1).

Cost 4 bps x m all arms. Signal, vol, vt, weight cap 0.98, band 0.5 pp as
production.

## Audit of moc_execution_v1 X3 (report)
- X3 (`simulate_delay`) shifts wants by +1 session and calls `simulate_close`,
  so at session i target = wants[i-1], i.e. the signal at completed close
  i-1 is acted on at close i. Look-ahead: none; weight/vol computed on the
  instrument closes through i-1 only (signal computed before close i occurs).
- which close vol uses: v20 computed from the synthetic instrument closes; in
  the resampled path vol_n(sc) through the session; the decision at close
  i-1 uses vol at close i-1. Correct.
- cost: 4 bps x m on |dw|, same as C0.
- resize timing: X3 resizes at close i using the 0.5 pp band vs the
  weight after intraday; same threshold. No double counting.
- verdict: implementation is internally consistent; the caveated issue is OOS
  data quality (FTSE opens in moc were mis-classified as real; corrected
  here), not look-ahead.

## Real-open windows (trimmed after checking open != prior close frequency
and OHLC all-equal placeholders)
- IXIC: 1990-04-06 .. 1999-12-31
- GDAXI: 1997-05-30 .. 2026-09-29
- N225: 2017-03-28 .. 2026-09-29
- FTSE: 1991-01-01 .. 1998-12-31 (opens stale after 2000; earlier years
  1984-1990 have mixed open != prior close 53-63% and are excluded)
G7/G8 use these four windows. Pass thresholds: n_c >= 3, n_d >= 3
(out of 4).

## Post-2000 pooled pass requirement (additional)
The D-arm must beat C0 in the pooled post-2000 parts of M3 (N225) and M4
(GDAXI) (FTSE has no real opens post-2000 and is excluded from this pool).
G7/G8 windows above already include recent N225/GDAXI data.

## Close-only robustness (report-only, second OOS check)
On all six windows (^GSPC 1957-99 and M1-M5), compare D1 vs "fill at close t"
using close-only prices (no opens needed).

## Gates
G1-G8 as ewma_band_v1 (G1 m=2 both financings; G2 DD <= C0+1pp;
G3 2017+ >= C0-1pp; G4 >=2/3 subperiods; G5 block-20 bootstrap median
delta >=0 both financings; G6 G1 at m=1; G7 real-open windows; G8 pooled OOS
bootstrap). G9 turnover <= 2x C0.
Pass thresholds for passing candidate: all of G1..G9.
Selection: highest m=2 base CAGR among passing candidates.
