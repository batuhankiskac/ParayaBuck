# Same BUY-capacity bug class

| Pattern | Classification | Evidence and disposition |
|---|---|---|
| Pure immediate BUY used safe planned target but stale submission account | FIXED_BY_SHARED_GATE | Fresh account/OPEN read now precedes persistence and submission. G1/G2 safe regressions. |
| Deferred SELL -> BUY had fresh gross/cash sizing but ignored working BUY commitments | FIXED_BY_SHARED_GATE | Both paths now use one commitment-aware gate; deferred phase still replans after authoritative SELL. |
| Local submitting and broker OPEN BUY represented twice | FIXED_BY_SHARED_GATE | Client ID dedup uses max valid remainder; explicit regression. |
| Partial BUY fill counted twice or freed too early | FIXED_BY_SHARED_GATE | Filled part is in marked position, only remainder is committed; partial/cancel tests. |
| Failed/partial OPEN retrieval treated as no orders | SAFE | Alpaca exhaustive OPEN retrieval raises on uncertainty; gate does not catch it as an empty list. |
| Unknown broker-only BUY price treated as zero | FIXED_BY_SHARED_GATE | Zero headroom with `committed_buy_unpriced`; test covers unknown quantity/notional. |
| Equity and crypto use separate capacity budgets | FIXED_BY_SHARED_GATE | Shared account, account-scoped ledger, sequential admission tests both orders. |
| Pending SELL considered free gross/slot capacity | SAFE | Replan and fresh broker snapshot; SELL intent itself does not free capacity. |
| Protection/short-cover BUY | SAFE | Classified risk reducing and handled by existing protective execution; broker OPEN BUYs are conservatively counted as commitments. |

No additional same-class path was found outside the shared immediate/deferred admission path. Submission through `_submit_orders` remains durable and idempotent; restart recovery resolves existing identities rather than creating a new capacity bypass.
