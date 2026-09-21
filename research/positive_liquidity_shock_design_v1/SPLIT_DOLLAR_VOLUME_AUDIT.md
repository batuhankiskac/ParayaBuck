# Split and dollar-volume audit

## Source semantics

The canonical manifest identifies the bars as SIP feed with `adjustment=split`.
The provider request path maps repository `splits` to Alpaca's split adjustment
and stores returned price and volume without a second transformation. Alpaca's
documented split adjustment reciprocally adjusts price and volume; its own
example shows a 20-for-1 split dividing price and multiplying volume. Therefore
`adjusted close * adjusted volume` preserves dollar volume.

The research contract is:

```text
DV_d = split_adjusted_close_d * split_adjusted_share_volume_d
```

Adjusted price must never be multiplied by raw volume. Split/stock-dividend
corporate-action rows are not reapplied because the bars already incorporate
the split basis.

## Structural fixtures

The audit includes explicit two-close 4-for-1 and 1-for-8 fixtures. It
calculates returns on both bases and verifies that reciprocal price and volume
rescaling preserves return, dollar volume and the daily Amihud term. It also
persists the adjacent stored AAPL 2020 and GE 2021 rows around their known
events. Those real rows demonstrate price-basis continuity; they do not
independently prove Alpaca's raw-to-adjusted transformation algorithm.

Retrospective rebasing by a later split is causal for this formula only because
both return and dollar volume are invariant to the constant reciprocal basis
change. This does not authorize other uses of future corporate-action data.

## Fail-closed boundary

The manifest is provenance, not independent proof of every provider adjustment
record. Nonfinite/nonpositive volume, price or mismatched-basis fixtures must
fail. The canonical data contain material zero-volume placeholders; no epsilon,
forward fill or zero-impact interpretation is permitted.

Provider references: [Alpaca Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
and [bar adjustment changelog](https://docs.alpaca.markets/us/changelog/optionally-adjust-bars-after-spin-offs).
