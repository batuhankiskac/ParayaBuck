# smooth_target_v1 — partial-adjustment / smoothed target weight (idea I3)

Status: **pre-registration**. Committed before any candidate arm was simulated on real data.
Only the synthetic smoke test and the control-only `--harness` anchor mode may run before the
commit that carries this file, `screen.py` and `DATA_MANIFEST.json`.

Owner-facing question: R2's Turkish taxable gain is ~91 % vol-target *trims* (46.5 m of ≈50.9 m
TRY, `deriv_vote_v1` §trading activity). Rt's realised taxable gain scales with the traded
volume `Σ|Δw|`, not with the number of decision points. Does trading to a *smoothed* version of
R2's target weight — so the same target is reached over several sessions instead of in one
resize, and the exits to flat stay immediate — raise the after-Turkish-tax CAGR at $100k versus
production R2, without a materially worse drawdown, at an unchanged vote, unchanged vol target
and unchanged early re-entry path?

Source: `docs/research/idea_survey_v3.md` §3, §5 (I3), §6.1 (the pre-registered relaxation of
G5) and `docs/research/deriv_vote_v1.md` (where the tax base comes from).
Reference arm, **not re-run here**: `gain_band_v2`'s A1 (`docs/research/gain_band_v2.md`).

## 1. Control

**C0 = production R2 exactly** = `equity_leveraged_trend_v1` 1.2.0: hold QLD at
`min(1, 0.35 / vol20)`, total cap 0.98, while at least 2 of 3 votes hold on the QQQ price close
(`close > SMA200`; 252-session price return > 0; `close >` the 126-session max/min midpoint);
while the vote is off, hold **half** that weight if `QQQ close > SMA50` **and**
`vol20 <` its trailing 252-session median **and** `close > close[-20]`, exiting below SMA50, and
the early path ends the moment the vote turns on. Decide at the completed close, fill at the next
open, 0.5 pp resize band, cost 4 bps × m per unit of traded notional.

`--harness` must reproduce (to 0.01 pp) the anchors quoted in the protocol:

| quantity | reference |
|---|---|
| C0 pre-tax CAGR base / calibrated, m = 2, main window 2000-03-09 → 2026-09-29 (6,679 sessions) | 15.48 % / 14.90 % (`research/fast_reentry_v1/RESULTS.json`, row `R2`) |
| C0 max DD base / calibrated | 46.76 % / 46.88 % |
| C0 m = 1 base / calibrated | 15.75 % / 15.17 % |
| C0 turnover/yr, m = 2 base | 5.9915 |
| production E2 without the early path (deviation row) | `E2_INTEGRATION_DEVIATION.json`, `E2_prod_price_h0`, base/calibrated × m = 1/2, 0.000000 pp |
| C0 after tax at $100k, windows 2000 / 2010 / 2017 | 10.160 % / 14.706 % / 22.886 % |
| C0 clean after-tax bootstrap medians | B1-recent 3.5155 %, B2 flat 20 % 3.9678 % |

Reference (reported, not gated): E2 (C0 without the early path) 14.67 % / 14.10 % pre-tax and
9.53 / 14.50 / 22.69 % after tax.

## 2. Arms — every parameter fixed here

All four arms keep **E2's vote**, R2's early path, the vol target `0.35`, the 0.98 cap, the
0.5 pp resize band and the decision/fill timing. Only the path by which the target weight is
reached changes. Let `vt_t = min(1, 0.35/vol20_t)` of the held instrument (2× synthetic leg on
QQQ total return) and `s_t = min(0.98, vt_t)` (the on-state sizing); let `V_t` be E2's vote and
`E_t` R2's early-path flag at the close of session `t`. **R2's raw target** is

```
w*_t = s_t                            if V_t            (on-state)
     = 0.5 · s_t                      if (not V_t) and E_t   (early leg)
     = 0                             otherwise
```

