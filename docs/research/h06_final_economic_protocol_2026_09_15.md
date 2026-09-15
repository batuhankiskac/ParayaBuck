# H06 final economic protocol — 2026-09-15

Status: `H06_FINAL_ECONOMIC_PROTOCOL_SEALED`.

This protocol was frozen before any post-decision H06 return was read or calculated.

- Signal: unique eligible transaction notional over exact raw market cap, prior 21 NYSE sessions.
- Selection: highest positive decile, deterministic CIK/symbol tie break.
- Holding: next-open entry, 63 completed sessions, next-open exit; reselection only extends expiry.
- Standalone: at most 55% equity and 10% per name; unused budget is cash.
- Shared: 95% unchanged accepted V3 plus 5% H06 relative to the equity sleeve; six positions and account caps remain binding.
- Costs: 1x/2x/3x slippage and spread; effective-dated regulatory fees unchanged.
- Statistics: 10,000 moving-block bootstrap draws (21 sessions, seed 20260909), 10,000 paired sign draws (seed 20260910), fixed +7-session placebo.
- One shot: no tuning, no rerun, no automatic promotion, no PAPER/LIVE/registry change.

Protocol self-hash: `ea5087aba7686f5f9e2b32b6260c3d86a37adaec8912b96bb47bfea7c98fa457`.
