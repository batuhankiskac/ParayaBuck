# riskoff_rotation_v2 pre-registration (confirmation + audit of v1)

## Question
Does R4 survive an audit and OOS confirmation?

Audit:
a) R1/R2 turnover near-identical (~12.46/yr) is expected: both deploy one unlevered defensive leg at 0.98 off-E2; trades differ (1578 vs 1607).
b) Bootstrap draws consumed chunk-outer with 20-session mean block, 3-session back-extension, seed 20261011, 5000 ten-year paths; C0 median bit-identical to ewma_band_v1 (7.16% base / 6.73% calibrated).
c) No look-ahead: trend/selection/vol use closes <= decision close; defensive fills at the next session open (bond synthetic intraday: open = previous close; GLD/GC open where present; otherwise previous close); costs on every switch.
d) GOLD proxy (GC=F): 18 market-move days >5%, accepted as market moves. BOND10* vs IEF 2002-2024: corr 0.96, CAGR 0.67 pp lower; vs TLT: corr 0.90, CAGR 0.94 pp lower. Accepted as trend-proxy instrument.

Arms: C0 = production E2 exactly; R4 = exact v1 R4 (better of BOND10*/GOLD by 252-session return if > T-bill 252-session return, sized min(1,0.10/vol20), cap 0.98).

Data: main window as v1; OOS: ^GSPC 1957-1999 (GS10 monthly yield, 1-month lag; gold pre-1975 NaN, BOND10*/bills selection fallback), M1 ^IXIC 1975-1999, M3 ^N225, M4 ^GDAXI, M5 ^FTSE measured from 2001-01-02 (local monthly 10y yields with 1-month lag: Japan IRLTLT01JPM156N, Germany IRLTLT01DEM156N, UK IRLTLT01GBM156N; gold in USD via GC=F/GLD; US DTB3 T-bill proxy for hurdle).

Gates: G1-G6 main re-computed after fixes. G9 replaced by turnover-cost stress: R4 still satisfies G1 both financings m=2 at 8 bps x m. G7 across all 5 usable windows (median CAGR delta >=0; >= -0.5 pp in all but one; DD diff <= +3 pp in all but one; never > +8 pp). G8 pooled bootstrap median delta >=0.

Smoke test only before commit.