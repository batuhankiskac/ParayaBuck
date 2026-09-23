# Same-bug-class audit

| Path | Classification | Evidence |
| --- | --- | --- |
| OPEN-order enumeration | SAFE | Existing `_get_open_orders` paginates to exhaustion and raises on failed pages; existing pagination tests run. |
| Targeted order lookup | SAFE after repair | Existing client-ID/broker-ID helper is reused; uncertainty propagates; missing OPEN is resolved by lookup. |
| Final local/broker OPEN comparison | SAME_BUG_PRESENT, repaired | Local provider errors were swallowed; malformed IDs could be skipped. Both now fail closed. |
| Other Account Activity types | NOT_USED | Production reconciliation requests only `/account/activities/FILL` in this path. |
| FILL timestamp watermark | SAFE within existing window | 24-hour overlap plus activity-ID ledger dedup; watermark advances only after complete traversal. Historical availability outside the query window is an operational limitation. |
| Portfolio event checkpoint | SAFE | Cumulative quantity/status and durable decision/client IDs are reused; no checkpoint schema changed. |

No migration, replay, cap-8, strategy, or risk-policy code was changed.
