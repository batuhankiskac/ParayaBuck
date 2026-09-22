# Core-satellite research policy audit

Source: `docs/research/core_satellite_combination_policy_v1.md`, read as a methodology contract, without inspecting campaign outcomes.

## Exact 5+1 contract

The policy defines Control A as **six V3 equity slots**, Control B as five V3 core equity slots plus one reserved satellite slot held in cash, and Treatment C as the same five-core/reservation sequence with an equity candidate in the sixth slot (`:16-30`). It splits the **0.55 equity sleeve** by slots: 5/6 core = 0.4583333333333333 and 1/6 satellite = 0.0916666666666667 (`:25-27`). The candidate's symbol is excluded from V3 core holdings in B and C (`:34-41`); unused budgets and vacant slots remain cash, without source transfer (`:43-48`). Each arm uses a shared ledger, not separately blended account curves (`:50-56`).

The policy explicitly requires unchanged production and crypto boundaries and says it is not a production configuration or PAPER/LIVE authorization (`:7-13`, `:79-89`, `:110`). It does **not** specify two simultaneously held crypto positions in the six global slots, reserve crypto slots, or define a joint equity/crypto total-account allocation test. Its six slots therefore denote the canonical **equity research experiment**, while `config/settings.yaml:182` and `src/parayabuck/execution/portfolio.py:266-306` denote the live/PAPER **account-wide** gate. The policy's sentence about an identical account ledger applies to its experiment arms; it should not be read as proof that live crypto was included.

## What the method establishes

If executed validly, A vs B isolates reserving one of six equity slots and associated capital, and B vs C isolates an equity candidate's addition to that reservation under a frozen account and cost contract. The method remains valid for the architecture it froze. It does not demonstrate that a candidate can be held simultaneously with two crypto names under the production six-account-slot cap, nor that the sixth equity slot is always available at runtime.

No retroactive reinterpretation is warranted. Future research that claims total-account production fit should freeze its crypto occupancy, equity capacity, event order, shared order queue, and account risk overlay before observing outcomes. A six-equity 5+1 experiment can remain a separate conditional equity-only diagnostic, clearly labeled as such.
