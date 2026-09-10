# H14c post-hoc capacity diagnostic — 2026-09-09

Status: `POST_HOC_EXPLORATORY_DIAGNOSTIC_ONLY`. This amendment was specified after H14b results, has no independent holdout, preserves every dated V3 weight, and cannot establish economic eligibility.

The candidate is SPY from the receipt-bound H14 cell. Its five percent sleeve is funded from measured V3 cash only on dates where adding one position passes equity <=55%, gross <=60%, cash >=40%, and positions <=6. Inactive dates use unchanged V3 returns. Costs are the existing 1x/2x/3x candidate curves; turnover remains null because no target turnover ledger is available. Capacity uses the strictly prior V3 decision, because a target stamped on the event date fills on the next open.

Full, recent, and true-holdout results include return/CAGR/Sharpe/Sortino/max drawdown/CVaR deltas, correlation, active-day contribution, calendar-year regime, block bootstrap, paired-sign permutation, and a separately labeled POST_REVIEW_DIAGNOSTIC +7 trading-session shifted native schedule. The actual schedule has thirty-one active dates across fourteen months; true holdout has zero actual active dates. The shifted schedule has 37 full-window capacity observations (19 recent, zero true-holdout), and is diagnostic only. H14a/H14b remains preserved separately and no integration or promotion is inferred.

- 1x: full delta return=0.003236481, CAGR=0.000727932, maxDD=0.002166309; active days=31; caps pass=True
- 2x: full delta return=0.003149800, CAGR=0.000708456, maxDD=0.002127137; active days=31; caps pass=True
- 3x: full delta return=0.003063124, CAGR=0.000688980, maxDD=0.002087966; active days=31; caps pass=True
