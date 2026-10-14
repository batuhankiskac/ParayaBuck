# vote_hysteresis_v1 — pre-registration (2026-10-14)

Written, and committed with `screen.py` and `DATA_MANIFEST.json`, before any non-control
arm is simulated on real data. The only real-data computation before this commit is
`screen.py --harness` (C0 = production E2 only). The full `--run` path was smoke-tested
on synthetic random prices only.

## Question

On top of production E2 (C0, symmetric 2-of-3 majority: enter when votes >= 2, exit when
votes < 2), does asymmetric threshold hysteresis on the vote count cut whipsaw round trips
without materially worse return/drawdown? C sizing/cap/band as production.

## Data (no network; byte copies)

- Main: `data/historical/stress_robustness_v1_{daily,dtb3}.csv`.
- OOS ^GSPC: `data/historical/ewma_band_v1_gspc.csv`.
- OOS M1–M5: `data/historical/ewma_band_v1_{index,tb3ms}.csv`.
- Harness reference: `research/vote_hysteresis_v1/E2_INTEGRATION_DEVIATION.json` (byte copy).

## Mechanics (unchanged)

Synthetic QLD* = daily-reset 2x on QQQ total return, 0.95%/yr expense + DTB3 (base) or
DTB3 + 0.75% (calibrated); decide at close, fill next open; cap 0.98; cost 4 bps x m
(m=2 primary, m=1 G6); idle cash zero. Main window 2000-03-09 -> 2026-09-29.

## Signal (all arms)

Production E2 1.1.0 votes on QQQ split-adjusted price close: close > SMA200;
252-session price return > 0; close > midpoint of 126-session max/min. Vote count 0..3.

## Arms (five; no others)

Sizing = production E2: w = E2_on * min(1, 0.35/vol20), capped at 0.98; resize band 0.5 pp.

- C0: on when votes >= 2 (symmetric production rule).
- Y1: enter when 3 of 3 votes on; exit when <= 1 of 3 (hold through 2-of-3).
- Y2: enter when >= 2 of 3; exit only when 0 of 3.
- Y3: enter when 3 of 3; exit when <= 1 of 3 OR QQQ closes > 5% below SMA200.
- Y4: enter when >= 2 of 3; exit when <= 1 of 3 for 3 consecutive closes.

State machine initializes off; thresholds applied to vote counts; early (warmup) sessions
have 0 votes.

## Gates

Reuse ewma_band_v1 G1–G8 verbatim; G9 turnover <= 2 x C0 (all arms use C0 band).
All six OOS windows usable.

G1 m=2 return both financings; G2 DD <= C0+1pp; G3 2017+ >= C0-1pp; G4 >=2 of 3
sub-periods; G5 block-20 bootstrap median delta >= 0 both financings; G6 G1 at m=1;
G7 six OOS windows; G8 pooled OOS bootstrap; G9 turnover.

## Selection rule

No band selection. An arm passes only if all G1–G9. Report round trips/yr and sells/yr.
