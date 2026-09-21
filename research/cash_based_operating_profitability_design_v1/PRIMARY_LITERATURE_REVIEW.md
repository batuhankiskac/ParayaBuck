# Primary literature review

## Ball, Gerakos, Linnainmaa and Nikolaev (2016)

The primary authority is the full paper and Appendix: [JFE DOI](https://doi.org/10.1016/j.jfineco.2016.03.002).
Its balance-sheet cash-based operating profitability characteristic is not
operating cash flow, free cash flow, EBITDA, gross profitability, ordinary
operating profitability, or accruals alone.

For fiscal year `t`:

`OP_t = REVT_t - COGS_t - (XSGA_t - XRD_t)`

`CbOP_t = [OP_t - ΔRECT_t - ΔINVT_t - ΔXPP_t + Δ(DRC_t + DRLT_t) + ΔAP_t + ΔXACC_t] / AT_(t-1)`

Every delta is year-over-year. The denominator is prior-year total book assets.
The paper's Appendix explicitly replaces missing Compustat balance-sheet
components with zero; that database convention cannot be copied into SEC XBRL
without evidence that a missing filing fact means economic zero.

The cash-flow-statement alternative is:

`CbOP_CFS_t = [OP_t + RECCH_t + INVCH_t + APALCH_t] / AT_(t-1)`

where the Compustat cash-flow variables already carry their economic signs.
This is a post-1988 alternative/robustness implementation, not permission to
silently replace the primary balance-sheet characteristic.

## Universe, timing and portfolios

- CRSP ordinary common shares on NYSE, Amex, and Nasdaq.
- One-digit SIC 6 financial firms excluded.
- Annual accounting information lagged six months.
- At each June end, NYSE CbOP breakpoints form deciles; portfolios are
  value-weighted, held July through June, and rebalanced annually.
- Sample: July 1963 through December 2014.
- Small/big uses the NYSE median market capitalization.
- The CbOP factor uses independent 2x3 size/profitability portfolios, not a
  concentrated top-name portfolio.

## Long-leg numbers

Table 4, p.37, monthly percent:

| Portfolio | Excess return | CAPM alpha | FF3 alpha |
|---|---:|---:|---:|
| All-stock high CbOP decile | 0.64 (t=3.37) | 0.14 (1.95) | 0.35 (5.98) |
| All-stock high-minus-low | 0.47 (3.17) | 0.65 (4.74) | 0.89 (8.48) |
| Big-stock high CbOP decile | 0.65 (3.41) | 0.16 (1.98) | 0.37 (5.39) |
| Big-stock high-minus-low | 0.43 (3.31) | 0.55 (4.34) | 0.75 (6.31) |

The high long leg has positive raw and FF3 evidence, but its CAPM alpha is
borderline and the much stronger published result is the diversified spread.

## Related primary work

- Ball et al. (2015) supplies the matched operating-profit numerator and
  deflator logic: [JFE DOI](https://doi.org/10.1016/j.jfineco.2015.02.004).
- Sloan (1996) studies operating accruals and cash-flow persistence, but uses a
  different accrual/cash-flow construction and timing:
  [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2598).
- Hribar and Collins (2002) show that balance-sheet accruals are contaminated
  by mergers, acquisitions, discontinued operations, and other non-articulation:
  [DOI](https://doi.org/10.1111/1475-679X.00041).

These papers motivate explicit acquisition/restatement checks; they do not
make the cash-flow alternative identical to primary CbOP.
