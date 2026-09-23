# Research versus PAPER/LIVE parity

| Behavior | Research migration | Production main | Match |
|---|---|---|---|
| Global count | Frozen 6/8 arms | Config-driven global slot overlay/helper | Parameterized |
| Reservations | Claims include pending orders | Cross-market reserved keys plus broker/local open-order symbols | Structural match; restart repair pending |
| Target gross | <=0.60 | Overlay caps target against gross/cash | Match |
| Realized gross | May drift above 0.60 | Marked account exposed; no continuous liquidation | Policy match |
| New BUY while realized over 0.60 | Trace has over-mark BUY reservations; exact timing unresolved | Pure-BUY path demonstrably admits such orders | **Material mismatch with production policy** |
| Equity sleeve | Research max 0.55 managed allocation | Config equity maximum_allocation 0.55; gross overlay | Match for frozen setting |
| Crypto sleeve/name | Frozen 0.05 allocation, 0.05/name | Config 0.05 allocation, 0.10 absolute sleeve, 0.05/name | Same configured strategy allocation; no cap change |
| Crypto count | Frozen current cap | Config 2 | Match |
| Per-name equity | 0.10 target | Config 0.10 | Match |
| Pending exits | No slot released until position zero | Same in reservation helper | Match in normal run |
| External/manual | Research begins flat, no manual position | Immutable external accounted in gross and slots | Production-only edge case |
| Rebalance band | 0.005 | Config 0.005 | Match; contributes to gross headroom defect |
| New-order filtering | Frozen sequential global admission | Runtime overlay, helper, broker/local open-order union | Partial; restart correctness prerequisite |

Research lacks external positions and broker restart faults; these rows cannot be called end-to-end parity passes.
