# V3 / IVOL Distinctness Audit

## Decision

Architecture **D — reject the family in the current repository state**.

This architecture decision means that no A/B/C implementation is admitted and
no candidate is frozen. The durable design status is
`DESIGN_BLOCKED_DATA_OR_EXECUTION`, because the common-stock universe and exact
monthly execution boundary required for an honest experiment are unavailable.

## External MAX-IVOL evidence

1. The seminal paper reports substantial MAX-IVOL correlation (about 0.75 for
   MAX1 and higher for MAX5), but MAX survives IVOL controls and the IVOL
   coefficient can reverse after MAX controls.
2. Later U.S. work reports correlation near 0.90. Hou and Loh exclude MAX from
   their joint explanatory horse race because collinearity makes attribution
   unstable. Egginton and Hur find both MAX and IVOL can retain content within
   an overpriced subset under a $5 floor.
3. Conditional MAX evidence often relies on overpricing, sentiment,
   capital-gains-overhang, 52-week-high, ownership, or news-state variables.
   Those inputs are either unavailable, outside this clean hypothesis, or in a
   closed research family.
4. Experimental and event evidence supports alternative interpretations such
   as volatility conditioning and overreaction rather than a unique
   lottery-preference channel.

Sources: [Bali et al.](https://pages.stern.nyu.edu/~rwhitela/papers/max%20jfe11.pdf),
[Hou & Loh](https://doi.org/10.1016/j.jfineco.2016.02.013),
[Egginton & Hur](https://doi.org/10.1016/j.jempfin.2018.03.001),
[Baars & Mohrschladt 2024](https://doi.org/10.1016/j.jebo.2024.02.004).

## Answers to the required questions

1. **Are MAX and IVOL nearly redundant?** Economically and statistically they
   are highly overlapping, but not literally identical. Published correlations
   reach roughly 0.90.
2. **Does MAX retain content after IVOL controls?** Often yes in the original
   and several extensions, although inference is fragile under collinearity.
3. **Does IVOL retain independent content after MAX controls?** Results are
   unstable. It disappears or reverses in some samples, but survives with MAX
   in the overpriced/$5 partition of Egginton and Hur.
4. **Is MAX subset-driven?** Strong evidence says the effect is concentrated in
   overpriced, high-sentiment, retail-oriented, small, illiquid, or otherwise
   speculative names. Some large-stock evidence exists, but is mixed.
5. **Would V3 mechanically remove much of the cross-section?** Likely yes, by
   construction: V3 rejects the top 30% of 63-bar residual volatility and then
   inverse-residual-volatility weights survivors. This is a structural
   inference from formulas and external overlap, not a local outcome result.

## V3 interaction

V3 already consumes the low-idiosyncratic-risk direction twice: eligibility
rejects high residual volatility and sizing favors lower residual volatility.
MAX1 is shorter-horizon and nonlinear, so it could contain some distinct
event/reversal information. But a standalone LOW-MAX strategy would still load
heavily on the same defensive/low-volatility axis, while a MAX feature inside
V3 would be an incremental filter whose causal meaning is difficult to
separate from the existing IVOL gate.

- Architecture A is not admitted: long-only low-leg alpha is weakly supported,
  top-six concentration is unsupported, and universe contamination is fatal.
- Architecture B is not admitted: the literature does not justify an arbitrary
  secondary universe or a hidden blend with another alpha.
- Architecture C is not admitted: it would test incremental filtering inside
  V3, not the required first-stage standalone alpha, and would confound MAX
  with V3's existing IVOL handling.
- Architecture D is therefore the only honest current architecture.

No candidate-versus-V3 return correlation or any other local economic measure
was calculated.
