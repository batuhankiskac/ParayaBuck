# Profit maximization v1 (2026-09-25)

Operator goal: maximize long-run ending wealth and beat index buy-and-hold,
first with no drawdown limit, then (on request) with the worst loss held at or
below the index's. The bar is **SPY/VOO buy-and-hold**, not the previous bot.

Decision: **`equity_leveraged_trend_v1` with volatility targeting (arm VT35) is
the PAPER equity identity since 2026-09-25 (operator approved).** It holds QLD (2x Nasdaq-100) while QQQ's
completed close is above its 200-session SMA, sized
`min(1, 0.35 / 20-session realized QLD volatility)`, and holds cash otherwise.
Decisions are daily. It runs at equity 0.93 plus the unchanged crypto 0.05.
The residual-momentum V1/V2/V3 identities and the dividend/gold sleeve were
removed from the code at the switch (recoverable from `ec4e2df`). Evidence
(pre-registration, data manifest, screens, arm configs, runs, results) is at
tag `archive/research/profit-max-v1-2026-09`.

## Findings

- **The current bot does not beat the index.** In the shared-ledger replay
  (2017-01-03 → 2026-07-30), VOO buy-and-hold made 14.5% CAGR. The shipped
  V3 + sleeve made 6.7–7.1%, and V3 alone at 0.93 investment made 8.3%. Most of
  the gap is structural: 40% idle cash, a 5% drawdown entry halt and a 0.5%
  daily-loss latch.
- **Dividend data defect.** Alpaca cash dividend rates are per share as paid,
  but the campaign bars are split-adjusted. That tripled SCHD's pre-2024 yield
  in the sleeve campaigns. With rates restated to post-split shares:
  - The shipped dividend sleeve P still beats V3-only A (7.08% vs 6.69%).
  - The gold variant G3 falls below P (6.72%). The G3 ship
    (`docs/research/gold_sleeve_production_v1.md`, pruned; tag
    `archive/research/pre-prune-2026-09-30`) was based on inflated data.
- **Leverage, not the filter, drives return.** The filter mainly shortens the
  worst losses:
  - QLD unfiltered: 31.9% CAGR, 62% max drawdown.
  - QLD with the 200-SMA filter (Q2): 30.8% CAGR, 39% max drawdown.

## Stage B (production engine, shared ledger, 100k start)

| Arm | CAGR | Sharpe | Max DD | 2022 | 2017–21 | 2022–26 | Ending NAV |
|---|---:|---:|---:|---:|---:|---:|---:|
| H_VOO (VOO hold) | 14.54% | 0.885 | 32.0% | −19.4% | 17.16% | 11.77% | 367k |
| Q2 (QLD trend) | 30.84% | 1.014 | 39.1% | −27.6% | 40.82% | 20.80% | 1.31M |
| **VT35 (QLD trend, vol target 35%)** | **28.98%** | **1.068** | **29.2%** | **−22.0%** | **36.79%** | **21.72%** | **1.14M** |
| Q2BH (QLD hold, control) | 31.92% | 0.870 | 62.3% | −58.8% | 49.39% | 15.22% | 1.42M |
| Q3 (TQQQ trend, report only) | 42.23% | 0.992 | 53.8% | −38.1% | 59.07% | 25.94% | 2.91M |
| V3 at 0.93 | 8.34% | 0.596 | 26.8% | −20.4% | 12.93% | 3.55% | 215k |

- **VT35 at 2x / 3x costs:** 28.70% / 28.38% CAGR (29.5% / 29.9% max DD),
  against VOO's 14.44% / 14.35%.
- **VT35 excess over H_VOO:** +14.44 pp CAGR at 1x costs. For reference, pure
  100% VOO total return with no costs made 15.12% CAGR with a 34.0% max DD.
- **Gates:** VT35 passes every gate, including a max drawdown at or below VOO's.
  Q2 passes every return gate but not the drawdown rule added for the
  sweet-spot search.
- **3x excluded:** 3x arms were excluded before the runs. At Nasdaq-100
  volatility the growth-optimal leverage is about 2x.

**2026-09-28 re-run on main after the dividend split-basis fix.** The Stage B
replay credited QLD dividends on split-adjusted bars with raw per-share rates,
over-crediting pre-2025 dividends by up to 16x. Re-running the same replay
(2017-01-03 → 2026-07-30, $100k, 1x costs) on corrected main changed VT35 as
follows (old → new):

- CAGR 29.34% → 28.98%; total return 1072.6% → 1041.5% (ending NAV 1.17M →
  1.14M)
- Sharpe 1.078 → 1.068; Calmar 1.006 → 0.994; max DD 29.16% (unchanged)
- Dividend income $10,903 → $4,767
- 2x / 3x costs CAGR 28.95% / 28.66% → 28.70% / 28.38%
- Excess CAGR over H_VOO +14.80 pp → +14.44 pp

H_VOO is unchanged (14.54% CAGR, 31.98% DD, Sharpe 0.885). The table above
shows the corrected VT35 CAGR, Sharpe and ending NAV. Its 2022 and sub-period
columns are from the original run and were not re-reported. The other arms
were not re-run.

## Method

- **Stage A:** close-to-close screen of buy-and-hold and 200-SMA trend rules on
  SPY/QQQ and their 2x/3x ETFs, with gates against SPY at 2x costs.
- **Stage A2:** risk sweet-spot screen with a rule fixed beforehand: keep arms
  with max DD ≤ SPY's that beat SPY over the full window and both halves, then
  select the highest CAGR. The arms were QLD/QQQ mixes, volatility targets of
  25% and 35%, and 30% gold overlays. VT35 was selected (29.4% CAGR, 28.2% max
  DD in the screen).
- **Stage B:** runs the production strategy through the product engine and
  shared ledger at 1x/2x/3x costs.
- **Robustness:** SMA length and weekly-check variants move QLD-trend CAGR
  between about 17% and 32%. The parameters are the literature default (200),
  not tuned.

## Caveats

- **One exceptional window.** 2017–2026 was a strong Nasdaq decade. Alpaca data
  starts in 2016, so a 2000–2002 style decline (Nasdaq −83%) is untested. A
  multi-year bear market with repeated false re-entries could cost far more
  than the 29% worst loss seen here.
- **Concentration.** The strategy concentrates in one leveraged ETF on one
  index. Daily-reset leverage decays in sideways, volatile markets.
- **Idle cash.** Cash earns nothing in the model. In downtrends the account
  sits in cash.
- **Risk limits.** The production switch widened the risk limits:
  - cash buffer 0.40 → 0.02
  - per-asset cap 0.10 → 0.93
  - gross 0.60 → 0.98
  - daily-loss latch 0.005 → 0.20
  - drawdown halt 0.05 → 0.75 (catastrophe brake)
  - QQQ/QLD become references, with `trade_reference_symbols: true`
- **Leveraged classification.** QLD is not in the local
  leveraged/inverse classification list, although it is a 2x ETF.
