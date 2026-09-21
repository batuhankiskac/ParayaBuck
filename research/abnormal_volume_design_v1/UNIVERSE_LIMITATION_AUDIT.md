# Universe limitation audit

The canonical point-in-time scanner is broad `US_EQUITY`, not a historically reconstructed common-stock-only universe. ETFs, ADRs, closed-end funds, acquisition vehicles, and other products can therefore be present. This prevents a strict reproduction of U.S. operating-company-common-stock samples.

For this candidate the limitation is acceptable for one shared-architecture A/B test because the signal is a stock/product's own 50-session dollar-volume shock, not a cross-sectional level and not a fundamental ratio. The shared universe also isolates the strategy change from universe changes. Acceptance does not make product composition irrelevant: pooled vehicles may react to flows rather than firm visibility, and ADR/corporate-action mechanics may differ.

Rules:

- Apply the same point-in-time scanner and lifecycle eligibility as V3.
- Exclude reference-only `SPY` and hedge-only `SH`, `PSQ`, `DOG` from candidate targets.
- Do not infer common-stock status from ticker text or current metadata.
- Record product-type metadata only when it is point-in-time available; missing taxonomy is `UNKNOWN`, not common stock.
- Prespecify a descriptive holding/event count by known type and `UNKNOWN` after the primary result. It cannot redefine the sample or choose parameters.
- Any claim is about the canonical screened `US_EQUITY` product universe, not a pure common-stock universe.

Verdict: `UNIVERSE_LIMITATION_ACCEPTABLE_FOR_SHARED_A_B_TEST`. It would be insufficient for a claim of exact GKM common-stock replication.

