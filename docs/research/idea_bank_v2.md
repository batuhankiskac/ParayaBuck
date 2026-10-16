# Idea bank v2 (2026-10-16): 60 new pre-registered ideas for improving K after Turkish tax

> Evidence: tag `archive/research/idea-bank-v2-2026-10` (not on `main`).

> Artefact: an *idea bank*, not a study.  No backtest was run, no gate was evaluated, no arm was simulated, no data was downloaded.  `docs/research/idea_bank_v2.md` and `docs/research/idea_bank_v2.json` are rendered from `research/idea_bank_v2/ideas_{a,b,c,d}.py` by `research/idea_bank_v2/render.py`, so the two files cannot drift.

**Verdict (what this is).** 60 ideas, 180 arms, every parameter fixed now and never tuned later, in 12 categories and spread deliberately beyond the axes the record has closed and beyond the 40 ideas of `idea_bank_v1` (IB01-IB40, screened in `batch_bank_v1`: 0 of 89 arms survived the held-out stage). 38 of the 60 are outside "signal tweaks on QQQ" - macro-release regimes, cross-asset signals, off-state assets other than bills/bonds/gold, on-state instrument choice, two-instrument allocations and the costs/frictions of the real LIVE account. Every idea names the closest *tested* study (with its INDEX row) and the difference that makes it new; the 15 strongest are ranked 1-15. 42 ideas need no new data at all; 72 of the 180 arms need a new execution kernel (9 of them only in the after-tax stage).

**Counts.** ideas 60 | arms 180 | categories 12 | strongest marked 15 | two-leg ideas 6 | kernel-option ideas 18 | hybrid ideas 1 | OOS coverage 6/6 31, 5/6 11, 3/6 18

## The 15 strongest, in rank order

| rank | id | idea | category | why it is ranked here |
|---:|---|---|---|---|
| 1 | IC01 | 52-week-high proximity vote | Trend-signal algebra (new statistics) | one new statistic on the core vote, no new data, the strongest single documented momentum predictor. |
| 2 | IC25 | Pre-FOMC announcement drift overlay | Calendar and structural-date effects | the largest documented calendar effect in equities, 8 sessions/yr, and the mirror arm distinguishes the drift from a pure exposure effect. |
| 3 | IC17 | High-yield credit-spread (OAS) veto | Regime models from macro releases and financial conditions | the best-documented early-warning series with a two-condition state; credit_vote_v1's failure was on an investment-grade level, not this state. |
| 4 | IC02 | Skip-month (12-1) momentum vote | Trend-signal algebra (new statistics) | free, literature-standard, and it only changes which sessions the vote spans. |
| 5 | IC42 | Two-leg routing on the TRY-indexed (taxable) gain, not the USD gain | Two-instrument allocations (two-leg kernel) | it scores the lot by the quantity the tax is actually levied on, which no shipped rule or banked idea does. |
| 6 | IC08 | HAR volatility blend instead of vol20 | Volatility and risk estimators | the estimator axis is formally closed, but the HAR blend is the one canonical family inside it that was never run, and it acts on the trim channel. |
| 7 | IC12 | Financial-conditions (NFCI) veto | Regime models from macro releases and financial conditions | the broadest available financial-conditions state with a documented publication lag; the veto family has lost before, but always as a single market spread. |
| 8 | IC39 | Margin-financed QQQ instead of QLD (broker leverage) | On-state instrument choice | a deterministic cost arbitrage between two ways of buying the same exposure (the mechanism IB27 was ranked #1 for), with a published input and an explicit cost stress. |
| 9 | IC41 | Dual-core: QLD + SSO, one FIFO book per index | Two-instrument allocations (two-leg kernel) | the diversification is real, the routing is IB28's mechanism (ranked #6 there), and the cost is a second book to maintain. |
| 10 | IC59 | Asymmetric band: immediate up-resizes, K's gate on downsizes | Tax structure and realisation policy | a one-knob change in the tax-free direction, cheap to test, and its failure mode (more small buys) is bounded by the spread. |
| 11 | IC52 | Minimum-notional order filter | Execution, frictions and the real LIVE (Alpaca) account | a direct attack on the trim channel with an account-level justification, and the drift it leaves is bounded by the band. |
| 12 | IC55 | FX conversion cost and the tax-payment schedule | Execution, frictions and the real LIVE (Alpaca) account | it is small in pp terms but it is real, systematic and currently invisible in every after-tax number the record publishes. |
| 13 | IC09 | Cross-sectional correlation sizing | Volatility and risk estimators | an orthogonal risk input with a clear mechanism; the 3-window OOS coverage is the structural weakness. |
| 14 | IC36 | Off-state risk-parity blend (GLD / IEF / SHY) with a monthly rescale | Off-state holdings beyond bills / bonds / gold | state-free, low turnover, diversified; the defensive premium itself was tested only through selection rules. |
| 15 | IC49 | Drawdown-budget governor (feedback target) | Drawdown / recovery-state sizing | it targets the quantity the acceptance test is stated in, with a dead zone that keeps it inactive in normal states; the kernel cost is the price. |

## 1. How the bank is meant to be consumed

- **Control.** C0 = production K = R2 (E2 2-of-3 vote + half-weight early re-entry) + A1u gain-scaled downsize band (0.5 / 3 / 8 pp at a USD FIFO gain fraction <=1% / <=5% / >5%) + H2 (a downsize filling in the last 10 NYSE December sessions defers to January).  Reference cells to reproduce before any arm runs: pre-tax 15.7649% base / 15.1785% calibrated, max DD 46.6698% / 46.7948%, after tax at $100k 10.7533 / 15.2354 / 23.9720%.
- **Stage 1 (pre-tax).** ewma_band_v1's G1-G9 against K: G1 return (CAGR >= C0, or Calmar >= 1.1x with CAGR >= C0 - 0.5 pp), G2 max DD <= C0 + 1 pp, G3 2017+ >= C0 - 1 pp, G4 CAGR above C0 in >= 2 of 3 sub-periods, G5 block-20 bootstrap (seed 20261011, 5,000 paths) median >= 0, G6 G1 at m = 1, G7 the six OOS windows, G8 the pooled OOS bootstrap, G9 turnover <= 2x C0; G1/G2/G3/G5/G6 under both financing models.
- **Stage 2 (after tax).** beats K at $100k in all three windows (2000-03-09 -> 2026-09-29; 2010-01-01 ->; 2017-01-03 ->) AND clean bootstrap B1-recent and B2-flat median delta >= 0 with share > 0 >= 55% (seed 20261011, block 20).
- **Held-out confirmation.** the pre-2000 OOS windows and the fresh-seed bootstraps are the confirmation set; the multiple-testing correction counts *all* arms run across the bank, not only the survivors of stage 1 (the batch_screen ledger already does this: N is the number of registered arms, not the number of survivors).
- **Reduced gates.** where a window or stage is not evaluable (documented per idea in `data.oos`), the study must pre-register the reduced gate before running, exactly as credit_vote_v1 did for its non-US windows; the batch_screen framework's own rule (>= 3 applicable OOS windows and >= 15 applicable instruments) applies, and an arm below either bound is `holdout_insufficient` and needs its own confirmation study.
- **Ordering.** the two-stage screen is cheap on stage 1 and expensive on stage 2, so the arms are written so that a stage-1 failure is informative: every arm states an *economic* mechanism, so a negative stage-1 result is evidence about the mechanism and not only about the parameter.

### 1.1 The plugin contract (targets from data / two-leg / a new kernel)

- **targets_from_data.** the arm is a new numpy function of the instrument's own OHLC(V) plus any external series aligned by date, producing the per-session target-weight array the engine already consumes; the K fill rule (band / USD gain fraction / December deferral) is unchanged.  This is fe_signals.arm_signals' existing extension point and needs no engine edit.
- **two_leg.** the arm holds QLD in one book and a second ETF in another, each with its own FIFO queue and one cash account (the kernel being built in parallel).  The JSON names the second instrument as `second_symbol=...` inside `plugin.detail`.  Pre-tax/OOS/ETF stages may run as a blended synthetic instrument (the convention IB27 used), the after-tax stage needs the kernel.
- **kernel_option.** the arm changes the fill/decision logic itself (a fill-price or fill-filter rule, a state-dependent leg or a second leg, a tax-funding route, the leg's total-return recurrence, the decision calendar).  It requires a copy of the engine inside research/<study>/ with the option added and a parity cell proving the option switched off reproduces the engine bit-for-bit (WORKER_PROTOCOL.md).
- **hybrid.** the arm is expressible by registering a new *combination* of the engine's RULE options through fe_sim.RULE (the engine's documented extension point, no engine file edited); if the RULE table cannot express it, it falls back to a kernel option.  Check fe_sim.RULE before writing a kernel.

Arms tagged `needs_kernel_option` in the JSON (the `kernel_scope` key says whether the cost is paid in every stage or only in the after-tax stage):

| idea | arms | kernel scope | why a kernel is needed |
|---|---|---|---|
| IC31 | A, B, C | all_stages | a priced off-state asset is a second leg (like IB25's SHY sleeve), which the single-instrument account cannot hold; pre-tax/OOS/ETF stages can run as a blended synthetic instrument, the after-tax stage needs the kernel. NeedsKernel: state-dependent leg |
| IC32 | A, B, C | all_stages | priced off-state asset = a second leg with its own FIFO book; NeedsKernel: state-dependent leg |
| IC33 | A, B, C | all_stages | priced off-state asset = a second leg; NeedsKernel: state-dependent leg |
| IC34 | A, B, C | all_stages | a priced off-state asset needs a state-dependent leg/second book; NeedsKernel: state-dependent leg |
| IC35 | A, B, C | all_stages | priced off-state asset = a state-dependent leg; NeedsKernel: state-dependent leg |
| IC36 | A, B, C | all_stages | a priced off-state blend is a second leg (three instruments, monthly rescale); NeedsKernel: state-dependent multi-leg holding |
| IC37 | A, B, C | all_stages | priced off-state asset = a state-dependent leg; NeedsKernel: state-dependent leg |
| IC38 | A, B, C | all_stages | on-state 2x and off-state 1x is a per-session leg switch; NeedsKernel: state-dependent leg (if the two-leg kernel's second leg can be sized to zero, it is expressible as two_leg with second_symbol QQQ) |
| IC39 | A, B, C | all_stages | the account must carry a loan balance and accrue daily interest on it; fe_sim has no margin modelling, so this is a kernel option (all stages). NeedsKernel: a cash loan inside the account |
| IC40 | A, B, C | all_stages | the panel offers only the window's own underlying at 2x/1x; an SPY-based 2x leg is a new leg construction (data plumbing + kernel). NeedsKernel: a second leg family |
| IC41 | A, B, C | all_stages | second_symbol=SSO; two books with separate FIFO queues and one cash account; pre-tax/OOS stages can run as a blended synthetic instrument (IB27's convention). NeedsKernel: two-leg account |
| IC42 | A, B, C | after_tax_stage_only | second_symbol=QQQ; the routing score reads both books' FIFO heads plus the TRY/Yi-UFE calendar. NeedsKernel: two-leg account + tax-aware fill decision |
| IC43 | A, B, C | after_tax_stage_only | second_symbol=QQQ; NeedsKernel: two-leg account + tax-aware fill decision |
| IC44 | A, B, C | all_stages | second_symbol=QQQ; NeedsKernel: two-leg account + a per-leg band rule |
| IC45 | A, B, C | all_stages | second_symbol=GLD; NeedsKernel: two-leg account (the sleeve is a priced second book) |
| IC46 | A, B, C | all_stages | second_symbol=IEF; NeedsKernel: two-leg account |
| IC49 | A, B, C | all_stages | the plugin API exposes market data only; the realised NAV drawdown lives in fe_sim's state, so the governor must be a kernel option (all stages). NeedsKernel: NAV-state feedback in the sizing step |
| IC52 | A, B, C | all_stages | the filter changes which permitted fills happen, inside the fill decision. NeedsKernel: a fill-notional filter (all stages); the 21-ETF and after-tax stages at $100k are where it bites |
| IC53 | A, B, C | all_stages | the fill schedule (how much fills on each of the next N opens) is kernel. NeedsKernel: multi-session fill schedule (all stages) |
| IC54 | A, B, C | all_stages | the fill price is chosen by the kernel. NeedsKernel: an intraday fill reference (all stages) |
| IC55 | A, B, C | after_tax_stage_only | it changes fe_tax's payment/conversion step (and arm B its timing). NeedsKernel: tax-payment accounting (after-tax stage only) |
| IC56 | A, B, C | all_stages | it changes the leg's total-return recurrence (netting the dividend before the price series is built). NeedsKernel: leg construction (all stages) |
| IC57 | A, B, C | all_stages | it changes the decision/fill calendar. NeedsKernel: session-type handling (all stages) |
| IC58 | A, B, C | all_stages | it changes the cash routing inside the engine. NeedsKernel: settlement-aware funding (all stages) |

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
| SPY, QLD (real), VOO | `data/historical/stress_robustness_v1_daily.csv` | SPY 1999-01-04 ->; QLD 2006-06-21 ->; VOO 2010-09-09 -> | benchmarks, real-fund checks, the S&P second leg |
| synthetic daily-reset 2x QLD* | `built by fe_signals.synthetic_2x from QQQ` | 1999-03-10 -> 2026-09-29 | the traded leg in every stage |
| DTB3 (financing / bill yield) | `data/historical/stress_robustness_v1_dtb3.csv` | 1954 -> 2026-09 | financing, cash yield, bill-rate states |
| TB3MS (monthly bill rate) | `data/historical/ewma_band_v1_tb3ms.csv` | 1934-01 -> | pre-1954 OOS financing |
| ^GSPC | `data/historical/ewma_band_v1_gspc.csv (1957-1999 close only) and ewma_band_v1_index.csv (1927-1956 OHLC)` | 1927-12-30 -> 1999-12-31 | GSPC_1957_1999 and M2 OOS windows (the loader reads the close column only) |
| ^IXIC / ^N225 / ^GDAXI / ^FTSE | `data/historical/ewma_band_v1_index.csv` | ^IXIC 1971-02-05->1999-12-31; ^N225 1965->2026-09-29; ^GDAXI 1987-12-30->; ^FTSE 1984-01-03-> (OHLC) | M1-M5 OOS windows and their local-index signal/leg |
| 21 quality-passing ETFs (XLB XLE XLF XLI XLK XLP XLU XLV XLY EWJ EWG EWU EWA EWH EWS EWL EFA EEM SPY DIA IWM) | `data/historical/range_vol_v2_*.csv / range_vol_v1_*.csv (OHLC+adjclose, no volume); volume+dividend in riskoff_rotation_v4_etfs.csv` | mostly 1999-01-04 ->; EFA 2001-08-27; EEM 2003-04-14; IWM 2000-05-26 | breadth/robustness stages |
| IEF / TLT / GLD / GC=F / TQQQ | `riskoff_rotation_v4_etfs.csv, riskoff_rotation_v2_*.csv, leverage_frontier_v1_daily.csv` | IEF/TLT 2002-07-30 ->; GLD 2004-11-18 ->; GC=F 2000-08-30 ->; TQQQ 2010-02-11 -> | bond/gold sleeves, the risk-parity blend, the instrument arms |
| ^VIX / ^VIX3M / ^VXN | `data/historical/vix_filter_v1_vix.csv, _vix3m.csv, _vxn.csv, _fred.csv` | ^VIX 1990-01-02 ->; ^VXN 2001-01-23 ->; ^VIX3M 2006-07-17 -> | equity-vol regimes (tested) |
| BAA10Y / T10Y3M / DGS10 / BAA / AAA / GS10 / TB3MS | `data/historical/credit_vote_v1_*.csv, leverage_frontier_v1_dgs10.csv` | BAA10Y 1986-01-02 ->; T10Y3M 1982-01-04 ->; DGS10 1962-01-02 ->; BAA/AAA monthly 1919-01 ->; GS10 monthly 1953-04 -> | credit/curve/rates states (BAA10Y, curve: tested) |
| USD/TRY (CBRT) and YI-UFE | `data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv` | USD/TRY 2000-01-03 ->; YI-UFE 1999-01 -> | the after-tax engine, the TRY-indexed lot scoring, lot ageing |
| CAPE / earnings yield / 10y real return (Shiller) | `data/historical/valuation_v1_shiller_monthly.csv` | 1871-01 -> 2024-09 (monthly) | already tested (valuation_v1); listed for completeness |
| ^NDX daily | `data/historical/valuation_v1_ndx_daily.csv, vol_shape_v1_ndx.csv` | 1985-10-01 -> 2000-03-22 | pre-2000 calibration windows |
| ^RUT | `data/historical/breadth_vote_v1_rut.csv` | 1987-09-10 -> 2026-10-14 | breadth (already tested) |
| BIL / SGOV / TLT daily (2016+) | `data/historical/tbill_sweep_v1_etf_daily_split_sip.csv` | 2016-01-04 -> 2026-07-31 | bill-sleeve reference |
| batch_screen extra-data registry (31 series, one CSV each) | `research/batch_screen/EXTRA_DATA.json + data/historical/batch_screen_v1_*.csv` | ^VIX 1990-01-02; ^VIX3M 2006-07-17; ^VXN 2001-01-23; DGS10 1962-01-02; T10YIE 2003-01-02; DTWEXBGS 2006-01-02; BAA10Y 1986-01-02; CPIAUCSL 1947-01; SMH 2000-06-05; SOXX 2001-07-13; HG=F 2000-08-30; SHY/IEF/TLT 2002-07-30; GLD 2004-11-18; the nine sector SPDRs 1998-12-22; ^STOXX50E 2007-03-30; ^GDAXI 1987-12-30; ^N225 1970-01-05; ^FTSE 1984-01-03; ^IXIC 1971-02-05; ^GSPC 1970-01-02; ^NDX 1985-10-01; local_index (a per-window map) | the registered `data.extra(key)` series every extra-data arm in this bank draws on |

### 3.1 New downloads required by the bank (17 series, all public)

| series | needed by | source |
|---|---|---|
| Alpaca SIP 1-minute/30-minute bars for the fill price (the LIVE source of truth) | IC54 | Alpaca data API (the same SIP feed the LIVE path consumes) |
| BAA and AAA (monthly Moody's yields, 1919-01 ->) | IC18 | FRED CSV (fredgraph.csv?id=BAA, fredgraph.csv?id=AAA) |
| BAMLH0A0HYM2 (US high-yield OAS, daily, 1996-12-31 ->) | IC17 | FRED CSV (fredgraph.csv?id=BAMLH0A0HYM2) |
| CFNAI (monthly, 1967-01 ->) | IC13 | FRED CSV (fredgraph.csv?id=CFNAI) |
| DCOILWTICO (daily WTI spot, 1986-01-02 ->) | IC19 | FRED CSV (fredgraph.csv?id=DCOILWTICO) |
| DFF (daily effective federal funds rate, 1954-07-01 ->) | IC16 | FRED CSV (fredgraph.csv?id=DFF) |
| EEM (iShares MSCI Emerging Markets ETF, 2003-04-14 ->) | IC29 | Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/EEM) |
| GDX (VanEck Gold Miners ETF, 2006-05-22 ->) | IC35 | Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/GDX) |
| HYG (iShares iBoxx $ High Yield Corporate Bond ETF, 2007-04-11 ->) | IC27, IC32 | Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/HYG) |
| ICSA (weekly initial claims, 1967-01-07 ->) | IC14 | FRED CSV (fredgraph.csv?id=ICSA) |
| LQD (iShares iBoxx $ Investment Grade Corporate Bond ETF, 2002-07-30 ->) | IC31 | Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/LQD) |
| NFCI (weekly financial conditions index, 1971-01-08 ->) | IC12 | FRED CSV (fredgraph.csv?id=NFCI) |
| NYSE early-close (13:00) session dates 2000-2026 | IC57 | nyse.com public calendar (hand-registered, not market data) |
| QQQ and QLD 30-minute bars (2000->) for arms A and B | IC54 | Yahoo chart API (interval=30m is only available for the last 730 days; a vendor or the Alpaca bars API is needed for the full window) |
| SSO (ProShares Ultra S&P500, 2006-06-21 ->) for the real-fund cross-check | IC40, IC41 | Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/SSO) |
| TIP (iShares TIPS Bond ETF, 2003-12-05 ->) | IC33 | Yahoo chart API (query1.finance.yahoo.com/v8/finance/chart/TIP) |
| scheduled FOMC meeting/announcement dates, 1994-2026 | IC25 | federalreserve.gov public calendar (hand-registered, not market data) |

### 3.2 Structural coverage facts that decide several ideas

- The pre-2000 OOS stage builds the 2x leg from the index itself and runs the R-USD rule, so an arm is evaluable out of sample only if its *signal* exists in that index's own history; extra series (sector SPDRs 1998, ^VIX 1990, IEF/GLD/SHY 2002, HYG 2007, DTWEXBGS 2006, EEM 2003, LQD 2002, TIP 2003, GDX 2006, T10YIE 2003) are absent on GSPC_1957_1999, M1 and M2 and only live on the modern spans of M3/M4/M5 (which run to 2026) - hence the many 3-of-6 arms and the pre-registered reduced 3-window G7 (the `credit_vote_v1` precedent).
- ^VIX (1990+) and the long FRED series (DGS10 1962, BAA10Y 1986, CPIAUCSL 1947, DFF 1954, BAMLH0A0HYM2 1996, NFCI 1971, ICSA 1967, CFNAI 1967, DCOILWTICO 1986, BAA/AAA 1919) keep 5 or 6 OOS windows; BAA-AAA is the only credit state old enough for the 1935-56 window.
- The harness loads the ^GSPC 1957-1999 window close-only (`fe_data.load_gspc` sets opens := the previous close), so every arm needing intraday structure (overnight/intraday decomposition, true range, intraday fills) loses that window - the raw file does carry OHLCV 1950-1999, only the loader is close-only.
- The after-tax stage starts in 2000 because the CBRT/YI-UFE calendar does, so no after-tax idea can ever be evaluated before 2000; and the tax is assessed per calendar year with FIFO and no loss carry-forward, which is what makes the two-leg routing and loss-netting ideas (IC42-IC44) interesting rather than cosmetic.
- The panel offers the window's own underlying at 2x and 1x only (`api.LEGS`) and carries no volume, so an arm needing a second leg, a state-dependent leg or a second instrument family is a kernel/data-plumbing item: that is why the off-state and on-state ideas (IC31-IC40), the drawdown-budget governor (IC49) and the execution arms (IC52-IC58) are tagged as they are, and why the two-leg ideas (IC41-IC46) need the second-book kernel. No arm in this bank needs volume - the panel has none and the record's volume-free vote family is accepted here.

