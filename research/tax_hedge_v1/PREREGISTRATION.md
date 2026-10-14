# tax_hedge_v1 pre-registration

Question: can a Turkish-resident investor reduce Turkish income-tax drag by hedging
exposure changes with inverse ETFs instead of trimming the winning long leg?
(Turkish law as modelled taxes realized gains; buying/closing a hedge realizes nothing,
and hedge losses offset gains within the year.)

Arms
- H0 = production E2 (QLD*) control. Reported as reference only.
- H1: long leg TQQQ* (daily-reset 3x QQQ TR, expense 0.84%, financing base DTB3 with
  (L-1) spread 0; "financing like QLD* but on 2x borrowed"). Long weight at entry =
  (2/3) x production target (so exposure = 2x target, leaving cash). While E2 stays
  on, target rises are met by unwinding SQQQ* first then adding TQQQ*; target falls
  are met by buying SQQQ* (daily-reset -3x, expense 0.95%, inverse financing (|L|-1) x DTB3)
  with cash, trimming TQQQ* (FIFO) only if cash is short. Trend exit sells everything.
- H2: as H1 but QLD* (2x) / QID* (-2x), long weight = 0.98 x production target at entry,
  cash reserve 0 (hedges funded by trims). Report-only.

Mechanics
- Same as after_tax_tr_v1 / riskoff_after_tax_v1 multi-account tax model (CBRT TRY conversion,
  YI-UFE indexation, 2026 GVK 103 brackets deflated, FIFO lots per instrument, losses net
  within the calendar year without carry-forward, 20% US dividend withholding credited,
  tax funding sales at market open, whole portfolio liquidated at the end).
- Strategy trading uses open prices; costs 4 bps x m = 8 bps per side (m=2); involuntary
  trim on cash shortfall counts as a strategy trade? Tax-funding sales counted separately.
- A 0.005 (0.5 pp) net-exposure gap is required before a hedge adjustment; trend flips and
  entries/exits are always executed.
- Windows: 2000-03-09 -> 2026-09-29, 2010-01-04 -> 2026-09-29, 2017-01-03 -> 2026-09-29.
- Capitals: $10k, $100k, $1M. Pre-tax main window uses base financing m=2.
- Synthetic inverses: daily-reset -L x QQQ total return with expense and (|L|-1) x DTB3 financing.

Verdict criterion (no gates): H1 after-tax CAGR must beat C0's in all three windows at $100k.
Report pre-tax CAGR/DD main window base m=2, after-tax CAGR, tax paid/year, sells/year,
and the volatility decay cost implied by long+inverse co-holding (reported as the pre-tax
CAGR gap versus C0 and as days with both legs held).
