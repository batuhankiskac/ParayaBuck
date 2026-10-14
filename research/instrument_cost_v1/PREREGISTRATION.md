# instrument_cost_v1 pre-registration (2026-10-14)

## Question

Production R2 (`equity_leveraged_trend_v1` 1.2.0 = E2 + early re-entry) holds QLD
(ProShares Ultra QQQ, 2x daily reset, expense ratio ~0.95%). Every pre-2000-and-after
research simulation of it used a *synthetic* QLD\* (daily-reset 2x on the QQQ total
return, minus 0.95%/yr expense plus DTB3 financing, i.e. an assumed financing spread).
With **real fund data**, is there a cheaper way to obtain the same exposure?

Two candidate implementations of "2x Nasdaq-100, hold QLD only while the signal is on":

- **T**: hold TQQQ (ProShares UltraPro QQQ, 3x daily reset, expense ~0.84%) at **2/3 of
  QLD's target weight** and leave the remaining 1/3 in cash (zero yield, as production).
  Same notional exposure (3x x 2/3 = 2x), lower expense ratio, but a 3x fund held at 2/3
  weight is **not** the same as a 2x daily-reset fund: the two reset paths differ
  (path dependence / volatility drag), and TQQQ's financing is 2 x DTB3 rather than 1 x.
  The study measures the real difference.
- A candidate 2x Nasdaq-100 daily ETF other than QLD with >= 3 years of history. None
  exists (see "Candidate sweep" below), so T is the only candidate arm.

The decisive measure is after Turkish income tax, as always for this owner.

## Candidate sweep (Yahoo chart API, existence + inception verified 2026-10-14)

Tickers queried on the Yahoo chart API (`/v8/finance/chart/<sym>`) and by search
(`/v1/finance/search`); inception = `meta.firstTradeDate`:

| Ticker | Name | Inception | Usable as a 2x Nasdaq-100 daily ETF with >= 3y? |
|---|---|---|---|
| QQQ | Invesco QQQ Trust | 1999-03-10 | no - 1x, signal/benchmark only |
| QLD | ProShares Ultra QQQ | 2006-06-21 | **yes** - 2x Nasdaq-100, production instrument = control |
| TQQQ | ProShares UltraPro QQQ | 2010-02-11 | 3x, not 2x - arm T at 2/3 weight |
| QLDX | - | does not exist (HTTP 404) | no |
| QQQU | Direxion Daily Magnificent 7 Bull 2X | 2024-03-07 | no - Mag-7 index, 2.4 y |
| QQUP | ProShares Ultra QQQ Mega | 2025-06-12 | no - QQQ Mega index, 1.3 y |
| EQQQ | ProShares Ultra QQQ Equal Weight | 2026-07-23 | no - equal-weight index, new |
| QQXL | ProShares Ultra QQQ Top 30 | 2025-08-15 | no - top-30 index, 1.2 y |
| QQQX | Nuveen Nasdaq 100 Dynamic Overwrite | 2007-02-01 | no - closed-end covered-call fund |
| RYVYX | Rydex NASDAQ-100 2x Strategy H | 2000-05-24 | no - mutual fund, no intraday/open, ~1.6% ER |
| QQU.TO | BetaPro NASDAQ-100 2x Daily Bull | 2008-12-01 | no - TSX, CAD-denominated |
| 00670L.TW | Fubon NASDAQ-100 2X Leveraged | 2017-02-14 | no - TWSE, TWD-denominated |

Consequence: **TQQQ is the only alternative instrument with real, long data**, so the
study has exactly one candidate arm. This is a 15.6-year, single-market
(US/Nasdaq-100), single-history test.

## Data

`download_data.py` (network used only for this) pulls `QQQ`, `QLD`, `TQQQ` daily bars
from the Yahoo chart API v8 and writes `data/historical/instrument_cost_v1_{qqq,qld,tqqq}.csv`
(columns as the existing research inputs: symbol,date,open,high,low,close,adjclose,volume,dividend).
`dividend` is reconstructed from the split+dividend adjusted `adjclose` as
`close[t-1]*(adjclose[t]/adjclose[t-1]) - close[t]`, so `(close[t]+dividend[t])/close[t-1]`
is exactly the fund's close-to-close total return; Yahoo's raw dividend events (not
split-adjusted) are not used. URLs, date ranges and SHA-256 are in `DATA_MANIFEST.json`
(and `DOWNLOAD_PROVENANCE.json`).

- Downloaded range 2010-01-04 -> 2026-10-14. **Analysis end = 2026-09-29**, the standing
  research window end used by every prior study (and by the after-tax windows).
- Joint session index = QQQ ∩ QLD ∩ TQQQ = **2010-02-11 -> 2026-09-29, 4,183 sessions**
  (TQQQ inception is the binding constraint).
- Sanity: the downloaded QQQ/QLD closes agree with the already-committed
  `stress_robustness_v1_daily.csv` to <= 5e-7, and total dividends to <= 0.2%.
