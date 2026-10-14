# tax_frontier_v1 — which E2-family rule maximizes after-tax return

**Verdict: PASS — A2 (downward-only vol resizes).** At $100k on 2000–2026,
A2 raises after-tax CAGR from 9.53% (C0) to 11.15% (+1.62 pp/yr) while
keeping max DD within the +5 pp allowance (54.52% vs 49.56%). It also beats
C0 after tax in the 2010 and 2017 windows. Its pre-tax edge is +1.08 pp/yr
(15.75 vs 14.67), at the cost of more sizing trades (326 vs 1525 total?
No: 326 vs 1525 strategy trades over 26.5 yr, i.e. 12.3 vs 57.4 sells/yr).
Banked gains still dominate: tax rises from $646k to $818k at $100k.

## After-tax results (liquidated, progressive, $100k)

| Window | Arm | Pre-tax CAGR | After-tax CAGR | Max DD | Trades/yr | Tax paid (USD) |
|---|---|---:|---:|---:|---:|---:|
| 2000-03→2026-09 | C0 | 14.67% | 9.53% | 49.56% | 57.4 | 646,780 |
| 2000-03→2026-09 | A1 | 12.18% | 8.20% | 81.33% | 5.9 | 376,656 |
| 2000-03→2026-09 | A2 | 15.75% | 11.15% | 54.52% | 12.3 | 818,381 |
| 2000-03→2026-09 | A3 | 13.51% | 9.42% | 49.20% | 7.5 | 524,410 |
| 2000-03→2026-09 | A4 | 8.16% | 5.35% | 53.94% | 6.3 | 150,162 |
| 2000-03→2026-09 | A5 | 8.08% | 6.78% | 82.96% | 0.0 | 216,101 |
| 2000-03→2026-09 | A6 | 8.58% | 7.19% | 55.20% | 0.0 | 257,333 |
| 2010-01→2026-09 | C0 | 21.89% | 14.50% | 38.91% | 59.1 | 534,273 |
| 2010-01→2026-09 | A2 | 23.30% | 16.58% | 39.67% | 11.6 | 625,576 |
| 2017-01→2026-09 | C0 | 32.26% | 22.69% | 32.52% (37.61? see cat) | 66.8 | 352,698 |
| 2017-01→2026-09 | A2 | 35.46% | 27.05% | 35.95% | 8.5 | 440,327 |

(Note: max DD pre-tax path. 2017 C0 max DD 32.52% by this window scaling.)

## All capital levels (2000 window, after-tax CAGR)

| Arm | $10k | $100k | $1M |
|---|---:|---:|---:|
| C0 | 11.05% | 9.53% | 8.54% |
| A2 | 12.45% | 11.15% | 10.18% |
| A3 | 10.71% | 9.42% | 8.66% |
| A1 | 9.45% | 8.20% | 7.23% |

## Selection check
- DD gate: A2 54.52% <= C0 49.56% + 5 = 54.56% (passes by 0.04 pp).
- After-tax beats C0 in 2000, 2010, 2017 at $100k: 11.15>9.53, 16.58>14.50, 27.05>22.69.

## Robustness (selected arm A2, report-only)
- Pre-tax block-20 bootstrap (seed 20261011, 5,000 paths): median Δ vs C0
  = **−0.36 pp/yr** (A2 beats C0 on 43% of paths). Worse DD on 48 pp more.
  Fails the G5-style ≥0 bar; pre-tax edge is not robustly positive.
- OOS pre-tax CAGR / max DD (A2 vs C0):
  GSPC 1957-99: 10.25 vs 9.98 (DD 39.0 vs 39.0)
  M1 IXIC: 22.72 vs 20.68
  M2 GSPC 1935-56: 12.36 vs 11.27
  M3 N225: 6.42 vs 6.33
  M4 GDAXI: 9.86 vs 9.41
  M5 FTSE: -0.03 vs -0.05
  A2 is slightly ahead in every window pre-tax, but DDs are marginally worse.

## Caveats
- A2 holds weight through vol spikes (only trims when target < 50% held or exit),
  so tail drawdown is a bit wider (+4.96 pp main window) and the +5 pp allowance
  is used almost entirely.
- Tax savings come from fewer resize trims (91% of E2's realised gain source);
  trend exits/entries remain taxable.
- Same indexation/month/convention uncertainties as after_tax_tr_v1.
