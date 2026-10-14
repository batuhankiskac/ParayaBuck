# intraday_stop_v1 — pre-registration

**Written and committed before any non-control arm was simulated on real data** (see the commit
hash in `docs/research/intraday_stop_v1.md`). Nothing below was changed after seeing arm results;
post-pre-registration changes to `screen.py` are listed in the Amendments section of that report.

## Question

Production **R2** (`equity_leveraged_trend_v1` 1.2.0) = E2 + the early re-entry path at half
weight. It decides once per session, at the close, and its position is a 2×-leveraged Nasdaq-100
synthetic (QLD\*). A session's *close* is not the worst price of the session: within 2020-02/03,
2018-12, 2008-10 and 2000-04 QLD\* fell far below the prior close intraday, and across a few
sessions the trend votes can stay on while the fund loses a third of its value. A resting
**protective stop-market on QLD\***, re-armed at each close while invested, would cap the
within-session loss without waiting for the votes to flip.

Does such a stop improve R2 **after Turkish income tax**, without materially worse drawdown?
The known risk is whipsaw: stopped out on a spike low and bought back higher at the next open,
paying two fees, a slippage spread and a taxable realisation.

## Data (fixed; SHA-256 in `DATA_MANIFEST.json`)

| Input | Content | Range |
|---|---|---|
| `data/historical/stress_robustness_v1_daily.csv` | QQQ daily open/high/low/close/adjclose/dividend | 1999-03-10 → 2026-09-29 |
| `data/historical/stress_robustness_v1_dtb3.csv` | DTB3 (3-month T-bill, secondary market) | 1954-01-04 → 2026-09-29 |
| `data/historical/ewma_band_v1_tb3ms.csv` | TB3MS (pre-1954 rate fallback) | 1934-01 → 1954 |
| `data/historical/range_vol_v1_{dia,iwm,spy}.csv`, `range_vol_v2_{19 names}.csv` | ETF adjusted OHLC | 1993-01 → 2026-09 |
| `data/historical/after_tax_tr_v1_usdtry_cbrt.csv` | CBRT USD/TRY forex buying | 1996-01 → 2026-09 |
| `data/historical/after_tax_tr_v1_yiufe.csv` | Yi-UFE monthly index | 2005-01 → 2026-09 |
| `research/fast_reentry_v1/{RESULTS.json,screen.py}` | pre-tax harness + R2 reference | — |
| `research/r2_tbill_combo_v1/{RESULTS.json,tax_engine.py}` | R2 after-tax reference + Turkish tax engine | — |
| `research/trend_robustness_v2/screen.py` | shared engine/metrics helpers | — |

No network access: every input is a byte copy of an existing committed research file. All data is
public market data (Yahoo chart API / FRED / CBRT), recorded by the upstream studies.

## Control

**C0 = production R2 exactly**, as implemented by `research/fast_reentry_v1/screen.py`'s arm `R2`
(the `--harness` mode must reproduce, within 0.01 pp):

| Reference | base m=1 | base m=2 | calibrated m=1 | calibrated m=2 |
|---|---:|---:|---:|---:|
| CAGR | 15.7538% | 15.4766% | 15.1719% | 14.8961% |
| max DD | 46.4143% | 46.7584% | 46.5364% | 46.8797% |
| turnover/yr | 5.9915 | 5.9915 | 5.9915 | 5.9915 |

Source: `research/fast_reentry_v1/RESULTS.json` (`main_window.{base,calibrated}.{m1,m2}.R2`).
After tax at $100k (base financing, m = 2, liquidated, progressive): 10.1600% / 14.7059% /
22.8861% on the 2000-03 / 2010-01 / 2017-01 windows; pre-tax liquidated 15.4732% / 22.1587% /
32.5330%. Source: `research/r2_tbill_combo_v1/RESULTS.json` (`after_tax_100k.windows.*.R2`).
B1-recent (progressive) median after-tax CAGR 3.5155%, B2 (flat 20%) 3.9678%
(`after_tax_bootstrap`).

## Mechanics (fixed)

Everything except the stop is production R2 / `fast_reentry_v1`:

