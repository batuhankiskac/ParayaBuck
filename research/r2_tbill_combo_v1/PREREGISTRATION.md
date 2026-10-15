# r2_tbill_combo_v1 pre-registration (2026-10-14)

Committed **before** any non-control arm is simulated on real data. Only the synthetic-price
smoke test of the run path runs before this commit.

## Question

Two independent studies produced candidates that were merged into production or left as
candidates:

- **R2** (`fast_reentry_v1`, tag `archive/research/fast-reentry-v1-2026-10`; now on main as
  `equity_leveraged_trend_v1` 1.2.0): while production E2 is off, an early re-entry path at
  **half** the production weight — enter if QQQ close > SMA50, QQQ 20-session close vol < its
  own trailing 252-session median, and close > close 20 sessions ago; exit if close < SMA50;
  the path ends the moment E2's own vote turns on. It passes all nine `ewma_band_v1` gates
  (+0.81 pp base CAGR, max DD 46.8% vs 49.6%).
- **S2_10** (`tbill_sweep_v1`, tag `archive/research/tbill-sweep-v1-2026-10`): idle cash in a
  T-bill ETF. BIL\* holds 0.98 while E2 is off; while E2 is on it parks the residual
  `0.98 − w` in BIL\*, re-sized only when the BIL weight leaves a 10 pp band. It passes every
  pre-registered T-gate (+0.21 pp base CAGR, max DD 46.0% vs 49.6%).

Both trade the *same* idle cash: R2 fills part of the trend-off gap with 0.5·vt of QLD\*,
S2_10 fills all of it with BIL\*. This study asks whether **combining them** (RS) beats each
alone, **pre-tax and after Turkish tax**. The after-tax leg is decisive for the owner: S2_10's
edge is small, and every BIL\* resize realises a taxable gain, so an arm that trades the bill
leg more often can lose after tax what it gains before it.

## Control C0

Production `equity_leveraged_trend_v1` 1.1.0 E2 exactly: at least 2 of 3 votes on the QQQ
price close (close > SMA200; 252-session price return > 0, zero hurdle; close > 126-session
max/min midpoint); size `vt = min(1, 0.35/vol20)` on synthetic QLD\*; total cap 0.98; decide
at the completed close, fill at the next open; 0.5 pp resize band; cost 4 bps × m per unit of
traded notional per leg; idle cash earns zero.

## Arms (4; no others, all parameters fixed)

- **C0** — production E2, single leg.
- **R2** — `fast_reentry_v1`'s R2 exactly (SMA 50, momentum lag 20, vol-median window 252,
  early weight 1/2), single leg.
- **S2** — `tbill_sweep_v1`'s **S2_10** exactly: the two-leg portfolio of C0 + BIL\* with
  X = 10 pp.
