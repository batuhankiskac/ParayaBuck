# tax_harvest_v1 — Turkish year-end tax-timing overlays on production R2 (pre-registration)

Committed **before** any non-control arm was simulated on real data. Everything below is frozen:
arms, parameters, windows, gates, selection rule. Bug fixes or later changes go in *Amendments*
at the end.

## Question

Turkish tax on realized foreign-security gains is assessed **per calendar year** on the *net*
realized TRY gain of the year (see the model below). Two overlays that move *when* a realization
lands, without changing the market exposure beyond an overnight round trip, could cut the tax
drag of production R2:

- **H1 (year-end loss harvest).** On the last trading session of December: if the QLD\* position
  holds at least one FIFO lot with an *unrealized loss* (in the engine's indexed TRY terms) **and**
  the year has net realized gains so far, sell the **whole** position at that session's close and
  buy the strategy's target back at the **next session's open** (first session of January).
  Pre-tax cost: two extra trades at the cost model plus the overnight gap (the position is out of
  the market for exactly one night, priced with the real close -> open data).
- **H1b.** Same, but the sale is at the close of the **second-to-last** December session and the
  re-buy at the **next** open (the last December session). Protects against a settlement-date
  (rather than trade-date) reading of "the year of the sale" — see *Legal assumptions*.
- **H2 (year-end resize deferral).** A *downward* vol resize (not a trend exit and not an
  early-path exit — those always execute in full) whose **fill** session falls in the last **10**
  trading sessions of December is not executed; the normal decision rule applies from the first
  session of January onwards. If the target has recovered by then, no trade happens at all.
- **H2b.** As H2, but only resizes whose FIFO lots would realize a **net gain** (engine TRY terms)
  are deferred.
- **H3** = H1 + H2.

Control: **production R2** (`equity_leveraged_trend_v1` 1.2.0), unchanged.

## Control and arms

- Control **R2**: production E2 + early re-entry. Target weight at the close of session `t`:
  `vt = min(1, 0.35 / vol20)` on synthetic QLD\* (daily-reset 2x on QQQ total return), total cap
  0.98; E2 on iff >= 2 of 3 votes on the QQQ price close (close > SMA200; 252-session price
  return > 0; close > midpoint of the 126-session max/min); while E2 is off, 0.5 x vt if QQQ close
  > SMA50 **and** QQQ vol20 < its trailing 252-session median **and** close > close 20 sessions
  ago, exited below SMA50, and E2's own vote overrides.
- Arms: **R2 (control), H1, H1b, H2, H2b, H3** — exactly six, five of them candidates.
  Every arm shares R2's target series *bit for bit*; only the execution layer differs. No other
  parameter changes anywhere.

## Data

Existing committed files only; no download in this campaign (SHA-256 in `DATA_MANIFEST.json`):
`data/historical/stress_robustness_v1_daily.csv` (QQQ/SPY/QLD bars),
`data/historical/stress_robustness_v1_dtb3.csv` (DTB3),
`data/historical/after_tax_tr_v1_usdtry_cbrt.csv` (USD/TRY, CBRT buying),
`data/historical/after_tax_tr_v1_yiufe.csv` (Yi-UFE, monthly).

## Mechanics (identical in every arm)

- Decide on the completed close, fill at the next open; 0.5 pp resize band (`trv2.BAND`); an entry
  from flat and an exit to zero always trade in full; idle cash earns zero; cost
  `4 bps x m x |delta w|` per traded unit.
- Synthetic QLD\* = daily-reset 2x on QQQ total return, expense 0.95%/yr, financing
  DTB3 + {base 0.0, calibrated +0.75%/yr}. Pre-tax reported at m = 1 and m = 2, both financings;
  the after-tax stage is base financing at m = 2 (the protocol's convention).
- Main window 2000-03-09 -> 2026-09-29 (6,679 sessions), start index 253.
- December windows: the December sessions of a calendar year are the sessions whose date month is
  `YYYY-12`; "last 10" = the last `min(10, count)` of them; "last" and "second-to-last" are the
  final two of those. Session index sets are built on the simulated window's own session list.
- **Fill session** = the session on which a trade decided at the previous close executes (its
  open). H2/H2b look at that session.
- Down-resize test: `0 < target < held weight` at the fill's open (equivalently the unit delta is
  negative and the target is positive). Exits (`target = 0`) and up-resizes execute always.
- H2b gain test: FIFO net gain of the units that would be sold, computed with the engine's own
  per-lot TRY formula (USD proceeds at the fill session's USD/TRY buying rate, cost in TRY at the
  lot's acquisition FX, Yi-UFE indexation applied only when the index rose >= 10% from the month
  preceding the acquisition to the month preceding the sale, relief floors the lot at zero).
  Deferral happens iff that net gain is strictly positive.

### H1/H1b trigger (frozen)

At the close of the trigger session (last / second-to-last December session) of year `Y`:
1. the QLD\* position is non-empty (units > 0 after that session's ordinary open fill);
2. **any** FIFO lot has an unrealized loss under the engine's formula at the trigger session's
   close price and that session's USD/TRY, i.e. `lot_gain(lot) < 0`;
3. `year_gain[Y] > 0`, the calendar year's net realized TRY gain accumulated so far (all ordinary
   resize/exit realizations of `Y` up to and including the trigger session; the harvest itself is
   excluded from the test).

If all three hold: sell 100% of the units at the trigger session's close (fee
`units x close x 8 bps`), realizing the FIFO lots in year `Y`. The re-buy is **not** forced: it is
the ordinary fill at the next open against the target decided at the trigger session's close, so
it happens iff `|target - 0| > 0.5 pp`. The sold `|delta w|` is not counted as turnover (it is a
tax-timing round trip, reported separately as `harvests`).

