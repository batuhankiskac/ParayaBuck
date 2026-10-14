# riskoff_rotation_v4 — independent confirmation of L2

Question: does L2 (r2 risk-off rotation) confirm on fresh instruments and data, and under
code audit + after-tax check?

Arms fixed:
- C0: each equity ETF's own E2 (2-of-3 SMA200/TSM252/DON126) with synthetic 2x daily-reset
  long when on, cash when off; sizing min(1,0.35/vol20), cap 0.98.
- L2: C0 when E2 on; when off choose BOND (real IEF TR) vs GOLD (real GLD TR) by 252-sess
  return if max(bond_ret,gold_ret) > bill 252-ret; chosen monthly on first session, mid-month
  choice held to month end if E2 turns off; sizing min(1,0.10/vol20) for defensive; resize only
  >10 pp. Cost 4bps x m per side.

Data: Yahoo v8 daily bars for 21 equity ETFs + IEF + GLD; DTB3 T-bill. Window per instrument:
common dates where IEF/GLD/equity all have bars, start at session 253. Quality rule <=5% bad.

Gates:
- F1 median Δ CAGR (calibrated, m=2) >=0 and >=60% instruments positive.
- F2 Δ max DD <= +3 pp in >=80%.
- F3 pooled block-20 bootstrap (2,000 paths each, seed 20261011) median Δ >=0.
- F4 F1 with doubled cost.
After-tax check report-only on main window, $100k, L2 vs C0.

Code audit section required in report.
