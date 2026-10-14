# ewma_band_v1 — pre-registration (2026-10-12)

Written, and committed with `screen.py`, `DATA_MANIFEST.json` and the data
copies, **before any non-control arm was simulated on any real data**. The
only computations on real data before this commit are `screen.py --harness`
(C0 = production E2 only: the e2_integration_v1 deviation reproduction, the
band-simulator equality check and the EWMA identity check). The full `--run`
path was smoke-tested on randomly generated synthetic prices only.

## Seen before writing

- **Production E2** (`e2_integration_v1`, tag
  `archive/research/e2-integration-v1-2026-10`): `equity_leveraged_trend_v1`
  1.1.0, research screen 2000-03-09 → 2026-09-29: 14.67% CAGR / 49.6% max DD
  (base, m = 2), 14.95% (m = 1), 14.10% (calibrated, m = 2), screen turnover
  5.97/yr; 2017-01 → 2026-07 31.42% / 32.5%. Stage B 1x NAV 1,329,642.62,
  31.05% CAGR / 32.19% max DD, turnover 3.84/yr, 13 in-market episodes.
- **`timesfm_v1` V0** (EWMA vol λ 0.94 on **VT35**, not on E2): +0.11 pp
  CAGR (13.67% vs 13.56%), max DD 46.1% → 43.7%, P1 and P2 won, 2017+
  −1.0 pp/yr (29.74% vs 30.73%), turnover −3%. Better vol forecast (QLIKE
  0.318 vs 0.400). EWMA on E2 has never been run.
- `signal_ensemble_v1`, `e2_confirmation_v1` (E2 vs SMA-only; bootstrap
  edge ≈ 0), `trend_robustness_v2`, `paper_config_v2`,
  `execution_model_audit_v1` (Stage B and live share decide-at-close /
  fill-next-open; the 0.5 pp band is the shared `execution.rebalance_band_pct`).
- The owner's statement that most production turnover (~3.8–4.7×/yr) comes
  from daily vol resizes rather than trend flips. **No band wider than
  0.5 pp has been simulated in this project's research record that I know of.**
- General market history of every window below.

## Question

On top of production E2 (C0), does (1) RiskMetrics EWMA vol instead of
vol20, (2) a wider resize band, or (3) both, improve the PAPER rule? A pass
leads to a Stage B replay on the production engine and a written
production-change specification; **this campaign changes no `src/` or
`config/`**.

## Data (no network)

- Main: `data/historical/stress_robustness_v1_{daily,dtb3}.csv` (committed).
- OOS ^GSPC: `data/historical/ewma_band_v1_gspc.csv` = byte copy of
  `signal_ensemble_v1_gspc.csv` (tag `archive/research/signal-ensemble-v1-2026-10`).
- OOS M1–M5: `data/historical/ewma_band_v1_{index,tb3ms}.csv` = byte copies
  of `e2_confirmation_v1_{index,tb3ms}.csv` (tag
  `archive/research/e2-confirmation-v1-2026-10`).
- Harness reference: `E2_INTEGRATION_DEVIATION.json` = byte copy of
  `research/e2_integration_v1/DEVIATION.json` (tag
  `archive/research/e2-integration-v1-2026-10`).
- SHA-256 of every input in `DATA_MANIFEST.json`.

## Mechanics (unchanged)

`trend_robustness_v2` / `signal_ensemble_v1` / `e2_integration_v1`: synthetic
QLD\* = daily-reset 2x on QQQ total return, 0.95%/yr expense + DTB3
(base) or DTB3 + 0.75% (calibrated); decide at the completed close, trade at
the next open; total weight cap 0.98; cost 4 bps × m per unit traded (m = 2
primary, m = 1 gated through G6); idle cash at zero. Main window 2000-03-09 →
2026-09-29.

## Signal (all arms) — production E2 1.1.0 on QQQ split-adjusted price close

At least 2 of: close > SMA200; 252-session price return > 0 (zero hurdle);
close > midpoint of max/min of the 126 closes ending at t.

## Arms (eight; no others)

`vt(v) = min(1, 0.35/v)`; target = E2 · vt, capped at 0.98.

- **vol20:** annualized sample std of the last 20 daily simple returns of
  the instrument (production `realized_volatility`).
- **EWMA:** windowed RiskMetrics, λ = 0.94, on the instrument's daily simple
  returns. At close t the window is the **252 returns ending at t**; σ² is
  seeded with the mean of the first 20 squared returns of the window, then
  σ² ← λσ² + (1 − λ)r² over the remaining 232; vol = √(252σ²). (Seed weight
  0.94²³² ≈ 6·10⁻⁷. Using a fixed window instead of `timesfm_v1`'s
  from-inception recursion makes the value independent of how much history a
  process holds, so a production implementation is deterministic across
  restarts; it needs 253 closes, which equals E2's existing
  `minimum_history_bars`.)
- **Resize band B:** an entry (held 0 → target > 0) or exit (held > 0 →
  target 0) always trades fully. While held > 0 and target > 0, the position is
  rebalanced to the full target only if |target − held| > B (held = weight
  after the overnight move, as in the precedent engine). C0's B = 0.5 pp is
  the existing band.

