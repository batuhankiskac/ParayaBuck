# Operator capacity monitoring V2

The existing dashboard risk panel now displays configured max positions, actual position count, effective occupied/reserved slots, broker working-order count, target and realized gross, gross BUY headroom, committed BUY notional, current cash ratio and cash BUY headroom, equity/crypto sleeve exposure, suppression and halt reasons, and reconciliation status. It samples the same read-only capacity calculation as admission through a ten-second display cache. The probe never places or cancels an order. If broker state is unavailable, the panel reports unavailable instead of displaying stale zero commitments. Gross/cash suppression is also logged and emitted through the existing no-trade journal.

Operator readiness depends on a clean startup reconciliation and visible fresh sample. The dashboard is observability, not an admission authority; trading still performs its own fresh read for each BUY phase.
