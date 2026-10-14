# riskoff_rotation_v1 pre-registration

Question: when production E2 is OFF (currently cash, zero yield), does holding an
UNLEVERED defensive asset gated by its OWN trend/momentum improve long-run
return vs S&P 500 without materially worse drawdown?

Control C0 = production E2 (`equity_leveraged_trend_v1` 1.1.0) exactly:
close > SMA200; 252-session price return > 0; close > 126-session channel
midpoint; at least 2 of 3. Hold QLD*, weight min(1,0.35/vol20), total cap 0.98.
Decide at close, fill next open, 0.5 pp resize band. Idle cash earns 0.

Mechanics reused from ewma_band_v1 / leverage_frontier_v1:
synthetic QLD* (daily-reset 2x QQQ total return), base and calibrated
financing (+0.75%/yr x (L-1)), cost 4 bps x m per unit traded, m=2 gated and
m=1 reported, window 2000-03-09 -> 2026-09-29.

Defensive assets (unlevered):
- BOND10: 10y constant-maturity par bond built from DGS10, minus 0.15%/yr
  (leverage_frontier_v1 construction).
- GOLD: real GLD from 2004-11-19, GC=F minus 0.40% before that
  (leverage_frontier_v1).
- BILLS/T-bill proxy: DTB3 - 0.10% for the hurdle in R3 only.

Vol20 and signals computed on each asset's own close level.

Trend gating:
- BOND10 trend up: 252-session total return > 0 AND close > 200-session SMA.
- GOLD trend up: 252-session total return > 0 AND close > 200-session SMA.

Arms (all parameters fixed):
- C0: control, no defensive.
- R1: E2 off -> BOND10 at weight 0.98 only if BOND10 trend up; else cash.
- R2: E2 off -> GOLD at weight 0.98 only if GOLD trend up; else cash.
- R3: E2 off -> the better of BOND10/GOLD by 252-session return, only if that
  return exceeds the T-bill 252-session return; else cash. Weight 0.98.
  (R3 does not add an SMA gate beyond the return hurdle; see note.)
- R4: same selection as R3, but the chosen defensive asset is sized by
  min(1, 0.10/vol20) (10% vol target) instead of 0.98; weight capped at
  0.98 total.

Each arm runs on the same QLD* / defensive close levels. Total portfolio weight
capped at 0.98 by proportional scaling.

Gates reused from ewma_band_v1 G1-G9:
- G1 m=2 return under both financings (arm CAGR >= C0).
- G2 max DD <= C0 + 1 pp.
- G3 2017+ CAGR >= C0 - 1 pp.
- G4 >= 2 of 3 sub-periods (2000-08, 2009-16, 2017+).
- G5 block-20 bootstrap median delta >= 0 under both financings.
- G6 G1 at m=1.
- G7 OOS windows (reduced; see below).
- G8 pooled OOS block-20 bootstrap median delta >= 0 (reduced; see below).
- G9 turnover <= 2 x C0 turnover.

OOS reduced sets (pre-registered, data-limited):
- BOND10 arms (R1, R3, R4): ^GSPC 1957-1999 and ^IXIC (M1) 1975-1999
  (US 10y yields available).
- GOLD arms (R2, R3, R4): ^IXIC 1975-1999 (gold reliable from 1975).
- R1/R2 do not run on windows where the other asset is required unless single
  asset; for mixed/selection arms, selection falls back to the asset available
  on that window.
- No OOS data for M2-M5, and no gold OOS beyond ^IXIC. G7/G8 therefore use
  the reduced window set; a reduced gate passes if the median CAGR delta >= 0
  across the available windows, at least all but one window have CAGR delta >=
  -0.5 pp, max DD diff <= 3 pp, and pooled bootstrap median delta >= 0 on the
  available windows.
- Arms missing a window treat that window as not applicable (not as pass).

Bootstrap: block-20, 5,000 paths, seed 20261011, main window pool; for
calibrated financing add 0.75%/yr. Block bootstrap supports 10y paths.

Amendments: none yet.
