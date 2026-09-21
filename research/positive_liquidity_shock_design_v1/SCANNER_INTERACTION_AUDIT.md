# Scanner interaction audit

## Verdict

`SCANNER_REMOVES_CORE_ANOMALY_POPULATION`

Production requires price at least $5, trailing 20-valid-bar median dollar
volume at least $20 million, data completeness at least 0.95 and ATR between
0.5% and 12%. Before those checks, historical Stage A admits only the top 300
symbols by trailing median dollar volume.

This is materially stronger than a modest investability floor. It truncates
the cross-section toward its most actively traded names. The original U.S.
effect survives in its large, most-liquid and S&P 500 subsamples, but is much
stronger among small/illiquid stocks. Korea finds the liquid-stock relation
negative. There is no clean post-publication U.S. proof that the top-300/$20M
population retains a positive long-only effect.

The scanner cannot be changed in this campaign. Structural scoreable-name
counts measure supply after the frozen scanner, but dense availability cannot
substitute for predictive evidence.
