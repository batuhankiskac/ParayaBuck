# relstrength_v1 — pre-registration (2026-10-14)

Written before any non-control arm is simulated on real data. Control C0
reproduces ewma_band_v1's production-E2 C0 row via `--harness`.

## Question

Does dynamic relative-strength rotation between two leveraged equity indices
(QLD* on Nasdaq-100, SSO* on S&P 500) beat the production rule, which holds
QLD* whenever E2 is on? Static QLD+SSO splits lost 1.2–2.7 pp
(trend_robustness_v2). Untested: choose the stronger index dynamically.

## Data

- `data/historical/stress_robustness_v1_daily.csv` (QQQ, SPY adjclose total-return
  proxy) and `stress_robustness_v1_dtb3.csv`.
- `data/historical/ewma_band_v1_gspc.csv`, `ewma_band_v1_index.csv`,
  `ewma_band_v1_tb3ms.csv` (for OOS GSPC and the market index pairs).

No downloads; byte copies already on disk (see DATA_MANIFEST.json).

## Instruments

- QLD* = daily-reset 2x on QQQ total return, expense 0.95%/yr + DTB3 (same model).
- SSO* = same construction on SPY total return, expense 0.89%/yr + DTB3.
- Vol sizing per held instrument: `vt = min(1, 0.35 / vol20)` of the held
  instrument's synthetic close; total weight capped at 0.98.

## Arms (fixed)

- C0 = production E2 on QQQ: hold QLD* when E2 on, else cash.
- Q1 = when E2 (on QQQ) is on, hold QLD* if QQQ 126-session return >= SPY's,
  else SSO*. Daily re-evaluation with 2 pp hysteresis on the relative return
  to limit switching. Hysteresis applied to the index currently held: switch
  only when the other index leads by > 2 pp.
- Q2 = Q1 with 252-session relative return.
- Q3 = each index (QQQ/SPY) uses its own E2 vote; hold the stronger
  (126-session return) of those still on; cash if neither. No hysteresis.
- Q4 = Q3, but when both are on and the 126-session return gap is < 5 pp,
  split 50/50 (each leg sized by its own vt, total capped 0.98).

Resize band = 0.5 pp (production) on per-leg weight drift; cost = 4 bps x m
per side of traded notional.

## Mechanics

Decide at the close, fill at next open, idle cash earns zero, as in
ewma_band_v1. Financing: base (DTB3 + 0.95% / 0.89%) or calibrated
(+0.75% pp). Main window 2000-03-09 -> 2026-09-29.

## Gates

Main window G1-G6, G9 exactly as ewma_band_v1 (G1 return at m=2 both
financings; G2 max DD <= C0+1 pp; G3 2017+ >= C0-1 pp; G4 >=2 of 3
sub-periods; G5 main block-20 bootstrap median delta >=0 both financings;
G6 return at m=1; G9 turnover <= 2 x C0).

OOS reduced G7 (pre-registered because relative rotation needs two aligned
indices): windows (1) ^IXIC vs ^GSPC 1975-01-02 -> 1999-12-31 (measured from
1975 on common US trading days; GSPC leg from ewma_band_v1_gspc, IXIC leg
from ewma_band_v1_index) and (2) ^GDAXI vs ^FTSE 1989-01-02 -> 2026-09-29
(common European trading days). Gate: pooled median delta >=0, all windows
with delta >= -0.5 pp, all windows with max-DD delta <= +3 pp and worst
+8 pp.

G8 = pooled (both pairs) block-20 bootstrap median delta >=0.

## Selection rule

No selection among arms other than reporting gates; each arm evaluated
individually. PASS requires pass_all gates.