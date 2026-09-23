# Controlled PAPER rollout design — not executed

1. Start from a pinned future production main SHA containing a verified pure-BUY gross/cash admission repair and the accepted restart/reconciliation/FILL pagination repair. Re-run full suite and clean startup reconciliation.
2. Change only `config/settings.yaml` `risk.max_positions` from 6 to 8 in a separately reviewed PAPER change. Retain crypto cap 2, gross 0.60, cash target 0.40, all sleeves, per-name limits, rebalance band and strategy settings. LIVE remains disabled.
3. Before first session, verify broker inventory, working orders, local reservations, effective claims and no ambiguous recovery state. Abort if reconciliation is incomplete.
4. During first session observe actual and effective slot counts, target and realized gross, cash ratio, equity/crypto sleeves, working orders, risk halt reasons and all BUY admission headroom. Investigate any risk-increasing BUY when current gross has no headroom; do not invent a permitted realized-drift threshold.
5. Roll back on incorrect slot accounting, unresolved broker/restart state, an order-caused gross/cash policy breach, or missing required telemetry. Revert the cap setting to 6 after canceling/settling unsafe pending openings under the existing recovery process. Do not force liquidation of seven or eight held positions solely because the cap returned to six; allow governed exits and block new openings until below cap.

This plan grants no deployment or publication authorization.
