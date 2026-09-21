# Abnormal-volume distinctness audit

## Verdict

`LIQUIDITY_SHOCK_DISTINCT_FROM_CLOSED_AV_HYPOTHESIS`

The closed abnormal-volume candidate was a one-day split-consistent dollar-
volume shock against 49 prior sessions, combined with a middle-40% one-day
formation-return condition and ranked by volume rank plus `DV/Q90`.

The Bali signal instead aggregates daily price impact over a calendar month:

```text
ILLIQ_t = mean(abs(return_d) / dollar_volume_d) * 1e6
LIQU_t  = -(ILLIQ_t - mean(ILLIQ_t-12..t-1))
```

It has no one-day formation-return band, no 49-session threshold and no high-
activity eligibility event. The original paper separately controls continuous
abnormal dollar volume and volume indicators while retaining `LIQU`.

Formula distinctness is not independence. Positive `LIQU` can be caused by
higher dollar volume, lower absolute price movement, or both. The structural
audit therefore reports contemporaneous relationships to dollar-volume change,
absolute-return change, current illiquidity and realized volatility. Those are
redundancy diagnostics only; no future-return relationship was calculated.

The prior candidate remains closed. No threshold, return band or portfolio
result from it is reused to rescue this design.
