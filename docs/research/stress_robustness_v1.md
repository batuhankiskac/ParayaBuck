# VT35 stress / robustness v1 (2026-09-29)

**Answer: VT35 passes the pre-registered bar for eventual small LIVE on
1999-12 → 2026-09 history (13.1% CAGR vs SPY 8.3%; max DD 47.0% vs SPY 55.2%,
ratio 0.85 ≤ 1.3), and the SGOV/BIL idle-cash overlay is adopted (+0.19 pp
CAGR, max DD 47.0% → 43.2%). But the 2000 bust cost VT35 41% and 7.5 years
underwater, and when resampled paths keep only about a month of trend
persistence (block bootstrap), VT35 loses to SPY in most paths.**

Evidence: `research/stress_robustness_v1/` (`PREREGISTRATION.md` written before
any result, `download_data.py`, `DATA_MANIFEST.json`, `stress.py`,
`RESULTS.json`). Data: Yahoo Finance daily OHLC and dividends for QQQ, SPY, QLD
and VOO, and FRED DTB3, downloaded 2026-09-29. Files are
`data/historical/stress_robustness_v1_{daily,dtb3}.csv`, with SHA-256 in the
manifest.

## Method

- **Close-to-close Stage A2 mechanics (not a Stage B engine replay):**
  - Signal on the completed close; fill at the next open (real Yahoo opens).
  - 0.5 pp rebalance band; 8 bps per traded notional (the 2x-cost gate level).
  - Equity sleeve 0.93. The remaining 0.07 is zero-return cash; crypto is not
    modelled.
- **Synthetic QLD:** 2 × QQQ total return − (0.95% + DTB3)/252 per session,
  reset daily. Nothing was calibrated.
- **Window:** first session with 200 QQQ closes (1999-12-21) → 2026-09-29.
  SPY buy-and-hold at 1.0 is the benchmark.

## Synthetic QLD validation (real QLD, 2006-06-21 → 2026-09-29)

| Measure | Value |
|---|---:|
| Daily tracking error (annualized) | 3.9% |
| Correlation of daily returns | 0.996 |
| CAGR real / synthetic | 25.3% / 26.1% (synthetic +0.77 pp/yr) |
| VT35 on real vs synthetic QLD (2006-07 → 2026-09) | 18.7% / 19.1% CAGR; 39.8% / 39.8% max DD |

The synthetic series is slightly optimistic, by about 0.3–0.8 pp/yr, so the
VT35 figures below are a little flattering. Yearly differences stay within
about ±1.6 pp (2008: −72.9% real vs −71.8% synthetic).

## Full history (1999-12-21 → 2026-09-29)

| Arm | CAGR | Max DD | Worst year | 2000–02 return / DD | 2007–09 return / DD | 2017→ CAGR | Max-DD peak → recovery |
|---|---:|---:|---:|---:|---:|---:|---|
| **VT35 (0.93)** | **13.1%** | **47.0%** | 2000 −19.9% | −33.6% / 41.0% | +49.8% / 33.0% | 29.0% | 2000-03 → 2007-10 (7.5 y) |
| VT35 + BIL | 13.3% | 43.2% | 2022 −17.7% | −28.3% / 36.8% | +49.1% / 33.2% | 29.2% | 2000-03 → 2007-07 (7.3 y) |
| VT35 (1.0, ref.) | 13.9% | 49.6% | 2000 −21.5% | −35.8% / 43.4% | +53.5% / 35.1% | 31.1% | 7.5 y |
| SPY hold | 8.3% | 55.2% | 2008 −36.8% | −37.5% / 47.5% | −16.1% / 55.2% | 15.2% | 2007-10 → 2012-08 (4.9 y; 2000 peak took 6.6 y) |
| QQQ hold | 8.9% | 83.0% | 2008 −41.7% | −73.3% / 83.0% | +7.3% / 53.4% | 21.4% | 2000-03 → 2015-02 (14.9 y) |
| 2x hold (0.93) | 7.7% | 98.1% | 2000 −68.7% | −96.0% / 98.1% | −14.8% / 79.7% | 33.2% | 2000-03 → 2020-07 (20.3 y) |

