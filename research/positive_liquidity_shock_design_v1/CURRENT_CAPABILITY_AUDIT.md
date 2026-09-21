# Current capability audit

## Starting state

Local `main` and `origin/main` were both
`d3dea037789c16c335744b56d216289a4536096c` after fetch. This branch was
created directly from that clean commit. No completed research branch was
merged.

## Production identity

- PAPER equity is `equity_residual_momentum_v3`, maximum allocation `0.55`.
- PAPER crypto is `crypto_low_variance_hysteresis_v1`, maximum allocation
  `0.05`.
- Account target gross is capped at `0.60`, target cash buffer is `0.40`, and
  `max_positions=6` is account-wide. Equity per-name weight is capped at
  `0.10`.
- LIVE is disabled and its dollar cap is zero.

V3 is weekly positive 12-1 SPY-residual momentum with 6/9/12 residual-return
rank consensus, a 70th-percentile IVOL rejection and inverse-residual-volatility
sizing. It is not a liquidity-shock strategy.

## Available research primitives

The canonical v5 equity dataset contains split-adjusted SIP daily OHLCV for
9,010 identities. The repository also has effective-dated lifetime records,
explicit terminal handling, cash-dividend events and an exact
`month_end_first_open` helper. The helper maps the final NYSE close of month M
to the first NYSE open of M+1, fails if that exact open is unavailable and does
not inspect completed fill-day volume.

The generic monthly rebalance path is not equivalent: it decides after the
first completed bar of the new month and normally fills later. A future
candidate would require a research-only harness using the exact helper.

## Missing or degraded capability

- No production Amihud/liquidity-shock implementation exists.
- Historical security type is not available point in time for the broad
  `US_EQUITY` universe.
- Historical spread, complete tradability and sector metadata are degraded.
- The bar schema has no explicit total-return field. The final RFS paper uses
  CRSP daily stock returns; manufacturing dividend-inclusive daily returns from
  split-adjusted bars and raw cash-dividend rates is not authorized.
- A standalone six-equity research portfolio is mechanically possible in an
  isolated ledger, but two crypto positions can leave only four equity slots in
  the shared account. A later 5+1 result would still require the canonical
  shared-ledger combination protocol.

Production strategy, scanner, risk, execution, PAPER, crypto and LIVE state are
unchanged.
