# EAR formula specification

## Primary Brandt et al. definition

Source: Brandt, Kishore, Santa-Clara, and Venkatachalam (2008), equation (4), pp. 9–10 and Tables 1–2, [author-hosted PDF](https://www.anderson.ucla.edu/documents/areas/fac/finance/santa_clara_surprises.pdf).

For firm `i`, fiscal quarter `q`, Compustat quarterly announcement date `t`, daily stock return `R(i,j)`, and daily return `FF(i,j)` on the size/book-to-market Fama–French portfolio containing firm `i`:

```text
stock_window(i,q) = product[j=t-1..t+1](1 + R(i,j))
benchmark_window(i,q) = product[j=t-1..t+1](1 + FF(i,j))

EAR(i,q) = stock_window(i,q) - benchmark_window(i,q)
```

Exact properties:

- Window: three trading days, `t-1`, `t`, `t+1`.
- Event date: Compustat Industrial Quarterly `RDQE` in the source study.
- Benchmark: one of six portfolios formed from the intersection of two size groups and three book-to-market groups, following Fama and French (1992).
- Arithmetic: separately compound stock and benchmark returns across the window, then subtract.
- Unit: decimal return; paper tables display percent.
- The source does not specify exact historical membership files, constituent breakpoints, annual reconstitution timing, point-in-time accounting lags, or how to resolve a missing/ambiguous membership. Those are data requirements, not free implementation choices.

The following are not the Brandt formula: raw stock return; stock return minus SPY; stock return minus a broad market return; a market-model residual; V3 residual return; or `sum(R-FF)`.

## Causal availability and post-window clock

The signal is incomplete through `t` and becomes computable only after the completed `t+1` return and matched benchmark return exist. Brandt et al. equation (5) evaluates subsequent abnormal return as:

```text
post_ABR(i,n) = product[j=t+2..t+n](1 + R(i,j))
                - product[j=t+2..t+n](1 + FF(i,j))
```

This establishes a post-signal evaluation interval beginning on trading day `t+2`. It does not establish an entry at the `t+2` open, because the paper uses daily returns and gives no opening-price execution convention. It also does not address after-hours, pre-market, weekend, or holiday announcements. Those issues require a separate causal event-session rule.

The formula range `t+2..t+n` has `n-1` indexed sessions although the tables call `n` a 30/60/90/120/180/240-day holding period. The campaign should not silently alter the primary formula to erase this notation mismatch.

## Primary breakpoint rule

For current quarter `q`:

1. Calculate EAR for eligible firm-quarter observations.
2. Rank the EAR observations from quarter `q-1`.
3. Take the prior-quarter quintile boundaries.
4. Assign each current-quarter `EAR(i,q)` using those fixed boundaries.

The source says this prevents lookahead from waiting for all quarter-`q` firms to announce. It does not state:

- a minimum number of prior-quarter observations;
- tie handling;
- winsorization or outlier treatment;
- exchange-specific breakpoint membership;
- a fallback for missing prior-quarter cutoffs;
- whether “quarter” means a synchronized calendar-quarter pool or another operational grouping when issuers have differing fiscal quarters.

No full-current-quarter or full-sample cutoff is literature-faithful. The natural continuous ranking quantity is the signed raw EAR; a historical percentile may be derived from the causal prior-quarter reference distribution, but Brandt et al. report quintile portfolios rather than a continuous score implementation.

## Eligibility in the primary study

- January 1987–December 2004.
- Compustat Industrial Quarterly plus CRSP coverage.
- Size and book-to-market calculable.
- Price at least $5 on the trading day before `RDQE`.
- Main sample excludes NAICS 52 financials and NAICS 22 utilities.

The paper provides no documented rule for concurrent M&A, bankruptcy, financing, split, or other corporate-action exclusions.

## Independently supported alternative: Chan et al. ABR

Chan, Jegadeesh, and Lakonishok (1996), equation (2), [published article](https://doi.org/10.1111/j.1540-6261.1996.tb05222.x), define:

```text
ABR(i,t) = sum[j=-2..+1](R(i,j) - R(EW-market,j))
```

This is a four-day additive market-adjusted return relative to the CRSP equal-weighted market index. At each month's start, firms are ranked on their latest prior ABR; NYSE issuers that announced within the prior three months set decile breakpoints; NYSE, AMEX, and Nasdaq stocks are assigned; portfolios are equal weighted.

This alternative has independent predictive evidence, including ABR percentile slopes of 3.7% (t=9.25) for subsequent six-month return and 5.5% (t=7.86) for one-year return in univariate monthly regressions. It must retain its own `[-2,+1]`, equal-weighted-market, additive, monthly-ranking design if invoked. Replacing the primary benchmark while retaining Brandt's other choices would create an untested hybrid.

## Unresolved primary-specification fields

| Field | Source answer |
|---|---|
| Intraday public timestamp | Not used; Compustat `RDQE` date only |
| After-close event-session shift | Not specified |
| Trade price | Not specified |
| Complete-signal earliest daily boundary | After `t+1` completed return |
| Paper's evaluated post-window start | `t+2` daily return |
| Brandt portfolio weighting | Not stated in manuscript |
| Brandt tie handling | Not stated |
| Brandt minimum breakpoint sample | Not stated |
| Exact 2×3 membership construction | Referenced to Fama–French; not fully operationalized in manuscript |

## Attestation

This specification transcribes source definitions only. No local candidate event return or subsequent economic outcome was computed.
