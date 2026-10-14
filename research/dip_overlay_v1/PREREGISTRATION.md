# dip_overlay_v1 — pre-registration (2026-10-14)

Written, and committed with `screen.py`, `DATA_MANIFEST.json` and the data copies,
**before any non-control arm is simulated on any real data**. Only `screen.py --harness`
(C0 = production E2 only) has been run on real data; the full `--run` path was smoke-tested
on randomly generated synthetic prices only.

## Question

On top of production E2 (C0), does temporarily raising QLD exposure after sharp short-term
drops in QQQ (a short-term reversal / "buy the dip in a bull market" overlay) improve
long-run return, without materially worse drawdown? Cost of extra turnover is 4 bps × m
per unit traded, stressed at m = 2.

## Data (no network)

- Main: `data/historical/stress_robustness_v1_{daily,dtb3}.csv` (committed).
- OOS ^GSPC: `data/historical/ewma_band_v1_gspc.csv`.
- OOS M1–M5: `data/historical/ewma_band_v1_{index,tb3ms}.csv`.
- Harness reference: `E2_INTEGRATION_DEVIATION.json`.
- SHA-256 in `DATA_MANIFEST.json`.

## Mechanics (unchanged from trend_robustness_v2 / ewma_band_v1)

Synthetic QLD* = daily-reset 2x QQQ total return, 0.95%/yr expense + DTB3 (base) or
DTB3 + 0.75% (calibrated); decide at the completed close, fill at the next open;
total weight cap 0.98; cost 4 bps × m per unit traded (m = 2 primary, m = 1 gated);
idle cash at zero. Main window 2000-03-09 → 2026-09-29. Block-20 bootstrap and OOS
procedures exactly as ewma_band_v1.

## Signal (all arms) — production E2 1.1.0 on QQQ price close

At least 2 of: close > SMA200; 252-session price return > 0; close > midpoint of
126-session max/min. `vt(v) = min(1, 0.35/v)` on 20-day realized vol of QLD*.
Production target weight (before overlay) = E2 · vt, capped at 0.98.

## Arms (five; no others)

Cap 0.98 always. Overlay only triggers while E2 is on and only on E2-on sessions;
otherwise target is 0.

- **C0** = production E2 (control).
- **P1** (dip boost): if QQQ 5-session price return < −5% at close t, target weight
  = min(0.98, 1.5 × production weight) for the next 5 sessions (t..t+4), overwriting
  production weight; a new trigger restarts the 5-session horizon.
- **P2** (RSI(2) dip): 2-period simple RSI (mean gain/loss over the last 2 daily
  price changes) < 10 at close t while E2 on sets target = 0.98 for up to 10 sessions,
  or until RSI(2) > 70 at a later close, whichever comes first; a new trigger while
  active resets the 10-session horizon.
- **P3** (trim on strength): if QQQ 5-session price return > +5% at close t, target
  weight = production weight × 0.67 for the next 5 sessions (t..t+4); new trigger
  restarts the horizon.
- **P4** = P1 and P3 together (multiplicative: dip factor 1.5 where P1 active, trim
  factor 0.67 where P3 active, capped at 0.98).

P2 uses 0.98 as an absolute target; P1/P3/P4 scale production weight.

## Gates

Reuse ewma_band_v1's G1–G8 unchanged. G9 (turnover, m = 2): because this idea is
inherently about trading more after dips, candidate turnover is allowed up to
2 × C0's turnover; the cost model (4 bps × m per unit, stressed at m = 2) is stated
above. Gate evaluation as implemented in screen.py.

## Selection

Among arms passing all gates, select the one with the highest base m = 2 CAGR
(tie: Calmar). If none pass, verdict FAIL and production E2 unchanged.

## Smoke testing

The full `--run` path was exercised only on synthetic random prices before this commit
via direct calls to `apply_overlay` / `simulate_band`; no real-data non-control arm was run.