**Flat exemption (all arms).** Where `(not V_t) and (not E_t)`, the arm's target is `0` — the
exit to flat is immediate at the next open, exactly as in R2. (The smoothed target of a
mean-based arm would otherwise decay over several sessions.)

* **S1 — 5-session mean.** target `w̄_t = mean(w*_{t−4..t})`, the window over the last five
  completed closes, zeros included; then the exemption.
* **S2 — 10-session mean.** `w̄_t = mean(w*_{t−9..t})`; then the exemption.
* **S3 — partial adjustment.** `w̄_t = w̄_{t−1} + 0.2 · (w*_t − w̄_{t−1})`, the state starting at
  0 and **reset to 0** on an exempt session (the state is the arm's own intended weight, not the
  band-delayed held weight); the exemption overrides the value at exempt sessions.
* **S4 — smooth the on-state only.** target `= mean(s_{t−4..t})` where `V_t`;
  `= 0.5 · s_t` (R2's own early-leg weight, **unsmoothed**) where the early path is active;
  `= 0` otherwise.

The 0.5 pp band is applied **after** smoothing, to the arm's target against the *held* weight —
never to `w*` — and entries and exits to flat still trade in full
(`simulate_band`: entry `held = 0 ∧ target > 0`, exit `held > 0 ∧ target = 0`).

## 3. Data

No new data. Every input is a byte copy of an already-committed research file; SHA-256 in
`DATA_MANIFEST.json`. Sources: `stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars,
FRED DTB3), `ewma_band_v1_{gspc,index,tb3ms}.csv` (the six out-of-sample histories),
`range_vol_v1_*` / `range_vol_v2_*` (the 21 gate-I instruments; EWC fails `range_vol_v2`'s
pre-existing data-quality rule and is excluded before simulation),
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (CBRT USD/TRY buying rate, Yİ-ÜFE),
`research/fast_reentry_v1/RESULTS.json` (the C0 pre-tax anchor) and
`E2_INTEGRATION_DEVIATION.json` (the production-E2 anchor). No network access.

## 4. Mechanics (identical in every study on this harness)

Synthetic daily-reset 2× QLD\* on QQQ total return, 0.95 %/yr expense, financing DTB3 (base) or
DTB3 + 0.75 pp (calibrated); decision on the completed close, fill at the next open; cost
4 bps × m; idle cash earns zero; main window 2000-03-09 → 2026-09-29; main sub-periods
P1 2000–2008, P2 2009–2016, P3 2017 → end; block-20 stationary bootstrap seed 20261011, 5,000
ten-year paths, draws identical to `signal_ensemble_v1`; six out-of-sample windows
(^GSPC 1957–1999 and `e2_confirmation_v1`'s M1 ^IXIC 1975–1999, M2 ^GSPC 1935–1956, M3 ^N225,
M4 ^GDAXI, M5 ^FTSE, calibrated, m = 2, R-USD); pooled OOS bootstrap (2,000 paths per market);
21 instruments each against *its own* E2 + early-path control (`fast_reentry_v2`'s gate I).
After tax: the corrected multi-instrument Turkish engine of `research/vt_level_tax_v1`
(vendored as `tax_engine.py`, byte copy of the version `deriv_vote_v1` archived: SHA-256
`7a5d54aa…`), $100k, base financing, m = 2, FIFO lots, Yİ-ÜFE indexation with the 10 % floor,
2026 progressive tariff deflated by `Yİ-ÜFE(Oct Y−1)/Yİ-ÜFE(Oct 2025)`, per-calendar-year
netting with **no** loss carry-forward, liquidation at the window end; plus the clean B1-recent
(progressive) and B2 (flat 20 %, losses carried forward) block-20 bootstraps of 1,000 ten-year
paths on the fixed 2015-09 → 2026-09 calendar with its real FX.

## 5. Gates (pre-registered, before any candidate arm ran)

G1–G4 and G6–G9 exactly as `ewma_band_v1` defines them, against C0, every gate required:

| gate | rule |
|---|---|
| G1 (base and calibrated, m = 2) | CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 − 0.5 pp |
| G2 (both) | max DD ≤ C0 + 1 pp |
| G3 (both) | 2017+ CAGR ≥ C0 − 1 pp |
| G4 | CAGR above C0 in ≥ 2 of 3 sub-periods |
| **G5n (both)** | **replaces G5**: pre-tax clean block-20 bootstrap median(arm − C0) ≥ **−0.10 pp** |
| G6 (both) | G1 at m = 1 |
| G7 | six OOS windows: median Δ CAGR ≥ 0, ≥ 5 of 6 with Δ CAGR ≥ −0.5 pp and Δ DD ≤ +3 pp, Δ DD never > +8 pp |
| G8 | pooled OOS bootstrap median Δ ≥ 0 |
| G9 | turnover ≤ 2 × C0 |
| gate I | 21 instruments, each vs its own control: **share Δ max DD ≤ +3 pp ≥ 80 %** (the only gated component; median Δ CAGR, share Δ CAGR > 0 and the pooled bootstrap median are reported as reference) |
| AT | after tax at $100k, base financing, m = 2: after-tax CAGR **above C0 in all three windows** |
| B1 / B2 | clean after-tax bootstrap median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 55 %, in both modes |

Rationale for the two relaxations, fixed now and not after seeing data: an arm whose effect is
measured in *realisations* is expected to be ≈ 0 pre-tax, so a bootstrap median that must be
*positive* rejects it by construction (`idea_survey_v3` §6.1, `gain_band_v2` A1: +0.0002 pp /
−0.004 pp, both failing G5 while winning every after-tax test); and in an OOS/instrument cascade
where the smoothing changes nothing about the signal, the drawdown-spread condition is the one
that detects the real failure mode (stale target held into a decline). No other gate is relaxed.

## 6. Selection rule

An arm is eligible only if **every** gate above passes (G1–G4, G5n, G6–G9, gate I, AT, B1, B2).
If several are eligible, select the one with the highest base-financing m = 2 CAGR. If none is
eligible, the verdict is FAIL and production R2 stays unchanged. A clean FAIL is a result and is
reported as such.

## 7. Expected effect (stated before the run)

Pre-tax ≈ −0.15 … +0.05 pp CAGR: the dead-band family of comparable effective size cut sells/yr
from 27 to 6–14 and *lost* 0.10–0.50 pp, because a stale below-target weight delays re-levering
into rallies (`ewma_band_v1` B25/B50/B100). Smoothing has the same asymmetry. Binding gates are
expected to be **G1** (return) and **G5n/G7/G8** (the pre-tax difference is ≈ 0, so these are
coin flips); G2 could improve (smoothing cuts the largest resizes) and G9 should pass easily.
After tax +0.2 … +0.7 pp is the survey's estimate; the mechanism (fewer lots consumed at the same
marginals) is mechanical but small. Turnover and sells/yr must fall; if they do not, the arm is
not doing what it says.

## 8. Reported quantities (report-only, no gate)

Per arm: realised net TRY gain (2000 window, progressive, by reason: trims / exits /
tax-funding sales / liquidation), tax paid in USD, sells/yr, Σ|Δw| (total and per year),
mean target weight and time in market, the six OOS windows, the pooled OOS bootstrap, gate-I
detail per instrument, the audit probes (look-ahead truncation, trailing-window recompute, fill
timing, cost linearity, state invariants, the flat-exemption invariant) and, **as a reference
only, never re-run here**, `gain_band_v2`'s A1 numbers as published.

## 9. Amendments

Anything changed after the run is recorded here, with the reason, and no gate, arm or parameter
is changed after seeing data. (Empty at the time of the pre-registration commit.)

## 10. Reproduction

```
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/smooth_target_v1/screen.py --smoke
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/smooth_target_v1/screen.py --harness
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/smooth_target_v1/screen.py --run
```

`--run` writes `RESULTS.json` and `SUMMARY.txt`; two consecutive runs must give byte-identical
`RESULTS.json`. Offline; ~9–10 minutes.
