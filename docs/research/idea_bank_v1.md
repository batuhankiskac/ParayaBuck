# Idea bank v1 (2026-10-16): 40 pre-registered ideas for improving K after Turkish tax

> Artefact: an *idea bank*, not a study.  No backtest was run, no gate was evaluated, no arm was simulated, no data was downloaded.  `.md` and `.json` are rendered from `research/idea_bank_v1/ideas_{a,b,c,d}.py` by `research/idea_bank_v1/render.py`, so the two files cannot drift.

**Verdict (what this is).** 40 ideas, 119 arms, every parameter fixed now and never tuned later, grouped into 11 categories and spread deliberately beyond the axes the record has already closed. Each idea names the closest tested study and the difference that makes it new; the 10 strongest are ranked 1-10. The bank is written for a two-stage automated screen (pre-tax gates G1-G9 + the robustness stages; after-tax windows + B1/B2 for survivors) with a held-out confirmation and a multiple-testing correction, and each arm is tagged with whether it is expressible as *target weights from data* (the engine's existing extension point) or needs a new execution kernel.

**Counts.** ideas 40 | arms 119 | categories 11 | strongest marked 10 | arms needing a kernel option 23.

**The 10 strongest, in rank order.**

| rank | id | idea | why it is ranked here |
|---:|---|---|---|
| 1 | IB27 | Beta-matched QQQ + QLD blend: same Nasdaq beta-2 exposure at a lower carry | a deterministic carry saving (a cost arbitrage between two Nasdaq instruments), no signal content |
| 2 | IB38 | Tax-unit and tax-regime layer: 20/D eligibility, rate-regime switch table, income splitting | the largest potential payoff (+3..+5 pp if a household member qualifies for GVK muk. 20/D) and zero market risk |
| 3 | IB01 | Efficiency-ratio (Kaufman) momentum vote | a new scale-free statistic on the strategy's core vote at zero data cost |
| 4 | IB22 | International lead-lag: same-session European move as a causal intraday signal | causally clean: Europe's close precedes the US close, so the signal is genuinely available at the decision |
| 5 | IB04 | Variance-ratio (Lo-MacKinlay) trend vote | a serial-dependence vote with a literature, orthogonal to every tested price vote |
| 6 | IB28 | Two-leg execution: route each resize to the leg with the cheaper FIFO lots | the broker-legal version of lot selection: two legs, route the downsize to the cheaper FIFO head |
| 7 | IB39 | Tax-regime stress (TRY/PPI decoupling) and an FX-regime band switch | the only tax-regime question the record names and never ran; can invalidate K's execution layer |
| 8 | IB20 | Semiconductor relative strength as a cyclical risk-on vote | the cheapest plausible leading cyclical signal (semis) and it is not a second index |
| 9 | IB12 | Real 10-year yield veto (nominal yield minus trailing CPI inflation) | the macro axis with the better-motivated variable (real rate, not a nominal spread level) |
| 10 | IB32 | Sticky sizing input: a dead-band on vol20 instead of on the target weight | acts on the trim channel that carries ~91% of the realised taxable gain, with a two-line change |

## 1. How the bank is meant to be consumed

- **Control.** C0 = production K = R2 (E2 2-of-3 vote + half-weight early re-entry) + A1u gain-scaled downsize band (0.5/3/8 pp at a USD FIFO gain fraction <=1% / <=5% / >5%) + H2 (a downsize filling in the last 10 NYSE December sessions defers to January).  Reference cells to reproduce before any arm runs: pre-tax 15.7649% base / 15.1785% calibrated, max DD 46.6698% / 46.7948%, after tax at $100k 10.7533 / 15.2354 / 23.9720%.
- **Stage 1 (pre-tax).** pre-tax main window + G1-G9 + the 21-ETF and rolling-start stages; kill anything failing G1/G2/G5/G9 or the OOS median.  ewma_band_v1's G1-G9 against K: G1 return (CAGR >= C0, or Calmar >= 1.1x with CAGR >= C0 - 0.5 pp), G2 max DD <= C0 + 1 pp, G3 2017+ >= C0 - 1 pp, G4 CAGR above C0 in >= 2 of 3 sub-periods, G5 block-20 bootstrap (seed 20261011, 5,000 paths) median >= 0, G6 G1 at m = 1, G7 the six OOS windows, G8 the pooled OOS bootstrap, G9 turnover <= 2x C0; G1/G2/G3/G5/G6 under both financing models.
- **Stage 2 (after tax).** after-tax windows + B1/B2 + the cost stress for survivors only.  beats K at $100k in all three windows (2000-03-09 -> 2026-09-29; 2010-01-01 ->; 2017-01-03 ->) AND clean bootstrap B1-recent and B2-flat median delta >= 0 with share > 0 >= 55% (seed 20261011, block 20).
- **Held-out confirmation.** the pre-2000 OOS windows and the E3 fresh bootstraps must be the only confirmation set; the multiple-testing correction must count *all* arms run across the bank, not only the survivors of stage 1.
- **Reduced gates.** where a window or stage is not evaluable (documented per idea), the study must pre-register the reduced gate before running, exactly as credit_vote_v1 did for its non-US windows.

### 1.1 The plugin contract (targets from data vs a new kernel)

- **targets from data.** the arm is a new numpy function of the instrument's own OHLC(V) plus any external series aligned by date, producing the per-session target-weight array the engine already consumes; the K fill rule (band / USD gain fraction / December deferral) is unchanged.  This is fe_signals.arm_signals' existing extension point and needs no engine edit.
- **kernel option.** the arm changes the fill/decision logic itself (a fill-price or fill-filter rule, a second leg with its own FIFO book, a state-dependent band width, a tax-funding route).  It requires a copy of the engine inside research/<study>/ with the option added and a parity cell proving the option switched off reproduces the engine bit-for-bit (WORKER_PROTOCOL.md).

Arms tagged `needs_kernel_option` in the JSON (the `kernel_scope` key says whether the cost is paid in every stage or only in the after-tax stage):

| idea | arms | why a kernel is needed |
|---|---|---|
| IB25 | A, B | a bond sleeve is a priced second leg (SHY), not cash; the engine's cash knob cannot carry price risk |
| IB26 | A, B, C | same as IB25 for the curve-conditioned IEF leg |
| IB27 | A, B, C | two legs = two FIFO lot books and one cash account; the engine holds one instrument per account (after-tax stage only: pre-tax/OOS/ETF stages run as a blended synthetic instrument) |
| IB28 | A, B, C | the routing rule must see both legs' FIFO heads inside the fill decision |
| IB36 | A, B, C | the fill decision must see the fill session's open and the prior close |
| IB37 | A, B, C | the fill price itself changes |
| IB38 | C | the routed split needs two books and a route cap; arms A and B are accounting-only |
| IB39 | B, C | the kernel's band widths (0.5/3/8 pp) become state-dependent |
| IB40 | A, B, C | tax-payment funding interacts with sim_one's cash routing inside fe_tax |

Every idea below that needs no kernel option is a change to the target-weight array only; that is the majority (see the counts above).

## 2. Evaluation contract (unchanged from the record)

| element | value |
|---|---|
| control | K (see section 1) |
| main window | 2000-03-09 -> 2026-09-29, 6,679 sessions, m = 2, base and calibrated financing |
| pre-tax gates | ewma_band_v1's G1-G9 against K: G1 return (CAGR >= C0, or Calmar >= 1.1x with CAGR >= C0 - 0.5 pp), G2 max DD <= C0 + 1 pp, G3 2017+ >= C0 - 1 pp, G4 CAGR above C0 in >= 2 of 3 sub-periods, G5 block-20 bootstrap (seed 20261011, 5,000 paths) median >= 0, G6 G1 at m = 1, G7 the six OOS windows, G8 the pooled OOS bootstrap, G9 turnover <= 2x C0; G1/G2/G3/G5/G6 under both financing models. |
| after-tax gates | beats K at $100k in all three windows (2000-03-09 -> 2026-09-29; 2010-01-01 ->; 2017-01-03 ->) AND clean bootstrap B1-recent and B2-flat median delta >= 0 with share > 0 >= 55% (seed 20261011, block 20). |
| OOS windows | GSPC_1957_1999, M1 ^IXIC 1975-1999, M2 ^GSPC 1935-1956, M3 ^N225 1966-2026, M4 ^GDAXI 1989-2026, M5 ^FTSE 1985-2026 |
| robustness stages | 21 quality-passing ETFs pre-tax (calibrated, m = 2, R-USD); E1: 21 ETFs after tax under R-INDEX at $100k; E2: 21 rolling January starts (2001-2021) after tax; E3: fresh bootstraps (seed 20261015, blocks 10/40); m = 4 cost stress after tax at $100k |

## 3. Data inventory

| series | file | coverage | role |
|---|---|---|---|
| QQQ OHLC+adjclose+volume+dividend | `data/historical/stress_robustness_v1_daily.csv` | 1999-03-10 -> 2026-09-29 | main-window instrument and every signal input |
| SPY, QLD (real), VOO | `data/historical/stress_robustness_v1_daily.csv` | SPY 1999-01-04 ->; QLD 2006-06-21 ->; VOO 2010-09-09 -> | benchmarks, real-fund checks |
| synthetic daily-reset 2x QLD* | `built by fe_signals.synthetic_2x from QQQ` | 1999-03-10 -> 2026-09-29 | the traded leg in every stage |
| DTB3 (financing / bill yield) | `data/historical/stress_robustness_v1_dtb3.csv` | 1954 -> 2026-09 | financing, cash yield |
| TB3MS (monthly bill rate) | `data/historical/ewma_band_v1_tb3ms.csv` | 1934-01 -> | pre-1954 OOS financing |
| ^GSPC | `data/historical/ewma_band_v1_gspc.csv (1957-1999 close only) and ewma_band_v1_index.csv (1927-1956 OHLC)` | 1927-12-30 -> 1999-12-31 | GSPC_1957_1999 and M2 OOS windows |
| ^IXIC / ^N225 / ^GDAXI / ^FTSE | `data/historical/ewma_band_v1_index.csv` | ^IXIC 1971-02-05->1999-12-31; ^N225 1965->2026-09-29; ^GDAXI 1987-12-30->; ^FTSE 1984-01-03-> (OHLC) | M1-M5 OOS windows and the international lead-lag signal |
| 21 quality-passing ETFs (XLB XLE XLF XLI XLK XLP XLU XLV XLY EWJ EWG EWU EWA EWH EWS EWL EFA EEM SPY DIA IWM) | `data/historical/range_vol_v2_*.csv / range_vol_v1_*.csv (OHLC+adjclose, no volume); volume+dividend in riskoff_rotation_v4_etfs.csv` | mostly 1999-01-04 ->; EFA 2001-08-27; EEM 2003-04-14; IWM 2000-05-26 | breadth stage |
| IEF / TLT / GLD / GC=F / TQQQ | `riskoff_rotation_v4_etfs.csv, riskoff_rotation_v2_*.csv, leverage_frontier_v1_daily.csv` | IEF/TLT 2002-07-30 ->; GLD 2004-11-18 ->; GC=F 2000-08-30 ->; TQQQ 2010-02-11 -> | bond/gold legs and the instrument arms |
| ^VIX / ^VIX3M / ^VXN | `data/historical/vix_filter_v1_vix.csv, _vix3m.csv, _vxn.csv, _fred.csv` | ^VIX 1990-01-02 ->; ^VXN 2001-01-23 ->; ^VIX3M 2006-07-17 -> | VRP arm |
| BAA10Y / T10Y3M / DGS10 / BAA / AAA / GS10 / TB3MS | `data/historical/credit_vote_v1_*.csv, leverage_frontier_v1_dgs10.csv` | BAA10Y 1986-01-02 ->; T10Y3M 1982-01-04 ->; DGS10 1962-01-02 ->; BAA/AAA monthly 1919-01 ->; GS10 monthly 1953-04 -> | macro/credit arms |
| USD/TRY (CBRT) and YI-UFE | `data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv` | USD/TRY 2000-01-03 ->; YI-UFE 1999-01 -> | the after-tax engine and the FX-regime arms |
| CAPE / earnings yield / 10y real return (Shiller) | `data/historical/valuation_v1_shiller_monthly.csv` | 1871-01 -> 2024-09 (monthly) | already tested (valuation_v1); listed for completeness |
| ^NDX daily | `data/historical/valuation_v1_ndx_daily.csv, vol_shape_v1_ndx.csv` | 1985-10-01 -> 2000-03-22 | pre-2000 calibration windows |
| ^RUT | `data/historical/breadth_vote_v1_rut.csv` | 1987-09-10 -> 2026-10-14 | breadth (already tested) |
| BIL / SGOV / TLT daily (2016+) | `data/historical/tbill_sweep_v1_etf_daily_split_sip.csv` | 2016-01-04 -> 2026-07-31 | bill-sleeve reference |

**New downloads required by the bank (5, all public, none of them market-state data that the harness cannot get):** CPI (CPIAUCSL, monthly SA index, 1947+) for IB12 (FRED CSV); SMH (semiconductor ETF, 2000-05-05+) for IB20 (Yahoo chart API); HG=F (COMEX copper front future, 2000+) for IB23 (Yahoo chart API); SHY (1-3y Treasury ETF, inception 2002-07) for IB25 (Yahoo chart API); scheduled FOMC meeting dates 1994-2026 for IB17 (federalreserve.gov public calendar (not market data)).

**Structural coverage facts that decide several ideas.** (a) The pre-2000 OOS stage builds the 2x leg from the index itself and runs the R-USD rule, so an arm is evaluable out of sample only if its signal exists in that index's own history: ^VIX (1990+) kills the VRP arm's OOS clause, IEF/GLD/SMH/HG=F/SHY (2002+) kill the bond, gold, semis, copper and SHY arms' OOS clauses, and the ^GSPC 1957-1999 window is close-only, which kills any high/low statistic there. (b) The after-tax stage starts in 2000 because the CBRT/YI-UFE calendar does, so no after-tax idea can ever have a true out-of-sample window; B1/B2, the 21 rolling January starts and the E3 fresh bootstraps are the substitutes. (c) Every non-evaluable window is paired with a pre-registered reduced gate (credit_vote_v1's convention), never with a silently dropped check.

