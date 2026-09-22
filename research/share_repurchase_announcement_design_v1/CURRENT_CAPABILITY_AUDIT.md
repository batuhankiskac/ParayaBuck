# Current capability audit

Research family: open-market share repurchase announcement underreaction.
Scope is source/design only, as of 2026-09-22. No candidate return data was
loaded, computed, ranked, correlated, or backtested.

## Starting state and Stage A receipt

Starting production main and freshly fetched origin/main both equaled
`b81e9d9fef3744723449f4c019f2a5dd75f2e2cb`. Stage A updated only
`docs/research/research_register.md` and
`docs/research/research_archive_inventory.md`. Updated clean main/origin/main:
`16db2eaad9061a1669166ef9ab6717853337fafc`.
Both annotated exit-audit tags were pushed and their remote peeled targets
verified before deleting the two completed local audit branches and the
remaining remote crypto V2 branch. V1 remote branch was already absent.
The unrelated `research/schedule-13d-activism-design-v1` was preserved.
This new branch was created only after Stage A succeeded.

Full normal suite on updated documentation: 1,138 passed, 1 skipped, 2 warnings.
Initial sandbox run: six localhost-bind PermissionErrors; same suite with
local port permission passed. No production code fix was required.

## Live checkout evidence (static, not live-account inspection)

CodeGraph was consulted before code search. Sources inspected:

- `src/parayabuck/backtest/lifetimes.py`: strict canonical lifetime schema and
  effective rename intervals; unresolved terminals fail closed.
- `src/parayabuck/portfolio/equity_residual_momentum.py` and
  `equity_residual_momentum_v3.py`: price-history residual-momentum family,
  not corporate-disclosure parsing.
- `src/parayabuck/markets/sectors.py`: finite symbol classification; unknown
  symbols default to equity. This is not a historical ordinary-share master.
- `config/settings.yaml`: PAPER, LIVE disabled, V3 equity allocation 0.55,
  crypto hysteresis allocation 0.05, six total account positions, 0.10 per-name
  maximum, gross exposure 0.60 and cash buffer 0.40. V3's raw top_n=8 does not
  override the account's six-position limit. No runtime account was queried.
- `docs/research/core_satellite_combination_policy_v1.md`: one reserved slot
  would have a real cash/core opportunity cost. A design decision is not
  combination approval.

## Available versus absent

The local `data/historical/equity_entity_lifetimes_full_v5.csv` contains 9,010
rows, 16 columns and one nonempty rename-chain field. SHA256:
`ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`.
Only this identity metadata CSV was read; no OHLCV file was opened.
It contains entity/provider IDs, symbols, lifetime dates, venue, quality and
terminal evidence, but no CIK or security-title/share-class column.

Targeted source and data filename/content searches found no canonical CIK-to-
entity bridge, announcement corpus, repurchase parser, or PIT market-cap panel.
That is a current capability finding, not proof that such data cannot be
acquired. Existing generic bar/lifetime/cost machinery does not establish
filing availability, issuer mapping, deduplication or event supply.

A future authorization/market-cap ranking would require causally available
historical shares, share-class treatment and a matching pre-event price.
Today's outstanding shares cannot fill the gap. No such ratio was calculated.
An earnings-controlled mechanism may also need PIT earnings expectations;
none was demonstrated in this campaign. No new strategy or event scheduler
was implemented because readiness must be established first.
