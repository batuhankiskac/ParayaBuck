# Design Red Team

## Findings

1. **MAX versus IVOL:** MAX is not identical to IVOL, but published correlation
   is roughly 0.75-0.90 and mutual controls are unstable.
2. **V3 consumption:** V3 already rejects the highest residual-volatility 30%
   and inverse-volatility weights survivors. It likely removes or attenuates
   much of the high-MAX cross-section by construction.
3. **Liquid-stock survival:** Historical $5/NYSE/size screens and an Australian
   largest-500 study are supportive, but U.S. NYSE-breakpoint/value-weighted
   replication and later subperiods are materially weaker.
4. **Long-only low leg:** The central anomaly is largely high-MAX
   underperformance. A long-only lowest-MAX top six is an unsupported
   concentration transformation, not the published portfolio.
5. **ETF/ETN contamination:** Broad `US_EQUITY` can make LOW-MAX a product-type
   classifier. This independently invalidates the current test.
6. **Mechanism ambiguity:** Lottery preference competes with IVOL, reversal,
   microstructure, attention, overreaction, skewness and aggregate-volatility
   insurance explanations. Firm-level causal attribution is not identified.
7. **One-observation noise:** MAX1 can be determined by a bad close, stale
   price, event, or corporate-action error. Split correction helps but does not
   remove idiosyncratic event noise.
8. **MAXN temptation:** Averaging top N observations may reduce single-print
   sensitivity, but increases IVOL resemblance and creates a parameter-search
   path. MAX1 would be the only predeclared starting definition if unblocked.
9. **Monthly costs:** Published work lacks decisive MAX-specific net-cost proof.
   A six-name portfolio makes every replacement large, and current generic
   monthly timing is one session late.
10. **Post-publication evidence:** It is mixed to weak. Investability-oriented
    U.S. replication sharply attenuates the effect and later developed-market
    samples can be insignificant or reversed.
11. **Equal weighting:** It avoids importing V3's IVOL design, but amplifies
    firm/event concentration and gives each potentially contaminated product a
    large allocation. Inverse-vol weighting would confound MAX with V3.
12. **Distinctness and computational justification:** A nonlinear one-month
    extreme can carry information different from 63-day residual volatility,
    but current evidence is insufficient to justify the exact long-only top-six
    experiment before solving product taxonomy and cadence.

## Adversarial alternatives rejected

- Adding 52-week-high, capital-gains-overhang, sentiment, news, or ownership
  controls would create a different hypothesis and reopen unavailable or closed
  families.
- Applying MAX inside V3 would skip standalone validation and confound the
  incremental test with the existing IVOL gate.
- A known-current-symbol blacklist would not be point-in-time and would create
  survivorship/classification leakage.
- Testing MAX1 through MAX5 and choosing the best would violate the pre-outcome
  single-candidate rule.
- Treating failure to beat V3 as alpha rejection would conflate standalone
  validity with portfolio contribution; that governance rule remains correct,
  but no candidate reaches that stage here.

## Red-team disposition

The family is not admitted to an economic test in the present repository.
Blocking is preferable to a false clean test. No alternative candidate is
proposed.
