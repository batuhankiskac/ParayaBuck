# Account-capacity architecture decision v1

Status: **research architecture decision only**. Production remains at `risk.max_positions: 6`.

The outcome-blind audit concluded `POSITION_CAP_ARCHITECTURE_CHANGE_DESERVES_FREEZE` at archived commit `da26dda53777042b58aba28af05779a2bcdd2449` (tag `archive/research/account-capacity-architecture-audit-v1-2026-09`). The active six-position setting is an account-wide cardinality guard, but neither its git lineage nor the reviewed Alpaca documentation establishes a specific risk or broker derivation for **six**.

With two crypto positions claiming slots, six total permits at most four equity names. At the configured 0.10 equity per-name limit, four names can express at most 0.40 equity, below the configured 0.55 equity sleeve. This is static capacity arithmetic, not a historical holdings or return finding. Eight is the smallest global count that could accommodate six equities plus two crypto positions without changing the gross, cash, sleeve, or per-name settings. Full sleeve use is not guaranteed by having eight slots.

The next campaign must freeze and test **only** account-wide six versus eight in a shared-account incumbent equity-plus-crypto replay, preserving every other strategy, exposure, eligibility, order, ownership, and safety rule. No historical strategy outcomes informed the audit decision. A later study must not interpret higher returns as a substitute for structural or operational correctness.

Until any separate migration is approved, the production fit contract remains **six total positions**. Future research claiming fit with two crypto slots must account for at most four equity slots. The existing six-equity 5+1 methodology remains valid for its frozen conditional equity experiment; it does not establish simultaneous two-crypto total-account capacity.

Full evidence: [final architecture report](../../research/account_capacity_architecture_audit_v1/FINAL_ARCHITECTURE_REPORT.md) and [machine-readable decision](../../research/account_capacity_architecture_audit_v1/ARCHITECTURE_DECISION.json).
