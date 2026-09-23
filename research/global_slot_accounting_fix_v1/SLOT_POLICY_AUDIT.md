# Maximum-position policy audit

The effective configuration remains `config/settings.yaml:179-182`:
`max_positions: 6`, `max_crypto_positions: 2`, gross 0.60, cash buffer 0.40.
`portfolio_risk_limits_from_config` maps these values to the shared
`PortfolioRiskLimits` used by both PAPER and LIVE. The architecture audit at
`research/account_capacity_architecture_audit_v1/POSITION_CAP_ENFORCEMENT_AUDIT.md`
describes an account-wide risk gate: every non-flat broker position, every
nonterminal local order, every broker-wide working order, and each earlier
market's same-cycle BUY claim is considered. A tiny nonzero residual is a
position. The exact six count is a configured policy, not a broker limit.

The pre-fix overlay (`src/parayabuck/execution/portfolio.py:265-337`)
computed target capacity using opposite-market positions and immutable
current-market positions, but treated a managed current-market position
omitted by the target as replaceable. This assumed a resulting SELL would
execute before a replacement BUY. The planner's rebalance band and trade
minimums can suppress that SELL. The documented two-phase path only applies
when an actual SELL order exists. Thus target portfolio count was enforced,
but actual occupied plus reserved order count was not universally enforced.

The enforced safety contract is one `(Market, canonical_symbol)` per nonzero
position or working opening order. A requested or partial exit retains its
slot until a fresh broker snapshot shows zero. Adds to held symbols do not
consume another slot. Terminal cancellation/rejection removes the order claim
once it is absent from both local and broker-open sources. This contract is
consistent with the architecture audit and the runtime's persisted-order
reservation design; no policy or strategy parameter was changed.
