# crash_entry_v1 — pre-registration

Campaign `crash_entry_v1`, branch `research/crash_entry_v1`. Written and committed **before**
any non-control arm was simulated on real data. Modes: `screen.py --smoke` (synthetic only),
`--harness` (controls only), `--run` (everything).

## Question

Production R2 (`equity_leveraged_trend_v1` 1.2.0 = E2 + the half-weight early re-entry path)
missed the 2020 V-recovery: its early path requires `vol20 < the trailing 252-session median of
vol20`, and right after a crash vol20 is still high (0.87 on 2020-04-14 against a 0.155 median),
so the path stayed off; E2's own 2-of-3 vote turned on 2020-04-08. `fast_reentry_v1` documented
this ("2020 is the counter-example to the thesis") and `fast_reentry_v2` confirmed R2's parameter
plateau is not robust; `r2_stage_b_v1` showed R2's in-window edge is one 2023 episode.

**Question: does a separate crash-recovery entry path — active only after a deep drawdown, while
E2 and R2's own early path are both off — recover part of a V-recovery that production R2 misses,
without worsening drawdown, pre-tax and after Turkish income tax?**

## Control

**C0 = production R2 exactly** (`equity_leveraged_trend_v1` 1.2.0): E2 1.1.0's 2-of-3 vote on the
QQQ price close (close > SMA200; 252-session price return > 0; close > the midpoint of the
126-session max/min), sized `vt = min(1, 0.35/vol20)` on the synthetic QLD\* leg, total weight
capped at 0.98, **plus** the early re-entry path (half of that weight while E2 is off: enter if
`close > SMA50` and `vol20 < its trailing 252-session median` and `close > close[-20]`; exit if
`close < SMA50`; ended when E2's own vote turns on).

## Arms (3; every parameter fixed here, no tuning)

The crash path shares one state machine in all three arms. Let `dd_high` = the highest close of
the trailing 252 sessions (full window only) and `vmax40` = the highest `vol20` of the trailing 40
sessions (full window only); `vol20` is the annualised 20-session sample vol of the **QQQ price
close**, and `vt` is production's `min(1, 0.35/vol20)` on the synthetic QLD\* leg.

- **Entry (evaluated at the completed close).** While E2's vote is off **and** the R2 early path is
  off **and** `close <= 0.80 * dd_high`, enter at factor `w_crash` when **all** of:
  `close > SMA20`, `vol20 < r * vmax40`, `close > close[-10]`.
- **Exit.** `close < SMA20`, or E2's vote turns on, or the R2 early path turns on (then R2's weight
  applies). The drawdown condition (`close <= 0.80 * dd_high`) is an **entry gate only**: it is not
  re-checked while the crash position is held. These two exits are exhaustive, as fixed by the task.
- **Weight priority** when several paths are on: E2 (1.0) > R2 early (`0.5`) > crash (`w_crash`) > 0.
  The crash path cannot be active at the same time as E2 or the R2 early path.
- **Band, costs, timing, sizing** are identical to production: decide at the completed close, fill
  at the next open; cost 4 bps x m per unit of traded notional (m = 2 gated); 0.5 pp resize band
  (entries and exits always trade in full).

| Arm | Crash factor `w_crash` | vol condition |
|---|---|---|
| **K1** | 0.5 x production `vt` | `vol20 < 0.7 * vmax40` |
| **K2** | 0.25 x production `vt` | `vol20 < 0.7 * vmax40` |
| **K3** | 0.5 x production `vt` | `vol20 < 0.6 * vmax40` |

No other parameter moves: SMA20, lag 10, the 20% drawdown, the 252-session high window and the
40-session vol-max window are the same in all three arms, and none of them is tuned.

`E2` (production 1.1.0, no early path) exists in `ARM_SPEC` for the harness reproduction only; it
is never a candidate.

## Data

Offline; byte copies of committed research inputs, SHA-256 in `DATA_MANIFEST.json`.

- QQQ open/close/dividend and SPY/QLD prices: `data/historical/stress_robustness_v1_daily.csv`;
  DTB3: `data/historical/stress_robustness_v1_dtb3.csv` (FRED).
- Out-of-sample windows: `ewma_band_v1_gspc.csv` (^GSPC 1957–1999), `ewma_band_v1_index.csv`
  (M1 ^IXIC 1975–1999, M2 ^GSPC 1935–1956, M3 ^N225 1966–2026, M4 ^GDAXI 1989–2026,
  M5 ^FTSE 1985–2026), `ewma_band_v1_tb3ms.csv` (pre-1954 rates).
- After tax: `after_tax_tr_v1_usdtry_cbrt.csv` (CBRT USD/TRY buying rate, daily) and
  `after_tax_tr_v1_yiufe.csv` (Yi-UFE monthly).
