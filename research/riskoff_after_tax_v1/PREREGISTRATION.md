# riskoff_after_tax_v1 pre-registration

Question: does riskoff_rotation_v3 L2 still beat production E2 (C0) after Turkish
personal income tax for a Turkish-resident investor?

Arms
- C0 = production E2 exactly (QLD* only; base financing, m=2, 0.5 pp band).
- L2 = C0 plus defensive leg rotation: monthly bond/gold selection, 10 pp defensive
  resize band, defensive assets IEF-cost bond10 (BOND10*) and gold proxy (GOLD*),
  vol-targeted at min(1,0.10/vol20), capped at total 0.98.
- SPY buy-and-hold is reported as reference.

Tax model (reuse after_tax_tr_v1)
- USD gains converted to TRY at CBRT rates; Yİ-ÜFE indexation when the index rose
  >=10% between the month before purchase and the month before sale; 2026 GVK 103
  progressive tariff deflated by Yİ-ÜFE; 22,000 TL declaration threshold (2026);
  20% US dividend withholding credited; FIFO lots; losses net within the calendar
  year with no carry-forward; tax funding sales at open if cash short; whole
  portfolio liquidated at the end at m=2 cost.
- Extended to multiple instruments: every trade in QLD*, BOND10*, GOLD* creates or
  consumes FIFO lots per instrument; gains/losses assessed per calendar year across
  all instruments; tax due stored per account and paid from shared cash.
- Tax payments from the shared cash at the first US session on/after 31 Mar and
  31 Jul of the following year.

Data
- Main prices: data/historical/stress_robustness_v1_daily.csv (QQQ/SPY) plus
  synthetic QLD* via leverage_frontier_v1 helpers; risk-off assets from
  leverage_frontier_v1_daily.csv; DTB3 from stress_robustness_v1_dtb3.csv.
- FX: data/historical/after_tax_tr_v1_usdtry_cbrt.csv
- Yİ-ÜFE: data/historical/after_tax_tr_v1_yiufe.csv

Windows / capital
- 2000-03-09 -> 2026-09-29 and 2017-01-03 -> 2026-09-29.
- $10k, $100k, $1M.

Reconciliation check
- With only QLD* trades (C0, pre-tax) reproduces the production harness to 0.01 pp.
- C0 after-tax CAGR reproduces after_tax_tr_v1 exactly at $10k and $100k.

Relevant output
- After-tax CAGR liquidated, total tax by year, max drawdown, trade counts,
  L2-C0 delta, defensive leg vs QLD realized TRY gains.

Report-only (no gates).
