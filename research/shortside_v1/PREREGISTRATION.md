# shortside_v1 — pre-registration (2026-10-14)

Written and committed with `screen.py`, `DATA_MANIFEST.json` before any non-control
arm was simulated on real data. The only real-data computations before this
commit are `screen.py --harness` (C0 = production E2 only).

## Question

On top of production E2, does holding an inverse Nasdaq-100 ETF in
downtrends (instead of cash) improve long-run risk-adjusted return without
materially worse drawdown? Positive historical asymmetry in 2000–02 and 2008
must not be destroyed by whipsaws in 2015–16/2018/2022.

## Data (no network)

- `data/historical/stress_robustness_v1_daily.csv` and `_dtb3.csv`.
- `data/historical/ewma_band_v1_gspc.csv`, `_index.csv`, `_tb3ms.csv`.
- `research/shortside_v1/E2_INTEGRATION_DEVIATION.json`.
- SHA-256 in `DATA_MANIFEST.json`.

## Mechanics (unchanged from production E2)

- Synthetic QLD* = daily-reset 2x QQQ total return, 0.95%/yr + DTB3.
- PSQ* = daily-reset -1x QQQ total return: `-R + credit - expense`, expense 0.95%/yr,
  credit max(0, DTB3 - spread), spread 0.75% base / 1.5% calibrated.
- QID* = daily-reset -2x analogously.
- Decide at close, fill at next open, total cap 0.98, cost 4 bps × m
  (m = 2 primary, m = 1 gated), idle cash zero, 0.5 pp resize band.

## Signal — production E2 1.1.0

At least 2 of: close > SMA200; 252-session price return > 0; close > midpoint of
126-session max/min. `votes` ∈ {0..3}.

## Vol targets

- Long side (C0): vt = min(1, 0.35/vol20_QLD).
- Short -1x (H1/H2/H4): vt = min(1, 0.20/vol20_QQQ).
- Short -2x (H3): vt = min(1, 0.35/vol20_QID).

## Arms (fixed)

- C0: production E2 (cash when off).
- H1: PSQ* at vt when 0 of 3 votes (all down).
- H2: PSQ* at vt when E2 is off (0 or 1 vote).
- H3: QID* at vt when 0 of 3 votes.
- H4: H1 but only while QQQ 20-session vol <= trailing 252-session median vol
  of QQQ vol.

Long side always E2 on; short side takes over disjoint sessions (cap 0.98).

## Gates (G1–G9, unchanged from ewma_band_v1)

G1 m=2 return both financings; G2 DD <= C0+1pp; G3 2017+ >= C0-1pp;
G4 >=2 of 3 sub-periods; G5 block-20 bootstrap median Δ >= 0 both; G6 G1 at m=1;
G7 six OOS windows; G8 pooled OOS bootstrap; G9 turnover <= 2× C0.
