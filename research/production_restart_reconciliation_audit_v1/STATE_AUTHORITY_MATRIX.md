# State authority matrix

| State | Broker authoritative? | Local authoritative? | Reconciled? | Evidence |
|---|---|---|---|---|
| Position quantity / average entry price | Yes, snapshot | Ownership quantity only | Compared; mismatch halts | \`alpaca_account.py:154-208\`; \`alpaca_reconciliation.py:425-459\` |
| Cash/equity/non-marginable power | Yes | Peak history only | Fetched on startup and decisions | \`alpaca_account.py:133-251\` |
| OPEN orders | Yes for existence | Local rows for intent/ownership | IDs compared; mismatch halts | \`alpaca_reconciliation.py:597-617\`; \`alpaca_client.py:102-183\` |
| Partial fills | Broker positions/order fields/activities | Cumulative local fill ledger | Trade updates and backfill; incomplete after missed event possible | \`alpaca_reconciliation.py:122-283\`; \`runtime_repository.py:844+\` |
| Strategy ownership | No strategy field at broker | Ledger/repository lineage | Quantity checked; absent local ownership classified external | \`alpaca_account.py:143-210\` |
| Pending openings/exits | OPEN broker orders plus local nonterminal rows | Local phase/order status | Union for planning, ID equality at startup | \`runtime_service.py:1490-1532\` |
| Slot reservations | Nonzero positions/OPEN symbols | Local nonterminal rows | Recomputed for each decision | \`execution/portfolio.py:556-590\`; \`runtime_service.py:1490-1532\` |
| Last rebalance / decision watermark | No | Repository | Restored; no broker equivalent | \`runtime_service.py:338-341,446-550\`; \`runtime.py:2382-2408\` |
| Crypto hysteresis | Broker holdings feed current held set | Strategy parameters and prior decision cadence | Recomputed from current holdings/ranks | \`crypto_low_variance_hysteresis.py:63-105\` |
| Peak/drawdown | Current equity from broker | Persisted peak/epoch and incidents | Peak reconciled; incident restored | \`alpaca_account.py:225-251\`; \`risk_peak.py:246+\`; \`runtime.py:2327-2380\` |
| Idempotency | Client ID lookup/order uniqueness | Deterministic decision ID and durable order row | Submission ambiguity lookup and later phase recovery | \`runtime_models.py:179\`; \`runtime_service.py:1611-1667\`; \`alpaca_orders.py:129-161\` |

No single global authority rule is adequate. The system deliberately fails closed when broker quantities and local ownership disagree. A broker-only/manual position remains externally owned and still contributes to account gross and slots.
