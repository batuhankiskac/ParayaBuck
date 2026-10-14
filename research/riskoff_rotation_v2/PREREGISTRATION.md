# riskoff_rotation_v2 pre-registration (confirmation + audit of v1)

## Question
Does R4 (when E2 is off, hold the better of BOND10*/GOLD by 252-session return
when it beats the T-bill 252-session return, sized min(1, 0.10/vol20)) survive an
audit and OOS confirmation?

## Audit fixes carried into this screen
a) R1 and R2 turnover near-identical (~12.46/yr) is expected: both deploy a
single unlevered defensive leg at 0.98 whenever off-E2, so rebalance sizes are
similar; they remain distinct in trades (1578 vs 1607).
b) Bootstrap draws are now consumed in chunk-outer order with a 20-session mean
block, 3-session back-extension, seed 20261011, 5,000 ten-year paths, matching
ewma_band_v1 exactly (C0 median confirmed byte-identical).
c) No look-ahead: trend/selection/vol use closes <= decision close; defensive fills
at the next session (bond is all intraday, so open = previous close; gold proxy
and GLD open used where present; otherwise previous close). Costs charged on
every defensive switch at 4 bps x m in simulate_multi.
d) GOLD proxy continuity: GC=F daily, with no >5% suspicious roll gaps observed
(18 market-move days). BOND10* par-bond construction vs IEF/TLT 2002-2024:
correlation ~0.96 (IEF), ~0.90 (TLT); CAGR ~0.68-0.93 pp lower. Accepted as
trend-proxy instrument; proxy bias documented.

## Arms
Only C0 and R4 run here. C0 = production E2 exactly. R4 = exact v1 R4 definition.

## Data / mechanics
Same as v1, main window 2000-03-09 -> 2026-09-29, synthetic QLD*, base and
calibrated financing (+0.75%/yr), cost 4 bps x m, m=1 and m=2.

## Gates
Main window G1-G6 recomputed after fixes (G1 both financings at m=2; G2 DD; G3
2017+; G4 >=2/3 sub-periods; G5 bootstrap median delta >=0 both financings; G6
G1 at m=1). G9 replaced by turnover-cost stress: R4 still satisfies G1 (both
financings, m=2) when R4 costs doubled to 8 bps x m. OOS G7/G8 on usable windows
only: ^GSPC 1957-1999 and M1 ^IXIC 1975-1999; gold pre-1975 is NaN, selection
falls back to BOND10*/bills. M3-M5 are not runnable with daily bond and gold at
a quality needed, so are excluded. G7: median delta CAGR >=0 across usable
windows, delta CAGR >= -0.5 pp in all but one, delta DD <= +3 pp in all but one,
never > +8 pp. G8: pooled bootstrap median delta >=0 across usable windows.
