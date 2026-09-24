# Corrected old gate decomposition

Authority: annotated `archive/research/residual-reversal-local-v1-2026-09` peeled to `a3a54cff64a9312300ef700f2fe9409435a064d3`, especially `corrected_replay_v1/gate_table.json`, `decision.json`, and `sleeve_attribution.json`. The earlier uncorrected report is invalidated.

The frozen candidate is `equity_short_term_residual_reversal_v1`: US equity daily SPY residuals with V3 aligned simple-return rolling OLS beta (273 returns), exactly 21 completed residual returns, skip 0, compounded score with more negative first, no positive-score gate, long only, weekly top six, V3 IVOL rejection q=0.70, inverse 63-return residual volatility weight, 10% name cap. No parameter is changed here.

Corrected standalone 1x: net return 0.8849, CAGR 0.0684, Sharpe 0.6483, Sortino 0.8499, maximum drawdown 0.1778, Calmar 0.3849. At 2x and 3x costs, net return is 0.7235 and 0.5747. Daily V3/reversal correlation is 0.4761. These are **archived** outcomes; this review calculates no new returns.

Gates 1 standalone viability, 2 cost robustness, 3 diversification, 5 drawdown, 6 validation contribution, 7 turnover economics, and 8 concentration: **PASS**. Gate 4 capital-neutral portfolio contribution: **FAIL**, the sole failed gate. Only Calmar improved among its full-period CAGR, Sharpe, Sortino, and Calmar comparisons. The historical classification `ALPHA_VALID_PORTFOLIO_REJECTED` remains valid.

Old treatment used V3 0.4125 and reversal 0.1375, with a research-only six-slot 4/2 sleeve quota and overlap-aware spare filling. All 495 treatment decisions had capacity shortfall. Mean realized V3 target exposure was approximately 0.2066 against 0.4125 intended, and reversal 0.0501 against 0.1375. Corresponding unused allocation was approximately 0.2059 and 0.0874. No crypto was in that treatment account.

Standalone reversal turnover was 302.5174 versus V3 168.4613; mean holding time 274.91 versus 519.54 hours. Weekly turnover creates real slot pressure but the archived turnover gate passed. No new turnover economics was calculated.
