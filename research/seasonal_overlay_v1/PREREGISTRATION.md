# seasonal_overlay_v1 — pre-registration (2026-10-14)

Written and committed with `screen.py`, `DATA_MANIFEST.json`, and the E2-deviation copy
**before any non-control arm is simulated on any real data**. The only computation on real
data before this commit is `screen.py --harness` (C0 = production E2 only). The full
`--run` path was smoke-tested on randomly generated synthetic prices only.

## Question

On top of production E2 (C0), does calendar seasonality as an exposure modulator improve
long-run return vs S&P 500 B&H without materially worse drawdown? Four modulators:
Sell-in-May (S1), seasonally varying vol target (S2), cash in September (S3), turn-of-month
exposure (S4). Seasonality needs only closes, so every OOS window is usable.

## Arms (all parameters fixed)

Control C0 = production E2 exactly (min(1, 0.35/vol20), cap 0.98, vol20 sample vol,
majority 2-of-3 trend vote on closes, 0.5 pp resize band, cost 4 bps/side x m).
Non-control arms multiply the E2 target weight before the cap 0.98, trading at the same
resize band and cost, unless stated:

- S1: target weight x 0.50 for sessions whose date falls in May 1 – Oct 31;
  x 1.00 otherwise. Decided on the last close of April / October (target indexed to the
  following session's date).
- S2: vol target varies by season instead of 0.35: 0.45 for Nov-Apr, 0.25 for May-Oct.
  The weight is min(1, target/vol20) with the same cap. (Same long-run average target.)
- S3: target weight x 0.00 (cash) for sessions in September; x 1.00 otherwise.
- S4: target weight x 1.00 only in the last trading session plus the first 3 trading
  sessions of each calendar month; x 0.50 otherwise.

Month and turn-of-month flags are taken from the session's calendar date in the data
(the resampled bootstrap uses the original session's calendar month/position, with the
same rule).

## Data

Main + OOS re-use ewma_band_v1's data byte-for-byte (see DATA_MANIFEST.json):
stress_robustness_v1_{daily,dtb3}.csv (main), ewma_band_v1_{gspc,index,tb3ms}.csv (OOS),
and E2_INTEGRATION_DEVIATION.json. No network data added; seasonality uses closes only.

Main window 2000-03-09 -> 2026-09-29. OOS windows: ^GSPC 1957-1999 (GSPC) and the five
e2_confirmation_v1 markets M1..M5. Bootstrap: ewma_band_v1's block-20 scheme (seed 20261011,
5,000 main paths, 2,000 per OOS market, 10,000 pooled).

## Mechanics

Unchanged from production E2: synthetic QLD* = daily-reset 2x QQQ total return, 0.95%/yr
expense + DTB3 (base) or DTB3 + 0.75% (calibrated); decide at the completed close, trade at
the next open; total weight cap 0.98; cost 4 bps x m per unit traded (m = 1 and m = 2);
idle cash at zero.

## Gates

G1-G8 as in ewma_band_v1, unchanged. G9: turnover <= 2 x C0 (band = C0_BAND so limit is
2 x) unless inherent extra trading; S1-S4 are stated to trade at season boundaries and are
not considered exempt, so G9 applies normally. Cost is additionally stressed at 2x in the
reporting. Pass = all of G1-G9 for an arm.

## Selection rule

selected = the passing arm with the highest m=2 base CAGR; if none pass, "none". No
tuning after seeing results.