## 4. The 60 ideas at a glance

| id | cat | rank | idea | plugin | data |
|---|---|---:|---|---|---|
| IC01 | trend-signal | 1 | 52-week-high proximity vote | targets_from_data | no new data |
| IC02 | trend-signal | 4 | Skip-month (12-1) momentum vote | targets_from_data | no new data |
| IC03 | trend-signal | - | CUSUM (change-point) filter vote | targets_from_data | no new data |
| IC04 | trend-signal | - | Kalman local-linear-trend slope vote | targets_from_data | no new data |
| IC05 | trend-signal | - | Return-entropy regime gate | targets_from_data | no new data |
| IC06 | trend-signal | - | Overnight-drift trend vote | targets_from_data | no new data |
| IC07 | trend-signal | - | Swing-structure (higher-high / higher-low) vote | targets_from_data | no new data |
| IC08 | vol-estimator | 6 | HAR volatility blend instead of vol20 | targets_from_data | no new data |
| IC09 | vol-estimator | 13 | Cross-sectional correlation sizing | targets_from_data | no new data |
| IC10 | vol-estimator | - | Day/night volatility decomposition sizing | targets_from_data | no new data |
| IC11 | vol-estimator | - | Tail-percentile (CVaR) risk sizing | targets_from_data | no new data |
| IC12 | macro-regime | 7 | Financial-conditions (NFCI) veto | targets_from_data | 1 new series |
| IC13 | macro-regime | - | Real-activity regime (CFNAI) vote | targets_from_data | 1 new series |
| IC14 | macro-regime | - | Initial-claims shock veto | targets_from_data | 1 new series |
| IC15 | macro-regime | - | CPI-acceleration veto | targets_from_data | no new data |
| IC16 | macro-regime | - | Fed-funds tightening-cycle veto | targets_from_data | 1 new series |
| IC17 | macro-regime | 3 | High-yield credit-spread (OAS) veto | targets_from_data | 1 new series |
| IC18 | macro-regime | - | Credit-quality-spread (Baa-Aaa) veto | targets_from_data | 1 new series |
| IC19 | macro-regime | - | Oil-shock veto | targets_from_data | 1 new series |
| IC20 | macro-regime | - | Bond-volatility regime veto | targets_from_data | no new data |
| IC21 | macro-regime | - | Breakeven-inflation momentum veto | targets_from_data | no new data |
| IC22 | macro-regime | - | Dollar (broad trade-weighted) regime veto | targets_from_data | no new data |
| IC23 | calendar | - | Day-of-week (Monday) exposure tilt | targets_from_data | no new data |
| IC24 | calendar | - | Turn-of-quarter exposure tilt | targets_from_data | no new data |
| IC25 | calendar | 2 | Pre-FOMC announcement drift overlay | targets_from_data | 1 new series |
| IC26 | calendar | - | Seasonal daylight (SAD) exposure tilt | targets_from_data | no new data |
| IC27 | cross-asset | - | Credit-equity relative strength (HYG vs QQQ) | targets_from_data | 1 new series |
| IC28 | cross-asset | - | Cyclical-vs-defensive sector ratio | targets_from_data | no new data |
| IC29 | cross-asset | - | Emerging-market relative strength (EEM vs SPY) | targets_from_data | 1 new series |
| IC30 | cross-asset | - | Stock-bond correlation regime | targets_from_data | no new data |
| IC31 | off-state | - | Off-state in investment-grade credit (LQD) | kernel_option | 1 new series |
| IC32 | off-state | - | Off-state in high-yield credit (HYG) | kernel_option | 1 new series |
| IC33 | off-state | - | Off-state in TIPS (TIP) | kernel_option | 1 new series |
| IC34 | off-state | - | Off-state in defensive sectors (XLU / XLP) | kernel_option | no new data |
| IC35 | off-state | - | Off-state in gold miners (GDX) | kernel_option | 1 new series |
| IC36 | off-state | 14 | Off-state risk-parity blend (GLD / IEF / SHY) with a monthly rescale | kernel_option | no new data |
| IC37 | off-state | - | Real-yield-gated Treasury off-state (IEF) | kernel_option | no new data |
| IC38 | off-state | - | Off-state in 1x QQQ (never fully out) | kernel_option | no new data |
| IC39 | on-state-instrument | 8 | Margin-financed QQQ instead of QLD (broker leverage) | kernel_option | no new data |
| IC40 | on-state-instrument | - | S&P 500 2x on-state instrument (SSO / synthetic 2x SPY) | kernel_option | 1 new series |
| IC41 | two-leg | 9 | Dual-core: QLD + SSO, one FIFO book per index | two_leg | 1 new series |
| IC42 | two-leg | 5 | Two-leg routing on the TRY-indexed (taxable) gain, not the USD gain | two_leg | no new data |
| IC43 | two-leg | - | Two-leg loss-netting route | two_leg | no new data |
| IC44 | two-leg | - | Two-leg band widths keyed to the indexed (taxable) gain fraction | two_leg | no new data |
| IC45 | two-leg | - | Permanent small gold sleeve funded by a lower equity target (two-leg) | two_leg | no new data |
| IC46 | two-leg | - | Permanent duration sleeve (QLD + IEF, risk-parity-lite) as a two-leg arm | two_leg | no new data |
| IC47 | drawdown-recovery | - | Recovery-ramp sizing of the early position | targets_from_data | no new data |
| IC48 | drawdown-recovery | - | Drawdown-velocity sizing | targets_from_data | no new data |
| IC49 | drawdown-recovery | 15 | Drawdown-budget governor (feedback target) | kernel_option | no new data |
| IC50 | statistical-filter | - | Hit-rate (sign-persistence) sizing | targets_from_data | no new data |
| IC51 | statistical-filter | - | Cross-sectional dispersion regime | targets_from_data | no new data |
| IC52 | execution | 11 | Minimum-notional order filter | kernel_option | no new data |
| IC53 | execution | - | Order slicing over N sessions | kernel_option | no new data |
| IC54 | execution | - | Delayed intraday fill (auction vs continuous) | kernel_option | 2 new series |
| IC55 | execution | 12 | FX conversion cost and the tax-payment schedule | kernel_option | no new data |
| IC56 | execution | - | Dividend-withholding cost on the traded leg | kernel_option | no new data |
| IC57 | execution | - | Early-close session handling | kernel_option | 1 new series |
| IC58 | execution | - | T+1 settlement funding constraint (cash account) | kernel_option | no new data |
| IC59 | tax-structure | 10 | Asymmetric band: immediate up-resizes, K's gate on downsizes | hybrid | no new data |
| IC60 | tax-structure | - | December up-resize acceleration (lot ageing) | targets_from_data | no new data |

## 5. Non-repetition proof: the closest INDEX study named by every idea