| Arm | Vol | Band |
|---|---|---|
| **C0** | vol20 | 0.5 pp (production E2) |
| **W1** | EWMA | 0.5 pp |
| **B25 / B50 / B100** | vol20 | 2.5 / 5 / 10 pp |
| **WB25 / WB50 / WB100** | EWMA | 2.5 / 5 / 10 pp |

The three bands are the whole grid (pre-registered; no other value is run).
WB25/WB50/WB100 are computed so the selection rule below can apply its
plateau test to WB; only **WB = WB_{x\*}** is a candidate for selection.

## Gates (all must pass; "both" = base and calibrated financing)

- **G1 (return, m = 2, both):** CAGR ≥ C0, **or** (Calmar ≥ 1.1 × C0 and
  CAGR ≥ C0 − 0.5 pp).
- **G2 (m = 2, both):** max DD ≤ C0 + 1 pp.
- **G3 (m = 2, both):** 2017-01-03 → end CAGR ≥ C0 − 1 pp.
- **G4 (m = 2, base):** CAGR > C0 in ≥ 2 of 3 sub-periods: P1 2000-03-09 →
  2008-12-31, P2 2009 → 2016, P3 2017 → end.
- **G5 (both):** main block-20 bootstrap — stationary, mean block 20, seed
  20261011, 5,000 ten-year paths, draws identical to `signal_ensemble_v1`
  (incl. its 3-session back-extension), m = 2; each drawn session carries QQQ
  overnight (with dividend for the instrument, without for the price-signal
  series) and intraday factors and DTB3 jointly; all arms on the same paths.
  **Median over paths of (arm CAGR − C0 CAGR) ≥ 0.**
- **G6 (m = 1, both):** G1 at 1x costs.
- **G7 (out of sample, historical, calibrated, m = 2):** six windows —
  ^GSPC price 1957-01-02 → 1999-12-31 (opens := previous close, as
  `signal_ensemble_v1` G8) and `e2_confirmation_v1`'s M1–M5 (same windows,
  open rule, T-bill series and calibrated financing). All signals on each
  index's price close with the production zero TSM hurdle (so C0 here is
  production E2, not `e2_confirmation_v1`'s E2). Pass iff median over the six
  of (arm − C0) CAGR ≥ 0, **and** arm CAGR ≥ C0 − 0.5 pp in ≥ 5 of 6, **and**
  max DD ≤ C0 + 3 pp in ≥ 5 of 6 and never > C0 + 8 pp.
- **G8 (out of sample, pooled bootstrap):** `e2_confirmation_v1`'s K5
  procedure (block 20, seed 20261011, one generator over M1..M5 in order,
  2,000 paths × (252 warm-up + 2,520) per market, m = 2, calibrated), all
  arms on the same paths: pooled median over 10,000 paths of
  (arm − C0) CAGR ≥ 0.
- **G9 (turnover, m = 2, base):** band arms (B_x, WB_x): turnover/yr ≤ C0's.
  W1: ≤ 2 × C0's (precedent G7).

## Band selection and plateau rule (stated before any result)

- **Plateau:** arm A_x (A ∈ {B, WB}) is plateau-eligible iff at least one
  grid neighbour (2.5 ↔ 5, 5 ↔ 10) passes G1, G2 and G3 under both
  financing models. An isolated passing value is not eligible.
- **x\*:** among B_x that pass all gates and are plateau-eligible, the one
  with the highest base m = 2 CAGR (tie: Calmar). If none, x\* = the B_x with
  the highest base m = 2 CAGR (fallback, recorded as such). **WB = WB_{x\*}.**
- **Selected arm:** among {W1 if it passes, every passing plateau-eligible
  B_x, WB if it passes and is plateau-eligible}, the highest base m = 2 CAGR
  (tie: Calmar). None → production E2 unchanged.

## If an arm is selected

Stage B replay on the production engine (the archived `e2_integration_v1`
runner adapted in this research folder; window 2017-01-03 → 2026-07-30,
$100k, QQQ/SPY/QLD substitute file), C0 vs the selected arm at 1x and 2x.
C0 must reproduce E2 1x NAV 1,329,642.62. Any band/EWMA expression lives only
in the research copy (a research strategy subclass and/or the Stage B config's
`execution.rebalance_band_pct`); nothing in `src/` or `config/` changes. The
Stage B result is reported, not gated (precedent `e2_integration_v1`), except
that a C0 reproduction failure stops it.

## Report only

Per arm: Sharpe, sub-period and calendar-year returns, the 2017-01-03 →
2026-07-30 window, time in market, **round trips/yr, resizes/yr, sells/yr
(exits + downward resizes; relevant to Turkish capital-gains taxation),
turnover/yr split into entries+exits and resizes**, for the full window and
2017+; bootstrap medians and turnover; per-market OOS numbers and per-market
bootstrap medians; count of sessions with an in-trend target below 10%
(where an engine-level band would differ from the strategy-level rule).

## Caveats known now

- Single history; EWMA's earlier pass was on VT35, and its 2017+ cost was
  seen. The OOS windows are price-only and partly seen (C0's SMA-only twin
  and E2-with-T-bill-hurdle were run there; production E2 with zero hurdle
  and all non-control arms were not).
- The band rule is evaluated at the open after the overnight move (precedent
  engine); the production engine checks drift at the decision close and
  fills at the next open. Stage B measures that difference.
- No parameter is tuned after results are seen; no arm, band value or window
  is added or removed.