- **Instrument.** Synthetic QLD\* = daily-reset 2× on QQQ total return: expense 0.95%/yr;
  financing DTB3 (base) or DTB3 + 0.75 pp/yr (calibrated); total-return basis from the file's
  `dividend` column (verified: `(close_t + div_t)/close_{t-1}` equals the `adjclose` ratio to
  7·10⁻⁵ relative). Session `i` grows `open*_i = close*_{i-1}·(1 + 2·((open_i + div_i)/close_{i-1} − 1))`
  overnight and `close*_i/open*_i` intraday.
- **Signals.** Production E2 (≥ 2 of: close > SMA200, 252-session price return > 0,
  close > midpoint of the 126-session max/min, on the QQQ *price* close) **plus** the early path
  (enter while E2 is off if close > SMA50, 20-session vol < its trailing 252-session median and
  close > close 20 sessions ago; exit below SMA50; at half weight until E2 turns on). Sized
  `vt = min(1, 0.35/vol20)` on the QLD\* returns, total weight capped at 0.98.
- **Decide at the close, fill at the next open**, 0.5 pp resize band (entries and exits always
  trade in full), cost 4 bps × m per unit traded, idle cash earns zero, main window
  2000-03-09 → 2026-09-29 (6,679 sessions).

### New in this study: the intraday path and the stop

- **QLD\* session low** (fixed formula):
  `low*_i = close*_{i-1} · (1 + 2·((low_i + div_i)/close_{i-1} − 1))`, i.e. the daily-reset 2× of
  the QQQ low's total return from the prior close. QQQ `high` is carried through the bootstrap so
  that the drawn OHLC tuple stays coherent; no rule uses it. Cross-check (report-only, not a gate):
  against the real QLD bars in the same file (2006-06-21 →), the modelled low return has
  correlation 0.988 and the count of sessions below −8% is 88 modelled vs 86 real (−12%: 18 vs 17).
- **Stop level** `S_i = (1 − X)·close*_{i-1}`, re-armed at every close while the arm holds,
  i.e. a daily-reset stop, not a ratchet. It applies to the whole held weight (production E2 leg
  and early path alike — it is an order on the instrument).
- **Trigger and fill.** Taking the weight held after the open trade of session `i` (a position that
  was entered at that same open is equally subject to the stop):
  - `open*_i ≤ S_i` → fill at `open*_i` (gap through the stop),
  - else `low*_i ≤ S_i` → fill at `S_i`,
  - else no trigger.
  The whole position is sold at `fill × (1 − slip)`; the fee is `cost × |Δw|` on the exit notional
  like every other trade; weight goes to 0 for the rest of session `i`.
- **After a stop.** The arm is flat for the rest of session `i` and does not earn the
  `close_i → open_{i+1}` gap. With cooldown `CD`, sessions `i+1 … i+CD` are forced flat
  (target 0) and from session `i+CD+1` the arm follows R2's target exactly (the target decided at
  the previous close, filled at that open). `CD = 0`: the arm is back on R2's target at the open of
  `i+1`. The stop is re-armed at each close while it holds again.
- **Slippage** `slip = 0.0010` (10 bps) is charged on stop fills only. `slip = 0.0030` (30 bps) is a
  report-only stress, not gated.

## Arms (5 including the control; no others, no other parameters)

| Arm | stop X | cooldown CD |
|---|---:|---:|
| **C0** | none (production R2) | 0 |
| **T8** | 8% | 0 |
| **T8c** | 8% | 5 |
| **T12** | 12% | 0 |
| **T12c** | 12% | 5 |

Every arm uses the identical target rule (R2); only the engine differs.

## Bootstrap and OOS (fixed)

- **Main block-20 bootstrap** (`signal_ensemble_v1` draws): stationary bootstrap, mean block 20,
  seed 20261011, 5,000 paths of `WARM=250` + `MEASURED=2520` sessions, 3-session back-extension,
  m = 2. **Each drawn session carries its QQQ (open, high, low, close, dividend) tuple and DTB3
  jointly** — the low is drawn with the same index as the open and close, so the intraday path stays
  coherent. Signals are recomputed on the resampled price level.
