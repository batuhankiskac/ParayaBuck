# H14c post-hoc capacity amendment — 2026-09-09

This is a research-only, `POST_HOC_EXPLORATORY` amendment defined after the H14b result. It has no independent holdout and cannot change H14a or H14b.

The dated V3 weights remain unchanged. On each H14 candidate event date, add at most a 0.05 SPY candidate sleeve funded from V3 cash only when the measured pre-addition values satisfy equity <= 0.55, gross <= 0.60, cash >= 0.45 so cash remains >= 0.40 after funding, and positions + 1 <= 6. On inactive dates the V3 return is used unchanged. The comparison is unchanged V3. Candidate paths use the receipt-bound 1x, 2x, and 3x cost cells; turnover is null when no target turnover ledger is available.

For each full (2022-01-01..2025-12-31), recent (2024-01-01..2025-12-31), and true holdout (2026-01-01..2026-07-31) window, report total return, CAGR using 252 sessions/year, population-volatility Sharpe, downside-root-mean-square Sortino, maximum drawdown, worst day, 5% lower-tail CVaR, V3 deltas, return correlation, active-day contribution, calendar-year regime, 10,000 21-session moving-block bootstrap, 10,000 paired-sign permutation, and +7 calendar-day placebo. These outputs are diagnostic statistics only; no result is eligible, admitted, promoted, or integrated.