- Reference rows for the harness: `research/fast_reentry_v1/RESULTS.json` (main-window E2 and R2
  rows), `research/crash_entry_v1/E2_INTEGRATION_DEVIATION.json` (production-E2 anchors), and the
  published after-tax anchors hardcoded in `screen.py::TAX_ANCHORS`.
- No network access. `yfinance` is not used.

## Mechanics

Synthetic QLD\*: daily-reset 2x on QQQ total return, 0.95%/yr expense plus DTB3 (base) or
DTB3 + 0.75 pp (calibrated). Idle cash earns zero. Main window 2000-03-09 → 2026-09-29
(6,679 sessions, `START = 253`). Sub-periods P1 2000–2008, P2 2009–2016, P3 2017 → end.

## Gates (pre-registered, unchanged from `ewma_band_v1`; all must pass)

"Both" = base and calibrated financing; all arms share the 0.5 pp band.

- **G1 (m = 2, both):** CAGR ≥ C0, or Calmar ≥ 1.1 x C0 with CAGR ≥ C0 − 0.5 pp.
- **G2 (both):** max DD ≤ C0 + 1 pp.
- **G3 (both):** 2017+ CAGR ≥ C0 − 1 pp.
- **G4:** CAGR above C0 in ≥ 2 of 3 sub-periods.
- **G5 (both):** block-20 bootstrap (seed 20261011, 5,000 ten-year paths, draws identical to
  `signal_ensemble_v1`) median of arm − C0 ≥ 0.
- **G6 (both):** G1 at m = 1.
- **G7:** six out-of-sample windows (^GSPC 1957–1999 and M1–M5), calibrated, m = 2: median
  Δ CAGR ≥ 0; Δ CAGR ≥ −0.5 pp in ≥ 5 of 6; Δ DD ≤ +3 pp in ≥ 5 of 6 and never > +8 pp.
- **G8:** pooled out-of-sample bootstrap over M1–M5 (2,000 paths each) median Δ ≥ 0.
- **G9:** turnover/yr ≤ 2 x C0.

After tax ($100k at t = 0, corrected multi-instrument Turkish engine, base financing, m = 2,
liquidated at the window end; windows 2000-03-09, 2010-01-01, 2017-01-03 → 2026-09-29):

- **TAX_all_windows:** after-tax CAGR > C0's in **all three** windows.
- **TAX_B1 / TAX_B2:** clean after-tax bootstrap, 1,000 block-20 paths of ten measured years
  (2,520 sessions, 3-session back-extension) drawn from QQQ open/close/dividend and DTB3 with the
  signal re-computed on the resampled price level while the **calendar stays real** (2015-09-01 →
  2026-09-30, so USD/TRY and Yi-UFE are the actual ones). B1 = Turkish progressive tariff,
  B2 = flat 20% on the annual net realised USD gain with loss carry-forward. Pass requires
  median Δ vs C0 ≥ 0 **and** share of paths with Δ > 0 ≥ 0.55 in **both** B1 and B2.

## Selection rule

`selected` = the candidate with the highest main-window base m = 2 CAGR among the candidates that
pass G1–G9 **and** `TAX_all_windows` **and** `TAX_B1` **and** `TAX_B2`. If no candidate passes,
`selected = none`.

## Reporting (not gated)

Per-episode P&L of the crash path (entry decision, last held session, sessions held, arm return,
C0 return, delta), grouped by the four risk periods 2000–2002, 2008–2009, 2020, 2022, plus the
three worst episodes overall and the leave-one-episode-out CAGR; time in market (E2 / early /
crash shares); round trips and turnover per year, full window and 2017+; an m = 4 cost stress.

## Harness (`--harness`; controls only)

1. E2 reproduces `e2_integration_v1`'s production-E2 deviation row exactly (0.01 pp on CAGR and
   max DD for base/calibrated x m = 1/m = 2).
2. The recomputed main-window E2 row and **C0 (= production R2) row** equal
   `research/fast_reentry_v1/RESULTS.json` on CAGR, max DD and m = 2 turnover, both financing
   models and m = 1 / m = 2 — i.e. R2 = 15.48% base / 14.90% calibrated CAGR, max DD 46.76%,
   m = 1 15.75%, and E2 = 14.67% / 14.10%, max DD 49.56% — to 0.01 pp.
3. The band simulator at 0.5 pp equals `trend_robustness_v2.simulate_multi` bit for bit; the fast
   rolling median equals the v1 loop reference bit for bit.
4. The after-tax engine at $100k reproduces the published E2 rows (`vt_level_tax_v1`'s C0 anchors,
   9.53 / 14.50 / 22.69) and R2 rows (`fast_reentry_v2`'s R2 row, 10.16 / 14.71 / 22.89) to
   0.02 pp (the R2 references are published to 2 dp).

`--run` stops if the harness fails.

## Amendments

None yet.
