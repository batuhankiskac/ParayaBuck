# Architecture red team

The challenge is structural and outcome-blind. Neither a cap increase nor retention is treated as presumptively correct.

## Challenge to a larger cap

1. A higher count creates more target lines, order-state transitions, reconciliation work, log volume, and concurrent claims. The order audit identifies a sell-first/buy-second path, but a migration needs a frozen worst-case order and API request budget for the selected cap, including partial fills and retries. A published broker request limit is not proof of actual account throughput.
2. Gross and per-name ceilings do not impose a minimum position weight. The $10 order floor, rebalance band, and asset increments are post-target order constraints; they cannot by themselves prevent tiny residual target holdings or repeated small adjustments. Fractional availability differs by asset.
3. Eight total names would allow six equity names with two crypto names; ten would permit all eight V3 selections plus two crypto names. The geometry alone does not show that the seventh or eighth equity name should be held. Eligibility and rank may be lower quality at the margin, but no outcome evidence is allowed here to quantify that issue.
4. More names are not assured to diversify common factor, sector, or liquidity exposure. Position count measures cardinality, not correlation or liquidation capacity. The existing scanner and per-name/sleeve controls still matter.
5. A larger count cannot fix stale snapshots, manual inventory ownership, ambiguous broker submissions, or failed exits. Any change must prove the same conservative slot accounting survives concurrent market decisions and recovery paths.

## Challenge to retaining six

1. With two crypto claims, four equity names at 0.10 each cap the equity sleeve at 0.40, leaving 0.15 of configured equity capacity unusable solely because of count. The code accepts unused capital as cash, but the reviewed origin does not establish that this particular underdeployment is intentional.
2. Six was configured in August 2026 without a documented calibration; truly account-wide cross-market enforcement arrived in September. No broker source found a six-position limit. Conservatism is a valid motive only if its operational or concentration tradeoff is made explicit.
3. A two-name, 0.05 total crypto sleeve can consume a third of the global slots while representing at most one-twelfth of the configured 0.60 gross target. This fixed slot price is poorly aligned with exposure, though cardinality can still reflect operational work.
4. The frozen 5+1 policy uses six **equity** slots. It does not reserve two simultaneous crypto slots. Retaining six is defensible only if future claims of total-account deployability use the correct conditional four-equity capacity or otherwise model occupancy and event order explicitly.
5. The generic `RiskLimits` default remains five while active YAML specifies six. This discrepancy does not change configured production, but it makes the intended canonical count less clear in omitted-config or programmatic use.

## Resolution standard

The decision should require an independently coherent capacity contract, bounded order/monitoring burden, and research/production parity. It should not use hypothetical return gains, prior candidate failures, or the desire to make any past strategy fit. A recommendation to *study* a migration is distinct from permission to change PAPER/LIVE configuration.
