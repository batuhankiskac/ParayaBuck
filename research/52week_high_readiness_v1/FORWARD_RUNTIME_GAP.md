# Forward PAPER/runtime gap

The research harness can rank the unchanged broad PIT eligible pool after the completed month-end bar `t`, with 252 completed sessions through `t`, before bounding the observation set.

Current PAPER cannot reproduce that candidate flow without architecture work. PAPER remains registered to `equity_residual_momentum_v3`; its current observation pipeline and production `RebalancePolicy(monthly)` do not implement the frozen candidate's post-close broad-pool-through-`t` ranking or last-completed-NYSE-session policy. The candidate is intentionally absent from the production registry.

This gap does not block the separate frozen economic research test once G1 passes. Even if later economics succeed, promotion would require a separate PAPER-parity implementation, causal data acquisition path, restart/reconciliation proof, and explicit governance approval. Nothing here authorizes PAPER or LIVE promotion.