## After-tax model (the engine, stated explicitly)

`research/tax_harvest_v1/tax_engine.py`, byte copy of the corrected multi-instrument engine
(`r2_tbill_combo_v1/tax_engine.py` = `vt_level_tax_v1/screen.py`; SHA-256 in the manifest).
Model of Turkish tax as the engine implements it:

- **Per calendar year, net.** Realized gains and losses of the same calendar year are summed per
  lot-book (`year_gain[Y]`); the progressive regime taxes `max(0, net)` — **losses do not carry
  forward** into the next year's base (a year with a net loss pays nothing and the loss is lost).
  The flat-20% bootstrap variant (B2) instead carries losses forward.
- **Trade date, not settlement date.** The tax year of a sale is the calendar year of the sale
  *trade date* (`days[i][:4]`); settlement is not modelled. Assumption stated in the report; H1b
  is the variant that would still work under a settlement-date reading.
- **TRY terms.** Each lot carries its acquisition USD/TRY buying rate and acquisition month. Gain
  = `units x sale_price x fx(sale) - units x cost_per_unit_usd x fx(acquisition)`; if Yi-UFE rose
  >= 10% between the month preceding the acquisition and the month preceding the sale, the cost is
  uplifted by that rise and the lot's gain is floored at zero (`Index_min_rise = 0.10`); the
  uplifted amount is reported as indexation relief.
- **Tariff.** 2026 schedule scaled by `Yi-UFE[Oct of year-1] / Yi-UFE[2025-10]`: 15% to 190,000
  TRY, 20% to 400,000, 27% to 1,000,000, 35% to 5,300,000, 40% above. Instalments on Mar 31 and
  Jul 31 of the following year, funded by selling the position if cash is short. Dividend
  withholding threshold 22,000 TRY (no dividends in this strategy). FIFO lots. Liquidation at the
  window end, and the liquidation-year tax is settled at the end.