## 4. The 40 ideas

| id | cat | rank | idea | plugin | data |
|---|---|---:|---|---|---|
| IB01 | trend | 3 | Efficiency-ratio (Kaufman) momentum vote | targets-from-data | full: QQQ 1999-03-10 -> 2026-09-29 |
| IB02 | trend | - | Median-location trend vote (robust mean vs mean) | targets-from-data | full |
| IB03 | trend | - | ATR/EMA channel (Keltner) vote instead of a Donchian midpoint | targets-from-data | full |
| IB04 | trend | 5 | Variance-ratio (Lo-MacKinlay) trend vote | targets-from-data | full |
| IB05 | trend | - | Hurst-exponent regime gate on the trend vote | targets-from-data | full |
| IB06 | trend | - | Range-expansion / contraction entries and an ATR-chandelier exit | targets-from-data | full |
| IB07 | vol | - | Mean-reverting GARCH(1,1) volatility forecast instead of backward-looking vol20 | targets-from-data | full |
| IB08 | vol | - | Realised-volatility term-structure sizing (short vol relative to long vol) | targets-from-data | full |
| IB09 | vol | - | Vol-of-vol sizing (scale down when realised volatility is itself unstable) | targets-from-data | full |
| IB10 | vol | - | ATR%-based volatility estimate (mean of true ranges, production-feasible) | targets-from-data | full |
| IB11 | vol | - | Downside/upside vol asymmetry as a sizing modifier | targets-from-data | full |
| IB12 | macro | 9 | Real 10-year yield veto (nominal yield minus trailing CPI inflation) | targets-from-data | full (DGS10 daily from 1962, CPI from 1947, 1-month publication lag honoured) |
| IB13 | macro | - | 10-year yield momentum veto (rising long rates) | targets-from-data | full |
| IB14 | macro | - | Credit-spread momentum (widening BAA-10y), not the spread level | targets-from-data | full |
| IB15 | macro | - | Variance-risk-premium proxy (implied minus realised variance) | targets-from-data | full |
| IB16 | calendar | - | Pre-holiday exposure top-up (session before a NYSE holiday) | targets-from-data | full |
| IB17 | calendar | - | FOMC cycle (the 5-week meeting cycle) exposure tilt | targets-from-data | reduced: 2000-03-09 -> 2026-09-29 is fully covered (FOMC dates exist from 1994) |
| IB18 | calendar | - | Options-expiration week tilt | targets-from-data | full |
| IB19 | calendar | - | US presidential-election cycle tilt | targets-from-data | full |
| IB20 | cross-asset | 8 | Semiconductor relative strength as a cyclical risk-on vote | targets-from-data | full window retained: sessions before the signal's first valid date (2000-05-05 + 126) take the 4th vote as *neutral* (2-of-3 behaviour), a convention fixed in advance |
| IB21 | cross-asset | - | Bond-equity momentum spread as a risk-regime modifier | targets-from-data | reduced start: 2003-07 (IEF + 252 warm-up); sessions before that take the multiplier as 1.0 (fixed convention) |
| IB22 | cross-asset | 4 | International lead-lag: same-session European move as a causal intraday signal | targets-from-data | full |
| IB23 | cross-asset | - | Copper/gold ratio trend (growth-vs-safety) as a regime filter | targets-from-data | reduced start: ~2001-09 (GC=F 2000-08-30 + 252 warm-up) |
| IB24 | off-state | - | T-bill sleeve with an entry/exit hysteresis (kill the failed S1 round trips) | targets-from-data | full |
| IB25 | off-state | - | Off-state residual in short-duration Treasuries (SHY) instead of bills | kernel (A, B) | reduced start: 2002-08 |
| IB26 | off-state | - | Curve-conditioned off-state leg: intermediate Treasuries only when the curve pays | kernel (A, B, C) | reduced start: 2003-07 (IEF + 252 warm-up) |
| IB27 | instrument | 1 | Beta-matched QQQ + QLD blend: same Nasdaq beta-2 exposure at a lower carry | kernel (A, B, C) | full |
| IB28 | instrument | 6 | Two-leg execution: route each resize to the leg with the cheaper FIFO lots | kernel (A, B, C) | full; after-tax stages only (it changes nothing pre-tax beyond IB27's carry) |
| IB29 | dd-recovery | - | Drawdown-depth-scaled exposure (two-sided test) | targets-from-data | full |
| IB30 | dd-recovery | - | Time-under-water (duration of the all-time-high drawdown) scaling | targets-from-data | full |
| IB31 | dd-recovery | - | Volatility-spike exit for the early leg (replace the SMA50 break) | targets-from-data | full |
| IB32 | dd-recovery | 10 | Sticky sizing input: a dead-band on vol20 instead of on the target weight | targets-from-data | full |
| IB33 | statistical | - | Realised-skewness crash-risk scaling | targets-from-data | full |
| IB34 | statistical | - | Lag-1 return autocorrelation gate on the trend vote | targets-from-data | full |
| IB35 | statistical | - | Conditional vol targeting: adjust exposure only in the volatility extremes | targets-from-data | full |
| IB36 | execution | - | Gap-aware fill filter: do not sell into the opening gap | kernel (A, B, C) | full |
| IB37 | execution | - | VWAP-proxy fills: exit at the session's typical price | kernel (A, B, C) | full |
| IB38 | tax | 2 | Tax-unit and tax-regime layer: 20/D eligibility, rate-regime switch table, income splitting | kernel (C) | full (after-tax stages only; the pre-tax stages are regime-invariant) |
| IB39 | tax | 7 | Tax-regime stress (TRY/PPI decoupling) and an FX-regime band switch | kernel (B, C) | after-tax stages only |
| IB40 | tax | - | Instalment funding source: satisfy the tax liability from the target reduction, not from an extra sale | kernel (A, B, C) | after-tax stages only |

## 5.1 Trend-signal algebra (6 ideas)

### IB01 Efficiency-ratio (Kaufman) momentum vote -- **rank 3**

**Mechanism.** The production momentum vote is a raw 252-session price return whose magnitude is irrelevant (only its sign counts).  The Kaufman efficiency ratio ER_n = |c_t-c_{t-n}| / sum_{i=1..n}|c_{t-i+1}-c_{t-i}| is a scale-free measure of how much of the traversed path was net progress: ER near 1 in a clean trend, near 0 in chop.  Substituting it for the TSM252 vote asks the uptrend to be *efficient* rather than merely positive, which should cut the false re-entries that cost R2 its 2022-12/2023-01 whipsaw and leave the 2001/2009/2023 recoveries (all high-ER) intact.

**Closest tested study.** `deriv_vote_v1 (C4 vol-scaled TSM hurdle tsv>0.25) and trend_robustness_v2` (docs/research/deriv_vote_v1.md).  *Difference:* deriv_vote replaced the vote with price *derivatives* (SMA cross, OLS log-price slope, rising midpoint) and with a 252-session return divided by annualised vol; the efficiency ratio is a path-length statistic that no arm in the record computes.

**Literature.** Kaufman, Trading Systems and Methods (adaptive moving average efficiency ratio) (https://www.wiley.com/en-us/Trading+Systems+and+Methods%2C+6th+Edition-p-9781119605355); Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012 (scale-free trend strength) (https://doi.org/10.1016/j.jfineco.2011.11.003).

**Signal inputs.** QQQ daily close (the arm's own instrument close in the OOS/ETF stages).

**Data.** in repo: `data/historical/stress_robustness_v1_daily.csv (QQQ close)`. New downloads: none. Main window: full: QQQ 1999-03-10 -> 2026-09-29. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only; every OOS index file carries a close column

**Plugin.** `targets_from_data` -- wants() replacement in fe_signals.arm_signals; no engine change.

**Arms (parameters frozen).**

- **A** -- 2-of-3 vote with TSM252 replaced by ER_20 > 0.30
- **B** -- 2-of-3 vote with TSM252 replaced by ER_60 > 0.30
- **C** -- E2 as production, and the early path's entry additionally requires ER_20 > 0.30

**Honest prior.** medium: a new statistic on the core vote, no new data, cheap to run.

### IB02 Median-location trend vote (robust mean vs mean)

**Mechanism.** Production's level vote is close > SMA200 -- a mean over the trailing window, which a single crash bar or a spike high biases upward for 200 sessions and which therefore holds the vote *off* too long after any single extreme print.  The median of the trailing 200 closes is the same idea with a robust location statistic; the claimed difference is fewer post-crash false negatives without any lookback change, i.e. it attacks exactly the lag the early path was invented to patch.

**Closest tested study.** `trend_robustness_v2 (SMA 100/150/200/250 ensemble) and signal_ensemble_v1` (docs/research/trend_robustness_v2.md).  *Difference:* the closed axis is the SMA *length* and its aggregation; no arm swaps the mean for a robust location statistic at a fixed length.

**Literature.** Huber, Robust Statistics (location estimators); any trend-filter survey (https://doi.org/10.1002/0471725250).

**Signal inputs.** QQQ daily close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- sma_up -> rolling median; no engine change.

**Arms (parameters frozen).**

- **A** -- MED200 (median of the last 200 closes) replaces SMA200 in the 2-of-3 vote
- **B** -- MED100 replaces SMA200
- **C** -- 3-of-4 vote {SMA200, MED200, TSM252, DON126}, on iff >= 3

**Honest prior.** medium-low: tiny change, but the vote is the strategy's core and the change is free.

### IB03 ATR/EMA channel (Keltner) vote instead of a Donchian midpoint

**Mechanism.** DON126 asks close > the midpoint of the 126-session max/min -- a *range* channel whose width is set by the largest historical excursion, so it is insensitive to how volatile the market currently is.  A Keltner channel (EMA_n +/- k*ATR_n) scales the breakout threshold with the instrument's own current true range: in a calm market it re-enters earlier than DON126, in a wild one later, which is the opposite of the fixed-width behaviour that made R2 sit out 2009-04 and 2020-04.

**Closest tested study.** `signal_ensemble_v1 / e2_confirmation_v1 (DON126 = production vote)` (docs/research/signal_ensemble_v1.md).  *Difference:* DON126's threshold is the channel midpoint of *price extrema*; the ATR channel's is a volatility-scaled distance from an EMA.  The nearest measured neighbour is fast_reentry_v1 R3 (Donchian-126 AND SMA50), still a price-extrema channel.

**Literature.** Keltner channel / ATR (Wilder 1978); Bollinger-band breakout literature (https://en.wikipedia.org/wiki/Keltner_channel).

**Signal inputs.** QQQ daily high, low, close.

**Data.** in repo: `stress_robustness_v1_daily.csv (QQQ OHLC)`; `data/historical/ewma_band_v1_index.csv (^IXIC/^GSPC/^N225/^GDAXI/^FTSE OHLC)`. New downloads: none. Main window: full. OOS windows: M1, M2, M3, M4, M5 -- high/low exist in ewma_band_v1_index for all five market windows, including ^GSPC 1927-1956; the ^GSPC 1957-1999 window is close-only (ewma_band_v1_gspc.csv), so a true-range statistic cannot be built there -> that one window is dropped and a reduced 5-window G7 is pre-registered as credit_vote_v1 did for its 3-window gate.

**Plugin.** `targets_from_data` -- don_up -> keltner_up; no engine change.

**Arms (parameters frozen).**

- **A** -- DON126 replaced by close > EMA20 + 1.0*ATR20 in the 2-of-3 vote
- **B** -- SMA200 replaced by close > EMA50 + 0.5*ATR20 in the 2-of-3 vote
- **C** -- early-path entry gate 'close > SMA50' replaced by 'close > EMA20 + 1.0*ATR20'

**Honest prior.** medium-low: an established channel family, untested here.

### IB04 Variance-ratio (Lo-MacKinlay) trend vote -- **rank 5**

**Mechanism.** Instead of asking whether price rose, ask whether *returns are trending*: the variance ratio VR_q = Var(q-session return)/(q * Var(1-session return)) estimated on the trailing 252 sessions is > 1 exactly when multi-day returns variance-add more than linearly, i.e. when returns are positively serially dependent.  Using VR_5 > 1 (or VR_20) as the momentum vote makes the on-state conditional on the *structure* of returns rather than their level, so a positive but mean-reverting drift does not buy 2x exposure.

**Closest tested study.** `deriv_vote_v1 (OLS slope of log close, golden cross, rising midpoint)` (docs/research/deriv_vote_v1.md).  *Difference:* deriv_vote's statistics are functions of the price path's *slope*; the variance ratio is a second-moment serial-dependence test with no price-derivative content. The scout inventory records 'autocorrelation / serial-dependence signals' as untested anywhere in the record.

**Literature.** Lo & MacKinlay, Stock market prices do not follow random walks, RFS 1988 (https://doi.org/10.1093/rfs/1.1.41); Moskowitz, Ooi & Pedersen 2012 (time-series momentum) (https://doi.org/10.1016/j.jfineco.2011.11.003).

**Signal inputs.** QQQ daily close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- new numpy statistic feeding the vote; no engine change.

**Arms (parameters frozen).**

- **A** -- 2-of-3 vote with TSM252 replaced by VR_5(252-session estimate) > 1.0
- **B** -- 2-of-3 vote with TSM252 replaced by VR_20 > 1.0
- **C** -- E2 as production AND VR_5 > 1.0

**Honest prior.** medium: literature-backed, low correlation with the tested price votes.

### IB05 Hurst-exponent regime gate on the trend vote

**Mechanism.** The Hurst exponent estimated by rescaled range over the trailing 252 sessions is the classical persistence statistic: H > 0.5 means increments are persistent (trend friendly), H < 0.5 mean-reverting.  Gating the E2 vote (or just the early path) on H > 0.5 tests whether the strategy should be allowed to run at all in mean-reverting regimes -- the regime in which the 1935-56 and 2000-02 whipsaws live.

**Closest tested study.** `deriv_vote_v1 / breadth_vote_v1 (regime-conditioned votes)` (docs/research/deriv_vote_v1.md).  *Difference:* no arm computes a persistence exponent; the closest are price-derivative votes.

**Literature.** Hurst 1951; Peters, Fractal Market Analysis (R/S on equity indices) (https://www.wiley.com/en-us/Fractal+Market+Analysis-p-9780471585244).

**Signal inputs.** QQQ daily close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- rolling R/S estimator added to fe_signals-compatible wants(); no engine change.

**Arms (parameters frozen).**

- **A** -- E2 on iff the 2-of-3 vote holds AND H(252, block sizes 10/20/40/80) > 0.50
- **B** -- same with the threshold H > 0.55
- **C** -- early-path entry additionally requires H > 0.50

**Honest prior.** low-medium: heavy estimator, both-sided evidence in the literature.

### IB06 Range-expansion / contraction entries and an ATR-chandelier exit

**Mechanism.** True range (max(high-low, |high-prev close|, |low-prev close|)) is the one statistic in the daily bar that the production rule ignores.  Two untested uses: (a) require an expansion bar (TR > 1.5*ATR20) on the session that triggers an early-path entry, so the entry is a breakout day and not a drift up; (b) exit the on-state when close falls more than 2.5*ATR20 below the highest close of the last 20 sessions (the chandelier exit), which is a trend-following exit that has no 200-session averaging lag.

**Closest tested study.** `intraday_stop_v1 (T8/T12 stops) and dip_overlay_v1 (5-day return triggers)` (docs/research/intraday_stop_v1.md).  *Difference:* the tested exit overlays use *fixed percentage* distances below the prior close; the chandelier exit is volatility-scaled and anchored on the running high, and no arm has used true range at all.

**Literature.** Wilder 1978 (ATR); Crabel, Day Trading with Short Term Price Patterns (NR7/range expansion) (https://en.wikipedia.org/wiki/Average_true_range).

**Signal inputs.** QQQ high, low, close.

**Data.** in repo: `stress_robustness_v1_daily.csv`; `ewma_band_v1_index.csv`. New downloads: none. Main window: full. OOS windows: M1, M2, M3, M4, M5 -- same reduction as IB03: ^GSPC 1957-1999 is close-only

**Plugin.** `targets_from_data` -- extra gates on the early path / an extra exit condition in the target series; no engine change.

**Arms (parameters frozen).**

- **A** -- early entry additionally requires TR_t > 1.5*ATR20
- **B** -- early entry additionally requires the 7-session range < 0.5*ATR20 (contraction, the opposite sign)
- **C** -- on-state forced to 0 when close < (highest close of the last 20 sessions) - 2.5*ATR20

**Honest prior.** low-medium: an exit overlay, the family that has lost every time, but the statistic is new.

## 5.2 Volatility estimators and sizing inputs (5 ideas)

### IB07 Mean-reverting GARCH(1,1) volatility forecast instead of backward-looking vol20

**Mechanism.** vol20 is the realised standard deviation of the last 20 returns: it is unbiased for the recent past and blind to the fact that volatility mean-reverts, so it under-sizes exposure exactly after a spike (when the next 20 sessions are usually calmer) and over-sizes it after a long calm stretch.  A GARCH(1,1) one-step forecast with fixed constants, sigma^2_{t+1} = omega + 0.10*r_t^2 + 0.85*sigma^2_t, omega set so the unconditional annualised volatility is 20%, is the standard term-structure-aware answer and is the only *forecast* class the record has not used (the TimesFM arm is a neural forecaster, EWMA is an exponential average with no mean reversion).

**Closest tested study.** `ewma_band_v1 W1 (RiskMetrics lambda=0.94) and timesfm_v1 V1` (docs/research/ewma_band_v1.md).  *Difference:* EWMA is a variance recursion with no long-run mean and no separate reaction to the last squared return; GARCH(1,1) mean-reverts to a fixed unconditional level.  The vol-*estimator* axis is declared closed by idea_survey_v5 on the strength of vol_window_v1/ewma_band_v1/range_vol_v1-3; this is a different estimator family and is filed as an axis-closure *test*, not as a new axis.

**Literature.** Bollerslev 1986 (GARCH); Andersen, Bollerslev, Diebold & Labys 2003 (realised vol forecastability) (https://doi.org/10.1016/0304-4076(86)90063-1).

**Signal inputs.** the arm's own instrument close (QQQ in the main window).

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- vt_of(vol) -> vt_of(garch_sigma); no engine change.

**Arms (parameters frozen).**

- **A** -- vt = min(1, 0.35/sigma_ann); sigma_ann = sqrt(252*sigma2), sigma2_{t+1} = omega + 0.10*r_t^2 + 0.85*sigma2_t with omega = 7.9365e-06 (=(0.20^2/252)*0.05), seeded with the sample variance of the first 20 returns
- **B** -- vt = min(1, 0.35/max(vol20, sigma_ann))
- **C** -- vt = min(1, 0.35/(0.5*vol20 + 0.5*sigma_ann))

**Honest prior.** medium-low: the estimator axis is closed, but this is the untested family inside it.

### IB08 Realised-volatility term-structure sizing (short vol relative to long vol)

**Mechanism.** vix_filter_v1 tested the *implied* term structure (VIX > VIX3M) and lost.  The realised analogue is untested and has a different information content: sizing on vol20 * (vol20/vol252)^lambda penalises exposure when short-horizon realised vol is elevated relative to the one-year level (the realised-curve is inverted), which is the state the vol-target trims are made in.  It is a *ratio*, not another window, so it is not a neighbour of the closed vol-window axis.

**Closest tested study.** `vol_window_v1 (V10/V40/V60/VMAX) and vix_filter_v1 X3/X4` (docs/research/vol_window_v1.md).  *Difference:* every tested arm picks a *level* (one window, or max of two, or an implied-vol level); none uses the short/long ratio as the sizing input.

**Literature.** Harvey et al., The Impact of Volatility Targeting, JPM 2018 (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538); Moreira & Muir 2017 (volatility-managed portfolios) (https://doi.org/10.1111/jofi.12513).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- vol_n(20) and vol_n(252) both already exist in fe_signals.

**Arms (parameters frozen).**

- **A** -- vt = min(1, 0.35/(vol20*(vol20/vol252)^0.5))
- **B** -- vt = min(1, 0.35/(vol20*(vol20/vol252)^1.0))
- **C** -- vt = min(1, 0.35/vol20) * clip(vol252/vol20, 0.85, 1.00) (an upside-only term-structure penalty)

**Honest prior.** medium-low: a new functional form on a closed axis.

### IB09 Vol-of-vol sizing (scale down when realised volatility is itself unstable)

**Mechanism.** Uncertainty about *risk* is a distinct state from the level of risk: a market whose 20-session vol is jumping around (high vol-of-vol) has a less reliable vol estimate, so a vol-targeting rule should size down.  Concrete arm: vov_t = the standard deviation of the last 20 daily values of vol20, and a multiplier clip(median_252(vov)/vov_t, floor, 1.0) applied to the production weight.  vix_filter_v1 X4 (VXN-implied sizing) is the closest tested arm and it *replaced* the vol estimate rather than modulating it.

**Closest tested study.** `vix_filter_v1 X4 (vt(max(vol20, 2*VXN/100)))` (docs/research/vix_filter_v1.md).  *Difference:* X4 used option-implied vol as an alternative vol level and requires ^VXN (2001+); idea_survey_v2 section 7.3 rejected the option-implied vol-of-vol class (VVIX/SKEW) on data grounds (VVIX starts 2007).  This uses the *realised* dispersion of the realised vol estimator, exists for the full 2000-2026 window and for every pre-2000 OOS window, and no arm in the record computes it.

**Literature.** Baltussen, van Bekkum & van der Grient, Unknown Unknowns, JFQA 2018 (vol-of-vol) (https://doi.org/10.1017/S0022109018000658).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only (needs 20+252 warm-up beyond the vote warm-up)

**Plugin.** `targets_from_data` -- nested rolling std in numpy; no engine change.

**Arms (parameters frozen).**

- **A** -- weight *= clip(median_252(vov)/vov_t, 0.60, 1.00); vov_t = std of the last 20 daily vol20 values
- **B** -- the same with floor 0.80
- **C** -- the same multiplier applied only while the vote is on (the early path unchanged)

**Honest prior.** low-medium: latent-state sizing with a documented literature, but only partial data (^VIX) in the tested neighbours.

### IB10 ATR%-based volatility estimate (mean of true ranges, production-feasible)

**Mechanism.** range_vol_v1-v3 showed that range-based vol estimates (Parkinson/Garman-Klass/Yang-Zhang) beat close-to-close vol20 in-sample, and then failed the implementability gate: the passing arm G3x needed a close-to-close/Parkinson ratio expanded from 1999.  A simple ATR%-based estimate, sigma_ann = 0.63*(ATR20/close)*sqrt(252), needs only 21 bars, is exactly implementable in production, and is the *mean* of true ranges rather than a variance of ranges -- the constant 0.63 is the a-priori Parkinson factor, not a fitted one.

**Closest tested study.** `range_vol_v3 (F1 rolling 252/504 ratio, F2 plain Parkinson, F3 frozen ratio)` (docs/research/range_vol_v3.md).  *Difference:* all three tested arms rescale a range-variance estimator by an estimated ratio; the ATR form has no rescaling constant to estimate and no full-history requirement, which is precisely the objection that closed that campaign.

**Literature.** Parkinson 1980; Garman & Klass 1980; Alizadeh, Brandt & Diebold 2002 (range-based vol) (https://doi.org/10.1016/S0304-405X(02)00140-X).

**Signal inputs.** the arm's own instrument high, low, close.

**Data.** in repo: `stress_robustness_v1_daily.csv`; `ewma_band_v1_index.csv`. New downloads: none. Main window: full. OOS windows: M1, M2, M3, M4, M5 -- ^GSPC 1957-1999 is close-only; there TR == |c_t-c_{t-1}| degenerates towards vol20 (disclosed)

**Plugin.** `targets_from_data` -- vt_of(vol20) -> vt_of(atr_sigma); no engine change.

**Arms (parameters frozen).**

- **A** -- vt = min(1, 0.35/sigma_atr), sigma_atr = 0.63*(ATR20/close)*sqrt(252)
- **B** -- vt = min(1, 0.35/(0.5*sigma_atr + 0.5*vol20))
- **C** -- vt = min(1, 0.35/max(sigma_atr, vol20))

**Honest prior.** medium-low: a production-feasible form of a family that failed for implementability.

### IB11 Downside/upside vol asymmetry as a sizing modifier

**Mechanism.** vol_window_v1 tested the downside semi-deviation *as the estimator* (V5).  Untested is the *ratio* of downside to upside semi-deviation: when realised downside vol leads upside vol (a crash-prone distribution), the same total vol is a worse risk, so the weight is multiplied by clip(semiUp/semiDown, 0.70, 1.00).  This is a shape-of-the-distribution signal that keeps vol20 as the level and uses the asymmetry only as a tilt.

**Closest tested study.** `vol_window_v1 V5 (SEMI = sqrt(2*252/20*sum(min(r,0)^2)))` (docs/research/vol_window_v1.md).  *Difference:* V5 replaced the vol level with the downside semi-deviation; this keeps vol20 and adds the asymmetry ratio.

**Literature.** Ang & Chen 2002; Bollerslev, Li & Zhao 2020 (good vs bad volatility) (https://doi.org/10.1016/S0304-405X(02)00138-1).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- weight *= clip(semiUp60/semiDown60, 0.70, 1.00)
- **B** -- weight *= clip(semiUp252/semiDown252, 0.70, 1.00)
- **C** -- weight *= clip(semiUp60/semiDown60, 0.70, 1.00), applied to the early-path leg only

**Honest prior.** low-medium.

## 5.3 Macro / credit / rates filters (4 ideas)

### IB12 Real 10-year yield veto (nominal yield minus trailing CPI inflation) -- **rank 9**

**Mechanism.** credit_vote_v1 tested *nominal* credit spreads and the nominal 10y-3m curve as risk-off triggers and lost 2.6-5.2 pp of CAGR, but the variable with a documented link to long-horizon equity returns is the *real* rate: the equity risk premium falls one-for-one in the real 10-year yield, and the 2022 de-rating is the clean example.  Arm A vetoes the on-state while DGS10 minus the trailing 12-month CPI change exceeds 2.0%; arm B halves the weight above 1.5%; arm C makes it a 4th vote (>= 3 of {SMA200, TSM252, DON126, real rate <= 2.0%}).  All three keep the trend votes as the primary signal and use the real rate only as a slow veto/tilt.

**Closest tested study.** `credit_vote_v1 K1 (BAA10Y <= its 200-session SMA) and K3 (curve inverted >= 20 sessions)` (docs/research/credit_vote_v1.md).  *Difference:* K1/K3 are nominal spreads compared to their own history (a self-referential trigger); the real rate is a level-vs-inflation quantity with an absolute threshold, and the scout inventory lists 'real-rate filters' as untested.

**Literature.** Asness, Fight the Fed Model, JPM 2003 (real-rate/valuation link) (https://www.aqr.com/Insights/Research/Journal-Article/Fight-the-Fed-Model); Campbell & Shiller 1998; Fama & French, The Equity Premium, JF 2002 (https://doi.org/10.1111/1540-6261.00437).

**Signal inputs.** FRED DGS10 (daily); FRED CPIAUCSL (monthly, 1-month lag).

**Data.** in repo: `data/historical/leverage_frontier_v1_dgs10.csv (1962-01-02 -> 2026-09-29)`; `data/historical/credit_vote_v1_DGS10.csv`. New downloads: FRED CPIAUCSL (monthly SA index, 1947+), saved as data/historical/idea_bank_v1_cpi.csv with URL + SHA-256 in the study manifest. Main window: full (DGS10 daily from 1962, CPI from 1947, 1-month publication lag honoured). OOS windows: GSPC_1957_1999, M1, M2 -- US CPI and DGS10/MODIS exist over the three US windows; M3-M5 (Japan, Germany, UK) have no US macro overlay -> a reduced 3-window G7/G8 is pre-registered exactly as credit_vote_v1 did.

**Plugin.** `targets_from_data` -- external series aligned by date (the engine's `rates`-style input pattern); no engine change.

**Arms (parameters frozen).**

- **A** -- weight = 0 while real10y > 2.0%, else production; real10y = DGS10 - CPI YoY (1-month lag)
- **B** -- weight *= 0.5 while real10y > 1.5%
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, real10y <= 2.0%}

**Honest prior.** medium-low for return, medium for drawdown: the veto family has lost every time, but the real rate is the untested, better-motivated variable.

### IB13 10-year yield momentum veto (rising long rates)

**Mechanism.** Duration is the discount rate applied to long-duration equity cash flows, and QLD is a 2x long-duration Nasdaq-100 position -- the most rate-sensitive equity expression available in the account.  The tested credit arms used *level* triggers; untested is the 60-session *change* in DGS10 as a risk-off trigger: veto while the 10y has risen more than 0.50 pp over 60 sessions (arm A), halve above 0.25 pp (arm B), or make it a 4th vote (arm C).  The 2022 and 2018 rate shocks are where this should bind.

**Closest tested study.** `credit_vote_v1 K3 (T10Y3M inverted for >= 20 consecutive sessions) and K1` (docs/research/credit_vote_v1.md).  *Difference:* K3 keys on the *slope sign* (inversion), not on the speed of the long-rate move; no tested arm uses a yield change.

**Literature.** Fama & French 2002 (term-structure slope as a return predictor); Estrella & Mishkin 1998 (https://doi.org/10.1111/1540-6261.00437).

**Signal inputs.** FRED DGS10 (daily); FRED T10Y3M / GS10-TB3MS for the pre-1982 US OOS windows.

**Data.** in repo: `leverage_frontier_v1_dgs10.csv (1962+)`; `credit_vote_v1_T10Y3M.csv (1982+)`; `credit_vote_v1_TB3MS.csv (monthly 1934+)`; `credit_vote_v1_GS10.csv (monthly 1953+)`. New downloads: FRED TB3MS daily-ified is not needed: GS10/TB3MS monthly splice as in credit_vote_v1, OR FRED TB3MS monthly (already in repo). Main window: full. OOS windows: GSPC_1957_1999, M1, M2 -- monthly GS10/TB3MS with a 1-month lag for the OOS windows (credit_vote_v1's convention)

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- weight = 0 while DGS10_t - DGS10_{t-60} > +0.50 pp
- **B** -- weight *= 0.5 while the same change > +0.25 pp
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, 60-session DGS10 change <= +0.50 pp}

**Honest prior.** low-medium.

### IB14 Credit-spread momentum (widening BAA-10y), not the spread level

**Mechanism.** The tested credit arm asked whether the BAA-10y spread was above its own 200-session mean -- a level test that is slow and self-referential.  Spread *momentum* is the variable credit desks actually trade: veto while the 60-session change exceeds +0.30 pp (arm A), halve above +0.15 pp (arm B), or a 4th vote (arm C).  Mechanically it fires earlier and unwinds earlier than the level test, which is where credit_vote_v1's 5 pp CAGR loss came from.

**Closest tested study.** `credit_vote_v1 K1/K2/K4 (BAA10Y vs its 200-session SMA, 4-vote majority, replacement of TSM252)` (docs/research/credit_vote_v1.md).  *Difference:* level-vs-SMA versus a fixed-horizon change; the BAA-10y series is the same input but the statistic is new.

**Literature.** Gilchrist & Zakrajsek, Credit spreads and business cycle fluctuations, AER 2012 (https://doi.org/10.1257/aer.102.4.1692).

**Signal inputs.** FRED BAA10Y (daily, 1986+); monthly BAA-AAA for the pre-1986 OOS windows.

**Data.** in repo: `data/historical/credit_vote_v1_BAA10Y.csv`; `credit_vote_v1_BAA.csv (monthly 1919+)`; `credit_vote_v1_AAA.csv (monthly 1919+)`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2 -- monthly BAA-AAA (1919+) with a 1-month lag, the credit_vote_v1 OOS convention

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- weight = 0 while BAA10Y_t - BAA10Y_{t-60} > +0.30 pp
- **B** -- weight *= 0.5 while the same change > +0.15 pp
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, 60-session spread change <= +0.30 pp}

**Honest prior.** low-medium.

### IB15 Variance-risk-premium proxy (implied minus realised variance)

**Mechanism.** vix_filter_v1 tested the VIX *level*, a VIX spike ratio and the VIX/VIX3M term structure as cash triggers and lost 0.7-3.2 pp.  Untested is the difference between implied and realised variance, VRP_t = (VIX_t/100)^2 - vol20^2, which is the priced compensation for variance risk rather than the level of fear.  Bollerslev-Tauchen-Zhou find the VRP predicts returns at the quarterly horizon; arms: halve the weight while VRP < 0 (arm A), a z-score tilt weight *= clip(1 + 0.5*z, 0.7, 1.1) with z on a 252-session window (arm B), and a 4th vote 'VRP > 0' (arm C).

**Closest tested study.** `vix_filter_v1 X1 (VIX>30), X2 (VIX/SMA60>1.5), X3 (VIX>VIX3M)` (docs/research/vix_filter_v1.md).  *Difference:* all three tested arms use the VIX level or its own term structure; none subtracts realised variance, i.e. none is a premium rather than a level.

**Literature.** Bollerslev, Tauchen & Zhou, Expected stock returns and variance risk premia, RFS 2009 (https://doi.org/10.1093/rfs/hhp008).

**Signal inputs.** ^VIX close (daily, 1990+); the arm's own instrument close (for vol20).

**Data.** in repo: `data/historical/vix_filter_v1_vix.csv (1990-01-02 -> 2026-09-29)`; `data/historical/vix_filter_v1_fred.csv (VIXCLS)`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999(1990-1999 slice) -- ^VIX starts 1990-01-02, so only the 1990-1999 part of the GSPC_1957_1999 window is evaluable and M1-M5 are not; exactly reproduce vix_filter_v1's G8 convention or pre-register a single-window gate.

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- weight *= 0.5 while VRP_t = (VIX/100)^2 - vol20^2 < 0
- **B** -- weight *= clip(1 + 0.5*z_t, 0.7, 1.1), z = (VRP - median252(VRP))/std252(VRP)
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, VRP > 0}

**Honest prior.** low-medium: the closest arm's family lost, and the OOS coverage is one window.

## 5.4 Calendar and structural-date effects (4 ideas)

### IB16 Pre-holiday exposure top-up (session before a NYSE holiday)

**Mechanism.** The pre-holiday effect (the last session before an exchange holiday earns an abnormal positive return) is one of the oldest documented calendar anomalies (Ariel 1990, Lakonishok-Smidt 1988) and is completely untested here -- the inventory confirms 'pre-holiday / holiday effect / day-of-week: NOT tested'.  The arms are deliberately written two-sided so the screen measures the sign rather than assuming it: A sets the target to the 0.98 cap on the session before a holiday (only while the trend vote is on); B adds 25% to the weight; C *cuts* to 75% (the mirror that a data-mining screen would need to be able to reject).  Holidays are detected from the session index itself -- a Monday-Friday date on which the market did not trade is a holiday or an unscheduled closure, and the two are treated identically -- so no new data is needed.

**Closest tested study.** `seasonal_overlay_v1 S4 (turn-of-month) and seasonal_placebo_v1` (docs/research/seasonal_overlay_v1.md).  *Difference:* S4 is a month-boundary effect and was killed by its placebo; the holiday calendar is an orthogonal date set, and the scout inventory records pre-holiday as untested.

**Literature.** Ariel, High stock returns before holidays, JF 1990 (https://doi.org/10.1111/j.1540-6261.1990.tb05116.x); Lakonishok & Smidt, Are seasonal anomalies real? RFS 1988 (https://doi.org/10.1093/rfs/1.4.403).

**Signal inputs.** the session calendar (derivable from any of the repo's daily files).

**Data.** in repo: `stress_robustness_v1_daily.csv (session index)`; `ewma_band_v1_index.csv (index session calendars)`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- the holiday set must be the *local* market's calendar for M3-M5; each window's own session index provides it, and US holidays fall out of the ^GSPC/^IXIC calendars automatically.

**Plugin.** `targets_from_data` -- a calendar multiplier on the target series; no engine change.

**Arms (parameters frozen).**

- **A** -- target = 0.98 (cap) on the last session before a detected holiday, while the trend vote is on
- **B** -- target *= 1.25 (capped at 0.98) on that session
- **C** -- target *= 0.75 on that session (the mirror sign)

**Honest prior.** low-medium: the effect is small and decayed, but it costs nothing and tests a genuinely orthogonal date set.

### IB17 FOMC cycle (the 5-week meeting cycle) exposure tilt

**Mechanism.** idea_survey_v2 rejected the *pre-FOMC announcement drift* (it disappeared after 2015 and adds ~8 round trips/yr), and no arm has ever used a policy calendar.  The 'FOMC cycle' of Cieslak, Morse & Vissing-Jorgensen is a different object: the equity premium concentrates in even weeks of the five-week cycle anchored on scheduled meetings.  Arms tilt the target within the cycle: A 1.0x in even weeks / 0.85x in odd weeks; B the same with 0.70x; C the mirror (0.85x in even weeks) so the screen can reject a sign-free artefact.  The tilt is a *multiplier on the existing target*, so no new trade type and no extra round trip is introduced beyond the ordinary resize band.

**Closest tested study.** `seasonal_overlay_v1 (calendar overlays: May-Oct, September, turn-of-month)` (docs/research/seasonal_overlay_v1.md).  *Difference:* the tested overlays are month-of-year effects; the FOMC cycle is a fortnightly policy calendar anchored on meeting dates -- not tested (the pre-FOMC *day* drift was rejected in a survey, not measured).

**Literature.** Cieslak, Morse & Vissing-Jorgensen, Stock returns over the FOMC cycle, JF 2019 (https://doi.org/10.1111/jofi.12838); Lucca & Moench, The pre-FOMC announcement drift, JF 2015 (the rejected variant) (https://doi.org/10.1111/jofi.12196).

**Signal inputs.** scheduled FOMC meeting dates 1994-2026 (public Fed calendar, hard-coded constant table).

**Data.** in repo: none. New downloads: federalreserve.gov published FOMC calendar dates 1994-2026, stored as data/historical/idea_bank_v1_fomc_dates.csv with source URL + SHA-256 (not market data; a public meeting calendar). Main window: reduced: 2000-03-09 -> 2026-09-29 is fully covered (FOMC dates exist from 1994). OOS windows: none -- no OOS window has FOMC dates (1994+) -> G7/G8 are not evaluable; pre-register a reduced gate (main window + 21 ETFs + rolling starts + bootstraps, no OOS clause), disclosed before the run.

**Plugin.** `targets_from_data` -- a calendar multiplier; no engine change.

**Arms (parameters frozen).**

- **A** -- target *= 1.00 when w is even and 0.85 when w is odd, w = floor((session date - the most recent scheduled FOMC meeting date)/7 calendar days) (week 0 = the meeting week)
- **B** -- the same cycle definition with 1.00 / 0.70
- **C** -- the mirror: 0.85 when w is even, 1.00 when w is odd

**Honest prior.** low: no OOS coverage at all and the nearest tested calendar family was placebo-rejected; included because the mechanism is untested and cheap.

### IB18 Options-expiration week tilt

**Mechanism.** Expiration weeks concentrate hedging and dealer-gamma flow, and the documented effect is a *reversal* pattern around monthly expiration (Stivers & Sun): the week of the third Friday has negative and more volatile returns.  Untested here, and it needs no new data -- the third Friday is computable from the session index.  A: weight * 0.75 in every expiration week; B: the same only in the quarterly (Mar/Jun/Sep/Dec) expirations, where the flow is largest; C: the mirror (weight * 0.85 outside expiration weeks, i.e. a positive tilt on them).

**Closest tested study.** `seasonal_overlay_v1 / seasonal_placebo_v1 (calendar overlays)` (docs/research/seasonal_placebo_v1.md).  *Difference:* no tested arm uses an expiration calendar; the scout inventory lists 'quarter-end / options expiry / month-end rebalancing flow: NOT tested'.

**Literature.** Stivers & Sun, Returns and option expiration (2013); Ni, Pearson & Poteshman (2005) (https://doi.org/10.1016/j.jbankfin.2012.09.008).

**Signal inputs.** the session calendar (third-Friday computation).

**Data.** in repo: `session indices in the daily files`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- calendar-only

**Plugin.** `targets_from_data` -- calendar multiplier; no engine change.

**Arms (parameters frozen).**

- **A** -- target *= 0.75 during the Mon-Fri week containing the third Friday of every month
- **B** -- target *= 0.75 only in the quarterly expiration weeks (Mar/Jun/Sep/Dec)
- **C** -- target *= 0.85 outside expiration weeks (mirror sign)

**Honest prior.** low.

### IB19 US presidential-election cycle tilt

**Mechanism.** Santa-Clara & Valkanov document that the equity premium is earned in the second half of the presidential term (years 3-4), a large-sample calendar effect computable for every window from the year alone.  Like the other calendar arms it is written two-sided: A 1.0x in years 3-4 / 0.85x in years 1-2; B 0.85x in the election year only; C the mirror (0.85x in years 3-4).  The screen's multiple-testing correction is what should decide whether a 90-year-old pattern survives on a 26-year sample.

**Closest tested study.** `seasonal_overlay_v1 / seasonal_placebo_v1` (docs/research/seasonal_placebo_v1.md).  *Difference:* the tested calendar set is intra-year (months, turn-of-month); no arm uses a multi-year political cycle.

**Literature.** Santa-Clara & Valkanov, The presidential puzzle, JF 2003 (https://doi.org/10.1111/1540-6261.00588).

**Signal inputs.** calendar year.

**Data.** in repo: none. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2 -- US windows only; the political cycle is US-specific

**Plugin.** `targets_from_data` -- calendar multiplier; no engine change.

**Arms (parameters frozen).**

- **A** -- target *= 1.00 in term years 3-4, *= 0.85 in years 1-2
- **B** -- target *= 0.85 in the election year only
- **C** -- target *= 0.85 in years 3-4, 1.00 in years 1-2 (mirror)

**Honest prior.** low.

## 5.5 Cross-asset signals (4 ideas)

### IB20 Semiconductor relative strength as a cyclical risk-on vote -- **rank 8**

**Mechanism.** The tested breadth panel (QQQ/SPY/IWM) is a poor discriminator because SPY and QQQ agree 91% of the time.  Semiconductors are the economically leading cyclical block inside the Nasdaq-100 itself: SMH's 126-session return minus QQQ's is a within-technology cyclical signal rather than a second index.  A: a 4th vote (>= 3 of {SMA200, TSM252, DON126, semis 126-return > QQQ's}); B: veto the on-state while SMH underperforms QQQ by more than 5 pp over 126 sessions; C: the early path's entry additionally requires it.

**Closest tested study.** `breadth_vote_v1 B4 (5-vote majority of {QQQ SMA/TSM/DON, SPY TSM, IWM TSM})` (docs/research/breadth_vote_v1.md).  *Difference:* B4 adds whole-index votes that agree with QQQ's 91% of the time; the scout inventory records 'semis/tech sub-industry signals: NOT tested'. The instrument is a sector ETF whose correlation with QQQ is high but whose *relative* trend carries the cyclical information.

**Literature.** Semiconductor cycle as a global growth bellwether (industry/BIS literature) (https://www.bis.org/publ/qtrpdf/r_qt1809.htm); Asness, Frazzini, Israel, Moskowitz & Pedersen, Size matters if you control your junk (sector momentum) (https://doi.org/10.1093/rfs/hhac008).

**Signal inputs.** SMH daily close (new); QQQ daily close (in repo).

**Data.** in repo: `stress_robustness_v1_daily.csv (QQQ)`. New downloads: Yahoo chart API SMH (2000-05-05 ->), saved as data/historical/idea_bank_v1_smh.csv with URL + SHA-256 in the study manifest. Main window: full window retained: sessions before the signal's first valid date (2000-05-05 + 126) take the 4th vote as *neutral* (2-of-3 behaviour), a convention fixed in advance. OOS windows: none -- SMH has no pre-2000 history -> G7/G8 are not evaluable for this arm; pre-register the reduced gate (main window + 21-ETF stage + rolling starts + bootstraps) as credit_vote_v1 did for its non-US windows.

**Plugin.** `targets_from_data` -- second series aligned by date; no engine change.

**Arms (parameters frozen).**

- **A** -- 3-of-4 vote {SMA200, TSM252, DON126, r_SMH126 > r_QQQ126}
- **B** -- weight = 0 while (r_SMH126 - r_QQQ126) < -5 pp
- **C** -- early-path entry additionally requires r_SMH126 - r_QQQ126 > 0

**Honest prior.** medium-low for return; the cleanest untested cyclical signal that needs only one small download.

### IB21 Bond-equity momentum spread as a risk-regime modifier

**Mechanism.** riskoff_rotation_v1-v4 tested *holding* the better of bonds/gold while the trend vote is off (and the whole off-state family is closed).  Untested is using the bond-equity momentum spread as a *modifier of the on-state*: when intermediate Treasuries lead the Nasdaq over 252 sessions, the long-duration equity position is being paid less for its risk, so A halves the weight while IEF's 252-session return exceeds QQQ's, B cuts to 0.75 only when the lead exceeds 10 pp, and C restricts the early path (a source of extra realisations) to sessions where bonds are not leading.

**Closest tested study.** `riskoff_rotation_v3/v4 (defensive leg chosen monthly by 252-session return) and riskoff_after_tax_v1` (docs/research/riskoff_rotation_v4.md).  *Difference:* the tested arms allocate the *off-state* capital; this leaves the allocation alone and uses the same spread to size the *on-state*.

**Literature.** Asness, Moskowitz & Pedersen, Value and momentum everywhere, JF 2013 (cross-asset momentum) (https://doi.org/10.1111/jofi.12021).

**Signal inputs.** IEF daily close (in repo); QQQ daily close.

**Data.** in repo: `data/historical/riskoff_rotation_v4_etfs.csv (IEF 2002-07-30 -> 2026-10-13, adjclose)`. New downloads: none. Main window: reduced start: 2003-07 (IEF + 252 warm-up); sessions before that take the multiplier as 1.0 (fixed convention). OOS windows: none -- IEF starts 2002-07-30 -> G7/G8 not evaluable; reduced gate pre-registered (main window, 21-ETF stage, bootstraps).

**Plugin.** `targets_from_data` -- second series aligned by date; no engine change.

**Arms (parameters frozen).**

- **A** -- weight *= 0.5 while r_IEF252 > r_QQQ252
- **B** -- weight *= 0.75 while r_IEF252 - r_QQQ252 > +10 pp
- **C** -- early-path entry permitted only while r_IEF252 - r_QQQ252 <= 0

**Honest prior.** low-medium.

### IB22 International lead-lag: same-session European move as a causal intraday signal -- **rank 4**

**Mechanism.** This is the only idea in the bank with a *timing* edge rather than a statistical one: at the US close (16:00 ET) that decides the trade, the European session of the *same* calendar day has already closed (^GDAXI/^FTSE close ~11:30 ET) and Japan's session closed before the US open.  So a same-session EU return is information the decision maker legally has, not a look-ahead.  A: the early path's entry additionally requires EU_t = 0.5*(r_GDAXI,t + r_FTSE,t) > 0; B: requires EU_t + r_N225,t-1 > 0; C: cuts the on-state weight to 0.75 on the session after EU_t < -2% (a 'Europe sold off hard' risk cut).  If the US-close decision is genuinely informed by the rest of the world's session, these are the only arms in the bank that can monetise it.

**Closest tested study.** `breadth_vote_v1 (same-close cross-index votes) and relstrength_v1 (Q1-Q4)` (docs/research/breadth_vote_v1.md).  *Difference:* those arms read another index's *same-close* value (no timing advantage: the US and the other index close together) or a monthly rotation; this exploits the time-zone ordering, which no arm in the record uses. signal_cross_check_v1 is about data-source noise, not lead-lag.

**Literature.** Rapach, Strauss & Zhou, International stock return predictability, JF 2013 (https://doi.org/10.1111/jofi.12041); Hou, Global momentum (international lead-lag) (https://doi.org/10.1016/j.jfineco.2006.07.004).

**Signal inputs.** ^GDAXI, ^FTSE, ^N225 daily close (all in repo) plus QQQ.

**Data.** in repo: `data/historical/ewma_band_v1_index.csv (^N225 1965+, ^GDAXI 1987+, ^FTSE 1984+)`. New downloads: none. Main window: full. OOS windows: M3, M4, M5 -- the arm needs non-US data, which exists only from 1984/1987 and not at all in 1935-56; GSPC_1957_1999, M1 and M2 are therefore not evaluable -> pre-register a reduced 3-window G7 (as credit_vote_v1 did) plus the 21-ETF stage.

**Plugin.** `targets_from_data` -- multiple series aligned by date in the target builder; no engine change.

**Arms (parameters frozen).**

- **A** -- early entry additionally requires 0.5*(r_GDAXI,t + r_FTSE,t) > 0
- **B** -- early entry additionally requires 0.5*(r_GDAXI,t + r_FTSE,t) + r_N225,t-1 > 0
- **C** -- on-state weight *= 0.75 on the session after 0.5*(r_GDAXI + r_FTSE) < -2%

**Honest prior.** medium: causally clean, uses existing data, no new parameters beyond the thresholds.

### IB23 Copper/gold ratio trend (growth-vs-safety) as a regime filter

**Mechanism.** The copper/gold ratio is the practitioner's growth-versus-safety indicator (Gundlach): copper prices industrial demand, gold prices real-rate fear, and their ratio leads cyclical equity risk appetite.  Only gold appears anywhere in the record (as an off-state holding) and no other commodity has ever been used.  A: a 4th vote on the ratio's 252-session return > 0; B: veto the early path while the ratio's 126-session return < 0; C: weight *= 0.75 while the ratio is below its 252-session median.

**Closest tested study.** `riskoff_rotation_v1-v4 / gold_riskoff_oos_v1 / leverage_frontier_v1 (gold as a holding)` (docs/research/gold_riskoff_oos_v1.md).  *Difference:* every tested use of gold is as a *holding* in the off state; the scout inventory records 'commodities other than gold: NOT tested' and the ratio-as-signal form is untested for gold too.

**Literature.** Gundlach copper/gold ratio; Bhardwaj & Janardanan (commodity ratio as a growth proxy) (https://doi.org/10.1016/j.jcomm.2021.100216).

**Signal inputs.** HG=F (COMEX copper front future, new) and GC=F (in repo) daily closes.

**Data.** in repo: `data/historical/leverage_frontier_v1_daily.csv (GC=F 2000-08-30 -> 2026-09-29)`. New downloads: Yahoo chart API HG=F (2000 ->); if unavailable, the ETF proxies CPER (2011+)/DBB (2007+) are not long enough, so HG=F is the requirement. Main window: reduced start: ~2001-09 (GC=F 2000-08-30 + 252 warm-up). OOS windows: none -- no pre-2000 futures data in the repo -> reduced gate (main window, 21-ETF stage, bootstraps).

**Plugin.** `targets_from_data` -- two external series; no engine change.

**Arms (parameters frozen).**

- **A** -- 3-of-4 vote {SMA200, TSM252, DON126, r_HG/GC(252) > 0}
- **B** -- early-path entry permitted only while r_HG/GC(126) >= 0
- **C** -- weight *= 0.75 while HG/GC is below its 252-session median

**Honest prior.** low-medium.

## 5.6 Off-state holdings (3 ideas)

### IB24 T-bill sleeve with an entry/exit hysteresis (kill the failed S1 round trips)

**Mechanism.** tbill_sweep_v1's S1 (hold BIL whenever the E2 vote is off) failed on the bootstrap by -0.08 pp because a 0.5 pp-banded trend rule turns off and on briefly, paying a BIL round trip each time; S2_10 fixed the *residual* but not that.  This arm leaves the bleed-the-asset question alone and prices only the *timing*: enter the bill sleeve only once the vote has been off for N consecutive sessions (A: 5, B: 10) and, in arm C, exit it only once the vote has been on for 5 consecutive sessions (an exit-side lag instead).

**Closest tested study.** `tbill_sweep_v1 S1 / S2_5 / S2_10 and r2_tbill_combo_v1 RS` (docs/research/tbill_sweep_v1.md).  *Difference:* S1 switches the sleeve on the same session the vote switches; the tested arms vary the *residual band*, not the entry/exit lag on the sleeve itself.

**Literature.** tbill_sweep_v1's own post-mortem (repo); no external literature needed (docs/research/tbill_sweep_v1.md).

**Signal inputs.** the E2 vote state (derived from the arm's own price series).

**Data.** in repo: `stress_robustness_v1_dtb3.csv (BIL* = DTB3 - 0.1356%/yr, tbill_sweep's construction)`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- BIL* is synthetic from DTB3/TB3MS, available in every window

**Plugin.** `targets_from_data` -- the sleeve is a second leg, but it is *cash-like and never rebalanced against the equity leg*, so it can be represented as a cash modifier on the target series in the pre-tax stage; the after-tax stage needs the existing engine's cash-yield knob (fe_sim.RunEnv(cash_rate=...)) rather than a new kernel.

**Arms (parameters frozen).**

- **A** -- enter BIL* on the residual + off-state weight after 5 consecutive off sessions; exit on re-entry
- **B** -- the same with N = 10 consecutive off sessions
- **C** -- enter BIL* immediately on the first off session, exit only after 5 consecutive on sessions

**Honest prior.** low-medium: the family loses after tax; this arm attacks the measured cost, not the tax.

### IB25 Off-state residual in short-duration Treasuries (SHY) instead of bills

**Mechanism.** Every tested defensive asset is either cash-like (BIL/SGOV), long duration (TLT/IEF/BOND10*) or gold.  The 1-3 year Treasury point has never been used, and it is the only one that carries a genuine yield pick-up over a bill (≈ +40-80 bp at 2026 curve levels) with moderate rate risk, and it is the classic 'off-state carry' instrument.  A: hold 0.98 in SHY while the vote is off; B: hold only the *residual* (0.98 - w) in SHY, re-sized only outside a 10 pp band (tbill_sweep S2_10's shape, different asset).

**Closest tested study.** `tbill_sweep_v1 S2_10 (BIL) and leverage_frontier_v1 (BOND10* = 10y par bond)` (docs/research/tbill_sweep_v1.md).  *Difference:* BIL* is DTB3 - 0.1356%/yr and BOND10* is a 10-year constant-maturity par bond; the 1-3 year point, which is the only intermediate-carry cell, is untested.  The scout inventory lists 'leveraged/other bond funds: NOT tested'.

**Literature.** riskoff_rotation_v1-v4 (repo) for the BOND10*/GOLD* precedent (docs/research/riskoff_rotation_v1.md).

**Signal inputs.** SHY daily close (new).

**Data.** in repo: `stress_robustness_v1_dtb3.csv (for the cash alternative)`. New downloads: Yahoo chart API SHY (inception 2002-07, ->), saved as data/historical/idea_bank_v1_shy.csv with URL + SHA-256. Main window: reduced start: 2002-08. OOS windows: none -- SHY starts 2002 -> reduced gate (main window, rolling starts, bootstraps).

**Plugin.** `targets_from_data` -- a cash-like sleeve; the same cash-rate-knob treatment as IB24, or the two-leg kernel for exactness.

**Arms (parameters frozen).**

- **A** -- hold 0.98 in SHY whenever the trend vote is off (production target otherwise) *(kernel option)*
- **B** -- hold the residual (0.98 - w) in SHY, re-sized only outside a 10 pp band *(kernel option)*

**Honest prior.** low: BIL lost after tax through realised sleeve gains, and SHY has more of them; the arm is here to measure the carry-vs-tax trade.

### IB26 Curve-conditioned off-state leg: intermediate Treasuries only when the curve pays

**Mechanism.** Both the credit_vote_v1 curve arm and the riskoff_rotation bond legs use the bond market in isolation.  This arm conditions the *defensive leg* on the curve: hold IEF instead of cash while the vote is off **and** the 10y-3m slope exceeds 1.0 pp (roll-down and carry are paid), fall back to cash otherwise.  A: the 1.0 pp threshold; B: the threshold plus a requirement that the 10y yield is not falling faster than -0.5 pp over 60 sessions; C: the mirror (IEF only when the curve is *flat or inverted*, the recession-timing trade).

**Closest tested study.** `riskoff_rotation_v1 R3/R4 (trend-gated BOND10*/GOLD* while off) and credit_vote_v1 K3 (inversion veto)` (docs/research/riskoff_rotation_v1.md).  *Difference:* R3/R4 gate the defensive leg on its own 252-session *trend*; K3 uses the curve as an equity veto.  Neither conditions the defensive asset on the curve slope.

**Literature.** Campbell, Sunderam & Viceira, Inflation bets or deflation hedges? (bond risk premia and the curve) (https://doi.org/10.3386/w21466).

**Signal inputs.** IEF close (repo); FRED T10Y3M (repo, 1982+); FRED DGS10 (repo).

**Data.** in repo: `riskoff_rotation_v4_etfs.csv (IEF 2002-07-30+)`; `credit_vote_v1_T10Y3M.csv`; `leverage_frontier_v1_dgs10.csv`. New downloads: none. Main window: reduced start: 2003-07 (IEF + 252 warm-up). OOS windows: GSPC_1957_1999, M1, M2 -- monthly GS10-TB3MS (1953+) exists for the US windows but BOND10* would have to be built from GS10 as riskoff_rotation did; pre-register that as the reduced OOS construction, or drop the OOS clause for this arm.

**Plugin.** `targets_from_data` -- same treatment as IB24/IB25.

**Arms (parameters frozen).**

- **A** -- while off: hold IEF at 0.98 if T10Y3M > 1.0 pp, else cash *(kernel option)*
- **B** -- as A plus a requirement that the 60-session DGS10 change > -0.50 pp *(kernel option)*
- **C** -- the mirror: hold IEF while off only if T10Y3M <= 1.0 pp *(kernel option)*

**Honest prior.** low.

## 5.7 On-state instrument choice (2 ideas)

### IB27 Beta-matched QQQ + QLD blend: same Nasdaq beta-2 exposure at a lower carry -- **rank 1**

**Mechanism.** K delivers a target Nasdaq beta of B = 2*f*vt (f is the R2 path factor: 1 on, 0.5 early) by holding w_QLD = f*vt of a 2x fund, i.e. it pays the fund's expense plus the internal financing on the *whole* notional.  Half of that beta can be bought more cheaply with the unlevered index fund: QQQ costs ~20 bp/yr and no financing, and one unit of QQQ plus one unit of QLD delivers three units of beta at a cost of (20 bp + 95 bp + DTB3 + spread).  The minimum-carry construction that delivers exactly beta B with C = min(0.98, B) of cash invested is: w_QLD = B - C, w_QQQ = C - w_QLD (with w_QQQ = 0 and w_QLD = C when C = B).  At the measured mean vol-target level (mean(vt) = 0.8397, f = 1) that is w_QLD = 0.70, w_QQQ = 0.28 instead of 0.84 QLD: about 45 bp/yr of carry saved at an identical instantaneous beta, with the same 0.98 capital deployed (idle cash earns nothing, so deploying it is free).  This is a pure cost arbitrage between two Nasdaq instruments, not a signal, and it is the only idea in the bank whose prior is arithmetic rather than statistical.

**Closest tested study.** `instrument_cost_v1 (TQQQ at 2/3 weight, a beta-matched substitution) and early_qqq_v1 (the early leg in unlevered QQQ*/SPY*/SSO*)` (docs/research/instrument_cost_v1.md).  *Difference:* TQQQ at 2/3 keeps the *leveraged notional* at 2x and merely swaps the fund; early_qqq_v1 tested an unlevered substitution only for the half-weight early leg and measured +0.02 pp/yr.  Neither builds the cash-constrained *blend* that holds the unlevered fund up to the 0.98 cap: at vt >= 0.49 the unlevered fund alone cannot reach beta B, so the arm must mix, and the mixture is what removes the financing on the (0.98 - w_QLD) share of the position.  The saving scales with the weight (zero at vt = 0.98, maximal near vt = 0.49), which is exactly the region early_qqq_v1 never operated in.

**Literature.** Leveraged-ETF decay/financing literature (Cheng & Madhavan 2009; Tang & Xu 2014) (https://doi.org/10.2139/ssrn.1343444); instrument_cost_v1's own finding that financing dominates expense (repo) (docs/research/instrument_cost_v1.md).

**Signal inputs.** the arm's own instrument close (QQQ in the main window); the production vt and the R2 path factor.

**Data.** in repo: `stress_robustness_v1_daily.csv (QQQ OHLC+dividend)`; `the harness's synthetic 2x QLD* (built from QQQ in every window)`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- both legs are derivable in every window: the index itself is the unlevered leg (1x total return minus a 20 bp expense) and the existing synthetic 2x is the levered leg, so the pre-tax OOS and 21-ETF stages are evaluable without new data.

**Plugin.** `hybrid` -- pre-tax / OOS / 21-ETF stages: run the blend as one *synthetic blended instrument* whose daily return is the blend's own return on the arm's own weight path -- a targets-from-data change, no engine edit (disclosed approximation: it prices the blend as one fund).  After-tax stages: two legs have two FIFO lot books and one cash account, which the engine does not model (see fast_engine_v1 'One instrument per account'), so an exact after-tax result needs a two-leg kernel option in a copy of the engine plus an off/on parity cell; otherwise the after-tax number must be reported as the single-instrument approximation and labelled as such..

**Arms (parameters frozen).**

- **A** -- blend in the on-state only (f=1); the early leg stays production half-weight QLD *(kernel option)*
- **B** -- blend everywhere, including the early leg (f=0.5), which makes the early leg unlevered QQQ *(kernel option)*
- **C** -- blend only when vt >= 0.60; production QLD otherwise *(kernel option)*

**Honest prior.** high: a deterministic cost saving with no signal content; the risk is the daily-reset tracking difference between the blend and pure QLD, and (arm B) early_qqq_v1's measured switch cost.

### IB28 Two-leg execution: route each resize to the leg with the cheaper FIFO lots -- **rank 6**

**Mechanism.** idea_survey_v2 I1 (sell the lowest-gain lots) was blocked because the broker offers no lot-level sells under FIFO.  A two-leg account creates the one legitimate version of the same economics: with QQQ and QLD both held (IB27), a required downsize can be taken from whichever leg's FIFO head carries the lower USD gain fraction, subject to a route cap of 10 pp on how far the other leg may drift from its share of the target.  Exits to zero and tax-funding sales are never routed (they must clear the position).  This is a genuine tax-reduction mechanism inside the broker's rules, and it is the tax-side twin of IB27.

**Closest tested study.** `gain_band_v3 A1u (the FIFO gain fraction decides *whether* a downsize fills) and idea_survey_v2 I1 (blocked lot selection)` (docs/research/gain_band_v3.md).  *Difference:* A1u uses the gain fraction of the single leg's FIFO head to *block* a sale; this uses it to *choose the leg*, which requires the two-leg account and a route cap and has never been run.

**Literature.** Berkin & Ye, Tax management, loss harvesting and HIFO accounting, FAJ 2003 (https://doi.org/10.2469/faj.v59.n4.2536); Krasner & Sosner, Loss harvesting or gain deferral? JWM 2024 (https://doi.org/10.3905/jwm.2023.1.213).

**Signal inputs.** the two legs' own price/lot history.

**Data.** in repo: `as IB27`. New downloads: none. Main window: full; after-tax stages only (it changes nothing pre-tax beyond IB27's carry). OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- pre-tax it is identical to IB27 arm A/B, so OOS reports the IB27 cells

**Plugin.** `kernel_option` -- requires the two-leg account from IB27 plus a routing rule with the FIFO books visible in the fill decision; parity cell = routing disabled reproduces IB27 bit-for-bit.

**Arms (parameters frozen).**

- **A** -- downsizes fill in the leg with the lower USD FIFO gain fraction, route cap 10 pp *(kernel option)*
- **B** -- as A with a route cap of 5 pp *(kernel option)*
- **C** -- as A, plus up-resizes fill in the leg furthest below its target share (the mirror preference) *(kernel option)*

**Honest prior.** medium: the mechanism is real and broker-legal; the size depends on how often the two legs' gain fractions differ materially.

## 5.8 Drawdown / recovery-state sizing (4 ideas)

### IB29 Drawdown-depth-scaled exposure (two-sided test)

**Mechanism.** The only state the strategy currently reacts to is vol and trend; its *own* drawdown is not a state variable.  Depth-scaled exposure multiplies the target by clip(1 - dd/D, floor, 1.0) with dd the drawdown from the 252-session high, so exposure is cut as the drawdown deepens (a risk-parity-of-losses idea), and the mirror arm *raises* it as the drawdown deepens (the recovery-value bet).  Both directions are pre-registered because dip_overlay_v1's tested trigger was a 5-day return (a fast dip), not a slow drawdown state, and the two have different trade sets.

**Closest tested study.** `dip_overlay_v1 P1/P2/P3 (5-day -5%, RSI(2), 5-day +5%) and crash_entry_v1` (docs/research/dip_overlay_v1.md).  *Difference:* P1-P3 are *fast* price triggers; the drawdown state is a slow, continuous state variable and no arm scales exposure by it.  crash_entry's entry path requires an exit >= 20% below the 252-high *and* falling vol, and never fired.

**Literature.** Grossman & Zhou, Optimal investment strategies for controlling drawdowns, Math Finance 1993 (https://doi.org/10.1111/j.1467-9965.1993.tb00044.x).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- multiplier on the target series; no engine change.

**Arms (parameters frozen).**

- **A** -- weight *= clip(1 - dd252/0.30, 0.50, 1.00)
- **B** -- weight *= clip(1 - ddATH/0.40, 0.50, 1.00), ddATH from the expanding all-time high
- **C** -- mirror: weight *= clip(1 + dd252/0.30, 1.00, 1.50)

**Honest prior.** low-medium: arm A is a de-risking bet (the DD gate is easy, the return gate is not); arm C is the value bet.

### IB30 Time-under-water (duration of the all-time-high drawdown) scaling

**Mechanism.** Depth and duration are different states: 2000-02 was deep and long, 2020 deep and short, 2022 moderate and medium.  Time-under-water (sessions since the last all-time high) has never been a state variable here, and in the trend-following literature the duration of the recovery is the practical proxy for 'the regime has changed'.  A: weight *= clip(1 - u/756, 0.50, 1.00); B: the same with u/378 (twice as aggressive); C: the mirror weight *= clip(1 + u/756, 1.00, 1.25).

**Closest tested study.** `rule_variants_v1 (exit buffers, min-hold, staging) and exposure_k_v1` (docs/research/rule_variants_v1.md).  *Difference:* no tested arm carries a duration-of-underwater state; the closest are fixed-session min-holds.

**Literature.** Underwater/duration-based risk control (drawdown duration literature; Grossman-Zhou 1993) (https://doi.org/10.1111/j.1467-9965.1993.tb00044.x).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- weight *= clip(1 - u/756, 0.50, 1.00), u = sessions since the last all-time high
- **B** -- weight *= clip(1 - u/378, 0.50, 1.00)
- **C** -- mirror: weight *= clip(1 + u/756, 1.00, 1.25)

**Honest prior.** low.

### IB31 Volatility-spike exit for the early leg (replace the SMA50 break)

**Mechanism.** The early path's entry is vol-filtered but its *exit* is purely a price level (close < SMA50), which is what produced the -3.10 pp and -1.85 pp whipsaws in December 2022 and January 2023 -- the two largest early-path losses.  Exiting on a vol spike instead (or as well) removes the position in the deteriorating-vol state rather than after the price has already crossed the average.  A: exit when vol20 > 1.5x its trailing 252-session median; B: 2.0x; C: exit on the union of the production SMA50 break and the 1.5x vol spike (an exit that can only fire earlier).

**Closest tested study.** `fast_reentry_v1/v2 (the early path's SMA50 exit and its vol-median *entry* filter)` (docs/research/fast_reentry_v1.md).  *Difference:* the vol-median test is part of the *entry* condition; the exit has always been the price level.  Nothing in the record keys an exit on a vol spike (intraday_stop_v1 keys on a fixed price distance; vix_filter on the VIX level).

**Literature.** fast_reentry_v1's own whipsaw episode table (repo); Moreira & Muir 2017 for the vol state (docs/research/fast_reentry_v1.md).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- the early-state machine's exit condition in numpy; no engine change.

**Arms (parameters frozen).**

- **A** -- early leg exits when vol20 > 1.5 * median252(vol20)
- **B** -- early leg exits when vol20 > 2.0 * median252(vol20)
- **C** -- early leg exits on (close < SMA50) OR (vol20 > 1.5 * median252(vol20))

**Honest prior.** medium-low: aimed at the measured early-path losses rather than at the rule's average behaviour.

### IB32 Sticky sizing input: a dead-band on vol20 instead of on the target weight -- **rank 10**

**Mechanism.** Every tested band (ewma_band_v1's 2.5/5/10 pp, A1u's gain-scaled band) acts on the target weight *after* the vol input has moved.  Untested is a dead-band on the input: keep using the last accepted vol20 until it has moved by more than X% (relative) from the value in force, so the target only changes when the vol *state* changes rather than when the estimate wiggles.  Because A1u already blocks the small gain-heavy trims, this arm's measurable effect is on the 0.5-3 pp drifts that still pass the band; it is the cheapest possible two-line change and it interacts directly with the 91%-of-gain trims diagnosis.

**Closest tested study.** `ewma_band_v1 (band widths 2.5/5/10 pp) and smooth_target_v1 (target averaging/partial adjustment)` (docs/research/ewma_band_v1.md).  *Difference:* both tested families band the *output*; a dead-band on the vol *input* has a different trade set (it also changes the level held when the input is stale).

**Literature.** Bongaerts, Kang & van Dijk, Conditional vol targeting / dead-band trading (FAJ 2020) (https://doi.org/10.1080/0015198X.2020.1790853).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- the vol series is filtered before vt_of; no engine change.

**Arms (parameters frozen).**

- **A** -- hold the last accepted vol20 until |vol20_new/vol20_accepted - 1| > 0.20
- **B** -- the same with a 0.35 relative dead-band
- **C** -- hold the last accepted *target weight* until the new target differs by more than 5 pp (the plain-band control)

**Honest prior.** medium-low: it targets the trim channel that carries 91% of the realised gain, but the band mechanism is already heavily mined.

## 5.9 Model-free statistical filters (3 ideas)

### IB33 Realised-skewness crash-risk scaling

**Mechanism.** The vol target reads the second moment only.  Realised skewness of the last 252 daily returns is the third: a negative skew means the return distribution has a fat left tail, the state in which a 2x daily-reset position is most exposed to reset decay.  Arm A multiplies the target by 0.75 while the 252-session skew is below -0.5; B uses -1.0; C uses a continuous multiplier clip(1 + skew, 0.60, 1.10).  No options data is needed, unlike every tested tail measure (VIX/VXN/VIX3M).

**Closest tested study.** `vix_filter_v1 (X1-X4, all option-implied) and vol_window_v1 V5 (downside semi-deviation)` (docs/research/vix_filter_v1.md).  *Difference:* V5 is a second-moment down-side measure; the tested tail filters are option-implied levels, and idea_survey_v2 section 7.3 rejected the option-implied SKEW index class on data grounds.  Realised skewness as a sizing input is untested and covers the full pre-2000 OOS windows.

**Literature.** Harvey & Siddique, Conditional skewness in asset pricing tests, JF 2000 (https://doi.org/10.1111/0022-1082.00265).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- weight *= 0.75 while skew252 < -0.5
- **B** -- weight *= 0.75 while skew252 < -1.0
- **C** -- weight *= clip(1 + skew252, 0.60, 1.10)

**Honest prior.** low-medium.

### IB34 Lag-1 return autocorrelation gate on the trend vote

**Mechanism.** The cheapest possible serial-dependence test: rho1 = the lag-1 autocorrelation of the trailing 252 daily returns.  A trend rule should not be exposed to a 2x instrument when returns are negatively autocorrelated (a bid-ask-bounce / mean-reverting tape).  Arms: A requires rho1 > 0 for the E2 vote; B relaxes to rho1 > -0.05 (a nuisance-threshold version); C requires rho1 > 0 only for the early path.  IB04's variance ratio is the longer-horizon cousin of this; both are pre-registered so the screen can price the cheap version against the expensive one.

**Closest tested study.** `deriv_vote_v1 (price-derivative votes); scout inventory: serial-dependence signals untested` (docs/research/deriv_vote_v1.md).  *Difference:* no arm in the record computes any return autocorrelation statistic.

**Literature.** Lo & MacKinlay 1988; Campbell, Lo & MacKinlay, The Econometrics of Financial Markets, ch. 2 (https://doi.org/10.1093/rfs/1.1.41).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- E2 on iff the 2-of-3 vote holds AND rho1(252) > 0
- **B** -- E2 on iff the 2-of-3 vote holds AND rho1(252) > -0.05
- **C** -- early-path entry additionally requires rho1(252) > 0

**Honest prior.** low-medium.

### IB35 Conditional vol targeting: adjust exposure only in the volatility extremes

**Mechanism.** Bongaerts-Kang-van Dijk's conditional vol targeting: a vol-targeting rule pays for continuous re-sizing in the middle of the vol distribution and gains most of its risk reduction in the tails, so an arm that adjusts exposure only when vol20 is outside its own 252-session quartiles (A) or deciles (B) should keep most of the drawdown control with fewer resizes.  idea_survey_v3 set this aside as 'bounded at < 0 pp by the dead-band arms' but never measured it; arm C is the plain fixed 3 pp band, a width between the tested 2.5 and 5 pp (the only untested width in that family), as the like-for-like control.

**Closest tested study.** `ewma_band_v1 B25/B50/B100 (2.5/5/10 pp output bands) and exposure_k_v1 (level)` (docs/research/ewma_band_v1.md).  *Difference:* the tested bands hold the weight while the *target* drifts; this holds it unless the *vol state* is extreme, which is a state-conditional rule, and the conditional-targeting form has never been run (only set aside).

**Literature.** Bongaerts, Kang & van Dijk, Conditional volatility targeting, FAJ 2020 (https://doi.org/10.1080/0015198X.2020.1790853).

**Signal inputs.** the arm's own instrument close.

**Data.** in repo: `stress_robustness_v1_daily.csv`. New downloads: none. Main window: full. OOS windows: GSPC_1957_1999, M1, M2, M3, M4, M5 -- closes only

**Plugin.** `targets_from_data` -- no engine change.

**Arms (parameters frozen).**

- **A** -- re-size only when vol20 is outside its trailing 252-session [P25, P75]; otherwise hold the weight
- **B** -- the same with [P10, P90]
- **C** -- the plain fixed 3 pp band (control; between the tested 2.5 and 5 pp)

**Honest prior.** low: the survey's own prior was '< 0 pp', so this is an axis-closing measurement.

## 5.10 Execution timing (2 ideas)

### IB36 Gap-aware fill filter: do not sell into the opening gap

**Mechanism.** Every tested fill-timing arm shifts *when* the fill happens (next close, t+2, MOC, overnight).  Untested is conditioning *whether* it happens on the fill price itself: a band-permitted downsize is skipped if the next open has already gapped more than 1% below the decision close (the sale would lock in the gap), and the ordinary rule is re-evaluated at the next close.  A/B differ in the gap threshold (1.0% / 0.5%); C applies the mirror to up-resizes (skip buying an opening gap above +1.0%).  This is the only idea in the bank that uses the *open* price as a decision input, and the mechanism can only help the execution cost, never the signal.

**Closest tested study.** `exec_delay_v1 (D1-D4) and moc_execution_v1 (X1-X3)` (docs/research/exec_delay_v1.md).  *Difference:* those arms move every fill to another session; none conditions the fill on the realised execution price, and the scout inventory lists 'VWAP/TWAP/limit ladders' as untested.

**Literature.** Almgren & Chriss, Optimal execution of portfolio transactions (implementation shortfall) (https://doi.org/10.21314/JOR.2001.041); Bogousslavsky, The cross-section of intraday and overnight returns, JFE 2021 (https://doi.org/10.1016/j.jfineco.2020.09.001).

**Signal inputs.** the arm's own instrument open and close (with a one-session decision lag).

**Data.** in repo: `stress_robustness_v1_daily.csv (QQQ open/close)`; `ewma_band_v1_index.csv (index OHLC for M1-M5, M2 included)`. New downloads: none. Main window: full. OOS windows: M1, M2, M3, M4, M5 -- ^GSPC 1957-1999 has close-only data (opens := previous close in the harness), so the gap filter is inert there -> reduced 5-window G7

**Plugin.** `kernel_option` -- sim_one's fill decision must see the fill session's open and the prior close; parity cell = threshold set to 0 reproduces K bit-for-bit.

**Arms (parameters frozen).**

- **A** -- skip a permitted downsize when the next open < decision close * (1 - 0.010); re-evaluate next close *(kernel option)*
- **B** -- the same with a 0.005 threshold *(kernel option)*
- **C** -- the same at 0.010 for up-resizes (skip buying a gap up) and no filter on downsizes *(kernel option)*

**Honest prior.** medium-low: execution-cost arms have lost, but this one cannot damage the signal (it only defers fills).

### IB37 VWAP-proxy fills: exit at the session's typical price

**Mechanism.** Fill price is a modelling choice: production fills at the next open, and the tested alternatives are the next close and the decision close.  The typical price (high+low+close)/3 of the fill session is the standard VWAP proxy for a *market order worked through the session* and has never been used; it is the honest way to model a large resize that would be spread over the day.  A: exits and downsizes fill at the typical price, entries and up-resizes stay at the open; B: all fills at the typical price; C: conservative -- sells at min(open, typical), buys at max(open, typical).

**Closest tested study.** `moc_execution_v1 (same-close fills) and exec_delay_v1 (D1-D4)` (docs/research/moc_execution_v1.md).  *Difference:* every tested variant uses an auction price (open or close) on some session; execution_model_audit_v1 records the *live* fill VWAP as a diagnostic but no the intra-session typical price has never been modelled, and overnight_hold_v1's finding (QQQ earns ~+9.8%/yr overnight, ~-2.2% intraday) makes the sign of this arm genuinely uncertain in both directions.

**Literature.** Berkowitz, Logue & Noser 1988 (VWAP as an execution benchmark) (https://doi.org/10.3905/jpm.1988.409144).

**Signal inputs.** the arm's own high, low, close (fill session).

**Data.** in repo: `stress_robustness_v1_daily.csv`; `ewma_band_v1_index.csv`. New downloads: none. Main window: full. OOS windows: M1, M2, M3, M4, M5 -- same close-only reduction for ^GSPC 1957-1999

**Plugin.** `kernel_option` -- sim_one's fill price; parity cell = 'open' reproduces K bit-for-bit.

**Arms (parameters frozen).**

- **A** -- exits and downsizes fill at (h+l+c)/3 of the fill session; entries/up-resizes at the open *(kernel option)*
- **B** -- all fills at (h+l+c)/3 *(kernel option)*
- **C** -- sells at min(open, (h+l+c)/3); buys at max(open, (h+l+c)/3) *(kernel option)*

**Honest prior.** low-medium: a modelling-choice arm whose outcome informs the cost model as much as the strategy.

## 5.11 Tax unit, regime and funding (3 ideas)

### IB38 Tax-unit and tax-regime layer: 20/D eligibility, rate-regime switch table, income splitting -- **rank 2**

**Mechanism.** Every arm in 80-odd studies is priced for one taxpayer under the frozen 2026 progressive tariff, and the same rule earns 11.69% after tax at $10k against 9.19% at $1M -- i.e. the *tariff*, not the rule, moves the answer by 1.5 pp.  Three things follow, none ever simulated: (a) a regime switch table that re-runs R2/A1u/H2/K under tau = 0, flat 20% and the progressive tariff on each arm's own path, which measures the rate at which K's execution layer stops paying for itself; (b) income splitting: two independent accounts of $50k (or three of $33.3k) each running K, household CAGR = the per-account CAGR, which multiplies the progressive ladder and the loss pool; (c) a routed split, where downsizes fill in whichever account's FIFO head is cheaper.  The 20/D exemption (GVK muk. 20/D, a 20-year exemption on foreign-source capital gains for individuals becoming Turkish residents from 1/1/2026) is a *precondition* answered by an adviser, not an arm: if any household member qualifies, the entire ~5 pp/yr drag moves taxpayer and the pre-tax optimum becomes the after-tax optimum.

**Closest tested study.** `cash_yield_v1 (the one account-setting study) and vt_level_tax_v1 / tax_asym_v3 (capital scaling)` (docs/research/cash_yield_v1.md).  *Difference:* capital was swept in the after-tax stages ($10k-$1M) but the *tax unit* (how many taxpayers, which regime) has never been varied, and idea_survey_v5 I1 -- whose arms these are -- was never run.  The scout inventory records the tax-unit/tax-regime layer as 'SURVEY ONLY, never simulated'.

**Literature.** Kleven, Kreiner & Svarer, Taxation of couples (tax-unit neutrality) (https://doi.org/10.1111/j.1468-0297.2009.02259.x); AQR, The Enduring Appeal of Gain Deferral, Parts 1-3 (https://www.aqr.com/Insights/Research/Journal-Article/The-Enduring-Appeal-of-Gain-Deferral); Krasner & Sosner, Loss harvesting or gain deferral? JWM 2024 (https://doi.org/10.3905/jwm.2023.1.213).

**Signal inputs.** none (accounting and tax-regime inputs only).

**Data.** in repo: `after_tax_tr_v1_usdtry_cbrt.csv`; `after_tax_tr_v1_yiufe.csv`; `the engine's TARIFF_2026 and the flat-20% account (fe_tax)`. New downloads: none. Main window: full (after-tax stages only; the pre-tax stages are regime-invariant). OOS windows: none -- the TRY/YI-UFE calendar starts 2000, so no after-tax OOS exists; the roller of E2 starts, E3 bootstraps and B1/B2 are the substitutes, as in every after-tax study.

**Plugin.** `hybrid` -- arm A is the existing engine with mode/capital varied (targets-from-data, no kernel edit); arm B is two/three independent engine runs summed (an accounting layer, no kernel edit); arm C needs a two-book kernel option with a route cap, and an off/on parity cell proving two unrouted identical accounts reproduce K's NAV exactly..

**Arms (parameters frozen).**

- **A** -- regime switch table: {R2, A1u, H2, K} x {tau=0, flat-20%, progressive-2026} x {$10k,$100k,$1M} x the six windows (report-only for the arms; it *is* the decision input)
- **B** -- two $50k accounts and three $33.3k accounts, each running K; household CAGR reported vs K at $100k and vs K at $50k/$33.3k
- **C** -- routed split: two accounts, downsizes routed to the cheaper FIFO head, route cap 10 pp; up-resizes to the more-below-target account *(kernel option)*

**Honest prior.** high for the measurement, unknown for the gain: +3..+5 pp if a household member qualifies for 20/D, ~+0.2..+0.5 pp from splitting alone, 0 if not.

### IB39 Tax-regime stress (TRY/PPI decoupling) and an FX-regime band switch -- **rank 7**

**Mechanism.** K's A1u+H2 premium is a function of indexation relief, which is a function of the gap between USD/TRY depreciation and YI-UFE inflation; that gap has never been varied, and idea_survey_v4 named the decoupled calendar as the missing regime check.  Arm A is that stress as a pre-registered arm: re-run R2/A1u/H2/K on synthetic calendars (TRY flat with 20%/yr PPI; TRY depreciating 30%/yr with PPI at 5%; and the realised 2016-2026 calendar as the anchor) and report whether K's edge inverts.  Arms B and C then *act* on the regime: multiply A1u's three band widths by 1.5 (0.5/3/8 -> 0.75/4.5/12 pp) while the trailing 252-session realised indexation relief on the account's own sales exceeds 0.5 (relief is large, so each deferred sale is worth more), and by 0.5 in the mirror arm.

**Closest tested study.** `state_defer_v1 (D3: key deferral on the estimated tax > 0.05% of NAV) and monthly_defer_v1` (docs/research/state_defer_v1.md).  *Difference:* state_defer keyed a *deferral trigger* on the lot book's own indexed gain and declared the realization-timing axis closed; this arm keeps K's calendar and gain-band triggers and modulates the *band width* on a macro state, plus a calendar stress that no after-tax study has run.  It is filed as an axis-closing measurement with a low prior, not as a new deferral device.

**Literature.** Constantinides 1983; AQR gain-deferral series (deferral value rises with the rate and the horizon) (https://www.aqr.com/Insights/Research/Journal-Article/The-Enduring-Appeal-of-Gain-Deferral); idea_survey_v4 section 6.3 (the named, never-run regime stress) (docs/research/idea_survey_v4.md).

**Signal inputs.** USD/TRY (CBRT, repo); YI-UFE (repo); the engine's own realised-relief fraction.

**Data.** in repo: `data/historical/after_tax_tr_v1_usdtry_cbrt.csv (2000-01-03 ->)`; `data/historical/after_tax_tr_v1_yiufe.csv (1999-01 ->)`. New downloads: none. Main window: after-tax stages only. OOS windows: none -- the pre-tax OOS stage has no TRY calendar (the R-USD rule), so G7/G8 cannot see this arm; pre-register the reduced gate on the after-tax windows + B1/B2 + the 21-ETF after-tax stage, and report the stress table separately.

**Plugin.** `kernel_option` -- band widths are the kernel's RULE constants (0.5/3/8 pp); making them state-dependent needs a per-session width multiplier in a copy of the engine, with an off/on parity cell (multiplier = 1.0 reproduces K bit-for-bit).  Arm A needs no kernel edit at all (the calendar is an existing input)..

**Arms (parameters frozen).**

- **A** -- stress table: K vs R2/A1u/H2 on synthetic calendars {TRY flat + PPI 20%/yr; TRY -30%/yr + PPI 5%; realised 2016-2026} at $100k
- **B** -- A1u band widths x 1.5 while the trailing 252-session realised indexation relief fraction > 0.5 *(kernel option)*
- **C** -- A1u band widths x 0.5 while the same state holds (mirror) *(kernel option)*

**Honest prior.** low-medium: arm A is a risk measurement that can invalidate K's execution layer; B/C are small and adjacent to a closed axis.

### IB40 Instalment funding source: satisfy the tax liability from the target reduction, not from an extra sale

**Mechanism.** The engine funds the March/July instalments by selling units, which realises gain on top of the ordinary resizes (about 1% of the realised base, on the report's own accounting).  Two untested routings remove that extra sale: (A) when a band-permitted downsize occurs in the same year as an unpaid liability, route its proceeds to the tax account instead of reinvesting to the target; (B) suppress the next up-resize while a liability is unpaid (fund the tax by simply holding less); (C) both.  It is a cash-flow rule, not a signal, and it only ever removes a trade.

**Closest tested study.** `tax_budget_v2 (Y1/Y2/Y3 all *create* trades), exit_timing_v2 X1-X3 and cash_yield_v1 (which measures the *yield* on money parked for the March/July instalments)` (docs/research/tax_budget_v2.md).  *Difference:* every tested tax arm creates, defers or re-times a realisation, and cash_yield_v1 prices the interest earned on the parked instalment money; none changes the *funding source* of a payment the account already owes.

**Literature.** After-tax rebalancing / tax-aware trading: Berkin & Ye, FAJ 2003; Israel & Moskowitz, The role of shorting, tax-aware portfolios (https://doi.org/10.2469/faj.v59.n4.2536).

**Signal inputs.** the engine's own tax-payment schedule and realised-gain state.

**Data.** in repo: `after_tax_tr_v1_usdtry_cbrt.csv`; `after_tax_tr_v1_yiufe.csv`; `the engine's instalment schedule (fe_tax)`. New downloads: none. Main window: after-tax stages only. OOS windows: none -- same reduced gate as IB39 (no after-tax OOS exists).

**Plugin.** `kernel_option` -- the payment/funding interaction lives in fe_tax plus sim_one's cash routing; needs an enable flag with a parity cell (flag off = K bit-for-bit)..

**Arms (parameters frozen).**

- **A** -- route a band-permitted downsize's proceeds to the unpaid liability (up to the amount due) instead of to the target *(kernel option)*
- **B** -- suppress the next up-resize while a liability is unpaid and the cash is short *(kernel option)*
- **C** -- both A and B *(kernel option)*

**Honest prior.** low: the effect is bounded by the ~1% of the base that instalment sales carry, but it can only remove a trade.

## 6. Suggested batching for the automated screen

- **Wave 1 (cheapest, no new data, no kernel):** IB01, IB02, IB04, IB05, IB07, IB08, IB11, IB12, IB13, IB14, IB16, IB18, IB19, IB22, IB24, IB29, IB30, IB31, IB32, IB33, IB34, IB35, and IB38 arm A -- 23 ideas whose targets-from-data arms run on the existing loaders with the K rule unchanged.
- **Wave 2 (one small download or one kernel option):** IB03, IB06, IB10, IB15, IB20, IB21, IB23, IB25, IB26, IB36, IB37, IB39, IB40, plus IB38 arms B/C and IB27/IB28.
- **Wave 3 (weakest data situation, run last):** IB17 (no OOS coverage, a hand-maintained calendar).
- The multiple-testing correction must count the union of all waves: 119 arms is the family size, not the number of survivors.

## 7. What is deliberately *not* here

The bank avoids every axis the record has already closed. The closed families and the studies that closed them:

| closed family | closed by |
|---|---|
| Trend horizon / aggregation (SMA 100-250, Donchian 126, 2-of-3, proportional, single-signal, hysteresis, confirmation days, exit buffers, min-hold, staged thirds) | trend_robustness_v2, signal_ensemble_v1, vote_hysteresis_v1, rule_variants_v1, e2_confirmation_v1 |
| Total-return votes and T-bill / 2% hurdles | tsm_tr_v1 |
| Vol estimator / window / output band (EWMA 0.94, vol10/40/60, max(vol20,vol60), semi-deviation, Parkinson/GK/YZ, 2.5/5/10 pp bands) | vol_window_v1, ewma_band_v1, range_vol_v1-v3 |
| Vol-target level and leverage grid | vt_level_tax_v1, leverage_frontier_v1, exposure_k_v1, tax_asym_v1-v3 |
| Vol-target shape min(1, c/sigma^p) | vol_shape_v1 |
| Valuation / ERP-conditioned exposure | valuation_v1 |
| Idle/risk-off asset (BIL, BOND10/IEF, GOLD, PSQ/QID, sector and country ETFs, bill sweep as a rule) | tbill_sweep_v1, r2_tbill_combo_v1, leverage_frontier_v1, riskoff_rotation_v1-v4, riskoff_after_tax_v1, gold_riskoff_oos_v1, shortside_v1 |
| Macro / credit / VIX filters at the *level* (BAA10Y vs its SMA, curve inversion, VIX level / spike / term structure, VXN sizing) | credit_vote_v1, vix_filter_v1 |
| Execution price and timing (MOC, next close, t+2 open, split fills, overnight hold, intraday stops) | moc_execution_v1, exec_delay_v1, overnight_hold_v1, intraday_stop_v1 |
| Seasonality and calendar (May-Oct half, September cash, turn-of-month, month-end deferral) | seasonal_overlay_v1, seasonal_placebo_v1, monthly_defer_v1 |
| Instrument choice among 2x/3x funds (TQQQ at 2/3, early leg in QQQ/SPY/SSO, QLD vs SSO rotation) | instrument_cost_v1, early_qqq_v1, relstrength_v1 |
| Realization timing (year-end loss harvest, December deferral, gain-scaled band, monthly deferral, inverse hedge, state-keyed deferral, exit-leg timing, smoothing) | tax_harvest_v1/v2, gain_band_v1-v3, monthly_defer_v1, tax_hedge_v1, state_defer_v1, exit_timing_v2, smooth_target_v1 |
| Annual tax budget / deliberate gain realization | tax_budget_v2 |
| Early-leg size and trigger (ladder, 2-of-3 quorum, crash path) | ladder_v1, early_quorum_v1, crash_entry_v1, fast_reentry_v2 |
| Dip buying / stops / crash entry / short side | dip_overlay_v1, intraday_stop_v1, crash_entry_v1, shortside_v1 |
| Lot-level selection (FIFO blocked by the broker) and two-queue wrappers | idea_survey_v2 I1 (never run, blocked); relstrength_v1 for the wrapper |
| ML / sequence models | timesfm_v1 |

Four ideas sit adjacent to a closed axis and are filed as *axis-closing measurements* with a low prior, not as expected wins: IB07 (vol estimator), IB35 (output band / conditional targeting), IB39 arms B/C (state-keyed realization timing) and IB17 (calendar). Their `closest_tested` entries say so explicitly, and a study that runs them must not treat a pass as a licence to relax the multiple-testing correction.

## 8. Caveats

1. **No result is claimed.** Every number in this document is a *parameter* (a threshold, a window, a band width), not an outcome. Nothing here was simulated; the priors are judgements and are labelled as such.
2. **Arms are capped at three per idea and every parameter is fixed in this document**, before any arm runs. Adding a fourth arm, moving a threshold or re-splitting a window after seeing a result is a data-mining event and must be reported as an amendment.
3. **The after-tax OOS gap is inherited.** The CBRT/YI-UFE calendar starts in 2000, so no after-tax arm can be confirmed out of sample; every tax idea (IB38-IB40) is therefore gated on the measured windows, B1/B2, the 21 rolling starts and the E3 bootstraps only.
4. **Gate asymmetry.** G5 (a pre-tax bootstrap) rejects tax-only arms by construction and G2/G7 accept de-risking arms trivially on drawdown, so a de-risking idea (IB29A, IB30A, IB33, IB35) must be judged on the return/after-tax gates and an exposure-matched variant reported where the mean weight moves (vol_shape_v1's two-tier convention).
5. **Legal preconditions are not arms.** IB38's 20/D eligibility and the exemption-band question, and IB28's routing, need a mali musavir opinion before any *implementation*; the simulation can measure the arithmetic in the meantime, and this document is not tax advice.
6. **Data facts are asserted from the stored files** (paths and coverage in section 3); each new download must be recorded in the running study's DATA_MANIFEST.json with its URL, date range and SHA-256, per WORKER_PROTOCOL.md.

## 9. Amendments

None. This artefact predates every arm it proposes; no parameter, gate, category or rank was changed after any computation, because none was performed.

