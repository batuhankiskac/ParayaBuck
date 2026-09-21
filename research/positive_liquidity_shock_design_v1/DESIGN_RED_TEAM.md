# Design red team

This review challenges the family before any local economic outcome exists.

| # | Challenge | Finding |
|---:|---|---|
| 1 | Is the primary formula unique? | Yes. Final RFS `LIQU_t = -(ILLIQ_t - mean(ILLIQ_{t-12:t-1}))`; the earlier standardized working-paper variant is not the final specification. |
| 2 | Is daily OHLCV sufficient for Amihud? | Structurally, yes; exactly matching CRSP daily stock return remains unresolved because the local bars do not expose a total-return field. |
| 3 | Is split-consistent dollar volume reliable? | The split basis is internally invariant when price and volume are adjusted reciprocally. The manifest says `split`; synthetic and named real fixtures agree. Provider transformation metadata is not independently embedded. |
| 4 | Does an attractive long leg exist? | Yes in the original diversified U.S. decile evidence. |
| 5 | Is the spread mostly the short leg? | No, but it is asymmetric: about 39% of EW FF3 spread alpha comes from the high leg and 61% from the low leg. |
| 6 | Is the effect small/illiquid dependent? | Materially. The original effect is much stronger in small and illiquid stocks. |
| 7 | Does the $20M scanner remove the core population? | Yes. It deliberately truncates the segment with the strongest published effect. |
| 8 | Is modern U.S. evidence sufficient? | No exact, persuasive post-publication U.S. large/liquid concentrated long-only replication was found. |
| 9 | Are Japan/Korea directly comparable? | They support a broad phenomenon, not exact U.S. implementation parity; mechanisms and liquid-subset results differ. |
| 10 | Is this abnormal volume in disguise? | Not algebraically: the numerator and 12-month illiquidity baseline differ from the closed AV design. Mechanical component overlap remains. |
| 11 | Is it just low realized volatility? | No categorical identity, but the local contemporaneous correlation with return volatility is `-0.600`, a material overlap warning. |
| 12 | Is it just current illiquidity? | The score is a change, not a level, but the contemporaneous correlation with current `ILLIQ` is `-0.923`; level exposure is dominant locally. |
| 13 | Does V3 IVOL filtering absorb selection? | Likely partially, because lower absolute-return variation mechanically raises positive liquidity shock. No local outcome-controlled test was performed. |
| 14 | Does broad `US_EQUITY` contaminate extremes? | Yes. Complete PIT common-stock/product taxonomy is unavailable, so ETF, ETN, preferred and structured-product shocks cannot be excluded honestly. |
| 15 | Does top-six have external support? | No. Published evidence is from diversified portfolios and continuous cross-sectional tests. |
| 16 | Does one slot have credible alpha density? | No external evidence establishes issuer-level reliability in the single most extreme observation. |
| 17 | Is monthly next-open timing faithful? | It can be expressed causally with the exact next-open helper, but the generic monthly path is one session late and cannot be reused unmodified. |
| 18 | Are NYSE breakpoints required? | They are part of the original portfolio construction. Local historical exchange/taxonomy gaps prevent exact reproduction. |
| 19 | Is PIT market cap/value weighting available? | Not to the standard required for an exact value-weighted replication. A rank-only adaptation would be a new design choice. |
| 20 | Are 12 months after IPO sufficient? | Requiring 12 consecutive valid monthly observations is a transparent fail-closed local rule; the paper does not specify a missing-month convention. |
| 21 | Are zeros/missing sessions safe? | Yes in the audit: zero volume remains a scanner bar and updates the next return's price basis, but it never enters Amihud and is never imputed. There are 676,349 such rows. Lifecycle matching is unambiguous and duplicate symbol/session input aborts the audit. |
| 22 | Is monthly turnover reasonable? | Unknown without an economic/holdings experiment, which was prohibited. No claim is made. |
| 23 | Has publication decay weakened the anomaly? | Modern international evidence is mixed and one recent study finds negative shocks dominate while positive increases are small/mostly insignificant. Exact modern U.S. support is insufficient. |
| 24 | Is continuation distinct from contemporaneous reaction? | Conceptually yes: the formation variable is contemporaneous liquidity change and the paper tests subsequent returns. The local audit calculated no subsequent return. |

## Red-team conclusion

The formula is measurable, so ambiguity alone does not force a block. The
evidence does not justify converting a diversified, small/illiquid-sensitive
anomaly into a liquid top-six or one-slot long-only strategy. The broad-universe
taxonomy gap independently prevents faithful interpretation of extreme ranks.

`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`
