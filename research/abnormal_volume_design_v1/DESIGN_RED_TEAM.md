# Design red team

1. **The published premium is mostly long-short.** Daily GKM leg decomposition helps, but a six-name long leg is still an extrapolation. G2–G8 test it directly.
2. **Capacity may erase the effect.** Evidence weakens in large/liquid stocks and direct historical cost studies are cautionary. The shared scanner plus 2x/3x costs and fill gates are intentionally demanding.
3. **Six slots amplify idiosyncratic events.** GKM held broad portfolios. Occupancy, contributor concentration, and leave-one-out gates prevent one lucky name from validating the design.
4. **Broad products blur the visibility mechanism.** ETFs and ADRs can enter because common-stock PIT taxonomy is unavailable. Claims are limited to the canonical broad universe and known-type counts are mandatory.
5. **Retrospective split adjustment can manufacture zeros.** Nonpositive volume invalidates the entire signal window; it is never imputed.
6. **Month-end is a later-study translation.** GKM's 20-day nonoverlap and Wang's month-end/monthly implementation motivate it, but it is not exact daily GKM replication. The schedule is frozen for auditability and cannot be optimized.
7. **Middle-40% filtering may lower occupancy.** The filter is literature-grounded and protects causal interpretation, but may leave fewer than six names. The breadth gate may reject the candidate.
8. **The secondary ordering is engineered.** Continuous relative magnitude resolves the six-slot problem absent from GKM and changes selection. It is frozen and separately labeled; it cannot be promoted as published methodology.
9. **Survivorship/identity risk remains.** The scanner is point-in-time, but the product taxonomy is not. G1 demands lifecycle and identity audit evidence before outcomes.
10. **Multiple-testing pressure can reappear through diagnostics.** Unconditioned signals, subsets, horizons, and thresholds cannot select a replacement. Failure ends this candidate.
11. **V3 distinctness does not imply diversification.** Return correlation, drawdown overlap, and shared-capital feasibility belong only to a later combination stage.
12. **Mechanism is not identified.** Attention/visibility is proposed language only; price pressure, information, disagreement, and flows remain plausible.
13. **The existing harness can silently violate the design.** Generic monthly timing is one session late and the normal CLI omits terminal lifecycles. G1 requires a dedicated harness and tests before any return is computed.
14. **The manifest's final date is not the common endpoint.** The partial 2026-07-31 cross-section lacks SPY and must be excluded from both arms.

Red-team conclusion: one fixed experiment is warranted, but the design has no presumption of success.

## Required-question disposition

| Challenge | Disposition |
|---|---|
| Raw-volume split discontinuity | Yes; raw/absolute share volume is rejected. Only scale-invariant split-consistent dollar-volume rank is allowed. |
| PIT turnover without shares outstanding | Impossible here; turnover is rejected. |
| Overlapping event portfolios | Not required by GKM's main nonoverlapping tests; a single monthly account is a disclosed later-study translation. |
| Long-leg support | Credible for daily GKM, not guaranteed for a six-name modern portfolio; future gate required. |
| Liquid-stock survival | Gross evidence exists but is weaker and direct costs are adverse; future cost/liquidity gates required. |
| Scanner already captures attention/liquidity | Scanner uses trailing median dollar volume as eligibility, not a one-day own-history rank; it is not formula-equivalent. |
| Scanner truncates low-volume comparison set | Yes, but the strategy trades only the high leg and the scanner is held fixed across A/B. Claims exclude the missing low leg. |
| Same-day return proxy | Middle-40% own-return rank explicitly excludes extremes. |
| Return-control survival | Reported by GKM; local survival remains untested. |
| ETF/ETN contamination | Does not mechanically invalidate own-history rank but limits firm-visibility interpretation; type counts mandatory. |
| Monthly fidelity | Supported by later U.S. evidence; disclosed departure from exact 20-session GKM timing. |
| Post-publication robustness | Later U.S. spread evidence exists; concentrated long-only robustness remains open. |
| Realistic costs | Unknown and historically concerning; 1x/2x/3x and fill gates are mandatory. |
| Equal weighting | Mechanism-aligned and avoids importing V3; concentration and cap gates constrain risk. |
| Formula fidelity | Inclusive-50 top-decile rank and middle-40% return control are GKM-based; the six-slot relative-intensity ordering is an explicit selection-relevant engineering adaptation. |
