# V3 distinctness audit

## Verdict

`EAR_INFORMATION_DISTINCT_ENOUGH_FROM_V3`

This verdict concerns signal identity.  It does not override the separate
modern-evidence or concentration failures.

## Structural comparison

| Property | EAR | Canonical V3 |
|---|---|---|
| Information clock | One identified quarterly public event | Weekly cross-section of completed daily bars |
| Measurement window | Three announcement-centered trading sessions in BKSV | Positive 12–1 residual gate plus raw 6/9/12-month residual-return percentile-rank consensus |
| Benchmark | Contemporaneous size/book-to-market matched portfolio | Residual return relative to SPY-based market model |
| Availability | Only after the full event window completes | After the configured formation/skip history is complete |
| Economic content | Price response to the full earnings-release information bundle | Persistent idiosyncratic price trend over months |
| Refresh | Each quarterly announcement | Weekly rebalance |

The current V3 contract is documented in
[`canonical_v3_baseline.md`](../../docs/research/canonical_v3_baseline.md):
`formation_bars=252`, `skip_bars=21`, 6/9/12-month ranking horizons, positive
12–1 residual gate, `top_n=8`, and an IVOL rejection filter.  The implementation
inherits the positive gate and residual-risk machinery and changes the
cross-sectional score to the multi-horizon rank consensus in
`src/parayabuck/portfolio/equity_residual_momentum_v3.py`.  It does not ingest
earnings events or announcement-window returns.

## Published overlap test

The January 22, 2008 BKSV version explicitly acknowledges mechanical overlap:
a large three-day EAR enters
subsequent cumulative price momentum.  Their Table 6 nevertheless separates
the signals.  For calendar month `m`, EAR ranks use announcement returns from
months `m-2` through `m-4`; momentum ranks use returns from `m-13` through
`m-2`.  Independent double sorts show the EAR spread in four of five momentum
quintiles (20–70 basis points per month), and fewer than 35% of names overlap
between the highest EAR and highest momentum quintiles.  The EAR-only monthly
quintiles are `−0.16, 0.04, 0.10, 0.24, 0.53%`; the `EAR5−EAR1` spread is
0.69%, reported significant at 1%.  Source: [January 22, 2008 BKSV author PDF](https://www.anderson.ucla.edu/documents/areas/fac/finance/santa_clara_surprises.pdf),
SHA-256 `d55a0f2cc116437957f282345e31699a0e50598256e53fca12d1d3fc5b9e5b10`,
§4.4.1 and Table 6.

Version reconciliation: the June 2007 `ear.pdf` draft instead reports an
EAR-only 0.49% top quintile and 0.56% spread.  Those draft values were removed;
all BKSV numbers in this audit now come from the later January 22, 2008 source.

CJL also conclude that past announcement-window abnormal return and past price
return predict future returns after controlling for one another.  This is a
different EAR definition, but it independently supports the conceptual
separation.  Source: [Chan, Jegadeesh and Lakonishok (1996)](https://www.lsvasset.com/pdf/research-papers/Momentum-Strategies-Papers.pdf).

## Limits of the verdict

The published momentum control is conventional 12–2 raw momentum, not the
exact V3 residual 6/9/12 consensus with a 12–1 gate and IVOL filter.  No local
future-return regression or outcome correlation was calculated.  A positive
EAR can mechanically improve both signals after enough time passes, so
portfolio overlap is plausible.  The evidence is sufficient to reject the
claim of identity, but it does not prove incremental portfolio economics or
capital complementarity.  Those would require a later frozen experiment only
if the candidate cleared all other design gates.