- **RS** — R2's QLD\* leg + S2_10's BIL\* leg. QLD\* targets are R2's (0.5·vt on the early
  path, vt under E2, 0 otherwise). The bill leg's "off" state is `active = E2 on OR early
  path active`; while E2 is off **and the early path is active**, the rest `0.98 − w` is idle
  and goes to BIL\* under the same 10 pp band; while fully off (E2 off, no early), BIL\* is
  held at 0.98 as in S2_10.

### Bill instrument BIL\* (identical to `tbill_sweep_v1`)

Synthetic: session `i` accrues `(rate[i] − 0.1356%)/252` between its open and close; open =
previous close. Trades at the same 4 bps × m per side. `rate` = DTB3 (base and calibrated
financing both), TB3MS before 1954-01-04; in the out-of-sample windows the same rate series
that market's financing uses (US T-bills as a USD proxy for ^N225 / ^GDAXI / ^FTSE, as
`tbill_sweep_v1` did).

### Bill band rule (identical to `tbill_sweep_v1` S2_10)

Let `w` be the arm's QLD\* target decided at the completed close. While active:
`hi = 0.98 − w`, `lo = hi − 0.10`; if the current BIL weight is above `hi` or below `lo`,
trade to `max(0, 0.98 − w − 0.05)`, otherwise do not touch the bill leg. While fully off:
BIL target 0.98, traded when the drift exceeds the 0.5 pp band. The QLD\* leg always uses the
production rule (entry/exit forced, otherwise resized only when the drift exceeds 0.5 pp).

## Data

Restored byte-identical inputs, SHA-256 in `DATA_MANIFEST.json`:

- `data/historical/ewma_band_v1_{gspc,index,tb3ms}.csv`,
  `data/historical/stress_robustness_v1_daily.csv`, `stress_robustness_v1_dtb3.csv`
  (reused from `ewma_band_v1`, same blobs as `fast_reentry_v1`).
- `data/historical/after_tax_tr_v1_usdtry_cbrt.csv`, `after_tax_tr_v1_yiufe.csv`
  (reused; SHA-256 verified against `research/vt_level_tax_v1/DATA_MANIFEST.json`).
- No new network download: BIL\* is synthetic and the OOS rate series are already on disk.

Main window 2000-03-09 → 2026-09-29 (6,679 sessions). Financing base (DTB3) and calibrated
(DTB3 + 0.75%).

## Mechanics

Synthetic QLD\*: daily-reset 2x on QQQ total return, 0.95%/yr expense plus DTB3 (base) or
DTB3 + 0.75% (calibrated). Cost multiplier m ∈ {1, 2, 4} (m = 4 report only). Idle cash earns
zero. Signals on the QQQ **price** close; the instrument series is the total-return synthetic.

## Harness checks (mandatory before any real arm run)

1. C0 (two-leg simulator with an empty bill leg) reproduces `e2_integration_v1`'s production
   E2 row: base/calibrated × m = 1/m = 2 CAGR, max DD and turnover, to 0.01 pp.
2. R2 reproduces `research/fast_reentry_v1/RESULTS.json` (CAGR, max DD, turnover;
   base/calibrated × m = 1/m = 2) to 0.01 pp.
3. S2 reproduces `research/tbill_sweep_v1/RESULTS.json` S2_10 (base/calibrated × m = 1/m = 2)
   to 0.01 pp.
4. The single-leg path of the two-leg simulator equals `trend_robustness_v2.simulate_multi`
   bit for bit at 0.5 pp for the C0 wants.
5. The after-tax engine reproduces `vt_level_tax_v1`'s C0 anchors at $100k on the three tax
   windows (pre-tax CAGR, progressive CAGR, progressive max DD) to 0.005 pp, and the
   two-leg after-tax runner with an empty bill book equals the single-leg engine exactly.

`--run` stops if the harness fails.

## Gates (candidate RS)

`ewma_band_v1`'s G1–G9 unchanged, evaluated **against C0**, both financing models where the
gate says "both":

- **G1 (m = 2, both):** CAGR ≥ C0, or Calmar ≥ 1.1 × C0 with CAGR ≥ C0 − 0.5 pp.
- **G2 (both):** max DD (m = 2) ≤ C0 + 1 pp.
- **G3 (both):** 2017+ CAGR (m = 2) ≥ C0 − 1 pp.
- **G4:** CAGR above C0 in ≥ 2 of 3 sub-periods (P1 2000–08, P2 2009–16, P3 2017 → end).
- **G5 (both):** block-20 bootstrap, seed 20261011, 5,000 paths of 10 years, draws identical
  to `signal_ensemble_v1`; median of arm − C0 ≥ 0.
- **G6 (both):** G1 at m = 1.
- **G7:** six out-of-sample windows (^GSPC 1957–1999; M1 ^IXIC; M2 ^GSPC 1935–56; M3 ^N225;
  M4 ^GDAXI; M5 ^FTSE), calibrated, m = 2: median Δ CAGR ≥ 0, Δ CAGR ≥ −0.5 pp in ≥ 5 of 6,
  Δ max DD ≤ +3 pp in ≥ 5 of 6 and never > +8 pp. All six windows are runnable for the
  compound arms because BIL\* is built from the same T-bill series the market's financing
  uses; no window is dropped.
- **G8:** pooled out-of-sample bootstrap over M1–M5 (2,000 paths each, block 20, calibrated,
  m = 2) median Δ ≥ 0.
- **G9:** turnover/yr (both legs) ≤ 2 × C0.

Two additional requirements on RS, pre-registered here:

- **G1R (both financings):** `g_return(RS, R2)` — RS CAGR ≥ R2, or Calmar ≥ 1.1 × R2 with
  CAGR ≥ R2 − 0.5 pp, at m = 2, under base and calibrated financing.
- **G5R (both financings):** the main block-20 bootstrap median of **RS − R2** ≥ 0.

## After tax (report only, but decisive)

The corrected engine of `vt_level_tax_v1` (branch `research/vt_level_tax_v1`), as vendored by
`fast_reentry_v2` (`research/r2_tbill_combo_v1/tax_engine.py`, byte copy, SHA-256 recorded).

- $100k, base financing, m = 2, cost 4 bps × 2 per side, liquidation of both legs at the end
  of the window; 2026 progressive tariff with bracket indexation by Yi-UFE; capital gains
  computed in TRY from the purchase-date and sale-date CBRT USD/TRY buying rate; indexation
  relief when Yi-UFE rose ≥ 10% between purchase month and the previous month; instalments
  assessed at the year boundary and paid in two halves (Mar 31, Jul 31), funded pro rata from
  the two legs' market value when cash is short.
- **BIL\* is modelled as a normal ETF lot book**: gains realised on sale enter the same annual
  net realised gain as the QLD\* leg (a single Turkish tax base across instruments). BIL\*
  is a synthetic total-return series, so it pays no distributions; the model's dividend path
  is therefore unused for it (real BIL pays monthly distributions — caveat).
- Windows: `2000_03_to_2026_09`, `2010_01_to_end`, `2017_01_to_end` (same as
  `vt_level_tax_v1`). Report after-tax CAGR (liquidated) and sells/yr for C0, R2, S2, RS.
- **B1-recent** (progressive tariff) and **B2** (20% flat, losses carried forward): the clean
  after-tax bootstrap of `vt_level_tax_v1` — block-20 stationary bootstrap, 1,000 paths,
  seed 20261011, 10-year paths on a fixed 2015-09-01 → 2026-09-30 calendar with the fixed FX
  of those dates; report median Δ CAGR and share > 0 for **RS − R2** and **RS − C0**.

## Selection / verdict rule (fixed now)

RS is **"worth integrating"** if and only if **all** of:

1. RS passes every gate G1–G9 vs C0;
2. G1R passes in both financiings and G5R holds in both financings;
3. RS's after-tax CAGR at $100k is **strictly greater** than R2's in **all three** windows;
4. B1-recent **and** B2 both give median Δ (RS − R2) ≥ 0 with share > 0 ≥ 55%.

Otherwise the verdict is **FAIL** and no arm is selected. A clean fail is a fine result; no
parameter, gate or arm is changed after seeing data.

## Planned reporting

Main-window table (CAGR base/calibrated, max DD, Calmar, Sharpe, 2017+, P1/P2/P3, m = 1,
bootstrap median, turnover by leg), the S2_10-style bill-trade counts, sub-period and
calendar-year deltas, the six OOS windows, the OOS pooled bootstrap, the m = 4 cost stress,
the per-episode P&L of R2's early path (report only), the after-tax table and the two
after-tax bootstraps, and the max actual gross exposure (must stay ≤ 0.98 + drift).
