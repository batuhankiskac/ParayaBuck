# mf_sleeve_v1 — pre-registration

Owner question (2026-10-23): is there a genuinely untried return source beyond K's short-term
overlays? Option chosen: **a managed-futures (trend-following) sleeve beside K**, the one
diversifier `idea_survey_v3` set aside only for lack of a long series (DBMF 2019+, KMLM 2020+).

## Disclosure

The proxy series (`build_proxy.py`) was built and its calendar-year returns printed **before**
this file was committed; no arm had been simulated. Its parameters are the textbook
time-series-momentum construction (Moskowitz, Ooi & Pedersen 2012; 1/3/12-month signals as in
KMLM's index) and are frozen as written; they are not changed after seeing the year table. Seen
before commit: the proxy is a weak replica of the CTA indices (2008 +33 %, but 2014 −4 % and
2022 −4 % where the SG Trend Index gained roughly +19 % and +27 %), because nine markets cannot
reproduce a 50–100-market CTA book. Its result is therefore a **lower-quality** answer, to be
checked against real DBMF / KMLM data (2019+) once the Yahoo host is allowed in this environment.

## Proxy (fixed)

Nine markets (^GSPC, ^N225, ^GDAXI, ^FTSE, a DGS10 duration-7.5 synthetic Treasury, WTI spot,
GC=F, HG=F, DTWEXBGS), forward-filled onto the NYSE calendar; signal = mean sign of the 21/63/252
session excess return; raw position = signal × 0.10 / vol60; book scaled to 12 % ex-ante vol
(60-session covariance), gross ≤ 4; 3 bp per unit traded, 0.85 %/yr fee, collateral earns DTB3,
positions from close t earn t + 1. File `data/historical/mf_sleeve_v1_proxy.csv`, SHA-256
`4260aed1cac08fbc1372ed0447fe6f697e1c1ff2fa7156c14d6776832bcb5369`, extra key `mf_proxy`.

## Arms (fixed, plugin `research/ideas/MF1.py`)

| arm | leg A (QLD*) | leg B (mf_proxy) |
|---|---|---|
| `MF1/S10` | 0.90 × K's target | 0.098 constant |
| `MF1/S20` | 0.80 × K's target | 0.196 constant |
| `MF1/S30` | 0.70 × K's target | 0.294 constant |

Rule K on both legs (0.5 pp band, A1u gain band, H2), next-open fills (the proxy's single column is
both fill and close, the framework's convention for an extra leg), one Turkish taxpayer, $100k.

## Gates

The batch_screen framework's own Stage 1 and Stage 2 gates, unchanged
(`research/batch_screen/PREREGISTRATION.md` §4). OOS / ETF windows are `NotApplicable` by
construction. Adoption would additionally require the real-fund check: DBMF 2019-05 → and KMLM
2020-12 → in place of the proxy, after-tax Δ vs K ≥ 0.

Prior (stated before the run): a sleeve with a lower expected return than K's should cost about
`s × (K − MF)` after tax, partly offset by a rebalancing gain; expected −0.3 … −1.5 pp/yr at
S20, with a smaller max DD.
