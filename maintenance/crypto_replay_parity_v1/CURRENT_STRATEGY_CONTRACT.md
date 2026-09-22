# Current crypto strategy contract

Audited current checkout; authoritative selection: `config/settings.yaml`, `strategy.crypto`.

- Identity `crypto_low_variance_hysteresis_v1`, version1.0.0, daily signal bars; lookback22 daily returns from23 closes; top2;96h UTC epoch-block cadence; retention rank<=4.
- `crypto_low_variance.py:_score_candidates` computes sample standard deviation of arithmetic close-to-close returns, unannualized, ranks lowest first, ties by canonical symbol. Scanner membership is required before ranking. Closes must be finite/positive and endpoint span exactly22 calendar days. It does not independently check every interior timestamp; normalized complete daily inputs supply that guarantee.
- `crypto_low_variance_hysteresis.py:decide` retains positive held weights >1e-12 that remain ranked<=4 and eligible, sorts retained by rank/symbol, caps to2, fills vacancies from best rank upward. Targets equal weight across selected symbols; no qualified candidates means cash. Alpha targets are then scaled/clipped by product allocation/risk/execution rules.
- Configured sleeve allocation0.05; risk max crypto exposure0.10, position0.05, positions2; cash buffer0.40, gross0.60, daily loss0.005, max drawdown0.05. A replay that overrides these is a distinct protocol, even with the same strategy name.
- Configured pool22 pairs; expanded source has25 pairs. Raw presence does not establish scanner eligibility. Daily scanner configuration and point-in-time membership must be pinned separately. Runtime scanner uses1h history14d, completeness>=0.9, median dollar volume>=1000, ATR0.004..0.2, spread<=200bps, BTC correlation<=0.97 and category caps. Backtest universe construction is a separate adapter and its inputs must be disclosed.

No strategy/configuration changes were made. Historical headline comparisons require the exact historical selection, risk and scanner configuration; identity alone is insufficient.