- **Windows and capitals.** 2000-03-09 -> 2026-09-29, 2010-01-01 -> 2026-09-29,
  2017-01-03 -> 2026-09-29, each at **$100,000 and $1,000,000** of initial capital, after-tax
  CAGR on the **liquidated** terminal USD value.
- **Clean after-tax bootstrap** (`tax_asym_v2` / `vt_level_tax_v1` design, as re-used by
  `r2_tbill_combo_v1`): stationary block-20, seed 20261011, 1,000 paths of 10 measured years
  (2,520 sessions, +3 back-extension sessions, warm-up 253) drawn from QQQ open/close/dividend and
  DTB3; signals recomputed on the resampled price level; the **calendar stays real and fixed** on
  2015-09-01 -> 2026-09-30 with its realized USD/TRY and Yi-UFE, so FX/inflation regimes are not
  resampled. Two tax variants: **B1-recent** = the progressive regime above, **B2** = 20% flat on
  the year's net realized USD gain with loss carry-forward. Both at $100k and $1M.
- Legal assumptions (not tax advice): wash-sale-style rules do not exist in the model (the H1
  round trip is not disallowed); no BIST offsets, no other income, no FX-conversion cost on the
  tax payment, frozen 2026 thresholds, one CBRT buying-rate series. Deferral is only worth
  something in this model through (a) the tariff brackets/scale, (b) the indexation base reset and
  (c) the timing of the instalments; the report measures all three.

## Gates

Pre-registered. G1/G2 are pre-tax and deliberately loose (the overlays barely touch the market
exposure); the after-tax gates decide.

1. **G1 (both financings, m = 2):** `CAGR(arm) >= CAGR(R2) - 0.10 pp`.
2. **G2 (both financings, m = 2):** `maxDD(arm) <= maxDD(R2) + 1 pp`.
3. **G3 (after tax, progressive, liquidated, m = 2, base financing):** for **every** window x
   capital cell (3 windows x {$100k, $1M}), `after-tax CAGR(arm) > after-tax CAGR(R2)`.
4. **G4 (clean bootstrap):** for **every** capital x tax-model cell
   ($100k/$1M x B1-recent/B2), `median(after-tax CAGR(arm) - R2) >= 0` **and** share of paths with
   a positive delta `>= 55%`.
5. Reported but not gated: pre-tax turnover, harvest/deferral counts, per-year tax,
   deferred-vs-saved split, after-tax drawdown.

**Selection rule.** An arm is a candidate iff it passes G1, G2, G3 and G4. If none passes, the
verdict is FAIL and production R2 is unchanged. If several pass, select the one with the highest
B1-recent $100k median delta, tie-broken by the 2000-2026 $100k after-tax delta. No arm may be
promoted on a realized-history win against a failing bootstrap (the campaign's standing rule).

**Reported decompositions (required by the task).** Per calendar year, tax assessed in TRY for R2
and each arm on the 2000 window at $100k; the net total delta (= saved, permanent) and the sum of
the negative per-year deltas (= tax pushed out of harvest/December years, i.e. deferred); the full
H1/H1b harvest log (year, realized gain, year gain before/after).

## Verification plan

- `--smoke` (synthetic prices, no market data; run before this commit): `probe_gain` == the
  engine's own sell gain on 300 random lot books; overlay `none` reproduces the reference pre-tax
  `simulate_band` NAV path to < 1e-9; every arm finite; after-tax <= pre-tax.
- `--harness` (control only): R2 and C0 pre-tax CAGR / max DD / turnover reproduce
  `fast_reentry_v1/RESULTS.json` and `e2_integration_v1`'s deviation table at m = 1/2, base and
  calibrated, within 0.01 pp; the after-tax engine reproduces the R2 and C0 anchors at $100k on all
  three windows (CAGR, max DD) within 0.005 pp; the control's clean-bootstrap medians reproduce
  `r2_tbill_combo_v1` within 0.10 pp.
- `--run` twice must give byte-identical `RESULTS.json`.

## Amendments

(none)