- **Other losing years for VT35:** 2001 −11.4%, 2005 −12.3%, 2008 −17.2%,
  2011 −16.8%, 2015 −12.1%, 2022 −18.8%. Whipsaw years (2004–05, 2011,
  2015–16) are VT35's weak spots: it lost while SPY gained.
- **Exposure:** 172 trend flips. The market was in an uptrend in 73% of
  sessions, and the mean QLD weight was 0.66 of the sleeve.

**Decision rule, as pre-registered:**
- VT35 DD / SPY DD = 0.85 ≤ 1.3, and CAGR 13.1% > 8.3%, so VT35 is
  **acceptable for eventual small LIVE**.
- The BIL overlay adds +0.19 pp full-period CAGR (+0.19 pp for 2017→) and
  lowers max DD (47.0% → 43.2%), so the overlay is **adopted**.

## Random monthly start dates (overlapping windows, descriptive)

| | 5-year VT35 | 5-year SPY | 10-year VT35 | 10-year SPY |
|---|---:|---:|---:|---:|
| Windows | 261 | | 201 | |
| CAGR p5 / p25 / p50 / p75 / p95 | −0.8 / 7.2 / 13.4 / 22.1 / 30.7% | −1.7 / 2.8 / 11.4 / 14.7 / 17.3% | 4.3 / 8.9 / 14.1 / 19.5 / 24.5% | 0.2 / 7.1 / 9.5 / 13.4 / 15.3% |
| Max DD p50 / p95 | 30.6 / 39.8% | 33.7 / 55.2% | 39.8 / 39.8% | 51.5 / 55.2% |
| **Share VT35 CAGR > SPY** | **73%** | | **94%** | |
| Worst VT35 window | −6.5% CAGR (start 2000-03) | | +1.5% CAGR (start 2000-01) | |

## Monte Carlo (stationary block bootstrap of 1999–2026 sessions, 5,000 × 10 years)

| Mean block (sessions) | VT35 CAGR p5 / p25 / p50 / p75 / p95 | VT35 max DD p50 / p95 | P(VT35 DD > 50%) | VT35 worst 252-session year p50 / p5 | SPY CAGR p50 | P(SPY DD > 50%) | Share VT35 > SPY |
|---|---|---:|---:|---:|---:|---:|---:|
| **20 (pre-registered)** | **−6.8 / 1.0 / 7.1 / 14.1 / 26.0%** | **51.1 / 73.1%** | **53.6%** | **−25.6 / −39.8%** | 8.9% | 10.1% | **43.8%** |
| 60 (post-hoc) | −3.4 / 4.2 / 10.0 / 16.9 / 28.2% | 46.0 / 67.5% | 37.0% | — | 8.9% | 12.9% | 59.4% |
| 250 (post-hoc) | 0.6 / 7.7 / 13.2 / 19.1 / 28.4% | 40.4 / 58.6% | 17.2% | — | 9.0% | 25.3% | 78.4% |

VT35 has a negative 10-year CAGR in 21.7% of paths at block 20, and 4.1% at
block 250. The trend filter's value depends on trends that last for months.
When resampling breaks the history into roughly one-month pieces, VT35 has
about SPY's median return with about 1.5 times its drawdown. Longer blocks,
which keep real multi-month trends, recover the historical edge. The
pre-registered MC is therefore the pessimistic case. It is reported, not
gated.

## Caveats

- **Close-to-close simulation, not Stage B.** It uses synthetic QLD before
  2006, which is slightly optimistic. 1999–2006 financing uses DTB3 (5–6% in
  2000), which the synthetic series includes. Crypto and the production risk
  latches are not modelled; the 0.75 drawdown halt would not have triggered.
- **The pass rests on two historical bear markets**, and in both the 200-SMA
  exited early. The 7.5-year underwater stretch after 2000 and the
  whipsaw-year losses are the realistic pain. A small LIVE allocation should
  be sized for a 45–50% drawdown.
- **The rolling windows overlap heavily.** The 94% 10-year win share comes
  from about 2.5 independent decades.
- **The 2026 bar was downloaded the same day.** Yahoo QLD had one dividend
  (2007-12-20) that looked inconsistent with the split basis. The effect is
  about 0.2% once.
