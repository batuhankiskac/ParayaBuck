# Controlled PAPER cap-8 configuration change package — prepared, not executed

Status: `READY_FOR_CONTROLLED_PAPER_CONFIG_CHANGE`. This package authorizes no deployment. Production `config/settings.yaml` remains `risk.max_positions: 6`; `live.enabled: false`. No broker mutation, restart, or order submission is part of this campaign.

## Exact proposed change

On a future explicitly approved PAPER deployment of corrected main `d0c68057234a240a778e5adb06e42c6a3c136c34`, change only `config/settings.yaml` under `risk` from `max_positions: 6` to `max_positions: 8`. Keep equity and crypto strategies, allocations, 0.60 gross, 0.40 cash buffer, 0.10 equity name, 0.05 crypto name, crypto exposure 0.10, crypto count 2, rebalance band, and LIVE disabled exactly as they are. Any intervening main change requires parity review of the actual deployment SHA.

## Predeploy gate

1. Confirm deployed source is the corrected main SHA (or a reviewed descendant), clean worktree, full suite/Ruff/mypy green, and a PAPER account. Confirm `live.enabled: false` and no other configuration delta.
2. Confirm broker OPEN collection is exhaustive, no ambiguous local/broker orders, FILL paging completes, position/cash/order/slot state reconciles, risk latches restore, and no startup halt remains.
3. Confirm the operator dashboard returns a fresh capacity sample with configured cap, actual/effective slots, working count, gross/cash headroom, committed BUY notional, sleeves, suppression/halt reasons, and reconciliation status.
4. Obtain explicit operator approval for the exact PAPER configuration change and deployment window. Do not infer it from this readiness decision.

## First PAPER startup

Start PAPER only after the approved single-key change. The startup broker reconciliation barrier must complete before decisions or new order submission. Halt if OPEN/FILL retrieval, known-order lookup, ownership reconciliation, or capacity sampling is uncertain. Verify displayed maximum positions is eight and crypto cap remains two before allowing normal PAPER decisions.

## Observation and rollback

Watch actual positions, effective occupied/reserved slots, broker working orders, target versus realized gross, committed BUY notional, gross/cash BUY headroom, cash ratio, equity/crypto sleeves, suppression/halt reasons, and reconciliation status. Confirm a passive realized-gross excursion causes no forced liquidation while new risk-increasing BUYs remain blocked and risk-reducing SELLs remain possible.

For an operator-chosen rollback, change only `risk.max_positions: 8 -> 6` and perform a reconciled PAPER restart. Seven or eight existing positions are not forcibly liquidated; new openings are blocked until authoritative normal exits reduce effective occupancy below six. Pending/unfilled SELLs do not free slots. LIVE stays disabled throughout and has no automatic progression.
