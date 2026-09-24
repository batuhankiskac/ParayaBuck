# Re-entry decision

`RESIDUAL_REVERSAL_CAP8_REENTRY_BLOCKED`

## Current main SHA

`be440821e34d859f2e693c45532566eb0dafb1f8`; review branch `research/residual-reversal-cap8-reentry-review-v1` was created from clean main.

## Old campaign authority and frozen identity

The annotated archive tag `archive/research/residual-reversal-local-v1-2026-09` peels to `a3a54cff64a9312300ef700f2fe9409435a064d3`. The binding corrected protocol/report/decision/gate/attribution files are listed in `OLD_CAMPAIGN_AUTHORITY_RECEIPT.json`. Candidate identity remains exactly `equity_short_term_residual_reversal_v1`: daily SPY residual, V3 timestamp-aligned OLS beta 273, 21 completed residual returns, skip zero, negative-first compounded score, no positive gate, weekly long-only top six, IVOL q=0.70, inverse 63-return residual volatility and 0.10 name cap.

## Old standalone evidence and gates

Archived 1x standalone net return 0.8849, CAGR 0.0684, Sharpe 0.6483, Sortino 0.8499, maximum drawdown 0.1778 and Calmar 0.3849. Archived 2x/3x net returns 0.7235/0.5747; daily V3 correlation 0.4761. Seven of eight corrected gates passed. **Gate 4, capital-neutral portfolio contribution, was the sole failure.** The historical `ALPHA_VALID_PORTFOLIO_REJECTED` result remains valid.

## Old capacity pathology

The old six-slot equity-only treatment used V3 0.4125 and reversal 0.1375 with research-only 4/2 slot quotas and overlap-aware spare filling. Capacity shortfall occurred on 495/495 decisions. Mean realized target exposure was V3 about 0.2066 and reversal about 0.0501; unused allocation was about 0.2059 and 0.0874. This severe constraint motivates re-entry review; it does not itself prove a new result.

## Current cap-eight architecture and crypto interaction

Current configuration has V3 0.55, crypto 0.05, eight global positions, two crypto positions, maximum gross 0.60, cash buffer 0.40, and LIVE disabled. The independent migration tag peels to `9c5e61382e8cdb71fdd3442d945719024accedd9` and accepted cap eight. Incumbent-only global count denials decreased 1112 to 438. That experiment did not include reversal. Crypto consumes global slots, with no fixed reserve; pending/working openings count and an intended SELL releases none until a fresh zero-position observation.

## Structural cap-six versus cap-eight comparison and overlap

`SLEEVE_CAPACITY_COMPARISON.csv` is a reproducible **target-demand diagnostic only** over 495 archived weekly equity labels. It pairs frozen archived equity requests with the latest isolated corrected causal crypto target, not endogenous shared-account crypto holdings. In the 269 rows with crypto requests, mean crypto request count is 1.985; cap six admits about 4.015 equity names and cap eight about 6.015. Across all 495 rows, cap eight selects 990 additional equity-name events under the proposed research-only priority rule. Mean admitted *target* exposure moves from V3 0.1401 to 0.2116 and reversal 0.0588 to 0.0756; these are diagnostic target weights, not realized exposure. The old 4/2 policy cannot be compared directly with this different priority rule. V3/reversal targets overlap on 251/495 labels, with 344 same-symbol slot savings, mean 0.695 names per label. Equity/crypto market keys do not overlap.

These numbers cannot establish actual current candidate count denials or count-induced unused allocation. The current shared-account replay has one equity engine, while crypto hysteresis depends on shared account holdings. Archived equity labels are not a physically timed replay. Working orders, pending opens, SELL completion, gross/cash and re-planning are absent from the diagnostic. The current account's two additional global slots are substantially consumed by crypto in the overlapping period; candidate materiality remains **unresolved**, not assumed from `8 > 6`.

## Slot arbitration and capital neutrality

`CURRENT_SLOT_ARBITRATION_SPEC.md` recommends crypto-first causal admission followed by deterministic source-rank equity arbitration using the lower admitted/requested exposure fraction. Same-symbol equity contributions share one account slot and retain source labels. V3/reversal/crypto budgets stay fixed; all infeasible capital remains cash. This contract is pre-outcome but still needs research-only physical implementation and validation.

## Standalone reuse, universe, and slot time

`OLD_STANDALONE_EVIDENCE_REUSABLE` for the historical alpha verdict, conditional on a no-return reversal-specific physical chronology equivalence check before future economics. The current v5 bar and lifetime hashes match archive receipts. Historical broad `US_EQUITY` is not proven PIT common-stock-only; this is shared with V3 and reversal includes exchange-traded products. Reversal's archived turnover 302.5174 versus V3 168.4613 and mean holding time 274.91 hours increase slot pressure; archived turnover economics nevertheless passed. No new turnover economics was calculated.

## Rationale and exact next action

The old capacity failure is real, and the incumbent-only cap migration is exogenous. Candidate-specific physical shared-account capacity has not yet been measured faithfully. The required no-return replay must rebuild both equity target streams and endogenous crypto decisions on the current physical clock, with current slot reservations and source attribution. Until then the architecture-change materiality condition is unproved. Next action: `BUILD_AND_VERIFY_NO_RETURN_PHYSICAL_SHARED_ACCOUNT_CAPACITY_REPLAY`.

New economics calculated: `NONE`. Production changes: `NONE`. Future economic test: `NOT AUTHORIZED`.

Final branch SHA: the branch starts at `be440821e34d859f2e693c45532566eb0dafb1f8`; this review does not commit or change that SHA.
