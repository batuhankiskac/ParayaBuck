# Literature rationale

This review supports the economic mechanism only. It is not evidence that the
ParayaBuck implementation will work.

## Mechanism support

Moreira and Muir document that portfolios taking less risk when volatility is
high can improve Sharpe ratios because expected returns do not move one-for-one
with volatility. Their evidence covers the market and several equity factors,
including momentum. This supports testing a continuous inverse-volatility
exposure rule, but does not validate this candidate's 21-day SPY measure,
252-observation median baseline, data, execution, or thresholds.

Andersen, Bollerslev, Diebold, and Labys establish realized volatility as a
usable time-series measure and report persistence/forecastability in realized
volatility. This supports using completed-bar realized volatility as an
information set, while the experiment must still prove that the exact signal is
available at each decision timestamp without lookahead.

Daniel and Moskowitz show that momentum crashes are partly forecastable and are
associated with panic states after market declines, high volatility, and market
rebounds. That motivates checking whether the treatment reduces exposure before
major control drawdowns. The paper studies a different dynamic strategy and
does not establish that broad-market SPY volatility alone is sufficient for this
long-only residual-momentum portfolio.

## Primary sources

- Moreira and Muir, *Volatility-Managed Portfolios*, Journal of Finance,
  [publisher record](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513)
  and [NBER working paper 22208](https://www.nber.org/papers/w22208).
- Andersen, Bollerslev, Diebold, and Labys, *Modeling and Forecasting Realized
  Volatility*, Econometrica,
  [publisher PDF](https://onlinelibrary.wiley.com/doi/pdf/10.1111/1468-0262.00418).
- Daniel and Moskowitz, *Momentum Crashes*, Journal of Financial Economics,
  [NBER working paper 20439](https://www.nber.org/papers/w20439).

## Boundary

The literature rationale was written before the static comparator and economic
outcomes. It does not select parameters, create a regime classifier, import a
published strategy, or override any experiment gate. All acceptance evidence
must come from the frozen local experiment and its independent replay.