- Reused inputs (unchanged, hashes in the manifest): `stress_robustness_v1_dtb3.csv` (base
  financing for the report-only synthetic comparison), `after_tax_tr_v1_usdtry_cbrt.csv`,
  `after_tax_tr_v1_yiufe.csv` (Turkish after-tax calendar).
- Reference numbers for the *synthetic* control are read from the committed
  `research/fast_reentry_v1/RESULTS.json` (pre-tax) and
  `research/r2_tbill_combo_v1/RESULTS.json` (after tax).

## Control (Q = production, on real data)

Production R2 mechanics on the **real QLD** prices:

- Signals on the **QQQ price close** (split-adjusted, dividends excluded, as production):
  E2 = at least 2 of 3 votes (close > SMA200; 252-session price return > 0;
  close > the midpoint of the 126-session max/min); early re-entry R2 = while E2 is off,
  enter at half weight if close > SMA50 AND 20-session annualized close vol < its own
  trailing 252-session median AND close > close 20 sessions ago; exit the early position
  below SMA50; E2's own vote overrides.
- Sizing: `vt = min(1, 0.35/vol20)` where vol20 is the 20-session annualized vol of
  **QLD's total-return close** (the held instrument, as production does), total weight
  capped at 0.98, half weight while only the early path is active.
- Decide at the completed close, fill at the next open; idle cash earns zero.
- Resize band 0.5 pp (entries and exits always trade in full); cost 4 bps x m per unit
  traded, **m = 2 gated** (m = 1 reported).
- Trading starts at the first session with production's 253-bar minimum history, so the
  measured window is **days[252] = 2011-02-10 -> 2026-09-29** (first decision 2011-02-11,
  15.60 years, 3,930 sessions). Sessions before that hold nothing.

## Arms (every parameter fixed)

| Arm | Instrument | Target weight | Status |
|---|---|---|---|
| **Q** | real QLD | `vt_QLD` (cap 0.98) | control |
| **T** | real TQQQ | `(2/3) x vt_QLD` | **candidate** |
| Q\* | synthetic QLD\* built from real QQQ (0.95%/yr + DTB3) | `vt_QLD` | report-only, never gated |
| T_alt | real TQQQ | `vt_TQQQ` (vol on TQQQ itself, no 2/3 factor) | report-only sensitivity |

- Signals, band, cost and windows are identical in every arm; only the instrument and the
  weight scale differ.
- **T's vol convention (pre-registered):** compute `vt` from **QLD's** vol20 and multiply
  the QLD-equivalent target by 2/3, i.e. `target_T = (2/3) x min(0.98, min(1,0.35/vol20_QLD))`
  applied to the same E2/R2 on/off and half-weight path. Justification: (a) it implements
  "2/3 of the QLD-equivalent weight" literally; (b) it holds the notional exposure constant
  (3x x 2/3 = 2x) and holds the *risk* estimate constant, so the difference between T and Q
  is the instrument's path/drag, which is the question; (c) deriving the weight from TQQQ's
  own vol20 (`vt_TQQQ = 0.35/vol20_TQQQ`, ~2/3 of `vt_QLD` automatically) lands at the same
  place up to vol-estimation noise, and is tested as the report-only T_alt arm.
- Q\* isolates the real fund from the research simulation used in every prior study
  (same signals, same window, synthetic daily-reset 2x with base financing) - it is the
  bridge between this study and the published R2 numbers.
- Dividend treatment: every arm is simulated/accounted on the fund's **total-return level**
  (`tr_open`, `tr_close` built by reinvesting each dividend at the ex-date open). The funds'
  3-year average dividend yield is ~0.1-0.3%/yr (reported), so taxed-as-capital-gain instead
  of dividend-withholding is a <<0.1 pp/yr approximation at both arms.

## Mechanics

- Pre-tax engine: `research/fast_reentry_v1/screen.py`'s `simulate_band` (the production
  band simulator, verified bit-for-bit against `trend_robustness_v2.simulate_multi` on both
  the synthetic control and the real QLD series).
- After-tax engine: verbatim copy of the corrected multi-instrument Turkish engine in
  `research/vt_level_tax_v1/screen.py` (the `tax_frontier_v1` progressive 2026 tariff with
  Yi-UFE indexation of the acquisition month, the 22,000 TRY dividend withholding threshold,
  instalments on Mar 31 / Jul 31, USD/TRY CBRT buying rate, $100k at t = 0, liquidation at
  the window end at real prices and 4 bps x 2 per trade). Validated in-line against
  `r2_tbill_combo_v1`'s published after-tax anchors for C0 and R2 on the synthetic QLD\*.
- Pre-tax bootstrap: stationary block bootstrap, mean block 20, seed 20261011, 5,000 paths
  x (250 warm-up + 2,520 measured) sessions + 3 back-extension sessions, drawn from the
  **real joint session pool** (one draw drives QQQ price, QLD and TQQQ together, by date);
  signals recomputed on the resampled QQQ price level; all arms on the same paths; m = 2.
  Δ = T - Q per path.
