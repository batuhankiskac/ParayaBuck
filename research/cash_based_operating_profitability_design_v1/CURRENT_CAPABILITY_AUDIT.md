# Current ParayaBuck capability audit

## Verified production state

- Start SHA: local `main` and `origin/main` both
  `d3dea037789c16c335744b56d216289a4536096c`.
- PAPER equity: `equity_residual_momentum_v3`, maximum allocation 0.55.
- PAPER crypto: `crypto_low_variance_hysteresis_v1`, maximum allocation 0.05.
- LIVE disabled with hard dollar cap zero.
- Account: target gross at most 0.60, target cash at least 0.40, at most six
  positions account-wide, at most 0.10 per equity name.
- Scanner: $5 minimum price, $20 million 20-day median dollar volume, 95%
  completeness and Stage-A top-300 liquidity path.

V3 is price-path based: positive 12-1 residual momentum, raw 6/9/12 percentile
consensus, SPY residualization, IVOL rejection, and inverse-volatility sizing.
CbOP's issuer-accounting source is distinct, but source distinctness is not
portfolio-contribution proof.

## Execution and lifecycle

The candidate-neutral `month_end_first_open` primitive can represent final June
NYSE close followed by the first July open and fails closed if that open is
unavailable. The generic product scheduler has monthly/weekly/hourly policies,
not a native annual-June policy; a later economic experiment would require a
research-only annual decision path.

Entity lifetimes contain 9,010 effective-dated identities but no CIK. Terminal
events, dividends and splits are handled; merger consideration/successor
security conversion is not a general security-master solution. An annual CbOP
hold should exit on canonical lifecycle events and leave cash—never substitute
midyear.

## Missing capability

Current `main` has no production accession-vintage fundamentals panel and no
fundamentals channel in `PortfolioContext`. No profitability strategy is
registered. Generic SEC acquisition patterns from prior research are reusable
only for fair access, caching, hashes and accession traces.

The current $5/$20m/top-300 scanner would materially narrow the paper's broad
ordinary-common-share universe. It is an architecture adaptation, not paper
fidelity, and cannot be selected based on returns.
