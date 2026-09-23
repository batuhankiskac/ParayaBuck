# Cross-market crowd-out V3

## Crypto → Equity

| Arm | Events | Distinct timestamps | Target shortfall notional |
|---|---:|---:|---:|
| 6 | 314 | 201 | 4,545,383.252163 |
| 8 | 328 | 211 | 5,163,078.193661 |

- Cap 6: `2021-04-05T20:00:00+00:00` equity `ALLY`, occupied/reserved claims 6, opposite-market claims 2.
- Cap 6: `2021-04-12T20:00:00+00:00` equity `ALLY`, occupied/reserved claims 6, opposite-market claims 2.
- Cap 6: `2021-04-12T20:00:00+00:00` equity `SCHW`, occupied/reserved claims 6, opposite-market claims 2.
- Cap 8: `2021-04-05T20:00:00+00:00` equity `URI`, occupied/reserved claims 7, opposite-market claims 2.
- Cap 8: `2021-04-12T20:00:00+00:00` equity `AES`, occupied/reserved claims 8, opposite-market claims 2.
- Cap 8: `2021-04-12T20:00:00+00:00` equity `XME`, occupied/reserved claims 8, opposite-market claims 2.

## Equity → Crypto

| Arm | Events | Distinct timestamps | Target shortfall notional |
|---|---:|---:|---:|
| 6 | 93 | 68 | 6,361,202.772609 |
| 8 | 95 | 82 | 6,938,240.270107 |

- Cap 6: `2021-10-13T00:00:00+00:00` crypto `BTC/USD`, occupied/reserved claims 6, opposite-market claims 5.
- Cap 6: `2021-10-17T00:00:00+00:00` crypto `LTC/USD`, occupied/reserved claims 6, opposite-market claims 5.
- Cap 6: `2021-10-21T00:00:00+00:00` crypto `BTC/USD`, occupied/reserved claims 6, opposite-market claims 5.
- Cap 8: `2021-10-13T00:00:00+00:00` crypto `BTC/USD`, occupied/reserved claims 8, opposite-market claims 8.
- Cap 8: `2021-10-13T00:00:00+00:00` crypto `LTC/USD`, occupied/reserved claims 8, opposite-market claims 8.
- Cap 8: `2021-10-17T00:00:00+00:00` crypto `BCH/USD`, occupied/reserved claims 8, opposite-market claims 8.

Target rejection is a point-in-time decision. No symbol-specific blocking duration or denied-symbol hypothetical P&L is defined by this replay. Count shortfall is requested target notional at those decisions, not inferred unused cash.
