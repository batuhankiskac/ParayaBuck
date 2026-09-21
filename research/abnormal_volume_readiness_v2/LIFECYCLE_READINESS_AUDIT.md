# Lifecycle readiness audit

The strict v5 loader verified 9,010 unique identities: 2,151 explicit terminals, 6,858 horizon-censored active identities, one unresolved inactive identity (`EOSEW`), and one rename chain (`FB` to canonical `META`). The terminal mapping SHA-256 is `2dc07522b9b4e9acabfd8b57e5fd60702b4169cd831e3becbb733a369b1bce25`.

The reconstruction joins bars to stable entity identity, rejects unresolved or ambiguous identity, requires exact 51-close history, evaluates lifecycle at decision without using later current status, and rechecks lifecycle plus exact first-open availability at fill. A failed fill is dropped without replacement or weight redistribution. Terminal equality with the fill session remains admissible because daily evidence does not prove intraday untradability. A later explicit terminal inside the structural holding interval is persisted only as a timestamp/class risk-reducing event, without price or P&L.

Synthetic tests cover ambiguous and unresolved identity, insufficient/new-listing history, termination between decision and fill with no replacement, terminal-on-fill equality, and split-preserving reconstruction. The full runner emitted one structural terminal event and the verifier reproduced it exactly.

Result: `PASS`.