- Clean after-tax bootstraps B1-recent (progressive) / B2 (flat 20% with loss carry-forward):
  1,000 paths of 2,776 sessions on a **fixed real calendar** (first 2,776 joint sessions in
  2015-09-01 -> 2026-09-30, so USD/TRY and Yi-UFE are real), market returns resampled from
  the real joint pool, capital $100k, liquidation at the end.

## Windows

- **W0 ("2010 window")** = days[252] (2011-02-10) -> 2026-09-29: 15.60 years. The pre-TQQQ
  part of 2010 and everything before it cannot be simulated with real funds, so this is the
  full measured window, not calendar 2010.
- **W17** = 2017-01-03 -> 2026-09-29.

## Gates (T must pass all six; all versus Q, same window)

- **GT1 return (m = 2):** `CAGR(T) >= CAGR(Q)` in W0 **and** W17.
- **GT2 drawdown (m = 2):** `maxDD(T) <= maxDD(Q) + 1 pp` in W0 **and** W17.
- **GT3 bootstrap:** block-20 real-paired bootstrap median Δ CAGR (T - Q) **>= 0**.
- **GT4 after tax:** at $100k, progressive regime, liquidated, `CAGR(T) > CAGR(Q)` in W0
  **and** W17.
- **GT5 clean after-tax bootstrap:** for **both** B1-recent and B2,
  median Δ >= 0 **and** share(Δ > 0) >= 55%.
- **GT6 turnover:** `turnover/yr(T) <= 2 x turnover/yr(Q)` (m = 2).

No other arm is gated. m = 1 CAGRs, time in market, trades/yr, tracking drag, liquidity and
spread proxies are report-only.

## Selection

`selected = T` iff T passes GT1-GT6; otherwise `selected = none` and Q (production's real
QLD) stands. There is no plateau rule (a single candidate). No Stage B replay is proposed
either way unless T passes.

## Report-only additions

- Tracking per calendar year: each fund's realized total return vs `k x QQQ` daily total
  return compounded daily (k = 2 for QLD, 3 for TQQQ), the annual drag in pp.
- Fund CAGR vs the research synthetic on the same sessions.
- Liquidity: average/median daily dollar volume, Corwin-Schultz high-low spread proxy from
  the real high/low/close, mean daily range.
- `Q*` and `T_alt` arms in the historical table.
- Pre-tax m = 1 cost stress, event counts (round trips, resizes, sells), time in market.

## Amendments

The pre-registration above is unchanged: same arms, same parameters, same gates,
same selection rule. The following changes to `screen.py` were made after the
pre-registration commit (`238e6b66`) and before the first completed `--run`; none
of them changes a gated metric, an arm parameter or a gate.

1. **Crash fix, `boot_summary` arm list.** The historical table reports the
   report-only `Q*` arm, which is not in the bootstrap arm set, so `--run` raised
   `KeyError: 'Qstar'` after the bootstrap. The bootstrap summary is now computed
   for `BOOT_ARMS = ("Q", "T", "T_alt")` and `Q*` prints as "not bootstrapped".

2. **Crash fix, `gates_for` after-tax lookup.** GT4 read
   `at[wn][arm]["progressive"][...]`, but `run_window` returns the engine result
   dict directly; the nested key does not exist. Now
   `at[wn][arm]["cagr_usd_liquidated"]`. Gate definition unchanged.

3. **Report-only fix, per-year tracking benchmark.** The `k x QQQ` daily-reset
   benchmark compounded `1 + k*r` with `r` already a gross ratio (it must be
   `1 + k*(r-1)`), which overflowed float64 after a few years. Only the
   report-only `tracking` block is affected; gates, arms and windows are
   untouched.

4. **Report-only addition.** `tracking` now also reports the real fund against the
   *calibrated* synthetic (base financing + 0.75 pp), and the liquidity block adds
   the Corwin-Schultz share-of-positive-days / mean-over-positive and the
   one-tick/price spread floor, because the raw Corwin-Schultz mean is dominated
   by its unbounded positive tail on these high-volume ETFs.

5. **Note on the pre-registered justification (c) for T's vol convention.** The
   claim that deriving the weight from TQQQ's own vol20 "lands at the same place
   up to vol-estimation noise" is **wrong**, and the report says so: production's
   `CAP = 0.98` clamps the QLD arm's weight in calm windows while the TQQQ arm's
   weight is never clamped, so `mean(vt_TQQQ)/mean(vt_QLD) = 0.82`, not 2/3, and
   the report-only `T_alt` arm therefore runs about 22% more notional exposure.
   The candidate arm T itself is unaffected (its realized weight is exactly 2/3 of
   Q's: `mean(wants_T/wants_Q) = 0.6667`).
