# Current capability audit

Capability status: `BLOCKED`; campaign decision: `DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`

## Repository state

The campaign started from a clean `main`. After `git fetch --all --prune`,
`HEAD`, local `main`, and `origin/main` were all
`d3dea037789c16c335744b56d216289a4536096c`. The research branch is
`research/opportunistic-insider-purchases-design-v1`.

Production remains PAPER-only: equity is `equity_residual_momentum_v3` at a
0.55 sleeve cap; crypto is `crypto_low_variance_hysteresis_v1` at 0.05; gross
is capped at 0.60; target cash is at least 0.40; a name is capped at 0.10; and
the account-wide position limit is six. LIVE remains disabled with a zero
dollar limit. These facts are in `config/settings.yaml:137-184,205-209`.

## What exists

- The historical lifetime loader supplies effective dates, current and
  historical symbols, provider asset IDs, exchange, a rename-chain field, and
  evidence/quality fields (`src/parayabuck/backtest/lifetimes.py:16-69`).
- The canonical V5 lifetime file contains 9,010 entity rows.
- The PIT scanner can apply the current $5 price and $20 million trailing
  20-session median-dollar-volume gates using only bars before the decision.
- The portfolio engine has completed-bar/next-tradable-bar ordering.
- `month_end_first_open` supplies an exchange-calendar month-end close to exact
  next-month first-open contract and fails closed rather than rolling a missing
  symbol to a later session.
- Account-wide slot accounting includes equity, crypto, external positions,
  open-order reservations, and unavailable inventory.

## What does not exist

There is no SEC Form 3/4/5 ingestion or ownership-XML parser in production or
research code. The historical lifetime schema has no issuer CIK, accession,
reporting-owner CIK, as-filed ticker provenance, security title/class/type,
CUSIP/FIGI, Series/Class identifier, or listing-event provenance. Exact ticker
matching therefore cannot prove issuer identity, common-equity identity, or
ticker-reuse safety.

The broad historical `US_EQUITY` data is explicitly not a common-stock
taxonomy (`docs/research/equity_universe_limitations.md`). The prior durable
security-master verdict is
`SECURITY_MASTER_CURRENT_ONLY_HISTORICAL_BLOCKED`.

There is also no SEC-acceptance event scheduler. Monthly aggregation can be
represented with the existing exact month-end/first-open primitive in a
research harness; event-level entry would require a new research-only adapter.

## Slot distinction

The canonical 5+1 protocol is an equity research comparator, not total-account
deployability proof. If crypto occupies two positions, only four global slots
remain. A future satellite that passes an equity 5+1 test could still be
undeployable in the actual account.

## Capability conclusion

The repository can support no-peek structural counts, PIT bar-based liquidity
filters, and conservative monthly execution. It cannot presently prove the
mandatory SEC-to-historical-security mapping or raw-filing provenance.
Those are real blockers, but the campaign is rejected earlier by the external
modern/liquid/long-only/one-slot evidence gate; resolving them would not
authorize economics under this frozen campaign.
