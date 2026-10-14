# fast_reentry_v1 pre-registration (2026-10-14)

## Question
Does an explicit "early re-entry" path on top of production E2 (2 of 3 votes) recover the
early phase of V-shaped rallies (2009, 2020, 2023-like) without worsening drawdown? Production E2
needs 2 of 3 votes to re-enter; SMA200 and TSM252 lag, so E2 re-enters late. A dedicated fast
entry path, active only while E2 is off, is tested.

## Control C0
Production E2 1.1.0: 2 of 3 votes (SMA200, 252-session price return > 0, DON126 midpoint) on QQQ
price close; size vt = min(1, 0.35/vol20) on synthetic QLD*, total cap 0.98; decide at close,
fill next open; 0.5 pp resize band.

## Arms (all parameters fixed)
Common: exits unchanged = production E2. While the early path is active, size with production vol
sizing (vt). The early path ends when E2's own vote turns on (then plain production) or when its
own exit condition fires. All arms trade at the next open and use the 0.5 pp resize band.

- R1: while E2 is off, enter early if QQQ close > SMA50 AND QQQ 20-session annualized close
  volatility < its own trailing 252-session median AND close > close 20 sessions ago. Exit the early
  position if close < SMA50. Weight = production vt.
- R2: R1 mechanics, but weight = 0.5 x production vt until E2 turns on (then full production vt).
- R3: while E2 is off, enter early if the DON126 vote is on AND close > SMA50. Exit if either
  turns off. Weight = production vt.
- R4: R1, but entry allowed only within 252 sessions after the latest E2 exit that followed a
  QQQ drawdown >= 20% from its 252-session high (crash-recovery only). Weight = production vt.

## Data
Inputs copied from ewma_band_v1 (see DATA_MANIFEST.json): ^GSPC 1927?->1999, multi-index
(^IXIC 1971-1999, ^GSPC 1934-1956, ^N225, ^GDAXI, ^FTSE), DTB3/TB3MS, stress daily + DTB3.
Main window 2000-03-09 -> 2026-09-29.

## Gates
Reuse ewma_band_v1 G1-G9, all six OOS windows, close-only prices, usable.

## Selection
Any candidate passing all gates; otherwise "none". No Stage B unless a pass.

## Planned reporting
Time in market, round trips/yr, and per-episode P&L of the early positions (early_active_share and
segment counts in RESULTS).