- **Reduced OOS: 21 ETFs.** The six `ewma_band_v1` index windows (^GSPC 1957–1999, M1–M5) are
  **not usable for a stop arm**: the old Yahoo index file's intraday extremes are placeholders or
  bad ticks, and a stop rule feeds directly on them. They are replaced, as the range_vol_v2
  quality rule allows, by the fixed 21-instrument ETF test:
  `XLB XLE XLF XLI XLK XLP XLU XLV XLY EWJ EWG EWU EWA EWH EWS EWL EFA EEM` (v2 file set) +
  `DIA IWM SPY` (v1 file set); **EWC is excluded** (it fails the quality rule at 5.6% bad bars).
  Quality rule (range_vol_v2): a bar is bad if `high == low`, or the open is missing/≤ 0/outside
  `[low, high]`; an instrument is excluded above 5% bad bars. Each ETF carries its own adjusted
  OHLC (`open/high/low × adjclose/close`, `close = adjclose`), its own E2 votes and early path, its
  own 2× daily-reset synthetic leg (same low formula), calibrated financing, m = 2. Each
  instrument's OOS window is its own full adjusted history from session 253 (as in
  range_vol_v2's G7′/G8′), so the end dates differ per instrument.
- **ETF bootstrap.** Block-20 per instrument, seed 20261011, 2,000 paths of 2,772 sessions drawn
  from that instrument's own bars (OHLC tuple drawn jointly), pooled over the instruments.

## Gates (every one must pass; "both" = base and calibrated financing)

Against C0 = R2, `ewma_band_v1`'s definitions, unchanged:

1. **G1** (m = 2, both): CAGR ≥ R2, or Calmar ≥ 1.1 × R2 with CAGR ≥ R2 − 0.5 pp.
2. **G2** (both): max DD ≤ R2 + 1 pp.
3. **G3** (both): 2017+ CAGR ≥ R2 − 1 pp.
4. **G4**: CAGR above R2 in ≥ 2 of P1 (→2008), P2 (2009–16), P3 (2017→).
5. **G5** (both): main block-20 bootstrap median Δ CAGR ≥ 0.
6. **G6** (both): G1 at m = 1.
7. **G7′** (reduced OOS, replacing G7's six index windows — reason above): over the 21 ETFs,
   median Δ CAGR ≥ 0, share of instruments with Δ CAGR > 0 ≥ 60% (≥ 13 of 21), share with
   Δ max DD ≤ +3 pp ≥ 80% (≥ 17 of 21).
8. **G8′** (replacing G8): pooled ETF bootstrap median Δ CAGR ≥ 0.
9. **G9**: turnover/yr ≤ 2 × R2's.

After Turkish tax (base financing, m = 2, $100k at t = 0, liquidated at the window end,
progressive 2026 tariff + Yi-UFE indexation, the `vt_level_tax_v1`-corrected engine):

10. **G10**: after-tax CAGR > R2 in **all three** windows (2000-03→2026-09, 2010-01→end,
    2017-01→end).
11. **G11**: clean bootstrap **B1-recent** (progressive, fixed 2015-09→2026-09 calendar, 1,000
    block-20 paths) median Δ vs R2 ≥ 0 **and** share of paths > 0 ≥ 55%.
12. **G12**: clean bootstrap **B2** (flat 20%) as in G11.

A gate that cannot be evaluated is reported as failed/unavailable, never silently dropped.

## Selection rule (fixed)

Among arms passing G1–G12, select the one with the highest **base m = 2 CAGR**. If none passes,
no arm is selected and production R2 stays unchanged.

## Report-only statistics (not gates)

Stop-outs per year (main window, base, m = 2); per-episode attribution of every stop (return
difference vs R2 over `[close_{i-1}, close_{i+CD+1}]`, from the same NAV paths); the five biggest
saves and the five biggest whipsaws; the share of episodes where the arm re-entered above the stop
fill price; the 30 bp slippage stress; the m = 4 cost stress; trade counts per year; QQQ and ETF
data-quality shares; the synthetic-low vs real-QLD cross-check above.
