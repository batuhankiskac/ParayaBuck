# Same-bug-class sweep

| Pattern | Finding | Class |
|---|---|---|
| One-page broker OPEN orders | Exhaustive order-ID cursor helper used at startup and planner | SAFE |
| One-page FILL activities | Normal full pages use documented activity-ID token; malformed/repeated page ends as success | CONFIRMED_CORRECTNESS_BUG |
| Pending exit frees slot | Local nonterminal row and broker position remain in slot union | SAFE |
| Missing OPEN request treated as empty | Adapter raises; no partial list escapes | SAFE |
| Missing FILL history treated as empty | Non-list 200 response becomes \`[]\` | CONFIRMED_CORRECTNESS_BUG |
| Duplicate decision/order after restart | Deterministic decision/client IDs and durable rows, with broker lookup for \`submitting\` | SAFE on tested path |
| Terminal broker order still local nonterminal | Startup compares OPEN ID sets before generic terminal-order recovery | CONFIRMED_CORRECTNESS_BUG |
| State restore defaults silently | Corrupt risk row is ignored; durable safety incidents are separate | POTENTIAL_RISK |

Narrow source sweep: \`execution/alpaca_client.py\`, \`execution/alpaca_reconciliation.py\`, \`execution/alpaca_orders.py\`, \`portfolio/runtime_service.py\`, \`runtime.py\`, \`execution/ledger.py\`. No account-capacity migration or alpha research files were opened.
