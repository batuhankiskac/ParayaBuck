# Position-cap enforcement audit

Scope: current source and deterministic tests only. No historical holdings, returns, or candidate economics were inspected.

## Effective configuration and route

`config/settings.yaml:140-182` enables equity V3 at maximum allocation 0.55 (top 8, max single strategy target 0.10), crypto hysteresis at 0.05 (top 2), and risk limits of six total positions, two crypto positions, 0.10 per equity, 0.05 per crypto, 0.60 gross, and 0.40 cash buffer. The `PortfolioRiskLimits` dataclass defaults differ (notably six total/three crypto), and `RiskLimits` defaults to five total; production values come through `portfolio_risk_limits_from_config`, not either default (`src/parayabuck/execution/portfolio.py:85-125`, `src/parayabuck/config.py:210-237`).

The engine constructs one `PortfolioRuntime` per active market with a shared account broker/repository and risk limits (`src/parayabuck/runtime.py:945-991`). Each runtime calls `PortfolioRiskOverlay.apply` before `TargetToOrderPlanner.plan` (`src/parayabuck/portfolio/runtime_service.py:1015-1029,1065-1095`). The overlay applies the position-count trim **before** sleeve/gross scaling, and chooses ranked equity names by strategy rank when available (`src/parayabuck/execution/portfolio.py:263-348`). Thus V3's top eight is a candidate target set; it is not a promise of eight held equities.

## Exact count semantics

| Inventory or claim | Slot treatment | Evidence |
|---|---|---|
| Broker equity and crypto positions | One `(Market, canonical_symbol)` slot each when `qty != 0`; both markets share six | `src/parayabuck/execution/portfolio.py:276-306`; `src/parayabuck/core/models.py:188-190` |
| Fractional and tiny nonzero residual | One slot, with no de minimis threshold in `is_flat` | `src/parayabuck/core/models.py:188-190` |
| Current-market managed position omitted by new target | Replaceable after SELL then fresh snapshot; it is not a permanent fixed slot | `src/parayabuck/execution/portfolio.py:267-274,299-307`; `src/parayabuck/portfolio/runtime_service.py:1130-1208,1210-1300` |
| Current-market external/unapproved or mixed position | Fixed slot; external portion is immutable and pure external inventory cannot be grown by the bot | `src/parayabuck/execution/portfolio.py:184-235,244-260,299-304`; `src/parayabuck/execution/portfolio.py:499-513` |
| Opposite-market position | Fixed slot before current-market target selection | `src/parayabuck/execution/portfolio.py:276-308` |
| Same-cycle planned BUY from earlier market | Reserved only if an actual BUY order is in the earlier result | `src/parayabuck/runtime.py:3073-3097,3518,3567` |
| Account-local nonterminal order, BUY or SELL | Reserved until terminal, including partial fill and ambiguous status | `src/parayabuck/portfolio/runtime_service.py:1473-1493`; `src/parayabuck/portfolio/runtime_repository.py:810-839`; `src/parayabuck/portfolio/runtime_models.py:30-70` |
| Broker-wide manual/external open order | Symbol is a slot claim and same-symbol order suppression; order market inferred from crypto symbol syntax | `src/parayabuck/execution/portfolio.py:287-298,463-483`; `src/parayabuck/execution/alpaca.py:546-557` |
| Hedge/reference-only symbol with no broker position/open order | No count. A held or working-order symbol counts regardless of strategic role | Account/open-order set comprehensions above |

The broker adapter returns every broker position, classifies market and canonicalizes symbol, rejects duplicate `(market, symbol)` rows, and splits quantity into managed versus external (`src/parayabuck/execution/alpaca.py:426-488`). A single symbol with mixed ownership is still one slot. The overlay also rejects duplicate account-position keys (`src/parayabuck/execution/portfolio.py:99-109`). The current production strategy composition is one equity and one crypto runtime; the account-level key prevents a duplicate row from being silently collapsed, but this audit did not prove a general multi-strategy same-symbol allocation protocol.

## Count calculation and reservations

`fixed_slots` is the union of opposite-market current positions, same-cycle reservations, broker-visible open orders, local nonterminal orders, and current-market immutable positions. Optional target names are limited by both `max_positions - len(fixed_slots)` and the market limit (`max_crypto_positions` for crypto, global cap for equity). Fixed current-market names in the desired set are mandatory; the rest are kept in strategy rank order, or descending weight if unranked (`src/parayabuck/execution/portfolio.py:263-348`). A fixed external/open-order slot can therefore reduce fresh-entry capacity even when the associated working order later cancels.

Simultaneous market decisions, startup catch-up, and restart rebalance resume are ordered crypto first; actual planned crypto BUYs reserve slots before equity applies the gate (`src/parayabuck/runtime.py:2482-2508,3073-3097,3111-3120,3220-3241,3518,3537-3567`). The reservation is generated **after** the earlier market's overlay and planner, then passed **before** the later market's overlay. `tests/test_multi_market_portfolio.py:225-269,410-555` covers cross-market held slots, same-cycle reservation, crypto-first decisions/startup, and not reserving targets without executable buys.

## Race and failure controls; limits of proof

- A shared `_portfolio_lock` serializes engine decisions and rebalance advancement; a claimed account-snapshot lease fences fill invalidation across the decision boundary (`src/parayabuck/runtime.py:377,2076-2145,3213-3244`). Within one processing cycle, both markets use one account snapshot plus explicit reservations (`src/parayabuck/runtime.py:3102-3120`).
- A SELL+BUY rebalance persists sells and deferred buys; buys wait for terminal sells, then the target is re-applied to a fresh broker snapshot and BUY notional is capped by cash, gross capacity, and LIVE dollar cap (`src/parayabuck/portfolio/runtime_service.py:1130-1208,1210-1300,1339-1469,1520-1593`). Failed/partial exits can therefore leave fewer free slots and smaller/no buys. Ambiguous sell outcomes block the rebalance.
- Pending local orders reserve slots across restarts. `partial_fill` is nonterminal. Broker-wide open order symbols add a conservative independent claim; the planner suppresses same-symbol additions, allowing a local pending BUY to coexist only with a risk-reducing SELL when the broker-wide symbol guard is absent (`src/parayabuck/portfolio/runtime_models.py:30-70`; `src/parayabuck/execution/portfolio.py:443-483`; `tests/test_portfolio_execution.py:141-176`). Submission uses deterministic client IDs, persisted `submitting` rows, and ambiguity recovery (`src/parayabuck/portfolio/runtime_service.py:1594-1665`; `src/parayabuck/execution/alpaca_orders.py:129-161`).
- Terminal cancellation/expiry/rejection releases a ledger reservation, but a remaining nonzero broker position continues to occupy its slot. A working SELL retains its reservation until terminal and a fresh snapshot. Broker position and open-order snapshots can still lag; the code handles known races conservatively, but static inspection is not proof of broker atomicity or a formal distributed transaction.

The position cap is an actual account-wide risk gate, not a broker limit. It also acts as a conservative operational guard while positions/orders are unsettled. That operational role is visible in comments and tests; its necessity at exactly six is not established by this enforcement path.
