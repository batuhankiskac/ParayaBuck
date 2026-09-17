# Literature and SH Product Basis

This review predates the economic replay. It supports mechanism plausibility only; it does not validate the frozen ParayaBuck implementation.

## Mechanism evidence

- Moskowitz, Ooi, and Pedersen, “Time Series Momentum,” *Journal of Financial Economics* 104(2), 2012, DOI `10.1016/j.jfineco.2011.11.003`: own past returns predicted later returns across 58 liquid futures, with persistence over roughly 1–12 months. This supports testing a predeclared long-horizon own-return signal, but the paper uses diversified futures, monthly trading, excess returns, and volatility scaling rather than this SPY/SH overlay.
- Hurst, Ooi, and Pedersen, “A Century of Evidence on Trend-Following Investing,” *Journal of Portfolio Management* 44(1), 2017, DOI `10.3905/jpm.2017.44.1.015`: diversified trend following showed long historical persistence and crisis performance. It does not establish a single-index zero threshold, weekly cadence, or fixed SH weight.
- Faber, “A Quantitative Approach to Tactical Asset Allocation,” *Journal of Wealth Management* 9(4), 2007, DOI `10.3905/jwm.2007.674809`: long-horizon moving-average timing motivates reducing equity exposure to address volatility and drawdown. Its cash/tactical design is not the frozen 252-session sign rule or an inverse-ETF treatment.
- Brock, Lakonishok, and LeBaron, “Simple Technical Trading Rules and the Stochastic Properties of Stock Returns,” *Journal of Finance* 47(5), 1992, DOI `10.1111/j.1540-6261.1992.tb04681.x`: long historical index evidence found materially different return distributions under simple technical signals. It contains no investable SH or current cost/accounting evidence.
- Kaminski and Lo, “When Do Stop-Loss Rules Stop Losses?”, *Journal of Financial Markets* 18, 2014, DOI `10.1016/j.finmar.2013.07.001`: state-dependent risky-exposure reduction can sometimes reduce volatility. It supports the defensive-cash attribution control rather than proving inverse exposure adds value.

Counterevidence is material. Huang, Li, Wang, and Zhou, “Time Series Momentum: Is It There?”, *Journal of Financial Economics* 135(3), 2020, DOI `10.1016/j.jfineco.2019.08.004`, reports weak asset-level and out-of-sample evidence. Kim, Tse, and Wald, “Time Series Momentum and Volatility Scaling,” *Journal of Financial Markets* 30, 2016, DOI `10.1016/j.finmar.2016.05.003`, attributes much reported performance to volatility scaling. The protocol therefore treats the literature as mechanism motivation, never as candidate evidence.

## SH primary product evidence

- ProShares' current SH product page states that SH seeks `-1x` the **daily** S&P 500 return, rebalances daily, distributes quarterly, and can differ materially from negative one times the index over holding periods longer than a day: <https://www.proshares.com/our-etfs/leveraged-and-inverse/sh>.
- SH's SEC-filed Summary Prospectus dated 2025-09-26 describes daily rebalancing, derivatives and money-market holdings, compounding/path, correlation, financing, counterparty, transaction, and market-price/NAV risks: <https://www.sec.gov/Archives/edgar/data/1174610/000119312525220616/f42948d1.htm>.
- The SEC investor bulletin explains why daily-reset leveraged and inverse ETFs can diverge quickly from their stated daily multiple over multiple days: <https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/sec>.
- Avellaneda and Zhang, “Path-Dependence of Leveraged ETF Returns,” *SIAM Journal on Financial Mathematics* 1(1), 2010, DOI `10.1137/090760805`, derives the role of underlying return, realized variance, financing, and expenses. It supports using actual SH bars, not synthetic negative SPY returns.
- ProShares' annual SH shareholder report confirms that reported total returns reinvest distributions and reflect derivatives, financing, expenses, transaction costs, correlation, and volatility/compounding: <https://www.proshares.com/globalassets/proshares/documents/annual-reports/annual_sh.pdf>.

SH underwent a 1-for-4 reverse split effective before the 2024-11-07 open. The SEC-filed issuer announcement is <https://www.sec.gov/Archives/edgar/data/1415311/000119312524245617/d895310dex991.htm>. The local price panel is already split-adjusted; reverse splits therefore remain audit evidence and are not applied again. Cash distributions are credited once.

## Evidence boundary

The literature and product documents establish that the mechanism is plausible and that daily-reset/path-dependent accounting matters. They do not show that a 252-session SPY sign gate, weekly decisions, 0.10 SH overlay, unchanged V3, or six-slot ParayaBuck account improves economics. Only the frozen A/B/C replay, chronological validation, cost stress, actual SH data, capacity accounting, and independent verifier can supply that evidence.
