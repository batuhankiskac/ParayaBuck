# Synthetic crash matrix

The entries describe code-path outcomes; the audit never sent an order. “Halt” is safe against new exposure but fails automatic recovery.

| Case | Broker truth / local truth | Startup observation and action | Result |
|---|---|---|---|
| C1 before submit | No order / persisted decision and possibly \`submitting\` row | Broker ID lookup 404 in phase recovery; row canceled; later planning uses persisted decision | Safe if lookup absence is final |
| C2 request sent, no ack | Broker acceptance unknown / \`submitting\` | OPEN ID comparison; if accepted, ID is known locally; phase recovery queries ID; if absent, 404 path | Conditional on lookup consistency |
| C3 accepted, local status not persisted | Working order / \`submitting\` | OPEN IDs match; phase recovery marks submitted | Safe |
| C4 partial BUY | Position plus remaining OPEN BUY / stale local fill | Broker position and OPEN order loaded; quantity may classify broker stale if fill callback absent | Halt possible; no blind duplicate |
| C5 full BUY fill before local update | Broker position, no OPEN order / local nonterminal order, no ownership fill | Position classifies BROKER_STALE; local ID classifies MISSING_BROKER_ORDER; startup halts before normal phase recovery | **Automatic recovery gap** |
| C6 cancel sent, ack lost | Broker may be pending_cancel/canceled/filled / local nonterminal | OPEN/ID mismatch halts if terminal; pending_cancel remains OPEN; lookup required | Safe against early release; halt possible |
| C7 rejection lost | No broker OPEN order / local nonterminal | Missing ID drift and halt unless locally resolved before startup comparison | Safe halt |
| C8 partial SELL | Reduced broker position plus working residual / old owned qty | Account ownership drift may be exempted by pending local SELL remainder; otherwise halt; slot retained | Safe against early slot release |
| C9 full SELL | Broker flat / local holding and nonterminal SELL | Flat ownership recovery queries broker order, books fill if found; otherwise adjustment; ID drift may still halt | Conditional |
| C10 broker-only position | Real position / no ownership | Classified external; counted gross and slot; startup BROKER_STALE halts | Safe halt |
| C11 local-only position | Broker flat / local ownership | Stale closure/adjustment attempted; reconciliation still reports LOCAL_STALE and halts | Safe halt |
| C12 >500 working orders | All pages at broker / matching local IDs | Order-ID cursor exhausts pages and compares sets | Safe on stable pages |

Evidence: \`alpaca_reconciliation.py:391-617\`, \`alpaca_account.py:130-251\`, \`runtime_service.py:1311-1348,1490-1532\`, \`alpaca_client.py:102-183\`. C5's missing-ID condition is reproduced by the audit-only test. Other cases combine inspected branches and existing restart tests; they are not a live broker timing certification.
