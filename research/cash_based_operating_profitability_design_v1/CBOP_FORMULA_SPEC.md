# Cash-based operating profitability formula specification

## Canonical accounting identity

For fiscal year `t`:

```text
reported_sga_ex_rd_t = XSGA_t - XRD_t
op_t = REVT_t - COGS_t - reported_sga_ex_rd_t

cbop_t = (
    op_t
    - (RECT_t - RECT_t-1)
    - (INVT_t - INVT_t-1)
    - (XPP_t  - XPP_t-1)
    + ((DRC_t + DRLT_t) - (DRC_t-1 + DRLT_t-1))
    + (AP_t   - AP_t-1)
    + (XACC_t - XACC_t-1)
) / AT_t-1
```

All components use economic positive magnitudes after filing-specific sign
normalization. The denominator is lagged total assets, not current/average
assets, equity, sales, or market capitalization.

## Frozen semantic reading for this design audit

- Revenue: consolidated annual revenue.
- COGS: consolidated annual cost matching that revenue.
- SG&A/R&D: reproduce `XSGA-XRD`; never subtract R&D twice. If R&D is a sibling
  outside the as-filed SG&A subtotal, as-filed SG&A may already exclude R&D.
- Receivables, inventory, prepaids, deferred revenue, accounts payable and
  accrued expenses: current operating balances, with current and comparative
  prior-year instants taken from the same annual accession.
- Assets: comparative prior-year consolidated total assets in that accession.

## Balance-sheet versus cash-flow decision

The balance-sheet formula above remains the primary-paper reference. The
cash-flow-statement version is not selected as a rescue candidate: choosing it
would change the source design and the available sample. Hribar–Collins makes
it an important diagnostic for non-articulation, but not an interchangeable
formula.

No candidate is frozen because the final design is rejected on modern
long-only and concentration evidence. These definitions are an audit contract,
not authorization for an economic run.