| id | closest tested study (INDEX row) |
|---|---|
| IC01 | deriv_vote_v1 (golden cross, OLS log-price slope, rising midpoint, vol-scaled TSM hurdle) (`docs/research/deriv_vote_v1.md`) |
| IC02 | signal_ensemble_v1 / e2_confirmation_v1 (the 252-session TSM vote inside E2) (`docs/research/signal_ensemble_v1.md`) |
| IC03 | deriv_vote_v1 (rising midpoint, golden cross) and vote_hysteresis_v1 (asymmetric entry/exit thresholds) (`docs/research/deriv_vote_v1.md`) |
| IC04 | deriv_vote_v1 (OLS slope of log close, golden cross) and timesfm_v1 (neural forecast) (`docs/research/timesfm_v1.md`) |
| IC05 | deriv_vote_v1 (regime-conditioned votes) and breadth_vote_v1 (`docs/research/deriv_vote_v1.md`) |
| IC06 | moc_execution_v1 (measured the overnight/intraday split) and overnight_hold_v1 (held overnight only) (`docs/research/moc_execution_v1.md`) |
| IC07 | signal_ensemble_v1 (Donchian-126 midpoint) and trend_robustness_v2 (`docs/research/signal_ensemble_v1.md`) |
| IC08 | ewma_band_v1 W1 (RiskMetrics lambda 0.94) and vol_window_v1 (vol10/40/60, max(vol20,vol60)) (`docs/research/vol_window_v1.md`) |
| IC09 | vol_window_v1 and range_vol_v1-v3 (own-series volatility estimators); breadth_vote_v1 (a panel vote) (`docs/research/range_vol_v3.md`) |
| IC10 | vol_window_v1 and ewma_band_v1 (alternative estimators of the close-to-close series) (`docs/research/vol_window_v1.md`) |
| IC11 | vol_window_v1 (downside semi-deviation) and range_vol_v1-v3 (`docs/research/range_vol_v1.md`) |
| IC12 | credit_vote_v1 (BAA10Y level vs its SMA and yield-curve votes/vetoes) (`docs/research/credit_vote_v1.md`) |
| IC13 | credit_vote_v1 (macro votes/vetoes built on market spreads and the curve) (`docs/research/credit_vote_v1.md`) |
| IC14 | credit_vote_v1 (market-spread macro states) (`docs/research/credit_vote_v1.md`) |
| IC15 | valuation_v1 (CAPE / earnings-yield level) and IB12/IB13 (real-yield level, nominal-10y momentum — in the bank) (`docs/research/valuation_v1.md`) |
| IC16 | credit_vote_v1 (the yield curve and credit spreads) and vix_filter_v1 (`docs/research/credit_vote_v1.md`) |
| IC17 | credit_vote_v1 (BAA10Y relative to its SMA, plus curve votes/vetoes) (`docs/research/credit_vote_v1.md`) |
| IC18 | credit_vote_v1 (BAA10Y) and gold_riskoff_oos_v1 / e2_confirmation_v1 (the pre-2000 OOS stage) (`docs/research/e2_confirmation_v1.md`) |
| IC19 | vix_filter_v1 (VIX states) and credit_vote_v1 (spread states) (`docs/research/vix_filter_v1.md`) |
| IC20 | vix_filter_v1 (VIX level, spike and term structure; VXN sizing) (`docs/research/vix_filter_v1.md`) |
| IC21 | valuation_v1 (a valuation level) and IB12 (real 10-year yield level, in the bank) (`docs/research/valuation_v1.md`) |
| IC22 | IB22 international lead-lag (foreign *equity* index returns, in the bank) and vix_filter_v1 (`docs/research/vix_filter_v1.md`) |
| IC23 | seasonal_overlay_v1 / seasonal_placebo_v1 (May-October half exposure, cash in September, turn-of-month) (`docs/research/seasonal_overlay_v1.md`) |
| IC24 | monthly_defer_v1 (YI-UFE month boundaries) and seasonal_overlay_v1 (turn-of-month) (`docs/research/monthly_defer_v1.md`) |
| IC25 | seasonal_overlay_v1 (calendar overlays on E2) and exec_delay_v1 (fill-day shifts) (`docs/research/seasonal_overlay_v1.md`) |
| IC26 | seasonal_overlay_v1 / seasonal_placebo_v1 (May-October half exposure, September cash) (`docs/research/seasonal_placebo_v1.md`) |
| IC27 | breadth_vote_v1 (QQQ/SPY/IWM votes) and riskoff_rotation_v1-v4 (defensive rotation) (`docs/research/breadth_vote_v1.md`) |
| IC28 | breadth_vote_v1 (three equity indices) and riskoff_rotation_v1-v4 (sector ETFs as risk-off *holdings*) (`docs/research/breadth_vote_v1.md`) |
| IC29 | riskoff_rotation_v1-v4 (country/region ETFs as holdings) and relstrength_v1 (QLD vs SSO rotation) (`docs/research/riskoff_rotation_v4.md`) |
| IC30 | leverage_frontier_v1 (BOND10 as a risk-off asset) and riskoff_rotation_v1-v4 (bond-leg momentum) (`docs/research/riskoff_rotation_v1.md`) |
| IC31 | tbill_sweep_v1 (S1/S2_10 BIL sleeve) and r2_tbill_combo_v1 (the sleeve after tax: it lost) (`docs/research/r2_tbill_combo_v1.md`) |
| IC32 | tbill_sweep_v1 / r2_tbill_combo_v1 (bill sleeve, after tax it lost) and leverage_frontier_v1 (GOLD as risk-off, +2.3 pp CAGR but +4.3 pp DD) (`docs/research/leverage_frontier_v1.md`) |
| IC33 | IB26 (curve-conditioned IEF off-state, in the bank) and leverage_frontier_v1 (BOND10/gold risk-off) (`docs/research/leverage_frontier_v1.md`) |
| IC34 | riskoff_rotation_v1-v4 (sector/country ETFs as the defensive holding, selected by momentum) (`docs/research/riskoff_rotation_v4.md`) |
| IC35 | gold_riskoff_oos_v1 (gold as risk-off, failed every OOS gate) and leverage_frontier_v1 (GOLD risk-off, +2.3 pp CAGR / +4.3 pp DD) (`docs/research/gold_riskoff_oos_v1.md`) |
| IC36 | riskoff_rotation_v1-v4 (momentum selection among defensives; failed doubled cost in v2 and the DD gate in v4) and leverage_frontier_v1 (single-asset risk-off choices) (`docs/research/riskoff_rotation_v3.md`) |
| IC37 | leverage_frontier_v1 (BOND10 risk-off, failed the bootstrap tail gate) and r2_tbill_combo_v1 (the bill sleeve after tax) (`docs/research/leverage_frontier_v1.md`) |
| IC38 | early_qqq_v1 (the early leg in QQQ/SPY/SSO) and fast_reentry_v1/v2 (R2's conditional half-weight early path) (`docs/research/early_qqq_v1.md`) |
| IC39 | instrument_cost_v1 (TQQQ vs QLD) and early_qqq_v1 (unlevered legs) (`docs/research/instrument_cost_v1.md`) |
| IC40 | relstrength_v1 (rotation) and trend_robustness_v2 (index-vote split) and early_qqq_v1 (SSO as the early leg) (`docs/research/relstrength_v1.md`) |
| IC41 | relstrength_v1 (rotation between QLD and SSO by relative strength) and trend_robustness_v2 (the QLD+SSO split by index votes) (`docs/research/trend_robustness_v2.md`) |
| IC42 | gain_band_v1-v3 / band_h2_combo_v1 (the USD gain fraction driving the band widths) and gain_band_v2's A1/A2 variants (`docs/research/gain_band_v3.md`) |
| IC43 | tax_budget_v2 (deliberate year-end loss realisation in one leg) and tax_harvest_v1 H1 (`docs/research/tax_budget_v2.md`) |
| IC44 | gain_band_v1-v3 / band_h2_combo_v1 (K) and ewma_band_v1 (fixed bands 2.5/5/10 pp) (`docs/research/band_h2_combo_v1.md`) |
| IC45 | gold_riskoff_oos_v1 / leverage_frontier_v1 / riskoff_rotation_v4 (gold as a state-dependent holding) (`docs/research/gold_riskoff_oos_v1.md`) |
| IC46 | r2_tbill_combo_v1 (S2_10 bill sleeve, lost after tax) and tbill_sweep_v1 (the sweep rules) (`docs/research/r2_tbill_combo_v1.md`) |
| IC47 | ladder_v1 (early-leg conviction ladder) and crash_entry_v1 (drawdown-recovery entry path) (`docs/research/ladder_v1.md`) |
| IC48 | IB29 (drawdown-depth scaling, in the bank) and dip_overlay_v1 (short-term drop triggers) (`docs/research/dip_overlay_v1.md`) |
| IC49 | exposure_k_v1 / vt_level_tax_v1 / vol_shape_v1 (vol-target level and shape changes at matched average weight) and IB29/IB30 (price-based drawdown scaling, in the bank) (`docs/research/exposure_k_v1.md`) |
| IC50 | breadth_vote_v1 (a count of indices above their own SMAs) and deriv_vote_v1 (price-derivative votes) (`docs/research/breadth_vote_v1.md`) |
| IC51 | vol_window_v1 (own-series volatility) and breadth_vote_v1 (a cross-instrument count) (`docs/research/vol_window_v1.md`) |
| IC52 | gain_band_v1-v3 (drift bands in percentage points) and ewma_band_v1 (2.5/5/10 pp bands) (`docs/research/gain_band_v3.md`) |
| IC53 | exec_delay_v1 (fill-timing family including a split-fill arm) and IB36/IB37 (fill filters/prices, in the bank) (`docs/research/exec_delay_v1.md`) |
| IC54 | exec_delay_v1 (next close, t+2 open, split fills) and moc_execution_v1 (same-close fills) (`docs/research/exec_delay_v1.md`) |
| IC55 | after_tax_tr_v1 / r2_tbill_combo_v1 (the tax engine and the only other account-level friction, the cash sweep) (`docs/research/r2_tbill_combo_v1.md`) |
| IC56 | instrument_cost_v1 (fund-level costs and reset decay) and after_tax_tr_v1 (the tax model's dividend treatment) (`docs/research/instrument_cost_v1.md`) |
| IC57 | exec_delay_v1 (fill-day shifts) and execution_model_audit_v1 (the live/research divergence audit) (`docs/research/execution_model_audit_v1.md`) |
| IC58 | execution_model_audit_v1 (the divergence audit) and the engine's own cash routing (never varied) (`docs/research/execution_model_audit_v1.md`) |
| IC59 | gain_band_v1-v3 / band_h2_combo_v1 (K) and ewma_band_v1 (symmetrical bands) (`docs/research/band_h2_combo_v1.md`) |
| IC60 | tax_harvest_v1/v2 (H2: December downsize deferral) and monthly_defer_v1 (month-boundary deferral of downsizes) (`docs/research/tax_harvest_v2.md`) |

## 6. Closed axes of the record (a bank arm must state its difference from these)

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

## 7. The 60 ideas in detail

## 7.1 Trend-signal algebra (new statistics) (7 ideas)

### IC01 52-week-high proximity vote -- **rank 1**

**Mechanism.** Momentum is strongest when price sits near its 52-week high: the nearness-to-the-high statistic close/max(close,252) predicts returns better than the trailing return itself, because investors anchor on the reference point of the running maximum and are slow to bid price through it (George & Hwang). Production's momentum vote is a 252-session return whose magnitude is discarded, so it reads ON during a dead-cat bounce that recovers half of a crash (2001-10, 2008-12) and OFF during a shallow dip in a strong uptrend. Trading the return for the distance to the high keeps the recoveries that reclaim the high quickly and rejects the ones that stall below it, attacking at the vote level the whipsaw the early path was invented to patch.

**Literature.** [George & Hwang, The 52-Week High and Momentum Investing, Journal of Finance 2004](https://doi.org/10.1111/j.1540-6261.2004.00695.x); [Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012](https://doi.org/10.1016/j.jfineco.2011.11.003)

**Closest tested study.** deriv_vote_v1 (golden cross, OLS log-price slope, rising midpoint, vol-scaled TSM hurdle) (`docs/research/deriv_vote_v1.md`). *Difference:* deriv_vote replaced the vote with price *derivatives* and with a 252-session return divided by realised vol; trend_robustness_v2 swept SMA lengths 100-250 and their ensemble. No tested arm uses the running maximum as the reference, i.e. a statistic whose level is the high itself rather than an average or a slope.

**Signal inputs.** the arm's own instrument daily close (QQQ on the main window, the local index in the OOS windows)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ close); data/historical/ewma_band_v1_index.csv (^IXIC/^N225/^GDAXI/^FTSE close). New downloads: none. Main window: full: QQQ 1999-03-10 -> 2026-09-29 (252-session warm-up consumed inside the window). OOS evaluable: all six -- closes only; every OOS index file carries a close column

**Plugin.** `targets_from_data` -- rolling-max of the close inside fe_signals.arm_signals' wants(); no engine change

**Arms (parameters frozen).**
- **A** -- 2-of-3 vote with TSM252 replaced by close/max(close over the last 252 sessions) > 0.90
- **B** -- 2-of-3 vote with TSM252 replaced by close/max(close,252) > 0.95
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, close/max(close,252) > 0.90}, on iff >= 3

**Honest prior.** medium-high: one new statistic on the core vote, no new data, the strongest single documented momentum predictor.

### IC02 Skip-month (12-1) momentum vote -- **rank 4**

**Mechanism.** The standard momentum convention skips the most recent month because short-horizon returns reverse (Jegadeesh & Titman). Production's TSM252 runs to the last close, so a sharp 20-session drop inside an otherwise positive year leaves the momentum vote ON while the recent information is negative, and a 20-session spike inside a negative year leaves it OFF. Voting on r(t-252 -> t-21) > 0 removes the reversal-contaminated month and makes the vote a pure 11-month trend statement; the same horizon, a different estimator.

**Literature.** [Jegadeesh & Titman, Returns to Buying Winners and Selling Losers, Journal of Finance 1993](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x); [Asness, Moskowitz & Pedersen, Value and Momentum Everywhere, Journal of Finance 2013](https://doi.org/10.1111/jofi.12021)

**Closest tested study.** signal_ensemble_v1 / e2_confirmation_v1 (the 252-session TSM vote inside E2) (`docs/research/signal_ensemble_v1.md`). *Difference:* E2's momentum leg is the 252-session return to the decision close; deriv_vote_v1's C4 divides that return by vol. The 12-1 convention changes *which* sessions the return spans (excluding the last 20), a specification the record never varies.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ close); data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only; the 21-session offset needs no extra data

**Plugin.** `targets_from_data` -- index-shifted return inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- 2-of-3 vote with TSM252 replaced by r(t-252 -> t-21) > 0
- **B** -- 2-of-3 vote with TSM252 replaced by r(t-126 -> t-21) > 0 (a 6-1 momentum leg)
- **C** -- E2 as production and the early path's entry additionally requires r(t-252 -> t-21) > 0

**Honest prior.** medium: free, literature-standard, and it only changes which sessions the vote spans.

### IC03 CUSUM (change-point) filter vote

**Mechanism.** Replace the fixed-window level test with an event detector: accumulate the daily return normalised by a trailing volatility estimate into a symmetric CUSUM and switch the vote to ON when the sum crosses +h and OFF when it crosses -h (Page's filter; the sampling scheme in Lopez de Prado's CUSUM filter). Sensitivity is then measured in units of current volatility rather than in moving-average lag, and the state resets at each crossing, so exposure follows regimes instead of levels; a 3-sigma threshold is the classical control-chart calibration.

**Literature.** [Page, Continuous Inspection Schemes, Biometrika 1954](https://doi.org/10.1093/biomet/41.1-2.100); [Lopez de Prado, Advances in Financial Machine Learning (the CUSUM filter), 2018](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086)

**Closest tested study.** deriv_vote_v1 (rising midpoint, golden cross) and vote_hysteresis_v1 (asymmetric entry/exit thresholds) (`docs/research/deriv_vote_v1.md`). *Difference:* vote_hysteresis_v1 changed the *counts* of the 2-of-3 vote that trigger entry/exit and added confirmation days; deriv_vote used window differences. A vol-normalised cumulative-sum crossing has no window and no vote count - it is a control-chart change-point detector.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only

**Plugin.** `targets_from_data` -- recursive CUSUM inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- the momentum vote replaced by CUSUM(h = 3.0 * vol20_daily, reset at each crossing) > 0
- **B** -- same with h = 2.0 * vol20_daily
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, CUSUM(h = 3.0) > 0}, on iff >= 3

**Honest prior.** medium-low: an established event filter, no tuned parameter beyond h, but it replaces the core vote.

### IC04 Kalman local-linear-trend slope vote

**Mechanism.** A state-space local-linear-trend filter (level and slope as latent states, fixed signal-to-noise ratio q) produces a recursively smoothed slope whose estimation uncertainty is itself an output, so the vote can be a significance statement - slope/sqrt(P_slope) > z - rather than a sign. Unlike an OLS slope over a fixed window the gain adapts: the filter learns faster after a break and slower in noise, which is exactly the behaviour the fixed 200-session SMA lacks.

**Literature.** [Harvey, Forecasting, Structural Time Series Models and the Kalman Filter, 1989](https://doi.org/10.1017/CBO9780511599286); [Durbin & Koopman, Time Series Analysis by State Space Methods, 2nd ed., 2012](https://doi.org/10.1093/acprof:oso/9780199641178.001.0001)

**Closest tested study.** deriv_vote_v1 (OLS slope of log close, golden cross) and timesfm_v1 (neural forecast) (`docs/research/timesfm_v1.md`). *Difference:* The tested slope is a fixed-window regression (or a 50/200 SMA cross); the Kalman slope is recursive with an adaptive gain and carries its own variance, so the arm can gate on significance instead of sign. timesfm_v1 tested a *learned* sequence model - this is a two-state linear filter with no estimated parameters.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only

**Plugin.** `targets_from_data` -- a numpy recursion inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- 2-of-3 vote with TSM252 replaced by Kalman slope (q = 1e-5, r = 1) > 0
- **B** -- same with the significance test slope/sqrt(P_slope) > 1.0
- **C** -- E2 as production and the early path's entry additionally requires Kalman slope > 0

**Honest prior.** low-medium: the family is untested here, the parameter set is fixed and tiny, but the gain may just re-express vol20.

### IC05 Return-entropy regime gate

**Mechanism.** Approximate/sample entropy of the last 126 daily returns measures how repetitive the return sequence is: low entropy means the process has structure a trend rule can exploit, high entropy means noise in which any rule whipsaws. Gating the vote on entropy below its own trailing median is self-calibrating (no absolute threshold), so the arm says only 'trade when the market is currently more structured than it usually is', an orthogonal conditioning variable to every tested price statistic.

**Literature.** [Pincus, Approximate entropy as a measure of system complexity, PNAS 1991](https://doi.org/10.1073/pnas.88.6.2297); [Maasoumi & Racine, Entropy and predictability of stock market returns, Journal of Econometrics 2002](https://doi.org/10.1016/S0304-4076(02)00100-1)

**Closest tested study.** deriv_vote_v1 (regime-conditioned votes) and breadth_vote_v1 (`docs/research/deriv_vote_v1.md`). *Difference:* Every tested regime gate is built from prices (slopes, vote counts, spreads); entropy is computed on the return *sequence* and is invariant to the level and to scale - it cannot be rewritten as a price statistic.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only; the estimator needs 126+ sessions of warm-up

**Plugin.** `targets_from_data` -- a rolling entropy estimator inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- E2 on iff the 2-of-3 vote holds AND ApEn(126, m=2) <= its trailing 252-session median
- **B** -- same with the threshold at the trailing 252-session 40th percentile
- **C** -- early-path entry additionally requires ApEn(126, m=2) <= its trailing 252-session median

**Honest prior.** low: a heavy estimator with two-sided literature evidence; cheap enough to screen.

### IC06 Overnight-drift trend vote

**Mechanism.** The equity premium accrues overnight: on this very data QQQ earns about +9.8%/yr from close to open and about -2.2%/yr from open to close, and the strategy fills at the open and holds through the night (moc_execution_v1 measured the decomposition). A vote built on the cumulative *overnight* return therefore measures the component of the drift the position actually earns; the total-return vote is half intraday noise, which is one source of the false re-entries the early path patches.

**Literature.** [Lou, Polk & Skouras, A Tug of War: Overnight versus Intraday Expected Returns, JFE 2019](https://doi.org/10.1016/j.jfineco.2019.03.011); [Lachance, The Night Trading Session and the Equity Premium (overnight drift), 2023](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622)

**Closest tested study.** moc_execution_v1 (measured the overnight/intraday split) and overnight_hold_v1 (held overnight only) (`docs/research/moc_execution_v1.md`). *Difference:* moc_execution_v1 *measured* the second-moment split and concluded that same-close fills lose; overnight_hold_v1 traded only overnight (335 trades/yr). Neither ever used the overnight sub-return as a *signal*: the vote on the cumulative overnight return is new, and it keeps production's next-open execution.

**Signal inputs.** the arm's own instrument daily open and close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ OHLC); data/historical/ewma_band_v1_index.csv (^IXIC/^N225/^GDAXI/^FTSE OHLC). New downloads: none. Main window: full. OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- opens are needed; ^GSPC 1957-1999 is loaded close-only by fe_data.load_gspc (open := previous close), so the overnight return is identically zero there and that window is dropped; M2 carries OHLC but is 1935-56 -> also dropped for the reduced 5-window G7 (4 live windows + M1)

**Plugin.** `targets_from_data` -- open/close decomposition inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- 2-of-3 vote with TSM252 replaced by the 252-session cumulative close->open log return > 0
- **B** -- as A AND the 252-session cumulative open->close log return <= 0 (both conditions)
- **C** -- early-path entry additionally requires the 20-session cumulative close->open log return > 0

**Honest prior.** low-medium: the decomposition is real and large, but a 252-session overnight sum may be noisier than the total return it replaces.

### IC07 Swing-structure (higher-high / higher-low) vote

**Mechanism.** Define 26-session swing pivots (a pivot high is a session whose high exceeds the highs of the surrounding 13 sessions on each side; confirmed with the 13-session delay, so causal) and declare the trend up when the last two confirmed pivots are a higher high AND a higher low - the Dow-theory structural test. Reference levels are actual prior extremes rather than a window average or a channel midpoint, and the test is a discrete two-condition statement, so it is neither a return nor a level-vs-average rule.

**Literature.** [Dow theory / Edwards & Magee, Technical Analysis of Stock Trends (swing structure)](https://doi.org/10.1201/9781420063905); [Lo, Mamaysky & Wang, Foundations of Technical Analysis, Journal of Finance 2000 (pattern recognition on prices)](https://doi.org/10.1111/0022-1082.00265)

**Closest tested study.** signal_ensemble_v1 (Donchian-126 midpoint) and trend_robustness_v2 (`docs/research/signal_ensemble_v1.md`). *Difference:* DON126 is a continuous close-vs-(max+min)/2 test with one comparison; the swing test is a discrete sequence of two confirmed extrema. fast_reentry_v1's R3 (Donchian-126 AND SMA50) is still a price-extrema *channel*, not a pivot sequence.

**Signal inputs.** the arm's own instrument daily high, low and close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ OHLC); data/historical/ewma_band_v1_index.csv (OHLC for M1-M5). New downloads: none. Main window: full. OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- highs/lows are needed; the ^GSPC 1957-1999 loader is close-only (opens := previous close), so that window is dropped and the reduced 5-window G7 is pre-registered as credit_vote_v1 did

**Plugin.** `targets_from_data` -- pivot detection inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- 2-of-3 vote with TSM252 replaced by swing structure up (last two confirmed 26-session pivots: HH and HL)
- **B** -- 2-of-3 vote with SMA200 replaced by the same swing-structure test
- **C** -- early-path entry additionally requires swing structure up

**Honest prior.** low-medium: the family is untested, the confirmation delay makes it slow by construction.

## 7.2 Volatility and risk estimators (4 ideas)

### IC08 HAR volatility blend instead of vol20 -- **rank 6**

**Mechanism.** HAR-RV (Corsi) forecasts realised volatility by blending daily, weekly and monthly components with fixed weights. With weights frozen at 0.40*vol5 + 0.35*vol20 + 0.25*vol60 the estimate reacts faster than vol20 after a spike - so the strategy leaves the vol floor sooner and holds fewer sessions de-levered - and it is smoother in calm markets, so fewer sessions cross the 0.5 pp resize band. Both effects act on the trim channel, which carries about 91% of the realised taxable gain; that is why an estimator change can matter after tax even when the pre-tax difference is a few basis points.

**Literature.** [Corsi, A Simple Approximate Long-Memory Model of Realized Volatility, Journal of Financial Econometrics 2009](https://doi.org/10.1093/jjfinec/nbp001); [Harvey et al., The Impact of Volatility Targeting, Journal of Portfolio Management 2018](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)

**Closest tested study.** ewma_band_v1 W1 (RiskMetrics lambda 0.94) and vol_window_v1 (vol10/40/60, max(vol20,vol60)) (`docs/research/vol_window_v1.md`). *Difference:* Every tested estimator is a *single* recursion or a single window (or a max of two). HAR is an additive blend of three horizons with declared fixed weights, i.e. a level combination whose reaction speed is independent of the longest window. IB07 (GARCH) is the same axis-closure test in a different family and is in the bank, not tested.

**Signal inputs.** the arm's own instrument daily close (the traded 2x leg on the main window)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only; vol252 needs a 252-session warm-up

**Plugin.** `targets_from_data` -- vt_of(vol) -> vt_of(sigma_har) in wants(); no engine change

**Arms (parameters frozen).**
- **A** -- vt = min(1, 0.35/sigma_har), sigma_har = 0.40*vol5 + 0.35*vol20 + 0.25*vol60 (all annualised, of the traded leg)
- **B** -- vt = min(1, 0.35/sigma_har2), sigma_har2 = 0.50*vol20 + 0.30*vol60 + 0.20*vol252
- **C** -- vt = min(1, 0.35/max(vol20, sigma_har))

**Honest prior.** medium: the estimator axis is formally closed, but the HAR blend is the one canonical family inside it that was never run, and it acts on the trim channel.

### IC09 Cross-sectional correlation sizing -- **rank 13**

**Mechanism.** The mean pairwise correlation of the nine sector ETFs rises in stress (correlation risk; Pollet & Wilson): when everything moves together, the diversification inside the market has gone and the index's own volatility understates portfolio risk. Scaling the target by clip(corr_t / median252(corr), 0.7, 1.0) de-risks exactly when internal correlation breaks out, and it is a cross-sectional second-moment statistic, so it is not a member of the tested single-series volatility family.

**Literature.** [Pollet & Wilson, Average correlation and stock market returns, JFE 2010](https://doi.org/10.1016/j.jfineco.2010.02.004); [Ang & Chen, Asymmetric correlations of equity portfolios, JFE 2002](https://doi.org/10.1016/S0304-405X(02)00068-5)

**Closest tested study.** vol_window_v1 and range_vol_v1-v3 (own-series volatility estimators); breadth_vote_v1 (a panel vote) (`docs/research/range_vol_v3.md`). *Difference:* breadth_vote_v1 turned a panel into *votes* (count of indices above their SMA) and lost; range_vol tested OHLC estimators of the arm's own series. Nothing uses the panel's *correlation* as the sizing input.

**Signal inputs.** the nine sector SPDR daily closes (registered extra keys sec_xlb..sec_xly); the arm's own instrument close

**Data.** in repo: research/batch_screen/EXTRA_DATA.json (sec_xlb/xle/xlf/xli/xlk/xlp/xlu/xlv/xly, 1998-12-22 ->); data/historical/batch_screen_v1_xl*.csv. New downloads: none. Main window: full (the sector ETFs start 1998-12-22, before the 2000-03-09 main window). OOS evaluable: M3, M4, M5 -- the sector ETFs exist only on the modern spans of M3/M4/M5; GSPC_1957_1999, M1 and M2 are un-evaluable -> 3 applicable windows, so the arm risks holdout_insufficient and the reduced 3-window G7 must be pre-registered (credit_vote_v1 precedent)

**Plugin.** `targets_from_data` -- data.extra('sec_xl*') panel correlation inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- weight *= clip(corr_t/median252(corr), 0.70, 1.00), corr = 20-session mean of the nine SPDRs' average pairwise 60-session return correlation
- **B** -- weight *= clip(1 - 0.5*(corr_t/median252(corr) - 1), 0.70, 1.00), same corr
- **C** -- the same multiplier as A applied only while the vote is on (early path unchanged)

**Honest prior.** medium: an orthogonal risk input with a clear mechanism; the 3-window OOS coverage is the structural weakness.

### IC10 Day/night volatility decomposition sizing

**Mechanism.** The strategy fills at the open and holds through the night, so the risk it actually bears is the close-to-open variance: an overnight-only volatility estimate is the matched risk model, and the intraday variance should be down-weighted (the position's intraday exposure is a single open print, not the full open-to-close path). The overnight share of daily variance varies enough across regimes (2018-2020) that this is a different risk model, not another window length.

**Literature.** [Lou, Polk & Skouras, A Tug of War: Overnight versus Intraday Expected Returns, JFE 2019](https://doi.org/10.1016/j.jfineco.2019.03.011); [Andersen, Bollerslev & Cai, Intraday and interday volatility in the Japanese market, 2000](https://doi.org/10.1016/S0927-5398(99)00014-3)

**Closest tested study.** vol_window_v1 and ewma_band_v1 (alternative estimators of the close-to-close series) (`docs/research/vol_window_v1.md`). *Difference:* All tested estimators are computed on close-to-close returns. A decomposition into close->open and open->close components is a different statistic about the same series, and it matches the fill convention (decide at the close, fill at the next open).

**Signal inputs.** the arm's own instrument daily open and close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ OHLC); data/historical/ewma_band_v1_index.csv (OHLC). New downloads: none. Main window: full. OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- ^GSPC 1957-1999 is loaded close-only (open := previous close), so the decomposition is degenerate there and the window is dropped; reduced 5-window G7

**Plugin.** `targets_from_data` -- open/close variance decomposition inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- vt = min(1, 0.35/sigma_night20), sigma_night20 = annualised stdev of log(open_t/close_{t-1}) over 20 sessions
- **B** -- vt = min(1, 0.35/sigma_mix), sigma_mix = sqrt(sigma_night20^2 + 0.5*sigma_day20^2)
- **C** -- vt = min(1, 0.35/max(vol20, 1.20*sigma_night20))

**Honest prior.** low-medium: an economically matched risk model, but overnight variance is a noisier estimate than close-to-close.

### IC11 Tail-percentile (CVaR) risk sizing

**Mechanism.** The vol target is a second-moment statistic, but equities have a fat left tail: two periods with equal variance can have very different worst days. Sizing on the mean of the worst 5% of the last 252 daily returns (a 95% CVaR), rescaled to be Gaussian-comparable (x 2.063 x sqrt(252)), makes the target respond to how bad the bad days are rather than to total variance. This is a quantile-tail statistic, which no tested estimator computes.

**Literature.** [Rockafellar & Uryasev, Optimization of Conditional Value-at-Risk, Journal of Risk 2000](https://doi.org/10.21314/JOR.2000.038); [Barndorff-Nielsen & Shephard, Power and Bipower Variation with Stochastic Volatility and Jumps, Journal of Financial Econometrics 2004](https://doi.org/10.1093/jjfinec/nbh001)

**Closest tested study.** vol_window_v1 (downside semi-deviation) and range_vol_v1-v3 (`docs/research/range_vol_v1.md`). *Difference:* vol_window_v1 tested a downside *second moment* (semi-deviation); a CVaR is a quantile-average of the tail and reacts to the shape of the loss distribution, not only to its variance below the mean.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only; 252-session warm-up

**Plugin.** `targets_from_data` -- rolling tail-mean estimator inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- vt = min(1, 0.35/sigma_cvar), sigma_cvar = 2.063*sqrt(252)*|mean of the worst 5% of the last 252 daily log returns|
- **B** -- vt = min(1, 0.35/(0.5*sigma_cvar + 0.5*vol20))
- **C** -- vt = min(1, 0.35/max(sigma_cvar, vol20))

**Honest prior.** low-medium: a new estimator with a matching Gaussian calibration; the tail mean is a high-variance estimate over 252 sessions.

## 7.3 Regime models from macro releases and financial conditions (11 ideas)

### IC12 Financial-conditions (NFCI) veto -- **rank 7**

**Mechanism.** The Chicago Fed National Financial Conditions Index aggregates 105 weekly activity and financial indicators into one z-score (positive = tighter than average). It is the broadest published measure of the conditions that precede equity drawdowns, and unlike a single market spread it is not the same variable as the price it is meant to forecast. The arm vetoes the on-state while NFCI is above its own 3-year median AND rising over the last four weeks - the level-with-momentum state of 2000-02, 2007-08 and 2022 - so a high-but-improving reading does not block the recovery.

**Literature.** [Brave & Butters, Monitoring Financial Stability: A Financial Conditions Index Approach, Economic Perspectives 2011](https://www.chicagofed.org/publications/economic-perspectives/2011/1q-brave-butters); [Gilchrist & Zakrajsek, Credit Spreads and Business Cycle Fluctuations, AER 2012](https://doi.org/10.1257/aer.102.4.1692)

**Closest tested study.** credit_vote_v1 (BAA10Y level vs its SMA and yield-curve votes/vetoes) (`docs/research/credit_vote_v1.md`). *Difference:* credit_vote_v1 tested one market spread and the curve at their *levels*; NFCI is a 105-indicator composite and the arm conditions on the *combination* of a relative level and a 4-week change, which no tested macro veto uses.

**Signal inputs.** FRED NFCI (weekly, ffill to sessions, released with a 1-week lag)

**Data.** in repo: none. New downloads: NFCI (weekly financial conditions index, 1971-01-08 ->). Main window: full (NFCI starts 1971). OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- weekly series with a one-week lag; M2 (1935-1956) is un-evaluable -> reduced 5-window G7

**Plugin.** `targets_from_data` -- data.extra('nfci', ffill=True) inside wants(); the key must be registered in EXTRA_DATA.json

**Arms (parameters frozen).**
- **A** -- weight = 0 while NFCI(1-week lag) > its trailing 156-week median AND NFCI_t > NFCI_{t-4 weeks}
- **B** -- the same state scales weight by 0.5 instead of 0
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, NFCI <= its trailing 156-week median}, on iff >= 3

**Honest prior.** medium: the broadest available financial-conditions state with a documented publication lag; the veto family has lost before, but always as a single market spread.

### IC13 Real-activity regime (CFNAI) vote

**Mechanism.** The Chicago Fed National Activity Index is a monthly 85-indicator real-activity composite whose 3-month moving average has a published interpretation: below -0.70 the economy is historically in recession. Using that threshold as a veto is a regime model with an *externally published* parameter rather than a fitted one, and it conditions exposure on the state of the real economy, a different information set from the market prices every tested filter uses. (ISM's own PMI has no continuous free FRED series past 2016, so CFNAI is the honest vehicle for an activity release.)

**Literature.** [Chicago Fed National Activity Index (CFNAI) background and the -0.70 recession threshold](https://www.chicagofed.org/research/data/cfnai/current-data); [Estrella & Mishkin, Predicting U.S. Recessions (published-threshold regime rules), 1998](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr31.pdf)

**Closest tested study.** credit_vote_v1 (macro votes/vetoes built on market spreads and the curve) (`docs/research/credit_vote_v1.md`). *Difference:* credit_vote_v1's inputs are all traded prices; CFNAI is a real-activity release combining production, employment, consumption and housing, with a published threshold and a 1-month publication lag - a macro-release regime, not a market state.

**Signal inputs.** FRED CFNAI (monthly, 1-month publication lag, ffill to sessions)

**Data.** in repo: none. New downloads: CFNAI (monthly, 1967-01 ->). Main window: full. OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- monthly series, 1-month lag; M2 un-evaluable -> reduced 5-window G7

**Plugin.** `targets_from_data` -- data.extra('cfnai', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight = 0 while CFNAI_3mma (1-month lag) < -0.70
- **B** -- weight *= 0.5 while CFNAI_3mma (1-month lag) < -0.70
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, CFNAI_3mma >= -0.70}, on iff >= 3

**Honest prior.** medium-low: a published recession threshold, but activity releases are slow and the 2001 and 2020 recessions were short.

### IC14 Initial-claims shock veto

**Mechanism.** Weekly initial jobless claims are the highest-frequency labour release and the labour market turns before the equity cycle's worst stretches. A shock defined relative to the series' own recent low (4-week average more than 10% above its 26-week minimum) is scale-free, needs no fitting, and has the correct sign convention: it fires when firing *accelerates*, not when claims are merely high (the 2020 spike and the 2009-10 plateau both stay below their own new lows afterwards).

**Literature.** [DOL ETA weekly initial claims (release definition and timing)](https://oui.doleta.gov/unemploy/claims.asp); [Ludvigson & Ng, Macro Factors in Bond Risk Premia (weekly labour indicators as leading states), 2009](https://doi.org/10.1093/rfs/hhp001)

**Closest tested study.** credit_vote_v1 (market-spread macro states) (`docs/research/credit_vote_v1.md`). *Difference:* No tested macro arm touches a labour-market release; the closest is valuation_v1's monthly Shiller data, which is a valuation level rather than an activity shock.

**Signal inputs.** FRED ICSA (weekly initial claims, 1-week lag, ffill)

**Data.** in repo: none. New downloads: ICSA (weekly initial claims, 1967-01-07 ->). Main window: full. OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- weekly, 1-week lag; M2 un-evaluable -> reduced 5-window G7

**Plugin.** `targets_from_data` -- data.extra('icsa', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.5 while ICSA_4w_mean / min(ICSA_4w_mean over 26 weeks) - 1 > 0.10
- **B** -- weight = 0 while the same ratio > 0.20
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, the ICSA ratio <= 0.10}, on iff >= 3

**Honest prior.** medium-low: orthogonal and cheap, but claims are noisy and seasonally adjusted revisions are large.

### IC15 CPI-acceleration veto

**Mechanism.** Real yields (IB12, in the bank) look benign when inflation is falling; the state that forces policy tightening and de-rates long-duration equity is inflation *accelerating*. The arm vetoes while 6-month annualised CPI inflation exceeds the 12-month rate by more than 0.5 pp - a momentum statement about the release with no fitted level, so the 2013-2019 low-inflation regime and the 2023 disinflation both leave the strategy alone.

**Literature.** [FRED CPIAUCSL (CPI, all urban consumers, seasonally adjusted, 1947-01 ->)](https://fred.stlouisfed.org/series/CPIAUCSL); [Bansal & Yaron, Risks for the Long Run (inflation and long-duration equity), 2004](https://doi.org/10.1111/j.1540-6261.2004.00665.x)

**Closest tested study.** valuation_v1 (CAPE / earnings-yield level) and IB12/IB13 (real-yield level, nominal-10y momentum — in the bank) (`docs/research/valuation_v1.md`). *Difference:* valuation_v1 conditioned on a *valuation level*; every tested rates arm conditions on a yield level or a yield change. This conditions on the release's own acceleration, the variable the reaction function responds to.

**Signal inputs.** FRED CPIAUCSL (monthly index, 1-month publication lag) - already registered as extra key cpi

**Data.** in repo: data/historical/batch_screen_v1_cpiaucsl.csv (1947-01-01 -> 2026-09-01, registered as extra key cpi). New downloads: none. Main window: full. OOS evaluable: all six -- monthly 1947+ with a 1-month lag covers all six windows, including M2 (1935-1956) from 1947

**Plugin.** `targets_from_data` -- data.extra('cpi', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.5 while annualised 6-month CPI inflation > trailing 12-month CPI inflation + 0.5 pp
- **B** -- weight = 0 in the same state
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, CPI acceleration <= +0.5 pp}, on iff >= 3

**Honest prior.** low-medium: the mechanism is well motivated but the state is rare (2021-22 and 1972-74) and slow.

### IC16 Fed-funds tightening-cycle veto

**Mechanism.** The level of the policy rate is not the risk; the *speed and direction* of policy is. A mechanical veto while the effective fed funds rate has risen at least 75 bp over the last 126 sessions is the de-rating state (1994, 1999-2000, 2004-06, 2015-18, 2022) and it never fires in the zero-rate and easing regimes that contain the best recovery returns. The series is an administered rate, not a market price, so it carries no information already in the price the strategy trades.

**Literature.** [FRED DFF (effective federal funds rate, daily, 1954-07-01 ->)](https://fred.stlouisfed.org/series/DFF); [Bernanke & Kuttner, What Explains the Stock Market's Reaction to Federal Reserve Policy?, JF 2005](https://doi.org/10.1111/j.1540-6261.2005.00760.x)

**Closest tested study.** credit_vote_v1 (the yield curve and credit spreads) and vix_filter_v1 (`docs/research/credit_vote_v1.md`). *Difference:* The curve and the spreads are market prices that partly anticipate policy; DFF is the policy instrument itself, and the condition is a cumulative 6-month change rather than a level or a slope.

**Signal inputs.** FRED DFF (daily effective fed funds rate)

**Data.** in repo: none. New downloads: DFF (daily effective federal funds rate, 1954-07-01 ->). Main window: full. OOS evaluable: all six -- DFF starts 1954, so all six windows (incl. M2 1935-1956 from 1954) are evaluable

**Plugin.** `targets_from_data` -- data.extra('dff', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight = 0 while DFF_t - DFF_{t-126} >= +0.75 pp
- **B** -- weight *= 0.5 while DFF_t - DFF_{t-126} >= +0.50 pp
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, DFF 126-session change <= +0.75 pp}, on iff >= 3

**Honest prior.** low-medium: cheap and orthogonal, but 2022 shows an equity market that kept falling into easing and rallying into hikes.

### IC17 High-yield credit-spread (OAS) veto -- **rank 3**

**Mechanism.** The ICE BofA US high-yield OAS is the market's price of default risk and the canonical early-warning series: it widened before equity volatility in 2000, 2007, 2011, 2015, 2018, 2020 and 2022. Unlike the investment-grade BAA-10y spread tested by credit_vote_v1, the high-yield market is where credit-supply shocks and the excess bond premium sit (Gilchrist & Zakrajsek build their measure from the same universe). The veto is a *state*, not a level: spread above 1.20x its own 252-session median AND still widening over 20 sessions, so a high but improving spread does not block a recovery entry.

**Literature.** [Gilchrist & Zakrajsek, Credit Spreads and Business Cycle Fluctuations, AER 2012](https://doi.org/10.1257/aer.102.4.1692); [FRED BAMLH0A0HYM2 (ICE BofA US High Yield Index Option-Adjusted Spread)](https://fred.stlouisfed.org/series/BAMLH0A0HYM2)

**Closest tested study.** credit_vote_v1 (BAA10Y relative to its SMA, plus curve votes/vetoes) (`docs/research/credit_vote_v1.md`). *Difference:* credit_vote_v1 used the investment-grade risk spread (BAA minus the 10-year yield) at a *level* comparison; this uses the high-yield spread - a different segment whose information content is concentrated in default-risk pricing - and conditions on a magnitude state plus its own 20-session momentum.

**Signal inputs.** FRED BAMLH0A0HYM2 (daily, 1996-12-31 ->)

**Data.** in repo: none. New downloads: BAMLH0A0HYM2 (US high-yield OAS, daily, 1996-12-31 ->). Main window: full (1996-12-31 is before the main window). OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- 1996-12-31 starts inside GSPC_1957_1999 and M1 (live 1997-1999) and before M3/M4/M5; M2 un-evaluable -> reduced 5-window G7

**Plugin.** `targets_from_data` -- data.extra('hyoas', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight = 0 while HYOAS_t > 1.20 * median(HYOAS, 252 sessions) AND HYOAS_t > HYOAS_{t-20}
- **B** -- weight *= 0.5 in the same state
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, HYOAS <= 1.20x its 252-session median}, on iff >= 3

**Honest prior.** medium: the best-documented early-warning series with a two-condition state; credit_vote_v1's failure was on an investment-grade level, not this state.

### IC18 Credit-quality-spread (Baa-Aaa) veto

**Mechanism.** The quality spread (Moody's Baa minus Aaa) isolates the marginal borrower's extra default risk from the level of rates, so it is a purer aversion-to-credit state than BAA-10y and than the high-yield OAS in one respect: it has genuine pre-1950 history, which makes it the only credit series that can be tested on the 1927-1956 OOS windows. A veto while the spread exceeds 1.30x its median since 1929 is a slow, expanding-window state with no parameter tied to the modern sample.

**Literature.** [FRED BAA and AAA (Moody's seasoned corporate bond yields, monthly, 1919-01 ->)](https://fred.stlouisfed.org/series/BAA); [Bernanke, Gertler & Gilchrist, The financial accelerator in a quantitative business cycle framework, 1999](https://doi.org/10.1016/S1574-0048(99)10034-X)

**Closest tested study.** credit_vote_v1 (BAA10Y) and gold_riskoff_oos_v1 / e2_confirmation_v1 (the pre-2000 OOS stage) (`docs/research/e2_confirmation_v1.md`). *Difference:* Same issuer universe as credit_vote_v1's BAA series but differenced against Aaa rather than against the 10-year yield, and - decisively for the holdout stage - it is the one credit state old enough to be evaluated in the 1927-1956 windows, which no tested credit arm could be.

**Signal inputs.** FRED BAA and AAA (monthly, 1-month lag)

**Data.** in repo: none. New downloads: BAA and AAA (monthly Moody's yields, 1919-01 ->). Main window: full. OOS evaluable: all six -- monthly 1919+ covers all six windows including M2 (1935-1956); this is the bank's best OOS coverage for a credit state

**Plugin.** `targets_from_data` -- data.extra('baa'), data.extra('aaa') inside wants()

**Arms (parameters frozen).**
- **A** -- weight = 0 while (BAA - AAA)_t (1-month lag) > 1.30 * its expanding median since 1929
- **B** -- weight *= 0.5 in the same state
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, quality spread <= 1.30x its expanding median}, on iff >= 3

**Honest prior.** medium-low: the pre-1950 coverage makes it a rare macro idea that the holdout stage can actually confirm.

### IC19 Oil-shock veto

**Mechanism.** An oil-price spike is the archetypal supply shock: it raises headline inflation, compresses margins and precedes equity drawdowns (1973, 1979, 1990, 2008, 2022). Copper/gold (IB23, in the bank) measures *demand*; oil measures supply, so the two are complements rather than variants. The two-condition form - WTI up more than 50% over 126 sessions AND above its 252-session median - avoids the 2009 and 2020 rebounds, where oil rose sharply off a crash bottom while the median was still far above.

**Literature.** [Hamilton, Oil and the Macroeconomy since World War II, JPE 1983](https://doi.org/10.1086/261140); [FRED DCOILWTICO (Cushing WTI spot, daily, 1986-01-02 ->)](https://fred.stlouisfed.org/series/DCOILWTICO)

**Closest tested study.** vix_filter_v1 (VIX states) and credit_vote_v1 (spread states) (`docs/research/vix_filter_v1.md`). *Difference:* No tested macro arm uses a commodity price. copper/gold (IB23) is in the bank and is a *ratio* whose difference from this arm is the demand-vs-supply information; the registered extra data already carries HG=F but no oil series.

**Signal inputs.** FRED DCOILWTICO or CL=F (new download)

**Data.** in repo: none. New downloads: DCOILWTICO (daily WTI spot, 1986-01-02 ->). Main window: full. OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- 1986+ covers GSPC_1957_1999 and M1 from 1986; M2 un-evaluable -> reduced 5-window G7

**Plugin.** `targets_from_data` -- data.extra('oil', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.5 while r_oil(126) > +50% AND WTI > median252(WTI)
- **B** -- weight = 0 in the same state
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, r_oil(126) <= +50%}, on iff >= 3

**Honest prior.** low-medium: a clean mechanism; the state is rare and 2003-08 shows an oil bull market alongside a strong equity market.

### IC20 Bond-volatility regime veto

**Mechanism.** Rate volatility is the transmission channel of the 2022 equity drawdown and leads risk-off episodes generally: when bond vol doubles, duration-heavy and long-duration equity is re-priced. The canonical series (ICE BofA MOVE) has no long free daily history, so the arm uses the IEF realised 20-session volatility against its own 252-session median - a bond volatility state computed from a tradable instrument, the analogue of vix_filter_v1's equity-option state for the bond market.

**Literature.** [ICE BofA US Bond Market Option Volatility Estimate Index (MOVE; the 'bond VIX')](https://developer.ice.com/fixed-income-data-services/catalog/ice-data-indices-move-index); [Baele, Bekaert, Inghelbrecht & Wei, Flights to Safety (rate volatility and risk-off), RFS 2020](https://doi.org/10.1093/rfs/hhz055)

**Closest tested study.** vix_filter_v1 (VIX level, spike and term structure; VXN sizing) (`docs/research/vix_filter_v1.md`). *Difference:* vix_filter_v1's inputs are all equity-option-implied. A *realised bond* volatility state is a different market's risk price; it was unavailable to vix_filter_v1 (no bond vol series) and is available now from the registered IEF extra key.

**Signal inputs.** IEF daily total-return close (registered extra key ief)

**Data.** in repo: data/historical/batch_screen_v1_ief.csv (2002-07-30 ->, extra key ief). New downloads: none. Main window: full. OOS evaluable: M3, M4, M5 -- IEF starts 2002-07, so only the modern spans of M3/M4/M5 are live -> 3 applicable windows (holdout_insufficient risk); pre-register the reduced 3-window G7

**Plugin.** `targets_from_data` -- data.extra('ief') inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.5 while vol20(IEF) > 1.5 * median252(vol20(IEF))
- **B** -- weight = 0 while vol20(IEF) > 2.0 * median252(vol20(IEF))
- **C** -- the multiplier of A applied only while the vote is on (early path unchanged)

**Honest prior.** low-medium: a genuinely different volatility market, but 3-window OOS coverage and the VIX family's past failures weigh against it.

### IC21 Breakeven-inflation momentum veto

**Mechanism.** The 10-year breakeven (T10YIE) is the market's expected inflation; its *change* over 126 sessions is the state in which the reaction function tightens and long-duration equity de-rates, isolated from the nominal yield's real component. IB12 (real-yield level) and IB13 (nominal-10y momentum) are in the bank; neither isolates expectations, which is the component that moved in 2021-22 and that the Fed reacts to.

**Literature.** [FRED T10YIE (10-year breakeven inflation rate, daily, 2003-01-02 ->)](https://fred.stlouisfed.org/series/T10YIE); [Campbell, Sunderam & Viceira, Inflation Bets or Deflation Hedges? The Changing Risks of Nominal Bonds, 2017](https://doi.org/10.3386/w21318)

**Closest tested study.** valuation_v1 (a valuation level) and IB12 (real 10-year yield level, in the bank) (`docs/research/valuation_v1.md`). *Difference:* Real-yield *levels* and valuation *levels* were tested; breakeven *momentum* is the expectations component of the nominal yield, a distinct state variable that decomposes the same curve point.

**Signal inputs.** FRED T10YIE (daily breakeven, registered extra key t10yie)

**Data.** in repo: data/historical/batch_screen_v1_t10yie.csv (2003-01-02 ->, extra key t10yie). New downloads: none. Main window: full. OOS evaluable: M3, M4, M5 -- 2003-01 start -> only M3/M4/M5 have live values; reduced 3-window G7

**Plugin.** `targets_from_data` -- data.extra('t10yie', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.5 while T10YIE_t - T10YIE_{t-126} > +0.40 pp
- **B** -- weight = 0 while T10YIE_t - T10YIE_{t-126} > +0.70 pp
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, breakeven 126-session change <= +0.40 pp}, on iff >= 3

**Honest prior.** low-medium: the mechanism is sharp but the series is short (2003+) and the state is dominated by 2021-22.

### IC22 Dollar (broad trade-weighted) regime veto

**Mechanism.** A broad trade-weighted dollar surge is a global liquidity shock: dollar-funded carry unwinds and the earnings of the index's non-US revenue base de-rate (2014-15, 2018, 2022). The state used is a rate of change (60 sessions, more than +4%) *combined* with the dollar above its 252-session median, so the 2020 crash-to-recovery and the 2021 whipsaw (a mixed dollar) do not fire. No arm in the record uses a currency series as a risk state.

**Literature.** [FRED DTWEXBGS (nominal broad US dollar index, daily, 2006-01-02 ->)](https://fred.stlouisfed.org/series/DTWEXBGS); [Bruno & Shin, Cross-Border Banking and Global Liquidity (the dollar as a risk factor), 2015](https://doi.org/10.1093/restud/rdu028)

**Closest tested study.** IB22 international lead-lag (foreign *equity* index returns, in the bank) and vix_filter_v1 (`docs/research/vix_filter_v1.md`). *Difference:* This idea and the banked IB22 are complements: IB22 uses foreign equity moves as an intraday causal signal, while this uses the *currency* - the global funding variable - with a level-plus-change state. Nothing tested uses FX.

**Signal inputs.** FRED DTWEXBGS (registered extra key dtwexbgs)

**Data.** in repo: data/historical/batch_screen_v1_dtwexbgs.csv (2006-01-02 ->, extra key dtwexbgs). New downloads: none. Main window: partial: DTWEXBGS starts 2006-01-02, so on the main window the veto is only live from 2006 (a fixed convention: the state is treated as absent, weight unchanged, before 2006). OOS evaluable: M3, M4, M5 -- only M3/M4/M5 have live values from 2006 -> 3 applicable windows; reduced 3-window G7

**Plugin.** `targets_from_data` -- data.extra('dtwexbgs', ffill=True) inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.5 while the 60-session dollar change > +4% AND dollar > median252(dollar)
- **B** -- weight = 0 while the 60-session dollar change > +6%
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, 60-session dollar change <= +4%}, on iff >= 3

**Honest prior.** low-medium: a distinct, macro-motivated state; 2006+ coverage and the currency-equity correlation's instability are the risks.

## 7.4 Calendar and structural-date effects (4 ideas)

### IC23 Day-of-week (Monday) exposure tilt

**Mechanism.** The Monday effect - negative average returns concentrated in the first session of the trading week - is one of the oldest documented calendar regularities (French 1980; the 'weekend effect' literature links it to the accumulation of information while the market is closed). The arm holds 0.75x the production target on Mondays only. The tested calendar family covers months and turn-of-month (seasonal_overlay_v1, later a placebo); the weekday is a different, much finer partition of the same exposure decision.

**Literature.** [French, Stock returns and the weekend effect, Journal of Financial Economics 1980](https://doi.org/10.1016/0304-405X(80)90021-5); [Cross, The Behavior of Stock Prices on Fridays and Mondays, FAJ 1973](https://doi.org/10.2469/faj.v29.6.67)

**Closest tested study.** seasonal_overlay_v1 / seasonal_placebo_v1 (May-October half exposure, cash in September, turn-of-month) (`docs/research/seasonal_overlay_v1.md`). *Difference:* seasonal_overlay_v1 partitioned the year into months and the month into a turn-of-month window; a weekday partition is orthogonal to both. seasonal_placebo_v1's lesson - that the September effect was one episode - applies with equal force here, and the arm is therefore a *test* of the family's credibility, not a promotion candidate.

**Signal inputs.** the session's weekday (derived from the panel's dates)

**Data.** in repo: the panel's own date array. New downloads: none. Main window: full. OOS evaluable: all six -- dates only

**Plugin.** `targets_from_data` -- a weekday mask inside wants(); no engine change

**Arms (parameters frozen).**
- **A** -- target *= 0.75 on Mondays (production target otherwise)
- **B** -- target *= 0.90 on Mondays
- **C** -- mirror: target *= 0.75 on Fridays

**Honest prior.** low: the effect has decayed since the 1980s and the mirror arm is the honest control.

### IC24 Turn-of-quarter exposure tilt

**Mechanism.** Quarter ends are a heavier structural date than month ends: index rebalancing, quarterly window dressing and the institutional flow calendar concentrate in the last and first few sessions of a quarter, and the documented quarter-turn pattern in both equity and bond markets is separate from the month-turn pattern that was tested. The arm scales exposure in the three sessions spanning each quarter boundary and in the three sessions ending it, with both signs specified.

**Literature.** [Etula, Rinne, Suominen & Vaittinen, Dash for Cash: Monthly Market Impact of Institutional Liquidity Needs, RFS 2020](https://doi.org/10.1093/rfs/hhz106); [riskoff_rotation_v4 / monthly_defer_v1 (the tested boundary-effect studies this quarter-turn arm differs from)](docs/research/monthly_defer_v1.md)

**Closest tested study.** monthly_defer_v1 (YI-UFE month boundaries) and seasonal_overlay_v1 (turn-of-month) (`docs/research/monthly_defer_v1.md`). *Difference:* Both tested boundaries are *monthly* (12 dates/yr, and monthly_defer_v1 changed when downsizes fill, not the target). The turn of the quarter is four dates a year with a different flow mechanism (quarterly rebalancing and reporting), and this arm changes the target weight.

**Signal inputs.** the session's date

**Data.** in repo: the panel's own date array. New downloads: none. Main window: full. OOS evaluable: all six -- dates only

**Plugin.** `targets_from_data` -- a quarter-boundary mask inside wants()

**Arms (parameters frozen).**
- **A** -- target *= 0.75 in the 3 sessions ending the last session of Feb/May/Aug/Nov
- **B** -- target = min(0.98, target * 1.10) in the 3 sessions spanning the quarter turn (last session of the quarter and the two after)
- **C** -- mirror: target *= 0.75 in the 3 sessions after the quarter turn

**Honest prior.** low: a small flow effect, and the tested seasonal family's placebo result is the base rate.

### IC25 Pre-FOMC announcement drift overlay -- **rank 2**

**Mechanism.** Lucca & Moench document that the S&P 500 earns about 49 bp in the 24 hours *before* scheduled FOMC announcements - roughly 80% of the annual equity premium compressed into eight nights a year - while the announcement session itself carries no premium. The arm holds the full target (0.98, regardless of the vol target) on the pre-announcement session, held from the open of that session to the close of the announcement eve, and production's target otherwise. It is a single-window timing overlay, not a cycle model: IB17's five-week Cieslak-Morse-Vissing-Jorgensen cycle is in the bank and is a different empirical claim over different sessions.

**Literature.** [Lucca & Moench, The Pre-FOMC Announcement Drift, Journal of Finance 2015](https://doi.org/10.1111/jofi.12196); [Federal Reserve FOMC historical meeting calendars (scheduled dates, 1994-2026)](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)

**Closest tested study.** seasonal_overlay_v1 (calendar overlays on E2) and exec_delay_v1 (fill-day shifts) (`docs/research/seasonal_overlay_v1.md`). *Difference:* The tested calendar overlays are month-level (May-Oct, September) or month-turn windows; this is a one-session window around a scheduled event with a large, replicated literature effect. IB17 (a 5-week FOMC-cycle tilt) is in the bank, not tested, and it is a different claim (the cycle, not the pre-announcement 24h).

**Signal inputs.** the scheduled FOMC announcement dates 1994-2026 (public calendar, not market data)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (dates). New downloads: scheduled FOMC meeting/announcement dates, 1994-2026. Main window: full from 2000 (all announcements covered; 1994-1999 is beyond the main window). OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- the scheduled FOMC calendar starts 1994, so GSPC_1957_1999 and M1 are live only for 1994-1999 (the pre-1994 portion is neutral); M2 (1935-1956) is un-evaluable -> the reduced 5-window G7 is pre-registered, with the neutral-fill convention stated before the run

**Plugin.** `targets_from_data` -- an event-date mask inside wants(); no engine change (the fill stays next-open as production)

**Arms (parameters frozen).**
- **A** -- target = min(0.98, 1.0) on the session before each scheduled announcement; production target otherwise
- **B** -- target = min(0.98, 2.0 * production vt) on the pre-announcement session
- **C** -- mirror: target = 0.5 * production vt on the announcement session itself

**Honest prior.** medium-high: the largest documented calendar effect in equities, 8 sessions/yr, and the mirror arm distinguishes the drift from a pure exposure effect.

### IC26 Seasonal daylight (SAD) exposure tilt

**Mechanism.** Kamstra, Kramer & Levi show that the seasonal variation in the length of the night - a fall-weighted measure of daylight change - explains a large and replicated part of the autumn/winter return pattern through risk aversion, and the effect is identifiable separately from the months because it varies continuously with date and latitude. The arm scales the target by (1 - c * night_factor), where night_factor is the normalised night-length change for 40.7N peaking at the autumn equinox. Unlike the tested month buckets, the variable is continuous and externally parameterised, and the southern-hemisphere mirror gives a built-in placebo.

**Literature.** [Kamstra, Kramer & Levi, Winter Blues: A SAD Stock Market Cycle, American Economic Review 2003](https://doi.org/10.1257/000282803321455133); [seasonal_placebo_v1 (the tested seasonal family and its placebo verdict, the base rate for this arm)](docs/research/seasonal_placebo_v1.md)

**Closest tested study.** seasonal_overlay_v1 / seasonal_placebo_v1 (May-October half exposure, September cash) (`docs/research/seasonal_placebo_v1.md`). *Difference:* The tested seasonals are month buckets with no external variable; the SAD measure is continuous in the calendar, has a stated calibration (latitude, night length) and a falsifiable mirror - the same computation with the sign of the seasonal term inverted.

**Signal inputs.** the session's date and latitude 40.7N (computed, no download)

**Data.** in repo: the panel's own date array. New downloads: none. Main window: full. OOS evaluable: all six -- dates only; the measure is defined every session

**Plugin.** `targets_from_data` -- a computed seasonal factor inside wants()

**Arms (parameters frozen).**
- **A** -- target *= (1 - 0.15 * night_factor_t), night_factor in [0,1] peaking at the autumn equinox
- **B** -- same with coefficient 0.30
- **C** -- mirror sign: target *= (1 + 0.15 * night_factor_t) capped at the production target * 1.0 (an exposure control)

**Honest prior.** low: the tested seasonal family failed its placebo, so this enters as a test with the same prior.

## 7.5 Cross-asset relative signals (4 ideas)

### IC27 Credit-equity relative strength (HYG vs QQQ)

**Mechanism.** High-yield credit leads equity at turning points: credit investors are first-loss and better informed about the marginal issuer, and the high-yield market prices default risk continuously. When the equity vote is on while r_HYG(126) - r_QQQ(126) is negative, the equity market is priced for growth the credit market does not confirm - the classic internal divergence before 2007 and 2020. Both inputs are tradable prices, so the signal is implementable in the same account that trades the strategy.

**Literature.** [Gilchrist & Zakrajsek, Credit Spreads and Business Cycle Fluctuations, AER 2012](https://doi.org/10.1257/aer.102.4.1692); [Bhattacharya, Berliner & Rosen, The Lead-Lag Relationship Between Credit and Equity Markets, 2018](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3149576)

**Closest tested study.** breadth_vote_v1 (QQQ/SPY/IWM votes) and riskoff_rotation_v1-v4 (defensive rotation) (`docs/research/breadth_vote_v1.md`). *Difference:* breadth_vote_v1's panel is other *equity indices* and its vote is each index's own trend; the closest is IB21 (IEF-vs-QQQ momentum spread) which is in the bank, not tested. Here the second instrument is a credit vehicle, whose price embeds spread plus rates plus default experience - a different asset class measuring the same event from the other side.

**Signal inputs.** HYG daily total-return close (new download); the arm's own instrument close (QQQ / local index)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ). New downloads: HYG (iShares iBoxx $ High Yield Corporate Bond ETF, 2007-04-11 ->). Main window: partial: HYG starts 2007-04, so the relative-strength state is live only from 2007 (fixed convention: before that the state is treated as neutral and weight is unchanged). OOS evaluable: M3, M4, M5 -- HYG 2007+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7 and a holdout_insufficient risk

**Plugin.** `targets_from_data` -- data.extra('hyg') inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.75 while r_HYG(126) - r_QQQ(126) < -3 pp (12-month price returns)
- **B** -- weight *= 0.50 while r_HYG(126) - r_QQQ(126) < -6 pp
- **C** -- early-path entry additionally requires r_HYG(126) - r_QQQ(126) >= 0

**Honest prior.** low-medium: the mechanism is documented, but the arm is blind for the two worst episodes (2000-02) and the OOS coverage is 3 windows.

### IC28 Cyclical-vs-defensive sector ratio

**Mechanism.** The ratio of a cyclicals basket to a defensives basket (XLY+XLI+XLB vs XLP+XLU+XLV, equal-weighted, daily) is the market's own risk-appetite thermometer at the sector level: leadership rotates before the index level does, and the ratio's 126-session return measures whether money is moving *within* the market toward risk. Because it is a ratio of two internal baskets, it removes the level of the index and the direction of rates; what remains is composition.

**Literature.** [Hong, Torous & Valkanov, Do industries lead stock markets?, JFE 2007](https://doi.org/10.1016/j.jfineco.2005.09.010); [Stoval, Sector rotation and the business cycle, 2011](https://www.cmegroup.com/education/files/sector-rotation-and-the-business-cycle.pdf)

**Closest tested study.** breadth_vote_v1 (three equity indices) and riskoff_rotation_v1-v4 (sector ETFs as risk-off *holdings*) (`docs/research/breadth_vote_v1.md`). *Difference:* breadth_vote_v1 counted indices above their own SMAs (a vote, and SPY agreed with QQQ 91% of sessions); riskoff_rotation used sector ETFs as the *asset to hold*. This uses the cross-sector relative-strength *ratio* as a state with no holding decision, and IB20 (SMH-vs-QQQ RS) in the bank is a single-pair variant of the same idea.

**Signal inputs.** the nine sector SPDR total-return closes (registered extra keys sec_*)

**Data.** in repo: data/historical/batch_screen_v1_xl*.csv (1998-12-22 ->; extra keys sec_xlb..sec_xly). New downloads: none. Main window: full (the SPDRs start 1998-12-22, before the main window). OOS evaluable: M3, M4, M5 -- sector SPDRs are absent on GSPC_1957_1999, M1 and M2 -> 3 applicable windows; reduced 3-window G7

**Plugin.** `targets_from_data` -- data.extra('sec_xl*') inside wants()

**Arms (parameters frozen).**
- **A** -- 3-of-4 vote {SMA200, TSM252, DON126, r_cyclicals/defensives(126) > 0}, on iff >= 3
- **B** -- weight *= 0.75 while r_cyclicals/defensives(126) < 0
- **C** -- early-path entry additionally requires r_cyclicals/defensives(126) > 0

**Honest prior.** medium-low: a genuinely different (composition) input; the sector baskets are highly correlated with the index so the marginal information may be small.

### IC29 Emerging-market relative strength (EEM vs SPY)

**Mechanism.** EM equities outperform the US only when global liquidity is ample and the dollar is benign, so the EEM-vs-SPY relative-strength state is a direct read of the global risk-appetite cycle that does not depend on a US-only series; it is the equity-market twin of IC22's dollar state and, unlike the copper/gold ratio (IB23, in the bank), it is directly tradable and priced continuously. The arm uses it as a veto and as a fourth vote.

**Literature.** [Rey, Dilemma not Trilemma: The Global Financial Cycle and Monetary Policy Independence, 2015](https://doi.org/10.3386/w21162); [Miranda-Agrippino & Rey, US Monetary Policy and the Global Financial Cycle, RFS 2020](https://doi.org/10.1093/rfs/hhaa040)

**Closest tested study.** riskoff_rotation_v1-v4 (country/region ETFs as holdings) and relstrength_v1 (QLD vs SSO rotation) (`docs/research/riskoff_rotation_v4.md`). *Difference:* relstrength_v1 rotated between two *US* leveraged funds; riskoff_rotation_v4 used country ETFs as the defensive asset selected by momentum, after tax it was not run. Using an EM index's relative strength as an on/off *state* is a global-cycle signal, not a holding choice.

**Signal inputs.** EEM and SPY daily total-return closes (EEM is a new download; SPY is in the repo)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (SPY). New downloads: EEM (iShares MSCI Emerging Markets ETF, 2003-04-14 ->). Main window: full from 2003-04 (neutral before, a fixed convention). OOS evaluable: M3, M4, M5 -- EEM 2003+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7

**Plugin.** `targets_from_data` -- data.extra('eem') inside wants()

**Arms (parameters frozen).**
- **A** -- 3-of-4 vote {SMA200, TSM252, DON126, r_EEM(126) - r_SPY(126) > 0}, on iff >= 3
- **B** -- weight *= 0.75 while r_EEM(126) - r_SPY(126) < -5 pp
- **C** -- early-path entry additionally requires r_EEM(126) - r_SPY(126) > 0

**Honest prior.** low-medium: a clean global-cycle input, but the state is dominated by a handful of regimes and coverage is 3 windows.

### IC30 Stock-bond correlation regime

**Mechanism.** The sign of the stock-bond correlation decides whether duration diversifies equity (negative, 2000-2020) or compounds it (positive, 2022-2023), and the sign flips are persistent enough to trade. A rolling 60-session correlation of QQQ and IEF returns above +0.30 marks a regime in which the strategy's risk is undiversifiable inside the portfolio and the 2x equity position is the whole of the risk budget; the arm scales the target down there. The stock-bond correlation is a *second-moment cross-statistic* - no tested arm computes one.

**Literature.** [Ilmanen, How Do Bonds Perform in Different Macro Environments?, 2011; Campbell, Sunderam & Viceira, Inflation Bets or Deflation Hedges?, 2017](https://doi.org/10.3386/w21318); [Baele, Bekaert, Inghelbrecht & Wei, Flights to Safety, RFS 2020](https://doi.org/10.1093/rfs/hhz055)

**Closest tested study.** leverage_frontier_v1 (BOND10 as a risk-off asset) and riskoff_rotation_v1-v4 (bond-leg momentum) (`docs/research/riskoff_rotation_v1.md`). *Difference:* The tested bond legs answer 'should the off-state be bonds' (a holding question, using bond *momentum*). The correlation regime answers 'is the equity risk diversifiable at all' - a joint second-moment state, and IB21 (the IEF/QQQ momentum spread) in the bank is again a first-moment variant.

**Signal inputs.** IEF daily total-return close (extra key ief), the arm's own instrument close

**Data.** in repo: data/historical/batch_screen_v1_ief.csv (2002-07-30 ->). New downloads: none. Main window: full. OOS evaluable: M3, M4, M5 -- IEF 2002+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7

**Plugin.** `targets_from_data` -- data.extra('ief') inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= 0.75 while corr60(QQQ, IEF) > +0.30
- **B** -- weight *= 0.50 while corr60(QQQ, IEF) > +0.50
- **C** -- 3-of-4 vote {SMA200, TSM252, DON126, corr60 <= +0.30}, on iff >= 3

**Honest prior.** low-medium: the regime is real but rare (2022 and 2004 briefly) and the correlation is estimated from 60 sessions.

## 7.6 Off-state holdings beyond bills / bonds / gold (8 ideas)

### IC31 Off-state in investment-grade credit (LQD)

**Mechanism.** While the trend vote is off the tested holdings are bills (S1/S2_10, cash_yield), Treasuries (IEF/BOND10) and gold - each of which pays either a taxable interest flow (the bill sweep, taxed annually at the progressive rate with no Yi-UFE indexation) or no income at all. Investment-grade credit (LQD) pays a spread plus a coupon that accrues *inside the price*: the return is a capital gain in the Turkish system, indexation-eligible and realised only when the sleeve is sold, which is the exact asymmetry the bill sweep lacks. The mechanism is the credit risk premium plus the tax character of the return.

**Literature.** [Asvanunt & Richardson, The Credit Risk Premium, Journal of Fixed Income 2017](https://doi.org/10.3905/jfi.2017.27.3.006); [r2_tbill_combo_v1 (the tested bill-sleeve precedent this arm differs from)](docs/research/r2_tbill_combo_v1.md)

**Closest tested study.** tbill_sweep_v1 (S1/S2_10 BIL sleeve) and r2_tbill_combo_v1 (the sleeve after tax: it lost) (`docs/research/r2_tbill_combo_v1.md`). *Difference:* r2_tbill_combo_v1's sleeve (BIL) carries essentially no price risk and its return is taxed as interest; it lost after tax because the sleeve's gains are realised early. LQD is a *priced* asset with credit risk and a coupon in the price - different asset, different tax treatment of the same off-state slot. IB25/IB26 (SHY/IEF off-state) are in the bank and are Treasury analogues, not credit.

**Signal inputs.** LQD daily total-return close (new download); the arm's own instrument close (for the vote)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QLD*/QQQ for the vote). New downloads: LQD (iShares iBoxx $ Investment Grade Corporate Bond ETF, 2002-07-30 ->). Main window: partial: LQD starts 2002-07, so the 2000-2002 bear market - the most important off-state stretch in the window - is not covered by the arm (cash is held there by the fixed convention). OOS evaluable: M3, M4, M5 -- LQD 2002+ -> only M3/M4/M5 -> 3 applicable windows; reduced 3-window G7

**Plugin.** `kernel_option` -- a priced off-state asset is a second leg (like IB25's SHY sleeve), which the single-instrument account cannot hold; pre-tax/OOS/ETF stages can run as a blended synthetic instrument, the after-tax stage needs the kernel. NeedsKernel: state-dependent leg

**Arms (parameters frozen).**
- **A** -- while the vote is off, hold 0.98 in LQD; production target otherwise *(kernel: all_stages)*
- **B** -- while the vote is off, hold the residual (0.98 - w) in LQD (paired with production's cash on the rest) *(kernel: all_stages)*
- **C** -- hold 0.98 LQD while off AND LQD > its own SMA200, else cash *(kernel: all_stages)*

**Honest prior.** low-medium: the tax-character argument is the novel part; the missing 2000-02 coverage is a real handicap.

### IC32 Off-state in high-yield credit (HYG)

**Mechanism.** The same off-state slot at the low-rated end: HYG carries roughly 0.5-0.6 equity beta and a coupon that dominates its return in recoveries, so holding it while the vote is off converts the off-state into a partial risk-on position while realising the return as a *price* gain (indexation-eligible) rather than as interest. The counter-argument is precisely that it fires after equity has fallen, when credit is at its most fragile; the arm-B gate (HYG above its own SMA200) and the arm-C gate (the momentum leg still positive) are the two specified mitigations.

**Literature.** [Asvanunt & Richardson, The Credit Risk Premium, Journal of Fixed Income 2017](https://doi.org/10.3905/jfi.2017.27.3.006); [FRED BAMLH0A0HYM2 (the spread the high-yield premium is paid for)](https://fred.stlouisfed.org/series/BAMLH0A0HYM2)

**Closest tested study.** tbill_sweep_v1 / r2_tbill_combo_v1 (bill sleeve, after tax it lost) and leverage_frontier_v1 (GOLD as risk-off, +2.3 pp CAGR but +4.3 pp DD) (`docs/research/leverage_frontier_v1.md`). *Difference:* Gold was the only tested off-state asset with market risk and it added drawdown; the tested bill sleeve had none. HYG is a *credit* asset with an explicit premium and a coupon, and neither gold nor bills share its payoff shape.

**Signal inputs.** HYG daily total-return close (new download); the arm's own instrument close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv. New downloads: HYG (iShares iBoxx $ High Yield Corporate Bond ETF, 2007-04-11 ->). Main window: partial: HYG starts 2007-04 (cash before, fixed convention). OOS evaluable: M3, M4, M5 -- 2007+ -> M3/M4/M5 only; reduced 3-window G7

**Plugin.** `kernel_option` -- priced off-state asset = a second leg with its own FIFO book; NeedsKernel: state-dependent leg

**Arms (parameters frozen).**
- **A** -- while off, hold 0.98 HYG *(kernel: all_stages)*
- **B** -- hold 0.98 HYG while off AND HYG > its own SMA200, else cash *(kernel: all_stages)*
- **C** -- hold 0.98 HYG while off AND the momentum leg (TSM252 of the arm's own close) > 0, else cash *(kernel: all_stages)*

**Honest prior.** low: high-yield beta during the off-state is exactly the state in which credit suffers; the gates are what make it a test rather than a bet.

### IC33 Off-state in TIPS (TIP)

**Mechanism.** The bill sleeve's interest is taxed annually at the progressive rate with no Yi-UFE indexation, while an inflation-linked bond fund accumulates the inflation compensation *into price*, which is indexation-eligible and realised only on sale. For a Turkish resident whose relief is measured by Yi-UFE, holding inflation-linked paper in the off-state hedges the tax base itself (the lira's realised inflation) rather than only choosing a return; the asset also has negative equity beta in disinflationary bear markets (2008, 2020) and positive beta to exactly the risk that inflates the tax bill.

**Literature.** [Campbell, Shiller & Viceira, Understanding Inflation-Indexed Bond Markets, Brookings 2009](https://doi.org/10.1353/eca.0.0043); [after_tax_tr_v1 (the Yi-UFE indexation mechanics this arm exploits)](docs/research/after_tax_tr_v1.md)

**Closest tested study.** IB26 (curve-conditioned IEF off-state, in the bank) and leverage_frontier_v1 (BOND10/gold risk-off) (`docs/research/leverage_frontier_v1.md`). *Difference:* Every tested off-state asset is nominal (bills, BOND10/IEF, gold). TIPS are the only instrument whose return *is* the inflation that the Turkish indexation relief measures, so the choice interacts with the tax base rather than only with the return.

**Signal inputs.** TIP daily total-return close (new download); the arm's own instrument close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv. New downloads: TIP (iShares TIPS Bond ETF, 2003-12-05 ->). Main window: partial: TIP starts 2003-12 (cash before, fixed convention). OOS evaluable: M3, M4, M5 -- 2003+ -> M3/M4/M5 only; reduced 3-window G7

**Plugin.** `kernel_option` -- priced off-state asset = a second leg; NeedsKernel: state-dependent leg

**Arms (parameters frozen).**
- **A** -- while off, hold 0.98 TIP *(kernel: all_stages)*
- **B** -- while off, hold the residual (0.98 - w) in TIP *(kernel: all_stages)*
- **C** -- hold 0.98 TIP while off AND the 126-session change of the breakeven (T10YIE) > 0, else cash *(kernel: all_stages)*

**Honest prior.** low: the tax-character mechanism is the strongest part; TIPS' real return over 2004-2021 was near zero.

### IC34 Off-state in defensive sectors (XLU / XLP)

**Mechanism.** Utilities and staples are the equity-side substitutes for bonds: regulated or inelastic cash flows, high payout, low beta, and the dividend accrues in price (indexation-eligible) instead of as taxable interest. The distinction from riskoff_rotation_v1-v4 - which *rotated among* sector and country ETFs by trailing return and failed its drawdown gate on 5 of 21 instruments - is that this arm holds one fixed defensive sector with no selection rule and no rotation turnover: the riskoff_rotation failure was a selection failure.

**Literature.** [Blitz, Huij & Martens, Residual momentum / defensive equity premium, 2011; Frazzini & Pedersen, Betting Against Beta, JFE 2014](https://doi.org/10.1016/j.jfineco.2013.10.005); [riskoff_rotation_v4 (the tested sector-rotation precedent)](docs/research/riskoff_rotation_v4.md)

**Closest tested study.** riskoff_rotation_v1-v4 (sector/country ETFs as the defensive holding, selected by momentum) (`docs/research/riskoff_rotation_v4.md`). *Difference:* The tested family *selects* among defensives by trailing return every month (13.81% vs 14.13% under doubled cost in v2; DD violations in v4). This arm fixes the asset in advance and holds it whenever the vote is off - no selection, no rotation.

**Signal inputs.** XLU / XLP daily total-return closes (registered extra keys sec_xlu, sec_xlp; the 1x ETF is the holding)

**Data.** in repo: data/historical/batch_screen_v1_xlu.csv, _xlp.csv (1998-12-22 ->). New downloads: none. Main window: full (SPDRs start 1998-12-22). OOS evaluable: M3, M4, M5 -- SPDRs absent on GSPC_1957_1999/M1/M2 -> 3 applicable windows; reduced 3-window G7

**Plugin.** `kernel_option` -- a priced off-state asset needs a state-dependent leg/second book; NeedsKernel: state-dependent leg

**Arms (parameters frozen).**
- **A** -- while off, hold 0.98 XLU *(kernel: all_stages)*
- **B** -- while off, hold 0.98 XLP *(kernel: all_stages)*
- **C** -- while off, hold 0.49 XLU + 0.49 XLP (a fixed equal split, no selection) *(kernel: all_stages)*

**Honest prior.** low: defensive equity in a bear market still falls; the tax character is the only edge.

### IC35 Off-state in gold miners (GDX)

**Mechanism.** Gold itself was tested as the risk-off asset and lost its out-of-sample test (gold_riskoff_oos_v1). Gold *miners* are a different instrument: operating leverage to the gold price (beta to bullion around 1.5-2) plus equity-market participation, so holding GDX while off is a leveraged gold position with an equity kicker in the recovery - a payoff shape no tested off-state asset has, and one whose return is again a price gain rather than a taxable coupon.

**Literature.** [Baur & Lucey, Is Gold a Hedge or a Safe Haven?, Financial Review 2010](https://doi.org/10.1111/j.1540-6288.2010.00254.x); [gold_riskoff_oos_v1 (the tested bullion risk-off arm and its OOS failure)](docs/research/gold_riskoff_oos_v1.md)

**Closest tested study.** gold_riskoff_oos_v1 (gold as risk-off, failed every OOS gate) and leverage_frontier_v1 (GOLD risk-off, +2.3 pp CAGR / +4.3 pp DD) (`docs/research/gold_riskoff_oos_v1.md`). *Difference:* Bullion and miners share a driver but not a beta: the tested arms were unconditional switches into a non-yielding metal; GDX adds equity-market beta and operating leverage, so its behaviour in a 2008-style crash is materially different (it fell harder and recovered faster).

**Signal inputs.** GDX daily total-return close (new download)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv. New downloads: GDX (VanEck Gold Miners ETF, 2006-05-22 ->). Main window: partial: GDX starts 2006-05 (cash before, fixed convention). OOS evaluable: M3, M4, M5 -- 2006+ -> M3/M4/M5 only; reduced 3-window G7

**Plugin.** `kernel_option` -- priced off-state asset = a state-dependent leg; NeedsKernel: state-dependent leg

**Arms (parameters frozen).**
- **A** -- while off, hold 0.98 GDX *(kernel: all_stages)*
- **B** -- while off, hold 0.49 GDX + 0.49 cash (half size) *(kernel: all_stages)*
- **C** -- hold 0.98 GDX while off AND GDX > its own SMA200, else cash *(kernel: all_stages)*

**Honest prior.** low: the closest tested analogue (gold) failed every OOS gate; miners' extra beta cuts both ways.

### IC36 Off-state risk-parity blend (GLD / IEF / SHY) with a monthly rescale -- **rank 14**

**Mechanism.** Rather than *choosing* one defensive asset by trailing momentum (riskoff_rotation_v1-v4, whose drawdown gate failed on 5 of 21 ETFs and which lost after tax) or holding cash, hold a fixed inverse-volatility blend of the defensive assets already on file: weights proportional to 1/vol20 of GLD, IEF and SHY, normalised to 0.98, recomputed once a month. The rule is state-free, so it adds no selection risk; the monthly rescale keeps turnover at a fraction of the tested rotation's, and the return is the average defensive premium with the asset-level noise diversified away.

**Literature.** [Asness, Frazzini & Pedersen, Leverage Aversion and Risk Parity, FAJ 2012](https://doi.org/10.2469/faj.v68.n1.1); [riskoff_rotation_v1-v4 (the tested momentum-selected alternative)](docs/research/riskoff_rotation_v4.md)

**Closest tested study.** riskoff_rotation_v1-v4 (momentum selection among defensives; failed doubled cost in v2 and the DD gate in v4) and leverage_frontier_v1 (single-asset risk-off choices) (`docs/research/riskoff_rotation_v3.md`). *Difference:* The tested family picks the best-looking defensive every month (12 selections/yr, 11%+ turnover); this holds *all* of them at inverse-vol weights and touches the allocation four times a year at most. No tested arm uses inverse-volatility weights.

**Signal inputs.** GLD, IEF, SHY daily total-return closes (registered extra keys)

**Data.** in repo: data/historical/batch_screen_v1_gld.csv (2004-11-18 ->), _ief.csv, _shy.csv (2002-07-30 ->). New downloads: none. Main window: partial: the blend is live from 2004-11 (before that the arm holds cash while off, a fixed convention). OOS evaluable: M3, M4, M5 -- GLD/IEF/SHY 2002-2004+ -> M3/M4/M5 only; reduced 3-window G7

**Plugin.** `kernel_option` -- a priced off-state blend is a second leg (three instruments, monthly rescale); NeedsKernel: state-dependent multi-leg holding

**Arms (parameters frozen).**
- **A** -- while off, hold 0.98 * (1/vol20_i) / sum(1/vol20_j) in each of {GLD, IEF, SHY}, recomputed on the first session of each month *(kernel: all_stages)*
- **B** -- the same over {GLD, IEF, SHY, XLU} *(kernel: all_stages)*
- **C** -- the same as A but the whole blend scaled by production vt (0.98 -> vt) *(kernel: all_stages)*

**Honest prior.** medium: state-free, low turnover, diversified; the defensive premium itself was tested only through selection rules.

### IC37 Real-yield-gated Treasury off-state (IEF)

**Mechanism.** The tested Treasury off-state (BOND10/IEF, leverage_frontier_v1) is unconditional; the *reason* to hold duration rather than bills is being paid a positive real yield. Holding 7-10y Treasuries only while the 10-year real yield (DGS10 minus the 10-year breakeven) is above +0.25% means the off-state asset is compensated; below that (2011-2021) the sleeve is bills. The conditioning variable is the *level of the real yield* - an asset-valuation condition - not the curve slope that IB26 (in the bank) uses, a macro-cycle condition.

**Literature.** [Campbell, Sunderam & Viceira, Inflation Bets or Deflation Hedges? The Changing Risks of Nominal Bonds, 2017](https://doi.org/10.3386/w21318); [leverage_frontier_v1 (the tested unconditional BOND10 risk-off sleeve)](docs/research/leverage_frontier_v1.md)

**Closest tested study.** leverage_frontier_v1 (BOND10 risk-off, failed the bootstrap tail gate) and r2_tbill_combo_v1 (the bill sleeve after tax) (`docs/research/leverage_frontier_v1.md`). *Difference:* The tested sleeves are unconditional; the only conditional Treasury arm in the record is in the *idea bank* (IB26, curve-conditioned) and has never been screened. This conditions on the real yield level, a different state variable.

**Signal inputs.** IEF (extra key ief), DGS10 (extra key dgs10), T10YIE (extra key t10yie)

**Data.** in repo: data/historical/batch_screen_v1_ief.csv, _dgs10.csv, _t10yie.csv. New downloads: none. Main window: partial: live from 2003-01 (breakeven start); cash while off before that. OOS evaluable: M3, M4, M5 -- 2003+ -> M3/M4/M5; reduced 3-window G7

**Plugin.** `kernel_option` -- priced off-state asset = a state-dependent leg; NeedsKernel: state-dependent leg

**Arms (parameters frozen).**
- **A** -- while off, hold 0.98 IEF if real10y = DGS10 - T10YIE > +0.25%, else cash *(kernel: all_stages)*
- **B** -- the same with the threshold at +1.00% *(kernel: all_stages)*
- **C** -- while off, hold the residual (0.98 - w) in IEF under the +0.25% condition *(kernel: all_stages)*

**Honest prior.** low-medium: the condition is well motivated, but the unconditional version already failed the tail gate.

### IC38 Off-state in 1x QQQ (never fully out)

**Mechanism.** The off-state's cost is the entire equity risk premium of the flat stretches; a 1x index position keeps the price exposure that is the strategy's own tax base (a capital gain, indexation-eligible) instead of a taxable interest flow, and it removes the need for the E2/off distinction to be sharp. It is the unconditional limit of the early path: hold a vol-targeted 1x index position whenever the vote is off, rather than the conditional half weight R2 holds. early_qqq_v1 tested moving the *early leg* from half-QLD to QQQ and lost - this holds an *additional* 1x position in the states where the early leg does not fire at all.

**Literature.** [Frazzini & Pedersen, Betting Against Beta (the cost of being flat), JFE 2014](https://doi.org/10.1016/j.jfineco.2013.10.005); [early_qqq_v1 (moving the early leg to QQQ: -0.11/-0.05/-0.01 pp after tax)](docs/research/early_qqq_v1.md)

**Closest tested study.** early_qqq_v1 (the early leg in QQQ/SPY/SSO) and fast_reentry_v1/v2 (R2's conditional half-weight early path) (`docs/research/early_qqq_v1.md`). *Difference:* Both tested arms keep the early *leg* conditional (three AND-gates) and at half weight. This holds an unconditional 1x position whenever the vote is off, including the deep-bear states where the early gates fail - the states where R2 is fully flat.

**Signal inputs.** the arm's own instrument close; the local index as the 1x leg

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ); data/historical/ewma_band_v1_index.csv (OOS local indices). New downloads: none. Main window: full. OOS evaluable: all six -- the 1x leg is the window's own index (fe_signals.synthetic_2x at gearing 1 / the local index), so all six windows are evaluable - the best OOS coverage of the six off-state ideas

**Plugin.** `kernel_option` -- on-state 2x and off-state 1x is a per-session leg switch; NeedsKernel: state-dependent leg (if the two-leg kernel's second leg can be sized to zero, it is expressible as two_leg with second_symbol QQQ)

**Arms (parameters frozen).**
- **A** -- while off, hold 0.98 in the 1x index (unlevered); production target otherwise *(kernel: all_stages)*
- **B** -- while off, hold 0.49 in the 1x index *(kernel: all_stages)*
- **C** -- while off, hold min(1, 0.35/vol20_of_the_1x_leg) in the 1x index (a vol-targeted off-state) *(kernel: all_stages)*

**Honest prior.** low: it is extra exposure in the worst states (the failure mode exposure_k_v1 and leverage_frontier_v1 kept recording), but the tax character and the 6-window OOS coverage make it worth one screen.

## 7.7 On-state instrument choice (2 ideas)

### IC39 Margin-financed QQQ instead of QLD (broker leverage) -- **rank 8**

**Mechanism.** QLD's 2x exposure is embedded leverage: the fund pays an expense ratio (about 0.95%) on the whole position plus a swap spread over the bill rate on the borrowed half, continuously and in all states. Financing the same 2x exposure at the broker (a Reg-T margin loan on QQQ) costs the broker's margin rate on the borrowed half only while it is borrowed, has no expense ratio, and receives the index's dividends on the *whole* position instead of on the unlevered half. instrument_cost_v1 tested TQQQ - another fund - and found its expense saving smaller than its financing cost; the broker-margin route, where the cost is a published rate rather than a fund's embedded swap, was never tested. At a 2x target the candidate saving is the expense ratio on the full position (about 0.9 pp/yr) plus the swap-to-margin spread on the borrowed half; the cost is margin-call risk (tested by arm B's exposure cap) and that the loan is repriced daily.

**Literature.** [instrument_cost_v1 (TQQQ at 2/3 weight vs QLD: the tested fund-level alternative)](docs/research/instrument_cost_v1.md); [Frazzini & Pedersen, Betting Against Beta (the cost of the leverage constraint), JFE 2014](https://doi.org/10.1016/j.jfineco.2013.10.005)

**Closest tested study.** instrument_cost_v1 (TQQQ vs QLD) and early_qqq_v1 (unlevered legs) (`docs/research/instrument_cost_v1.md`). *Difference:* instrument_cost_v1 compared two *funds* (each with an expense ratio and a swap) and finished by rejecting the alternative; this compares a fund with a *broker loan*. The sign of the difference is not known in advance because the margin rate is an account-level published number, not a fund's embedded cost - and the arm is a leveraged QQQ position, so it also changes the dividend and reset mechanics.

**Signal inputs.** QQQ daily close (the vote and the traded instrument); the frozen broker margin rate

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ, SPY, QLD, VOO); data/historical/stress_robustness_v1_dtb3.csv (financing); the live broker's published margin rate, frozen at the current value and stressed +/-2 pp in arm C. New downloads: none. Main window: full. OOS evaluable: all six -- the OOS windows use the index itself at gearing 1; a margin loan at the broker rate over TB3MS is available there too, so all six windows remain evaluable

**Plugin.** `kernel_option` -- the account must carry a loan balance and accrue daily interest on it; fe_sim has no margin modelling, so this is a kernel option (all stages). NeedsKernel: a cash loan inside the account

**Arms (parameters frozen).**
- **A** -- hold 2x exposure as wt/2 in QQQ plus a cash loan of wt/2 of NAV at the frozen margin rate (no expense ratio, dividends on the full position) *(kernel: all_stages)*
- **B** -- as A but the loan is capped so gross exposure never exceeds 1.6x (the margin-call mitigation) *(kernel: all_stages)*
- **C** -- as A with the margin rate stressed +2 pp (the cost-sensitivity arm) *(kernel: all_stages)*

**Honest prior.** medium: a deterministic cost arbitrage between two ways of buying the same exposure (the mechanism IB27 was ranked #1 for), with a published input and an explicit cost stress.

### IC40 S&P 500 2x on-state instrument (SSO / synthetic 2x SPY)

**Mechanism.** The signal is Nasdaq-based and the instrument is Nasdaq 2x, so the entire concentration - including the index that fell 83% in 2000-02 - sits in one market. A 2x S&P position (the real SSO, or a synthetic daily-reset 2x built from SPY with the engine's own recurrence) keeps the leverage and the fill convention but changes the underlying to the broader index, whose sector weights are less concentrated in long-duration technology. relstrength_v1 *rotated* between QLD and SSO by relative strength and lost; trend_robustness_v2 *split* the position by two index votes and also lost. The untested question is simpler: is the on-state instrument the right underlying at all, given that the vote already runs on QQQ?

**Literature.** [relstrength_v1 (QLD vs SSO rotation: loses 1.2-4.1 pp CAGR) and trend_robustness_v2 (QLD+SSO split)](docs/research/relstrength_v1.md); [Goyal & Jegadeesh, Comovement, Journal of Finance 2018 (index membership and concentration)](https://doi.org/10.1111/jofi.12595)

**Closest tested study.** relstrength_v1 (rotation) and trend_robustness_v2 (index-vote split) and early_qqq_v1 (SSO as the early leg) (`docs/research/relstrength_v1.md`). *Difference:* The tested arms change the *allocation rule* between two leveraged funds or move only the early leg's instrument; none replaces the on-state instrument outright while holding the signal fixed. The measured failure of the rotation does not answer this question, since a rotation must also get the timing right.

**Signal inputs.** QQQ (or the local index) daily close for the vote; SPY daily close for the synthetic 2x leg

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ, SPY); the synthetic 2x recurrence of fe_signals.synthetic_2x applied to SPY. New downloads: SSO (ProShares Ultra S&P500, 2006-06-21 ->) for the real-fund cross-check. Main window: full via the synthetic 2x SPY leg. OOS evaluable: all six -- in the OOS windows the on-state leg is already 2x of that window's own index, so this arm degenerates to the control there; the informative stages are the main window, the 21-ETF stage and the real-fund cross-check - state that in the pre-registration as a reduced OOS gate

**Plugin.** `kernel_option` -- the panel offers only the window's own underlying at 2x/1x; an SPY-based 2x leg is a new leg construction (data plumbing + kernel). NeedsKernel: a second leg family

**Arms (parameters frozen).**
- **A** -- on-state instrument = synthetic daily-reset 2x of SPY; the vote stays QQQ's 2-of-3 *(kernel: all_stages)*
- **B** -- as A and the early leg is also 2x SPY (unconditional early instrument change) *(kernel: all_stages)*
- **C** -- as A but the vote is the 2-of-3 computed on the S&P index itself (signal and instrument aligned) *(kernel: all_stages)*

**Honest prior.** medium-low: a real concentration question with a clean test, but the tested rotation's failure is an unfavourable base rate.

## 7.8 Two-instrument allocations (two-leg kernel) (6 ideas)

### IC41 Dual-core: QLD + SSO, one FIFO book per index -- **rank 9**

**Mechanism.** Split the target exposure between a Nasdaq-2x book and an S&P-2x book. Two equally leveraged but 0.9-correlated books dilute the single-index concentration (the 2000-02 problem) while keeping the leverage, and - the reason a two-leg kernel is being built - every downsize can be routed to the book whose FIFO head carries the smaller gain, so the tax on the trims that carry about 91% of the realised taxable gain falls further than K's band alone can achieve. The routing is bounded by a per-session route cap, so the split cannot drift far from its target.

**Literature.** [relstrength_v1 (the tested QLD-vs-SSO rotation) and trend_robustness_v2 (the tested QLD+SSO index split)](docs/research/relstrength_v1.md); [Markowitz, Portfolio Selection, Journal of Finance 1952 (the diversification the two books buy)](https://doi.org/10.1111/j.1540-6261.1952.tb01525.x)

**Closest tested study.** relstrength_v1 (rotation between QLD and SSO by relative strength) and trend_robustness_v2 (the QLD+SSO split by index votes) (`docs/research/trend_robustness_v2.md`). *Difference:* The tested split sized the *second index* by its own trend vote and gave up 1.2-2.7 pp CAGR; the tested rotation switched instruments by relative strength. This holds both books simultaneously at a fixed 50/50 of the target and uses the second book for *tax routing* (IB28's mechanism, defined in the bank only for QQQ+QLD, never for a second index).

**Signal inputs.** QQQ and SPY daily closes (the vote and the second leg's own bar); the two books' FIFO lot records

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (QQQ, SPY). New downloads: SSO (ProShares Ultra S&P500, 2006-06-21 ->) for the real-fund cross-check. Main window: full (the S&P leg is synthetic 2x from SPY before 2006-06 and the real SSO afterwards in the cross-check). OOS evaluable: all six -- in the OOS windows the second index is not available, so the arm runs as the local index's 2x in one book - the OOS stage then measures only the routing effect; pre-register that as a reduced G7/G8 statement

**Plugin.** `two_leg` -- second_symbol=SSO; two books with separate FIFO queues and one cash account; pre-tax/OOS stages can run as a blended synthetic instrument (IB27's convention). NeedsKernel: two-leg account

**Arms (parameters frozen).**
- **A** -- each leg carries 0.5 of the target; downsizes fill in the leg with the lower USD FIFO gain fraction (route cap 10 pp), up-resizes in the leg further below its share *(kernel: all_stages)*
- **B** -- leg weights proportional to 1/vol20 of each index, same routing as A *(kernel: all_stages)*
- **C** -- as A, but the early path (half weight) is held only in the Nasdaq leg *(kernel: all_stages)*

**Honest prior.** medium: the diversification is real, the routing is IB28's mechanism (ranked #6 there), and the cost is a second book to maintain.

### IC42 Two-leg routing on the TRY-indexed (taxable) gain, not the USD gain -- **rank 5**

**Mechanism.** Turkish tax is levied on the TRY gain *after* Yi-UFE indexation, not on the USD gain: a lot bought when the lira was weak has an indexed gain far below its USD gain, and the gap grows with the inflation accumulated between the two legs' purchase months. K's band and IB28's routing both score lots by the USD FIFO gain fraction, which is a proxy for the taxable quantity that is wrong by exactly that accumulated indexation. With two legs the kernel can route a band-permitted downsize to the leg with the smaller *indexed-TRY* gain fraction, using each leg's FIFO head, the purchase month's Yi-UFE and the CBRT rate on the sale date.

**Literature.** [after_tax_tr_v1 (the Yi-UFE indexation and FIFO mechanics the routing scores)](docs/research/after_tax_tr_v1.md); [IB28 (USD-gain two-leg routing, in the bank) and gain_band_v1-v3 (the USD gain fraction defines the band widths)](docs/research/gain_band_v3.md)

**Closest tested study.** gain_band_v1-v3 / band_h2_combo_v1 (the USD gain fraction driving the band widths) and gain_band_v2's A1/A2 variants (`docs/research/gain_band_v3.md`). *Difference:* Every shipped realisation rule scores lots in USD; the tax is computed in TRY with indexation. The routing score is therefore a *different, tax-correct* quantity - and IB28's USD version, the closest idea in the bank, is not the same rule whenever TRY inflation moved between the legs' purchase dates (which is most of 2000-2026).

**Signal inputs.** the two legs' FIFO lot records (purchase dates), USD/TRY CBRT daily, Yi-UFE monthly

**Data.** in repo: data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv (after-tax stage inputs); data/historical/stress_robustness_v1_daily.csv (the two legs' prices). New downloads: none. Main window: full (the routing is inert before 2000 only in the sense that the after-tax calendar starts there). OOS evaluable: all six -- pre-tax, the routing is a pure lot-selection rule among two identical legs and changes nothing; its content is the after-tax stage - state that in the pre-registration (a reduced pre-tax gate: non-inferiority only)

**Plugin.** `two_leg` -- second_symbol=QQQ; the routing score reads both books' FIFO heads plus the TRY/Yi-UFE calendar. NeedsKernel: two-leg account + tax-aware fill decision

**Arms (parameters frozen).**
- **A** -- route downsizes to the leg with the smaller TRY-indexed FIFO gain fraction (route cap 10 pp); band widths = K's A1u on the USD fraction *(kernel: after_tax_stage_only)*
- **B** -- as A with a route cap of 5 pp *(kernel: after_tax_stage_only)*
- **C** -- as A, and the band widths are themselves keyed to the indexed-TRY fraction (0.5/3/8 pp at <=1%/<=5%/>5%) *(kernel: after_tax_stage_only)*

**Honest prior.** medium-high: it scores the lot by the quantity the tax is actually levied on, which no shipped rule or banked idea does.

### IC43 Two-leg loss-netting route

**Mechanism.** Turkish tax is assessed per calendar year on net TRY gains with no loss carry-forward, so a realised loss is only valuable if a gain is realised in the same year. With two books the kernel can realise a small loss in the cheaper-gain leg *in the same session* as a band-permitted downsize in the other, netting within the year - which tax_budget_v2's single-leg year-end pool fill could only do by selling the same instrument, and which tax_harvest_v1's year-end harvesting could not do at all because indexed lots are rarely at a loss by December. The route is bounded by a fraction of the downsize's TRY gain and by the requirement that a loss lot exists.

**Literature.** [tax_budget_v2 (the single-leg year-end loss-pool fill: fired 4 times in 26.5 years)](docs/research/tax_budget_v2.md); [tax_harvest_v1 H1 (year-end harvesting: indexed TRY lots are rarely at a loss)](docs/research/tax_harvest_v1.md)

**Closest tested study.** tax_budget_v2 (deliberate year-end loss realisation in one leg) and tax_harvest_v1 H1 (`docs/research/tax_budget_v2.md`). *Difference:* The tested arms realise losses at year-end in the same instrument, when nothing is being sold; this pairs the loss with a *concurrent* gain realisation in a different book, which is the only pairing that makes the loss usable under a no-carry-forward rule.

**Signal inputs.** the two legs' FIFO lot records; USD/TRY CBRT, Yi-UFE

**Data.** in repo: data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv; data/historical/stress_robustness_v1_daily.csv. New downloads: none. Main window: full. OOS evaluable: all six -- inert pre-tax (it only changes which lot is sold); the content is the after-tax stage - pre-register non-inferiority for the pre-tax stages

**Plugin.** `two_leg` -- second_symbol=QQQ; NeedsKernel: two-leg account + tax-aware fill decision

**Arms (parameters frozen).**
- **A** -- on a band-permitted downsize, additionally sell the loss leg for up to 25% of the downsize's realised TRY gain (only if a loss lot exists) *(kernel: after_tax_stage_only)*
- **B** -- the same with a 50% cap *(kernel: after_tax_stage_only)*
- **C** -- the same with a 25% cap, but only while the year-to-date realised TRY gain exceeds 100,000 TRY *(kernel: after_tax_stage_only)*

**Honest prior.** medium-low: the no-carry-forward rule makes the pairing valuable, but loss legs must exist at exactly the moment a downsize is permitted.

### IC44 Two-leg band widths keyed to the indexed (taxable) gain fraction

**Mechanism.** K's band widens with the USD gain fraction of the units a sell would consume (0.5/3/8 pp at <=1%/<=5%/>5%). The taxable quantity is the indexed TRY fraction, and for a lot held through high-inflation months the indexed fraction can be far below the USD one - so the shipped rule trims when the actual tax cost is small, and it is too tight for the lots whose indexed gain is large. With two books the widths can also be evaluated per leg, and the extra freedom is that each leg's own FIFO head sets its own band.

**Literature.** [gain_band_v3 (the USD-fraction band widths shipped as A1u)](docs/research/gain_band_v3.md); [IB39 (band widths by FX regime, in the bank): the closest variant, keyed to a market state rather than to the tax base](docs/research/band_h2_combo_v1.md)

**Closest tested study.** gain_band_v1-v3 / band_h2_combo_v1 (K) and ewma_band_v1 (fixed bands 2.5/5/10 pp) (`docs/research/band_h2_combo_v1.md`). *Difference:* K's score is the USD gain fraction; ewma_band's is the weight drift. Scoring the *indexed-TRY* fraction - the tax base after Yi-UFE relief - is a different state variable, and the per-leg evaluation is only possible with two books.

**Signal inputs.** each leg's FIFO lot record; USD/TRY CBRT, Yi-UFE

**Data.** in repo: data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv; data/historical/stress_robustness_v1_daily.csv. New downloads: none. Main window: full. OOS evaluable: all six -- the widths change the pre-tax fill schedule too (a different number of trims), so the pre-tax stages are evaluable - and must be, since G9 (turnover) applies

**Plugin.** `two_leg` -- second_symbol=QQQ; NeedsKernel: two-leg account + a per-leg band rule

**Arms (parameters frozen).**
- **A** -- band widths 0.5/3/8 pp at an indexed-TRY FIFO gain fraction of <=1%/<=5%/>5% (K with the score swapped), evaluated per leg *(kernel: all_stages)*
- **B** -- as A plus a fourth width step: 12 pp above a 15% indexed fraction *(kernel: all_stages)*
- **C** -- widths 0.5/3/8 pp on max(USD fraction, indexed fraction) per leg (the conservative combination) *(kernel: all_stages)*

**Honest prior.** medium: a one-variable change to the shipped rule using a better score for the same decision; the indexed fraction is small more often, so turnover may rise.

### IC45 Permanent small gold sleeve funded by a lower equity target (two-leg)

**Mechanism.** Gold as a *state-dependent* risk-off asset was tested and its OOS test failed (gold_riskoff_oos_v1); a permanent, small, band-rebalanced gold sleeve funded by a proportionally lower equity target is a different object with no timing content: it is a portfolio allocation between a leveraged equity core and a diversifying real asset, and the two-leg kernel makes its tax treatment explicit (its own FIFO queue, and rebalances routed to the cheaper book). The question it answers is whether the *average* cost of holding gold is repaid by its diversification, without any state variable to get wrong.

**Literature.** [Baur & Lucey, Is Gold a Hedge or a Safe Haven?, Financial Review 2010](https://doi.org/10.1111/j.1540-6288.2010.00254.x); [gold_riskoff_oos_v1 (the tested state-dependent arm) and leverage_frontier_v1 (GOLD risk-off)](docs/research/gold_riskoff_oos_v1.md)

**Closest tested study.** gold_riskoff_oos_v1 / leverage_frontier_v1 / riskoff_rotation_v4 (gold as a state-dependent holding) (`docs/research/gold_riskoff_oos_v1.md`). *Difference:* All tested gold arms switch the whole account into gold when a state fires; none holds a *fixed small* sleeve alongside the equity core. The risk profile is completely different (a permanent 8-10% sleeve cannot cause the DD increase the switches caused).

**Signal inputs.** GLD and QLD daily closes; the two books' FIFO lot records

**Data.** in repo: data/historical/batch_screen_v1_gld.csv (2004-11-18 ->, extra key gld); data/historical/stress_robustness_v1_daily.csv. New downloads: none. Main window: partial: GLD starts 2004-11; before that the sleeve is cash (a fixed convention). OOS evaluable: M3, M4, M5 -- GLD 2004+ -> M3/M4/M5 only; reduced 3-window G7

**Plugin.** `two_leg` -- second_symbol=GLD; NeedsKernel: two-leg account (the sleeve is a priced second book)

**Arms (parameters frozen).**
- **A** -- 0.90 x production target in the QLD book + 0.08 in GLD, rebalanced only outside a 2 pp band *(kernel: all_stages)*
- **B** -- 0.80 x production target + 0.18 GLD, same band *(kernel: all_stages)*
- **C** -- 0.88 x production target + a gold sleeve sized 0.10 x clip(vol20(QLD)/vol20(GLD), 0.5, 2.0) *(kernel: all_stages)*

**Honest prior.** low-medium: no timing content to get wrong; the tested gold arms' failures came from the switch, not from gold.

### IC46 Permanent duration sleeve (QLD + IEF, risk-parity-lite) as a two-leg arm

**Mechanism.** A permanent small duration sleeve alongside the leveraged equity core is the standard leveraged risk-parity construction. As an *after-tax* object its point is the realisation schedule: r2_tbill_combo_v1's bill sleeve lost after tax because its gains were realised whenever the trend state flipped; a permanent sleeve is touched only by a 2 pp rebalance band, so its realisations are rare and can be routed to the cheaper book. The question is whether the sleeve's diversification pays for the drag of holding it through the equity bull markets, at fixed weights, with no state variable.

**Literature.** [Asness, Frazzini & Pedersen, Leverage Aversion and Risk Parity, FAJ 2012](https://doi.org/10.2469/faj.v68.n1.1); [r2_tbill_combo_v1 (the tested bill sleeve after tax: -1.08 pp median bootstrap)](docs/research/r2_tbill_combo_v1.md)

**Closest tested study.** r2_tbill_combo_v1 (S2_10 bill sleeve, lost after tax) and tbill_sweep_v1 (the sweep rules) (`docs/research/r2_tbill_combo_v1.md`). *Difference:* The tested sleeves are state-keyed (bills only while the vote is off), so their gains are realised at every state flip - the diagnosed cause of the after-tax loss. A permanent sleeve with a 2 pp rebalance band realises on a completely different schedule; the two-leg kernel is what makes the routing explicit.

**Signal inputs.** IEF and QLD daily closes; the two books' FIFO lot records

**Data.** in repo: data/historical/batch_screen_v1_ief.csv (2002-07-30 ->, extra key ief); data/historical/stress_robustness_v1_daily.csv. New downloads: none. Main window: partial: IEF starts 2002-07 (cash before, a fixed convention). OOS evaluable: M3, M4, M5 -- IEF 2002+ -> M3/M4/M5 only; reduced 3-window G7

**Plugin.** `two_leg` -- second_symbol=IEF; NeedsKernel: two-leg account

**Arms (parameters frozen).**
- **A** -- 0.88 x production target in the QLD book + 0.10 in IEF, rebalanced only outside a 2 pp band *(kernel: all_stages)*
- **B** -- 0.78 x production target + 0.20 IEF, same band *(kernel: all_stages)*
- **C** -- 0.90 x production target + an IEF sleeve sized 0.08 x clip(vol20(QLD)/vol20(IEF), 0.5, 3.0) *(kernel: all_stages)*

**Honest prior.** low-medium: the after-tax precedent for a duration sleeve is negative, but that sleeve was state-keyed and this one is not.

## 7.9 Drawdown / recovery-state sizing (3 ideas)

### IC47 Recovery-ramp sizing of the early position

**Mechanism.** Exposure is cheapest exactly when price has started recovering but the vote is still off - the early path's territory - yet production sizes that position at a fixed half weight. Scaling the early weight by how much of the 252-session drawdown has been recovered (recovered = (close - trough126)/(max252 - trough126), 0 at the trough, 1 at the old high) makes re-entry a ramp instead of a step and, unlike ladder_v1 (which keyed the early size on a vol-normalised recovery metric and won all three after-tax windows but missed the B1 share at 53.1%), the variable is parameter-free and monotone in the price itself.

**Literature.** [ladder_v1 (early-weight ladder keyed on a vol-normalised recovery metric: +0.30/+0.21/+0.23 pp after tax, B1 share 53.1%)](docs/research/ladder_v1.md); [crash_entry_v1 (the tested crash-recovery entry *trigger*; all arms lost)](docs/research/crash_entry_v1.md)

**Closest tested study.** ladder_v1 (early-leg conviction ladder) and crash_entry_v1 (drawdown-recovery entry path) (`docs/research/ladder_v1.md`). *Difference:* ladder_v1 already changed the early weight, so this must be judged as a *different ladder variable*: the recovered fraction of the price drawdown, which is bounded, parameter-free and available from the close alone, instead of the vol-normalised recovery metric whose ceiling had to be chosen. crash_entry_v1 changed the trigger - this keeps production's early conditions untouched.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only; 252-session warm-up

**Plugin.** `targets_from_data` -- early-leg weight as a function of the recovered fraction inside wants()

**Arms (parameters frozen).**
- **A** -- while in the early state, early weight = production early weight x clip(recovered, 0.5, 1.0)
- **B** -- the same with clip(recovered, 0.25, 1.0)
- **C** -- early weight = 0.5 x production target x (1 + recovered), capped at the full production target

**Honest prior.** low-medium: the closest tested ladder arm was close (B1 53.1% vs the 55% gate), and this variable is simpler and needs no calibration.

### IC48 Drawdown-velocity sizing

**Mechanism.** The *speed* of a decline, not its depth, predicts continuation: a 10% fall in 10 sessions is a de-leveraging event, a 10% fall over a year is a grind, and only the former is followed by more of itself. Scaling exposure by the 20-session decline from the running 20-session high (dd20) is a fast risk control that acts inside the vote's 200-session lag, and it is a different statistic from IB29's depth variables (252-session and all-time-high drawdowns) and from dip_overlay_v1's fixed -5% triggers.

**Literature.** [dip_overlay_v1 (5-day -5% and RSI(2) triggers: all arms added drawdown or lost)](docs/research/dip_overlay_v1.md); [Daniel & Moskowitz, Momentum Crashes, JFE 2016 (drawdown dynamics and trend fragility)](https://doi.org/10.1016/j.jfineco.2015.12.002)

**Closest tested study.** IB29 (drawdown-depth scaling, in the bank) and dip_overlay_v1 (short-term drop triggers) (`docs/research/dip_overlay_v1.md`). *Difference:* The tested overlays use *depth* below a prior close and *fixed* percentage thresholds. The 20-session drawdown from a running high is a rate-of-change variable with a continuous (not trigger) response, which is what distinguishes it from both.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only

**Plugin.** `targets_from_data` -- dd20 scaling inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= clip(1 - dd20/0.10, 0.50, 1.00), dd20 = 1 - close/max(close over the last 20 sessions)
- **B** -- weight *= clip(1 - 0.5*(dd20/dd252), 0.50, 1.00) (speed relative to depth)
- **C** -- mirror: weight *= clip(1 + dd20/0.10, 1.00, 1.25) (buy the fast dip; the dip_overlay sign)

**Honest prior.** low: the family (fast risk overlays) has lost every time; the mirror arm is the informative control.

### IC49 Drawdown-budget governor (feedback target) -- **rank 15**

**Mechanism.** The vol target is a variance control, not a drawdown control, yet the owner's acceptance test is stated as a drawdown comparison against buy-and-hold. A governor that scales exposure so the *forecast* drawdown of the position stays inside a budget makes the drawdown the objective and volatility the instrument: sigma_budget_t = 0.35 x clip((0.35 - |NAV_dd_t|)/(0.35 - 0.20), 0.25, 1.0) and vt = min(1, 0.35/vol20, sigma_budget_t/vol20). It is *inactive* while the NAV drawdown is under 20% (sigma_budget = 0.35, exactly production) and de-risks progressively as the budget is consumed, which is a state the fixed vol target cannot see. Unlike IB29/IB30 it feeds back on the strategy's own NAV, so it is a kernel option under this API.

**Literature.** [Grossman & Zhou, Optimal Investment Strategies for Controlling Drawdowns, Mathematical Finance 1993](https://doi.org/10.1111/j.1467-9965.1993.tb00044.x); [exposure_k_v1 and vol_shape_v1 (the closed vol-target variants this arm is *not*: the target stays 0.35 until the NAV budget is consumed)](docs/research/exposure_k_v1.md)

**Closest tested study.** exposure_k_v1 / vt_level_tax_v1 / vol_shape_v1 (vol-target level and shape changes at matched average weight) and IB29/IB30 (price-based drawdown scaling, in the bank) (`docs/research/exposure_k_v1.md`). *Difference:* The closed exposure family changes the *constant* (or the shape) of the vol target, and the banked drawdown ideas scale on the *price* drawdown. This one is a feedback law on the realised NAV drawdown with a dead zone below 20% - inactive on most sessions by construction, so it cannot be a disguised lower vol target.

**Signal inputs.** the arm's own instrument close and the engine's NAV path

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv. New downloads: none. Main window: full. OOS evaluable: all six -- the NAV path exists in every window; the pooled OOS bootstrap needs the governor to be reproduced inside the bootstrap chunk (a kernel requirement, not a data one)

**Plugin.** `kernel_option` -- the plugin API exposes market data only; the realised NAV drawdown lives in fe_sim's state, so the governor must be a kernel option (all stages). NeedsKernel: NAV-state feedback in the sizing step

**Arms (parameters frozen).**
- **A** -- sigma_budget = 0.35 x clip((0.35 - |NAV_dd|)/(0.35 - 0.20), 0.25, 1.0); vt = min(1, 0.35/vol20, sigma_budget/vol20) *(kernel: all_stages)*
- **B** -- the same with the decline starting at a 15% NAV drawdown and a 0.15 floor *(kernel: all_stages)*
- **C** -- the same as A applied only to upsizes (downsizes keep K's band rule) *(kernel: all_stages)*

**Honest prior.** medium: it targets the quantity the acceptance test is stated in, with a dead zone that keeps it inactive in normal states; the kernel cost is the price.

## 7.10 Model-free statistical filters (2 ideas)

### IC50 Hit-rate (sign-persistence) sizing

**Mechanism.** The *frequency* of up days over the last 60 sessions is a different trend statistic from the return: two stretches with the same 60-session return can have hit rates of 45% (a few large up days carrying the whole move) or 60% (broad participation), and the broad version is the one that persists because it reflects many independent marginal buyers. Scaling the target by (hit60 - 0.45)/0.10 clipped is a parameter-light, non-parametric participation tilt that no tested arm computes - breadth_vote_v1 counted *instruments* above their SMAs, not days within one instrument.

**Literature.** [Moskowitz, Ooi & Pedersen, Time Series Momentum, JFE 2012 (participation)](https://doi.org/10.1016/j.jfineco.2011.11.003); [Bollen, Kashyap & Sokolov / sign-persistence evidence in the momentum literature, 2021](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3874517)

**Closest tested study.** breadth_vote_v1 (a count of indices above their own SMAs) and deriv_vote_v1 (price-derivative votes) (`docs/research/breadth_vote_v1.md`). *Difference:* breadth_vote_v1's statistic counts *instruments* and is a vote; this counts up-days within the traded instrument - a within-series frequency, which is invariant to the size of the moves and therefore to the volatility the sizing already uses.

**Signal inputs.** the arm's own instrument daily close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/ewma_band_v1_index.csv. New downloads: none. Main window: full. OOS evaluable: all six -- closes only

**Plugin.** `targets_from_data` -- a rolling hit-rate multiplier inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= clip((hit60 - 0.45)/0.10, 0.75, 1.10)
- **B** -- the same multiplier applied only while the vote is on (early path unchanged)
- **C** -- early weight *= clip((hit20 - 0.45)/0.10, 0.50, 1.00)

**Honest prior.** low: a new but weak statistic; it is cheap and orthogonal, which is what a bank entry needs to be.

### IC51 Cross-sectional dispersion regime

**Mechanism.** Return dispersion across the nine sector ETFs is the other half of IC09's correlation statistic: high dispersion with low correlation means the market is rotating (an idiosyncratic regime in which index exposure is less rewarded and drawdowns are shallower), low dispersion means macro-driven beta (2018, 2020, 2022) in which index risk is undiversifiable. Sizing inversely with normalised dispersion is a breadth-of-outcomes risk model - the dispersion of the cross-section's returns, not of the index's own returns.

**Literature.** [Stivers & Sun, Cross-sectional return dispersion and time variation in the value premium, 2010](https://doi.org/10.1007/s11156-009-0138-7); [Pollet & Wilson, Average correlation and stock market returns, JFE 2010](https://doi.org/10.1016/j.jfineco.2010.02.004)

**Closest tested study.** vol_window_v1 (own-series volatility) and breadth_vote_v1 (a cross-instrument count) (`docs/research/vol_window_v1.md`). *Difference:* A cross-sectional dispersion statistic is neither a volatility of the arm's own series nor a vote about levels; it measures how differently the market's constituents behaved, which the record never uses as a risk input.

**Signal inputs.** the nine sector SPDR daily closes (extra keys sec_*)

**Data.** in repo: data/historical/batch_screen_v1_xl*.csv (1998-12-22 ->). New downloads: none. Main window: full. OOS evaluable: M3, M4, M5 -- SPDRs absent on GSPC_1957_1999/M1/M2 -> 3 applicable windows; reduced 3-window G7

**Plugin.** `targets_from_data` -- data.extra('sec_xl*') inside wants()

**Arms (parameters frozen).**
- **A** -- weight *= clip(disp_t/median252(disp), 0.70, 1.00), disp = 20-session mean of the cross-sectional stdev of the nine SPDRs' daily returns
- **B** -- weight *= clip(median252(corr_t)/corr_t, 0.70, 1.00) (the correlation complement, IC09's statistic)
- **C** -- both multipliers of A and B multiplied together

**Honest prior.** low: dispersion and correlation are close relatives; arm C is the combination that would have to carry the idea.

## 7.11 Execution, frictions and the real LIVE (Alpaca) account (7 ideas)

### IC52 Minimum-notional order filter -- **rank 11**

**Mechanism.** K's A1u band suppresses small downward resizes by *drift*; a dollar floor suppresses the remaining ones by *size*: a permitted downsize whose notional is below a threshold is skipped and its drift accumulates toward the next band crossing. Every skipped sell is a taxable event avoided and every skipped buy is free (buying is untaxed, only the spread costs), so the filter is monotone in the tax direction. It is also a real LIVE friction: a sub-$250 order in a $100k account pays spread and, at tax time, a currency conversion for proceeds that are economically irrelevant; the tracking error it creates is bounded by the next band crossing.

**Literature.** [gain_band_v3 / band_h2_combo_v1 (the drift bands this filter complements)](docs/research/band_h2_combo_v1.md); [Alpaca order minimums and fractional trading (the account-level constraint)](https://docs.alpaca.markets/docs/orders-at-alpaca)

**Closest tested study.** gain_band_v1-v3 (drift bands in percentage points) and ewma_band_v1 (2.5/5/10 pp bands) (`docs/research/gain_band_v3.md`). *Difference:* Every tested band is a *weight-drift* threshold; this one is a *dollar-notional* threshold, so it bites in proportion to account size rather than to the size of the position change, and it is the only filter in the record that models the broker's minimum order economics.

**Signal inputs.** the permitted fill's notional (target weight x NAV), the arm's own instrument close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv. New downloads: none. Main window: full. OOS evaluable: all six -- no data requirement; the notional scales with the window's own NAV convention

**Plugin.** `kernel_option` -- the filter changes which permitted fills happen, inside the fill decision. NeedsKernel: a fill-notional filter (all stages); the 21-ETF and after-tax stages at $100k are where it bites

**Arms (parameters frozen).**
- **A** -- skip a permitted downsize when its notional < $250 (re-evaluate at the next decision) *(kernel: all_stages)*
- **B** -- skip a permitted downsize when its notional < $1,000 *(kernel: all_stages)*
- **C** -- skip when its notional < max($250, 0.05% of NAV) *(kernel: all_stages)*

**Honest prior.** medium: a direct attack on the trim channel with an account-level justification, and the drift it leaves is bounded by the band.

### IC53 Order slicing over N sessions

**Mechanism.** A state flip moves the whole target, so the resulting order is the largest the strategy ever places. Slicing it into N equal daily parts reduces market impact and - more quantifiably - exploits the close-to-open drift the strategy already banks: a sell whose fills are spread over three opens sells some of the position at later, on average higher openings, while a buy is penalised symmetrically. In the last week of December slicing also physically moves part of a realisation past the year boundary, which interacts with H2.

**Literature.** [Almgren & Chriss, Optimal Execution of Portfolio Transactions, Journal of Risk 2001](https://doi.org/10.21314/JOR.2001.041); [exec_delay_v1 (day-level fill shifts: next close / t+2 / split fills all failed the real-open OOS windows)](docs/research/exec_delay_v1.md)

**Closest tested study.** exec_delay_v1 (fill-timing family including a split-fill arm) and IB36/IB37 (fill filters/prices, in the bank) (`docs/research/exec_delay_v1.md`). *Difference:* exec_delay_v1's split arm was one of its day-level variants and it failed on the real-open OOS windows - which is why this must be pre-registered with those windows as the decisive gate. The distinct point here is that the schedule is tied to the *drift* the account earns (sells later, buys earlier), not to a generic delay.

**Signal inputs.** the size of the pending resize and the subsequent sessions' opens

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (OHLC); data/historical/ewma_band_v1_index.csv (OHLC). New downloads: none. Main window: full. OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- opens are needed for the later slices; GSPC_1957-1999 is close-only in the loader -> dropped, reduced 5-window G7

**Plugin.** `kernel_option` -- the fill schedule (how much fills on each of the next N opens) is kernel. NeedsKernel: multi-session fill schedule (all stages)

**Arms (parameters frozen).**
- **A** -- a resize larger than 25 pp of NAV fills in 3 equal parts on the next 3 opens *(kernel: all_stages)*
- **B** -- a resize larger than 50 pp fills in 5 equal parts on the next 5 opens *(kernel: all_stages)*
- **C** -- every resize fills in 2 equal parts on the next 2 opens *(kernel: all_stages)*

**Honest prior.** low-medium: the tested delay family failed on exactly the windows that matter, so this enters as a test of the drift argument rather than of slicing itself.

### IC54 Delayed intraday fill (auction vs continuous)

**Mechanism.** Production fills at the opening auction print - the most crowded and most gap-contaminated minute of the session for a leveraged ETF. A fill at a delayed intraday reference (the close of the first 30 minutes, or the first 30 minutes' VWAP) trades a small timing risk for a better expected price and, unlike the tested day-level delays, does not hold the decision one or two days late. exec_delay_v1 tested next-close, t+2 and split fills (all day-level); IB37's typical-price proxy replaced the fill *price* with (h+l+c)/3 for all fills and is a symmetric proxy, not an intraday delay.

**Literature.** [Bogousslavsky, The Cross-Section of Intraday and Overnight Returns, JFE 2021 (open-auction and intraday timing)](https://doi.org/10.1016/j.jfineco.2020.07.011); [exec_delay_v1 (the tested day-level fill variants)](docs/research/exec_delay_v1.md)

**Closest tested study.** exec_delay_v1 (next close, t+2 open, split fills) and moc_execution_v1 (same-close fills) (`docs/research/exec_delay_v1.md`). *Difference:* Every tested alternative moves the fill by whole days or to the decision close; a 30-minute delay is a different object (the position still exists for the same session's drift) and needs intraday data the harness does not hold - arm C is the version that can be run on the current daily panel.

**Signal inputs.** the fill session's intraday bars (new data) or, for arm C, its daily open/close

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (for the arm-C proxy). New downloads: QQQ and QLD 30-minute bars (2000->) for arms A and B; Alpaca SIP 1-minute/30-minute bars for the fill price (the LIVE source of truth). Main window: arm C: full; arms A and B: only as far back as the intraday series can be sourced (Alpaca SIP from 2016, which covers the 2017+ window but not 2000). OOS evaluable: GSPC_1957_1999, M1, M3, M4, M5 -- no intraday series exists for the OOS index windows, so G7 is replaced by the 2017+ window and the 21-ETF/after-tax stages - pre-register that reduced gate explicitly

**Plugin.** `kernel_option` -- the fill price is chosen by the kernel. NeedsKernel: an intraday fill reference (all stages)

**Arms (parameters frozen).**
- **A** -- fills at the close of the first 30 minutes of the fill session *(kernel: all_stages)*
- **B** -- fills at the first-30-minute VWAP of the fill session *(kernel: all_stages)*
- **C** -- fills at the midpoint of the fill session's open-to-close range (a daily-data proxy, runnable now) *(kernel: all_stages)*

**Honest prior.** low-medium: the mechanism is concrete, but the record's execution family has failed repeatedly and the intraday data requirement limits the evaluable windows.

### IC55 FX conversion cost and the tax-payment schedule -- **rank 12**

**Mechanism.** The after-tax engine converts the TRY tax base at CBRT rates with no spread and pays the liability out of the USD account at the same rate. In the real account the liability is paid in TRY, funded by selling USD, and the bank's spread plus the transfer is 0.2-1% of the payment. Since the tax costs the strategy about 5 pp/yr, a 0.5% conversion cost is roughly 2.5 bp/yr - small, systematic, and completely unmodelled. Arms A and C price it; arm B asks the only decision question it creates: pay on the due date, or pre-fund the liability by selling in a TRY window chosen to be favourable (the engine *times realisations* everywhere, but never the payment).

**Literature.** [after_tax_tr_v1 and vt_level_tax_v1 (the tax engine whose TRY conversion is free at CBRT rates)](docs/research/after_tax_tr_v1.md); [TCMB daily indicative USD/TRY rates (the published series the engine uses)](https://www.tcmb.gov.tr/kurlar/kurlar_tr.html)

**Closest tested study.** after_tax_tr_v1 / r2_tbill_combo_v1 (the tax engine and the only other account-level friction, the cash sweep) (`docs/research/r2_tbill_combo_v1.md`). *Difference:* cash_yield_v1 added a *yield* to idle cash; no study adds a *cost* to the tax step. The payment schedule is also untouched: the realization-timing studies (tax_harvest_v1/v2, monthly_defer_v1, exit_timing_v2, state_defer_v1, tax_budget_v2) all shape when gains are realised, never when the liability is converted and paid.

**Signal inputs.** USD/TRY CBRT daily, Yi-UFE monthly, the engine's annual liability; a frozen conversion-cost assumption

**Data.** in repo: data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv. New downloads: none. Main window: full (after-tax stage). OOS evaluable: all six -- the pre-tax stages are unaffected (there is no tax); the arm is an after-tax-stage measurement plus a payment-timing rule - pre-register non-inferiority for the pre-tax stages

**Plugin.** `kernel_option` -- it changes fe_tax's payment/conversion step (and arm B its timing). NeedsKernel: tax-payment accounting (after-tax stage only)

**Arms (parameters frozen).**
- **A** -- charge 0.50% on every TRY conversion when the liability is paid (a measurement arm: the same rule as K, priced honestly) *(kernel: after_tax_stage_only)*
- **B** -- pre-fund the liability: convert and set aside the due amount three months before the due date (a payment-timing rule) *(kernel: after_tax_stage_only)*
- **C** -- charge 1.00% (the cost stress) *(kernel: after_tax_stage_only)*

**Honest prior.** medium: it is small in pp terms but it is real, systematic and currently invisible in every after-tax number the record publishes.

### IC56 Dividend-withholding cost on the traded leg

**Mechanism.** The traded leg is a synthetic total-return 2x built from the index's price and dividends, which implicitly assumes dividends are received gross. The real account holds a US-domiciled ETF, and a Turkish resident's US-source dividends are withheld at source (20% under the treaty's default, less with a reduced-rate claim) with a foreign tax credit possible against the Turkish liability. QLD's yield is small, so the drag is around 0.1 pp/yr - but it is nevertheless a flow the engine does not model anywhere, and it is exactly the kind of item that has to be right for a LIVE account whose whole edge is a few tenths of a percentage point after tax.

**Literature.** [IRS United States-Turkey income tax treaty, dividend article (withholding and credit)](https://www.irs.gov/pub/irs-trty/turkey.pdf); [instrument_cost_v1 (fund-level costs: expense ratios and swap spreads are modelled, withholding is not)](docs/research/instrument_cost_v1.md)

**Closest tested study.** instrument_cost_v1 (fund-level costs and reset decay) and after_tax_tr_v1 (the tax model's dividend treatment) (`docs/research/instrument_cost_v1.md`). *Difference:* instrument_cost_v1 compared expense ratios and financing; the after-tax engine applies dividends to the price series. Neither models a *withholding* flow, and no study distinguishes a treaty-reduced rate from the default.

**Signal inputs.** the leg's dividend series (in repo) and a frozen withholding rate

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv (the QQQ dividend column the synthetic 2x leg is built from); data/historical/riskoff_rotation_v4_etfs.csv (ETF dividends). New downloads: none. Main window: full. OOS evaluable: all six -- the OOS indices are price-only, so the arm is identical to the control there; pre-register the reduced statement (the main window, the 21 ETFs and the after-tax stage are where the drag exists)

**Plugin.** `kernel_option` -- it changes the leg's total-return recurrence (netting the dividend before the price series is built). NeedsKernel: leg construction (all stages)

**Arms (parameters frozen).**
- **A** -- leg dividends are net of 20% withholding *(kernel: all_stages)*
- **B** -- leg dividends are net of 15% withholding (a reduced-rate claim) *(kernel: all_stages)*
- **C** -- 20% withholding on the on-state and early legs, with the withheld amount claimed as a credit against the Turkish liability (report-only in the after-tax model) *(kernel: all_stages)*

**Honest prior.** low-medium: a real, currently unmodelled friction whose size (about 0.1 pp/yr) is at the resolution limit of the gates.

### IC57 Early-close session handling

**Mechanism.** NYSE early closes (13:00) and half-days are a few percent of sessions and cluster around holidays, i.e. inside the December window that H2 already treats specially. The LIVE path has an early-close flush path, but the research engine treats those sessions as normal. Two untested behaviours: skip the decision on an early-close session (the printed close is the early close, and liquidity is thin) and defer the fill to the next full session; or decide on the previous full session's close so the order is placed into the thin session deliberately. The cost either way is one session of decision lag a few times a year.

**Literature.** [execution_model_audit_v1 §4 (the LIVE early-close flush path the research engine does not mirror)](docs/research/execution_model_audit_v1.md); [NYSE trading calendar, early closes (13:00) and the holiday schedule](https://www.nyse.com/markets/hours-calendars)

**Closest tested study.** exec_delay_v1 (fill-day shifts) and execution_model_audit_v1 (the live/research divergence audit) (`docs/research/execution_model_audit_v1.md`). *Difference:* exec_delay_v1 shifted *all* fills by a fixed number of days and failed on the OOS windows; this shifts only the 2-4 early-close sessions a year and is motivated by the already-shipped live path rather than by a return forecast, so it is a correctness question as much as a performance one.

**Signal inputs.** the NYSE early-close calendar 2000-2026 (public)

**Data.** in repo: none. New downloads: NYSE early-close (13:00) session dates 2000-2026. Main window: full. OOS evaluable: all six -- dates only; the OOS indices' own holiday calendars are not modelled, so the arm's effect there is nil - the main window and the after-tax stages carry it

**Plugin.** `kernel_option` -- it changes the decision/fill calendar. NeedsKernel: session-type handling (all stages)

**Arms (parameters frozen).**
- **A** -- skip the decision on an early-close session (carry it to the next full session) *(kernel: all_stages)*
- **B** -- decide on the previous full session's close and fill in the early-close session *(kernel: all_stages)*
- **C** -- skip only downsizes on early-close sessions (entries/exits/up-resizes keep production) *(kernel: all_stages)*

**Honest prior.** low: tiny and probably inside the noise, but it closes a documented live/research divergence.

### IC58 T+1 settlement funding constraint (cash account)

**Mechanism.** US securities have settled T+1 since 2024-05-28, but a *cash* account cannot use sale proceeds until settlement, whereas the engine's cash routing funds a same-session buy from a same-session sale instantly (this is how the early-path and resize logic is simulated). If the funded LIVE account is a cash account, a downsize-and-upresize pair in one decision would be infeasible and would cost a session of exposure. The arm models the constraint honestly (defer the buy half by one session) and includes the margin-account control, which is the account the owner must choose.

**Literature.** [SEC final rule, Shortening the Securities Transaction Settlement Cycle (T+1, effective 2024-05-28)](https://www.sec.gov/rules/final/2023/34-96930.pdf); [execution_model_audit_v1 (live vs research divergences: cash routing is one of the unmodelled ones)](docs/research/execution_model_audit_v1.md)

**Closest tested study.** execution_model_audit_v1 (the divergence audit) and the engine's own cash routing (never varied) (`docs/research/execution_model_audit_v1.md`). *Difference:* No study varies the funding assumption: every simulation lets proceeds fund same-session purchases. This is an account-type question (cash vs margin) with a measurable cost in the transition sessions, not a return forecast.

**Signal inputs.** the session's direction changes (a downsize and an up-resize in the same decision)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv. New downloads: none. Main window: full. OOS evaluable: all six -- no data requirement

**Plugin.** `kernel_option` -- it changes the cash routing inside the engine. NeedsKernel: settlement-aware funding (all stages)

**Arms (parameters frozen).**
- **A** -- if a decision contains both a downsize and an up-resize, defer the buy half by one session (cash-account convention) *(kernel: all_stages)*
- **B** -- defer only when the cash balance would otherwise go negative by more than 1% of NAV *(kernel: all_stages)*
- **C** -- defer every buy funded by same-session sale proceeds by one session (the strict version) *(kernel: all_stages)*

**Honest prior.** low: probably a handful of sessions a year, but it decides which account type the LIVE deployment needs.

## 7.12 Tax structure and realisation policy (2 ideas)

### IC59 Asymmetric band: immediate up-resizes, K's gate on downsizes -- **rank 10**

**Mechanism.** Buying is tax-free and selling is taxed, so the no-trade band should be asymmetric by *direction*: keep K's A1u gain-scaled gate on downsizes (0.5/3/8 pp by the USD FIFO gain fraction) and set the up-resize threshold to zero so any shortfall to the target is bought immediately. Production uses 0.5 pp in both directions because the band is one knob; the whole gain_band family varied the down-size side (and ewma_band varied a symmetric band), so the up-side threshold has never been tested. Removing it makes the strategy track a rising vol-target channel exactly - less underexposure after calm periods - at the price of more, smaller buys, which cost spread but no tax.

**Literature.** [gain_band_v3 (A1u: the shipped gain-scaled downsize band, up-resizes fixed at 0.5 pp)](docs/research/gain_band_v3.md); [ewma_band_v1 (symmetric 2.5/5/10 pp bands) and IB35-C (a plain 3 pp band control, in the bank)](docs/research/ewma_band_v1.md)

**Closest tested study.** gain_band_v1-v3 / band_h2_combo_v1 (K) and ewma_band_v1 (symmetrical bands) (`docs/research/band_h2_combo_v1.md`). *Difference:* Every tested band is symmetric or down-side only; the up-resize threshold has been held at 0.5 pp in every arm ever run. This is a single-parameter change in the one direction where the tax argument is unambiguous.

**Signal inputs.** the target weight and the two books' FIFO records (for the down-size score)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv (for arm C). New downloads: none. Main window: full. OOS evaluable: all six -- the up-band change is a pre-tax fill-schedule change too, so all stages are evaluable and G9 (turnover) applies

**Plugin.** `hybrid` -- a RULE with separate up/down band widths registered through fe_sim.RULE (the engine's documented extension point; the existing rule takes one band plus the A1u down-size rule). If the RULE table cannot express an asymmetric band, it becomes a kernel option - check fe_sim.RULE first

**Arms (parameters frozen).**
- **A** -- up-resize band 0.0 pp; down-resize band = K's A1u (0.5/3/8 pp by the USD FIFO gain fraction) *(rule-table change)*
- **B** -- up-resize band 0.25 pp; the same A1u down band *(rule-table change)*
- **C** -- up band 0.0 pp and the down band scored on the indexed-TRY gain fraction instead of the USD one *(rule-table change)*

**Honest prior.** medium: a one-knob change in the tax-free direction, cheap to test, and its failure mode (more small buys) is bounded by the spread.

### IC60 December up-resize acceleration (lot ageing)

**Mechanism.** Yi-UFE indexation relief accrues with the *age* of the lot, monthly, so a lot bought in early January enjoys a full year of indexation by the following December while one bought in late December does not. H2 (production) postpones *downsizes* out of December; the mirror, never tested, is to pull the next year's purchases forward: allow the target to exceed production by a fixed 2 pp (capped at 0.98) during the last 10 December sessions so the extra lots start ageing through the December-to-January indexation step. The cost is one month of extra exposure in the seasonally strongest month, and the risk is a January gap against a position that is 2 pp above target - the mirror of the risk H2 accepted.

**Literature.** [tax_harvest_v2 / H2 (December *downsize* deferral: +0.14/+0.13/+0.27 pp after tax; worst cases are January gaps)](docs/research/tax_harvest_v2.md); [after_tax_tr_v1 (the monthly Yi-UFE indexation mechanics the ageing argument rests on)](docs/research/after_tax_tr_v1.md)

**Closest tested study.** tax_harvest_v1/v2 (H2: December downsize deferral) and monthly_defer_v1 (month-boundary deferral of downsizes) (`docs/research/tax_harvest_v2.md`). *Difference:* Both shipped/tested December rules act on the *sell* side. Nothing in the record changes the *buy* side for tax reasons, so the mechanism (indexation ageing) has never been tested in the direction where it is free of tax.

**Signal inputs.** the session's position in the last 10 NYSE December sessions; the Yi-UFE calendar (after-tax stage)

**Data.** in repo: data/historical/stress_robustness_v1_daily.csv; data/historical/after_tax_tr_v1_yiufe.csv. New downloads: none. Main window: full. OOS evaluable: all six -- the pre-tax effect is a December exposure tilt, so all six windows are evaluable; the tax content is after-tax only

**Plugin.** `targets_from_data` -- a December target add-on inside wants(); the fill rule is unchanged (no kernel needed)

**Arms (parameters frozen).**
- **A** -- target = min(0.98, production target + 0.02) in the last 10 NYSE December sessions
- **B** -- target = min(0.98, production target x 1.05) in the last 10 NYSE December sessions
- **C** -- target = min(0.98, production target + 0.02) in the last 20 NYSE December sessions

**Honest prior.** low: it adds exposure (the failure mode the exposure family recorded) for a tax effect that is a fraction of a basis point per lot.
