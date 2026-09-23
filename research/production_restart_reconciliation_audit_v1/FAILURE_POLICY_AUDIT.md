# Failure policy

| Failure | Actual behavior | Classification |
|---|---|---|
| Account/positions request error | Reconcile raises; startup stops. Transient network failures yield retryable \`TransientStartupError\` | SAFE |
| OPEN orders request error or later page error | Adapter raises without partial result; startup stops | SAFE |
| FILL request raises | Backfill returns \`False\`; reconciliation continues; equity stale close is deferred, but other state may still be considered clean | POTENTIAL_RISK |
| FILL full page repeats/missing cursor | Logs warning, returns \`True\`, may advance watermark from incomplete set | CONFIRMED_CORRECTNESS_BUG |
| FILL response is not a list | Coerces to empty successful scan | CONFIRMED_CORRECTNESS_BUG |
| Local risk JSON cannot parse | Row ignored; incident table independently restored | POTENTIAL_RISK |
| Ownership quantity disagrees | Drift/exception halts | SAFE |
| Broker timeout during submit | Client-ID lookup; unresolved outcome blocks | SAFE subject to lookup visibility |
| Local portfolio row remains nonterminal after broker terminal result | Missing-order drift halts before routine phase lookup | CONFIRMED_CORRECTNESS_BUG (recovery availability) |

Evidence: \`runtime.py:910-947,1110-1120\`, \`alpaca_client.py:140-183\`, \`alpaca_reconciliation.py:122-168,597-617\`, \`ledger.py:1463-1498\`.
