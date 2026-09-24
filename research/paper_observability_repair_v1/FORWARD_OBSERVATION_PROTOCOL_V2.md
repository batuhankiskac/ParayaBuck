# Frozen PAPER forward observation protocol

Before PAPER: freeze SHA/config digest, V2 receipt schema, two-market auditor version, equity V3 0.55 allocation, crypto hysteresis V1 0.05 allocation, global cap 8, crypto cap 2 and S1-S12 gates. Refresh and validate both active universes with provider-backed data; offline doctor `REFRESH_NEEDED` is not a fresh universe. Verify account ownership/reconciliation and empty unresolved ambiguity before any entry.

Minimum duration: 14 consecutive calendar days, including at least two natural weekly scheduled equity opportunities and three 96-hour crypto opportunities, scanner refresh, order lifecycle observations and one controlled restart/catch-up drill for each market. Extend the window if those opportunities or required lifecycle evidence do not occur. Retain receipts, DB backup, journal and daily auditor JSON. Every S gate must pass; P&L is excluded. Do not tune strategy parameters from short-term PAPER P&L. A failed gate invalidates that observation period; investigate before restarting the clock.

Record the exact V2 deployment timestamp as the soak auditor `--start` boundary and freeze `--end` after the minimum duration.
