# Recovered base signal; no candidate freeze

1. Annual Compustat Segment supplier/principal-customer relationship, with ambiguous customer identity discarded. Apply the paper's six-month fiscal-year-end availability lag. Exact stale-edge policy is not specified in the paper and remains unresolved.
2. Require customer and supplier CRSP/Compustat common-stock security and a nonmissing customer return; supplier prior-month-end price > $5.
3. For multiple customers, equal-weight their completed prior-calendar-month CRSP return. Sales-share weighting is robustness, not base. CRSP's `RET` is a holding-period return with dividends; the primary paper does not specify separate delisting-return stitching. See [CRSP product description](https://www.crsp.org/research__trashed/crsp-us-stock-databases/) and [paper p. 1985](https://pages.stern.nyu.edu/~afrazzin/pdf/Economic%20Links%20and%20Predictable%20Returns%20-%20Cohen%20and%20Frazzini.pdf).
4. At the beginning of t rank suppliers by t-1 customer-portfolio return, form quintiles, hold during t, rebalance monthly. Primary Table III reports value- and equal-weighted supplier legs. Long-high is Q5, not an arbitrary `top_n`.

No faithful ParayaBuck candidate is frozen: a PIT public graph, exact security mapping, breadth, and causal execution translation are unproved. Completed prior-month closes must precede decision creation; fills occur at the next tradable event, never on the decision bar. This physical-time convention would need a preregistered comparison with the paper's monthly-return convention.
