# Leverage frontier v1 (2026-10-12)

**No cell or risk-off arm passes the pre-registered gate, so VT35 stays
unchanged.**

**Leverage and vol target (Stage 1).** Higher leverage (2.5x, 3x) and higher
vol targets (40%, 50%) raise historical CAGR by up to 4 pp/yr. Every cell
that clears the +1 pp return bar (G1) fails the tail-risk gate G3: in 10-year
bootstrap paths it ends with a drawdown above 70% far more often than VT35
does. Most of those cells also fail G2 (5th-percentile CAGR).

- The closest miss is **3x at a 35% vol target**:
  - CAGR 14.94% against 13.37%, with the same ~50% max DD.
  - P(DD > 70%) is 20.4% against C0's 12.4%, above the limit of 17.4%.
  - Under calibrated financing it also misses G2 by 0.03 pp.
- No setting of leverage or vol target lifts the bootstrap median above SPY's
  (best 8.49% against SPY's 8.68%).

**Risk-off asset (Stage 2).** In the risk-off state:

- **GOLD** made the largest improvement: +2.3 pp CAGR and +3.0 pp bootstrap
  median. It fails because its max DD is 4.3 pp worse than its base (the
  limit is +2 pp).
- **BOND10** adds 1.0 pp and cuts max DD from 49.6% to 41.1%. It fails
  because it wins only one of three sub-periods: 2000–08 wins, 2009–16 and
  2017+ lose.
- **BILLS** add 0.03 pp at 2x costs, against the +0.3 pp required.

Evidence: only this document is kept on main. The evidence directory
`research/leverage_frontier_v1/` and the downloaded inputs
`data/historical/leverage_frontier_v1_*.csv` were removed from main on
2026-10-12 and are preserved in tag `archive/research/leverage-frontier-v1-2026-10`
(commit shas below are from the original branch; on main the pre-registration
is `a1d8ab6`).

- `PREREGISTRATION.md`, including Amendment A1, was committed in `6429df1`
  before any arm was simulated.
- The code is `screen.py`; the outputs are `RESULTS.json`, `SUMMARY.txt`,
  `VALIDATION.json` and `DATA_MANIFEST.json`.
- The run is offline and deterministic and takes about 2.5 minutes. Two runs
  gave byte-identical `RESULTS.json`.

## Method

- **Mechanics:** the same as `trend_robustness_v2`:
  - Signal on QQQ's completed close relative to its 200-session SMA; trade at
    the next open.
  - 0.5 pp rebalance band; total weight capped at 0.98.
  - Cost 4 bps × m per unit traded, with m = 2 (gated) and m = 1.
  - Window 2000-03-06 → 2026-09-29.
- **Instruments:**
  - **NQ\*(L):** daily-reset L × QQQ total return, minus expense, minus
    (L − 1) × DTB3 financing. Expense is 0 at L = 1 (real QQQ), 0.95% at
    L = 1.5/2/2.5 and 0.84% (TQQQ) at L = 3. The 1.5x and 2.5x instruments
    are hypothetical: no such product exists.
  - **BILLS:** DTB3 − 0.10%.
  - **BOND10:** a 10-year constant-maturity par bond built from DGS10, minus
    0.15%.
  - **GOLD:** real GLD from 2004-11-19. Before that, GC=F futures minus
    0.40%, starting 2000-08-31.
- **Financing models (Amendment A1):**
  - *Base*: financing at DTB3 only.
  - *Calibrated*: an extra 0.75%/yr × (L − 1).
  - Validation showed the synthetic instruments beat real QLD by 0.77 pp/yr
    and real TQQQ by 1.64 pp/yr (correlation 0.996 and 0.999).
  - A Stage-1 cell had to pass G1–G7 under **both** models. Selection used
    the calibrated bootstrap median.
- **Bootstrap:**
  - Stationary block bootstrap; 5,000 ten-year paths, seed 20261011.
  - Blocks of 20 sessions are gated; blocks of 60 and 250 are report-only.
  - QQQ, SPY, DTB3, BOND10 and GOLD sessions are resampled jointly.
- **Harness check:** C0 reproduces `trend_robustness_v2`'s C0 historical and
  bootstrap numbers to within 0.0001 pp.
- **Stage 2:** the risk-off asset is held at 0.98 only while QQQ is below its
  SMA200, so in-trend behaviour is unchanged. Each arm is compared with its
  own cash base on the same window. GOLD uses 2000-08-31 →, because no clean
  gold series exists before that.

## Stage 1 — full grid (2x costs; bootstrap block 20, 10-year paths)

The five columns after the cell are the **base** financing model; the last
five are **calibrated** financing (+0.75%/yr × (L − 1)).

| Cell (L, VT) | CAGR | Max DD | Boot. median | Boot. p5 | P(DD>70%) | CAGR | Max DD | Boot. median | Boot. p5 | P(DD>70%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0x, 0.30 | 8.41% | 42.9% | 5.11% | −4.45% | 0.6% | 8.41% | 42.9% | 5.11% | −4.45% | 0.6% |
| 1.0x, 0.35 | 8.05% | 46.9% | 4.97% | −4.86% | 1.0% | 8.05% | 46.9% | 4.97% | −4.86% | 1.0% |
| 1.0x, 0.40 | 7.83% | 49.6% | 4.86% | −5.19% | 1.1% | 7.83% | 49.6% | 4.86% | −5.19% | 1.1% |
| 1.0x, 0.50 | 7.57% | 53.0% | 4.67% | −5.52% | 1.4% | 7.57% | 53.0% | 4.67% | −5.52% | 1.4% |
| 1.0x, none | 7.54% | 53.9% | 4.68% | −5.72% | 1.5% | 7.54% | 53.9% | 4.68% | −5.72% | 1.5% |
| 1.5x, 0.30 | 11.03% | 44.3% | 6.32% | −5.99% | 3.8% | 10.75% | 44.4% | 6.09% | −6.14% | 4.0% |
| 1.5x, 0.35 | 11.12% | 49.5% | 6.16% | −6.78% | 6.4% | 10.84% | 49.5% | 5.92% | −6.96% | 6.7% |
| 1.5x, 0.40 | 11.07% | 53.9% | 5.89% | −7.46% | 8.9% | 10.77% | 54.0% | 5.64% | −7.64% | 9.2% |
| 1.5x, 0.50 | 10.34% | 60.9% | 5.40% | −8.61% | 13.3% | 10.05% | 60.9% | 5.15% | −8.79% | 13.7% |
| 1.5x, none | 9.25% | 70.3% | 4.70% | −10.24% | 20.5% | 8.96% | 70.3% | 4.44% | −10.42% | 21.0% |
| 2.0x, 0.30 | 12.52% | 44.2% | 7.30% | −6.45% | 7.2% | 12.00% | 44.2% | 6.89% | −6.75% | 7.6% |
| **2.0x, 0.35 (C0)** | **13.37%** | **49.6%** | **7.40%** | **−7.56%** | **12.4%** | **12.81%** | **49.6%** | **6.96%** | **−7.86%** | **13.3%** |
| 2.0x, 0.40 | 13.88% | 54.5% | 7.33% | −8.67% | 18.2% | 13.30% | 54.6% | 6.87% | −9.00% | 19.1% |
| 2.0x, 0.50 | 13.80% | 62.7% | 6.64% | −10.34% | 27.7% | 13.20% | 62.7% | 6.16% | −10.69% | 28.6% |
| 2.0x, none | 10.98% | 81.4% | 4.29% | −14.75% | 49.0% | 10.40% | 81.5% | 3.81% | −15.07% | 50.5% |
| 2.5x, 0.30 | 13.19% | 44.2% | 7.68% | −6.77% | 9.7% | 12.50% | 44.4% | 7.18% | −7.17% | 10.3% |
| 2.5x, 0.35 | 14.37% | 49.3% | 7.99% | −8.10% | 17.5% | 13.60% | 49.4% | 7.43% | −8.51% | 18.6% |
| 2.5x, 0.40 | 15.22% | 54.3% | 8.13% | −9.39% | 24.9% | 14.40% | 54.4% | 7.49% | −9.85% | 26.3% |
| 2.5x, 0.50 | 16.24% | 63.1% | 7.66% | −11.69% | 39.3% | 15.36% | 63.2% | 6.97% | −12.15% | 41.0% |
| 2.5x, none | 12.03% | 88.7% | 3.00% | −19.80% | 74.8% | 11.13% | 88.7% | 2.24% | −20.23% | 75.6% |
| 3.0x, 0.30 | 13.64% | 44.4% | 7.80% | −7.05% | 10.6% | 12.83% | 44.6% | 7.19% | −7.47% | 11.6% |
| 3.0x, 0.35 | 14.94% | 49.8% | 8.34% | −8.40% | 20.4% | 14.01% | 50.1% | 7.65% | −8.89% | 21.5% |
| 3.0x, 0.40 | 15.97% | 54.1% | 8.49% | −9.89% | 30.2% | 14.96% | 54.2% | 7.76% | −10.42% | 31.9% |
| 3.0x, 0.50 | 17.46% | 62.9% | 8.35% | −12.58% | 48.3% | 16.32% | 63.0% | 7.46% | −13.14% | 50.1% |
| 3.0x, none | 12.41% | 93.3% | 0.83% | −25.10% | 90.2% | 11.20% | 93.4% | −0.17% | −25.73% | 90.9% |

SPY buy-and-hold: 8.47% CAGR and 55.2% max DD on the window. Its block-20
bootstrap median is 8.68% (p5 −0.75%).

### Gates

The table covers every cell that passes G1 under at least one model, plus
2.5x/0.35. The other 18 cells fail G1 under both models.

| Cell | Model | G1 +1 pp | G2 boot. median / p5 | G3 DD ≤ 65%, P(DD>70%) ≤ C0 + 5 pp | G4 2017+ | G5 2 of 3 sub-periods | G6 G1 at 1x | G7 plateau | Eligible |
|---|---|---|---|---|---|---|---|---|---|
| 2.5x, 0.35 | base | pass | pass | **fail** (17.5% vs 17.4%) | pass | pass | pass | **fail** (2.5x/0.30 13.19%) | no |
| 2.5x, 0.35 | calibrated | **fail** | pass | **fail** | pass | pass | **fail** | **fail** | no |
| 2.5x, 0.40 | both | pass | **fail** (p5) | **fail** | pass | pass | pass | pass | no |
| 2.5x, 0.50 | both | pass | **fail** | **fail** | pass | pass | pass | **fail** (2.5x/none) | no |
| 3.0x, 0.35 | base | pass | pass | **fail** (20.4% vs 17.4%) | pass | pass | pass | pass | no |
| 3.0x, 0.35 | calibrated | pass | **fail** (p5 −8.89% vs −8.86%) | **fail** (21.5% vs 18.3%) | pass | pass | pass | pass | no |
| 3.0x, 0.40 | both | pass | **fail** | **fail** | pass | pass | pass | pass | no |
| 3.0x, 0.50 | both | pass | **fail** | **fail** | pass | pass | pass | **fail** (3.0x/none) | no |

No cell is eligible under either model, so Stage 1 selects nothing. Stage 2
therefore ran on C0 only. Its gating model is base; calibrated is reported.

## Stage 2 — risk-off asset on C0 (2x costs, base financing)

| Arm | Window | CAGR arm / base | Max DD arm / base | Boot. median arm / base | Sub-periods won | 2022 return | Result (2x / 1x) |
|---|---|---:|---:|---:|---:|---:|---|
| C0 + BILLS | 2000-03-06 → | 13.40% / 13.37% | 47.2% / 49.6% | 7.39% / 7.40% | 2 | −19.2% / −20.2% | fail: CAGR +0.03 pp, boot. median / pass at 1x |
| C0 + BOND10 | 2000-03-06 → | 14.39% / 13.37% | 41.1% / 49.6% | 8.06% / 7.40% | 1 (2000–08 only) | −30.6% / −20.2% | fail: sub-periods / fail |
| C0 + GOLD | 2000-08-31 → | 16.97% / 14.69% | 45.9% / 41.6% | 9.05% / 6.08% | 2 | −21.8% / −20.2% | fail: max DD +4.3 pp / fail |

Calibrated financing changes no outcome. Arm and base both fall by about
0.55 pp: BILLS 12.85% / 12.81%, BOND10 13.83% / 12.81%, GOLD 16.39% / 14.12%.

## Worst cases (2x costs, base financing)

| Arm | 2000–02 return / DD | 2008 return; 2007–09 DD | 2022 return / DD | Longest underwater | Worst year | P(10-y CAGR < 0) | P(DD > 50%) | Boot. > SPY |
|---|---|---|---|---|---|---:|---:|---:|
| **C0** | −41.6% / 43.4% | −18.3%; 35.0% | −20.2% / 21.3% | 7.5 y (2000-03 → 2007-10) | 2022 −20.2% | 23.0% | 61.4% | 46.5% |
| 3.0x, 0.35 | −41.2% / 43.1% | −18.4%; 35.2% | −19.9% / 21.0% | 9.8 y (2000-03 → 2009-12) | 2005 −23.9% | 23.2% | 74.0% | 50.5% |
| 2.5x, 0.40 | −46.1% / 47.9% | −20.9%; 39.3% | −22.6% / 23.9% | 7.6 y | 2022 −22.6% | 24.7% | 80.3% | 50.1% |
| 3.0x, 0.50 | −54.3% / 56.2% | −25.7%; 46.9% | −27.5% / 29.0% | 9.8 y | 2022 −27.5% | 28.2% | 94.8% | 50.5% |
| C0 + BOND10 | −26.0% / 36.5% | −4.1%; 29.8% | −30.6% / 34.0% | 3.6 y | 2022 −30.6% | 21.1% | 61.1% | 49.3% |
| C0 + GOLD (from 2000-08) | −11.8% / 28.6% | −8.3%; 35.0% | −21.8% / 31.1% | 5.9 y (2004-01 → 2009-12) | 2022 −21.8% | 16.1% | 59.5% | 57.2% |

The C0 + GOLD bootstrap row uses the gold pool. On that pool, C0's own
numbers are 26.1%, 60.8% and 41.2%.

## Findings

- **Leverage buys historical return and pays for it in the tail.**
  - Going from 2x to 3x at the same 35% vol target adds 1.6 pp/yr
    historically (1.2 pp calibrated) without raising the full-window max DD.
    The vol target cuts exposure when volatility is high.
  - In bootstrap worlds, the share of 10-year paths with a drawdown over 70%
    rises from 12% to 20%.
  - The longest underwater spell grows from 7.5 to 9.8 years: the 2000 peak
    is not regained until 2009-12.
  - G3 caps exactly this, and it binds for every cell that clears G1.
- **The vol target matters more than the leverage.**
  - Without a vol target, every leveraged cell collapses: 2x reaches 81%
    max DD, 3x 93%.
  - From 1.5x upward, a 30% target gives the lowest max DD (≈ 44%) and the
    lowest P(DD > 70%) in its row. From 2x upward it costs 1.4–3.8 pp of
    historical CAGR.
  - 35% sits where historical CAGR has mostly been gained and the tail has
    not yet blown out.
- **Short-trend worlds still favour SPY.** Across the grid the block-20
  median never beats SPY's 8.68%. With 250-session blocks, which keep trends
  intact, C0 reaches 13.4% and 3x/0.35 reaches 15.0% (SPY ≈ 9.0%). This is
  the same finding as `stress_robustness_v1` and `trend_robustness_v2`.
- **Unlevered trend (1x) does not beat SPY** over 2000–2026: 7.5–8.4% CAGR
  against 8.47%.
- **Gold in the risk-off state is the strongest single effect seen.**
  - It adds +2.3 pp CAGR and +3.0 pp bootstrap median.
  - P(10-y CAGR < 0) falls from 26% to 16%, and the share of paths beating
    SPY rises from 41% to 57%.
  - It still fails the pre-registered max-DD condition. The arm's worst
    drawdown fell in the 2004–06 whipsaw period (peak 2004-01, trough
    2006-10, recovered 2009-12). That is not where C0 has its own worst
    drawdown on this window (2015-07 → 2016-07). 2022's drawdown was also
    worse: 31% against 21%.
  - Much of the gain comes from gold's 2001–2011 bull market, a single
    regime. Under the rule nothing is tuned after the fact, so this stays a
    report-only observation.
- **Bonds in the risk-off state help in deflationary busts and hurt in
  inflationary ones.**
  - In 2000–08: +5.6 pp/yr against cash.
  - In 2022 they lost alongside stocks: −30.6% against −20.2%.
  - They also lag in 2009–16 and 2017+.
- **T-bills** roughly double turnover (6.5 to 12.8 per year). At 2x costs
  the switching cost eats the yield.

## Caveats (from the pre-registration)

- **In-sample search.** A 25-cell grid was searched on a single history, and
  the bootstrap resamples that same history.
- **BILLS partly seen.** The T-bill result was partly known beforehand from
  `stress_robustness_v1` and `rule_variants_v1`.
- **BOND10 vs IEF.** BOND10 carries more duration than IEF (beta 0.84,
  −0.68 pp/yr CAGR).
- **Gold data.** The gold window is shorter and its pool differs. The
  pre-2004 gold is a futures proxy (correlation 0.89 with GLD, no drift).
- **1.5x and 2.5x.** These leverage levels are hypothetical.
- **Fills and taxes.** Synthetic risk-off legs fill at the previous close.
  Taxes are not modelled.

## Decision

VT35 stays the PAPER identity with no parameter change. No candidate
qualifies for a production step. This campaign is closed. No code,
configuration or runtime changed.
