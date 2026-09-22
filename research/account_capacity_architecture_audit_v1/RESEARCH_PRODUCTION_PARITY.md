# Research and production slot parity

Status: `RESEARCH_PRODUCTION_SLOT_PARITY_MISMATCH` for the **canonical six-equity-slot 5+1 method interpreted as deployable total-account capacity**. This is a structural, conditional mismatch; it does not invalidate the frozen equity-only research method or prove any historical candidate result wrong.

## Static comparison

| Contract | Six account slots with two crypto names |
| --- | --- |
| Research policy | Control A has six V3 equity slots; B/C have five V3 equity slots and one satellite equity reservation. The policy does not debit slots for crypto (`docs/research/core_satellite_combination_policy_v1.md:16-30`). |
| Production configuration | Equity and crypto are enabled, equity target budget 0.55, crypto target budget 0.05 and `top_n: 2`, with `risk.max_crypto_positions: 2` and `risk.max_positions: 6` (`config/settings.yaml:141-182`). |
| Production overlay | Account snapshot positions from both markets, reserved slots, and open-order symbols are counted before optional current-market target selection (`src/parayabuck/execution/portfolio.py:266-306`). With two occupied crypto slots and no other fixed claims, four equity slots remain. |

This is a **capacity contract mismatch**, not evidence that crypto has actually occupied two slots on a particular date. No historical holding distribution was read. The overlay is invoked independently for each market by runtime services (`src/parayabuck/portfolio/runtime_service.py:1017-1033`, `:1385-1397`), whereas the offline portfolio engine applies it to the account state it models (`src/parayabuck/backtest/portfolio_engine.py:1060-1073`). A standalone equity experiment can mechanically express six equity names when no crypto is modeled; production cannot express those six simultaneously with two crypto positions under the same global cap.

At the static per-name equity limit of 0.10, four equity slots can express at most 0.40 of account equity, below the configured 0.55 equity sleeve. This arithmetic is independent of price history or returns. The actual V3 weights are inverse-vol sized and may be smaller; this file makes no claim about realized deployment.

**Future protocol implication:** any claim of production total-account fit needs the same global slot count and explicit crypto occupancy/reservation assumptions in all research arms. The old canonical 5+1 method remains valid for its stated conditional equity experiment; it is not, alone, a parity proof for the enabled equity plus crypto account.
