# Split-volume audit

Alpaca's stock-bars documentation defines `adjustment=split` as split adjustment of both price and volume. The repository maps its `splits` basis to that provider value and persists the adjustment basis in cache keys ([Alpaca stock bars](https://docs.alpaca.markets/us/reference/stockbars)).

Reciprocal split adjustment preserves dollar volume. If a forward split factor is `k`, historical price becomes `P/k` and historical volume becomes `V*k`, leaving `(P/k)*(V*k)=P*V`. The same identity holds for reverse splits. The canonical observations checked around AAPL 4-for-1 and NVDA 10-for-1 boundaries exhibit this reciprocal behavior. A raw-versus-split cache comparison for MNST/XLE also preserved the price-volume product.

The important limitation is rounding. Reverse-split histories can contain tiny or zero adjusted volume even when OHLC is nonzero; DRYS-like histories show this failure mode. Those rows are unusable rather than evidence of no trading. The future implementation must fail closed on any nonpositive volume in the 50-observation window and record the exclusion.

The canonical artifact is retrospectively normalized through later splits. That makes absolute historical share counts unsuitable for point-in-time causal interpretation. The selected own-history rank and ratio are scale-invariant when a later split applies the same reciprocal factor throughout the decision window. Gate G1 requires a synthetic forward/reverse-split invariance test and a real-boundary audit before any economics.

The accompanying corporate-action manifest contains 270 forward and 1,207 reverse splits. These counts describe adjustment exposure; they are not candidate events or outcome information.

Verdict: `CONDITIONALLY_USABLE`. Split-adjusted dollar volume is the defensible object; raw volume and turnover are not approved.
