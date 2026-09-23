# Operational load V3

| Metric | Cap 6 | Cap 8 | Difference (8−6) |
|---|---:|---:|---:|
| Peak actual positions | 6 | 8 | 2 |
| Peak occupied/reserved claims | 6 | 8 | 2 |
| Peak working orders | 8 | 11 | 3 |
| Orders per decision | 2.173867 | 2.665964 | 0.492097 |
| Orders | 2,063 | 2,530 | 467 |
| Fills | 2,297 | 2,835 | 538 |
| Account reconciliation marks | 50,194 | 50,194 | 0 |
| Reservations created | 2,063 | 2,530 | 467 |
| Reservations released | 2,063 | 2,530 | 467 |
| Pending-order conflicts | 8,243 | 9,571 | 1,328 |

Both arms pass account-wide slot and reservation release gates. Open-order pagination focused tests passed locally; no broker API was called. Production capacity would require a separate parity review.
