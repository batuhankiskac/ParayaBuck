# Gold risk-off out of sample v1 (2026-10-12)

**Gold in the risk-off state fails out of sample. Every gate fails, so gold
risk-off is closed and will not be revisited. VT35 is unchanged.**

`leverage_frontier_v1` found its strongest single effect on 2000–2026: gold
held at 0.98 in VT35's risk-off state, instead of zero cash, added +2.3 pp
CAGR. This campaign tested the same rule on 1975–1998, data the project had
never used. The result reverses:

- **P (Nasdaq Composite):**
  - GOLD returns 14.32% CAGR against CASH's 17.37% (−3.05 pp).
  - Max DD is 57.9% against 44.3% (+13.6 pp).
  - The bootstrap median is also lower: 19.26% against 19.83%.
- **S1 (Nasdaq-100) and S2 (S&P 500):** GOLD loses 1.7 pp and 2.0 pp a year.
- **Costs and financing:** it loses under every cost and financing variant.
- **T-bills** in the risk-off state beat zero cash on every index (+0.8 to
  +1.8 pp). They are report-only.

Evidence (removed from main on 2026-10-12 and preserved in tag
`archive/research/gold-riskoff-oos-v1-2026-10`) was in `research/gold_riskoff_oos_v1/`:

- `PREREGISTRATION.md`, `screen.py`, `download_data.py`, `VALIDATION.json`,
  `DATA_MANIFEST.json` and the data files
  `data/historical/gold_riskoff_oos_v1_*.csv` were committed in `f582e0a`,
  before any arm was simulated on real data.
- `RESULTS.json` and `SUMMARY.txt` come from one `screen.py --run`. The run
  is offline and takes about 18 s. Two runs gave byte-identical
  `RESULTS.json`.

## Method

- **Indices** (Yahoo, price only, no dividends):
  - **P** = ^IXIC.
  - **S1** = ^NDX, from its warm-up end, 1986-07-17.
  - **S2** = ^GSPC.
  - P and S2 are measured 1975-01-02 → 1998-12-30.
- **Instrument:** synthetic daily-reset 2x of each index's daily return,
  minus 0.95%/yr expense, minus DTB3 financing.
  - **Calibrated** (primary) adds 0.75%/yr, as in `leverage_frontier_v1`
    Amendment A1.
  - **Base** financing is reported, and gated only through H5.
- **Mechanics:** VT35, unchanged.
  - Each index's own close against its 200-session SMA.
  - Weight min(1, 0.35/vol20), capped at 0.98.
  - Signal on the close, trade at the next open; 0.5 pp band.
  - Cost 4 bps × m per unit traded, with m = 2 (gated) and m = 1.
- **Arms:**
  - **CASH:** zero yield in the risk-off state.
  - **GOLD:** gold at 0.98 in the risk-off state, with no vol target and a
    0.40%/yr drag. This is the `leverage_frontier_v1` Stage-2 definition.
  - **BILLS:** DTB3 − 0.10%, report-only.
- **Gold source.** The USD London fix is no longer obtainable from the
  Bundesbank, FRED or LBMA's public JSON. Gold is therefore the Bundesbank
  Frankfurt Stock Exchange fixing:
  - Converted as DM/kg ÷ Bundesbank USD fixing ÷ 32.1507.
  - Valid until 1998-12-30, so every window ends then.
  - Each session uses the first fixing after that session's close. That
    fixing is struck before the next New York open, so a fill is never at a
    price from before the signal.
- **Bootstrap:**
  - Stationary block bootstrap, mean block 20.
  - 5,000 paths of 250 warm-up + 2,520 sessions, seed 20261011.
  - Each index, its gold and DTB3 are resampled jointly from that index's
    window.

## Data validation (before the pre-registration)

**Gold against the World Bank Pink Sheet monthly London PM fix**, 1975-01 →
1998-12 (288 months):

| Check | Value |
|---|---:|
| Mean absolute difference | 0.34% (stop threshold 2%) |
| Mean signed difference | +0.31% (Frankfurt higher) |
| Max absolute difference | 11.0% in 1980-12 (597.32 vs 538.26)* |
| Correlation of monthly returns | 0.983 |
| Total return 1975-01 → 1998-12 | +64.6% vs +65.5% |

\* The Pink Sheet's 1980-12 value is below both neighbouring months. Every
other year's mean absolute difference is ≤ 0.78%.

- **Gold gaps:** a missing fixing is carried forward at most 2 sessions in
  any window. No gap longer than 3 sessions falls inside a window.
- **DTB3:** carried forward at most 1 session.
- **Opens:**
  - ^IXIC has no intraday data before 1985, so its open is set to the
    previous close.
  - Yahoo's raw ^NDX and ^GSPC opens also equal the previous close on most
    sessions.
  - So for most of the window, fills happen at the previous close for every
    index and every arm.

## Results (2x costs, calibrated financing)

| Index | Window | Arm | CAGR | Max DD | Boot. median | Boot. p5 | P(10-y CAGR < 0) | P(DD > 50%) |
|---|---|---|---:|---:|---:|---:|---:|---:|
| P ^IXIC | 1975-01 → 1998-12 | CASH | 17.37% | 44.3% | 19.83% | 2.90% | 2.2% | 17.4% |
| | | **GOLD** | **14.32%** | **57.9%** | **19.26%** | 0.72% | 4.2% | 45.0% |
| | | BILLS | 19.21% | 39.3% | 21.54% | 4.95% | 1.1% | 11.0% |
| | | index (price) | 16.14% | 36.0% | 16.21% | 4.38% | 1.1% | 6.7% |
| S1 ^NDX | 1986-07 → 1998-12 | CASH | 15.61% | 43.0% | 20.74% | 0.73% | 4.1% | 44.7% |
| | | **GOLD** | **13.87%** | **50.4%** | **19.54%** | −1.11% | 6.1% | 58.9% |
| | | BILLS | 16.37% | 41.5% | 21.57% | 1.80% | 3.3% | 40.4% |
| | | index (price) | 22.31% | 39.9% | 22.42% | 8.47% | 0.3% | 13.0% |
| S2 ^GSPC | 1975-01 → 1998-12 | CASH | 11.35% | 39.7% | 9.65% | −3.34% | 11.9% | 27.9% |
| | | **GOLD** | **9.34%** | **58.1%** | **8.96%** | −5.66% | 16.5% | 51.8% |
| | | BILLS | 12.91% | 34.2% | 10.93% | −1.76% | 8.5% | 20.5% |
| | | index (price) | 12.79% | 33.5% | 12.80% | 4.25% | 0.7% | 1.6% |

All rows use price indices. Missing dividends lower the absolute CAGRs (S&P
500 dividends were about 3–5%/yr then), but not the gap between arms,
because the in-trend leg is identical in every arm.

## Gates (all must pass)

| Gate | Condition | GOLD | CASH | Result |
|---|---|---:|---:|---|
| H1 | GOLD CAGR > CASH on P | 14.32% | 17.37% | **fail** |
| H2 | GOLD max DD ≤ CASH + 5 pp on P | 57.9% | 44.3% (limit 49.3%) | **fail** |
| H3 | Boot. median GOLD ≥ CASH on P | 19.26% | 19.83% | **fail** |
| H4 | GOLD ≥ CASH − 0.5 pp on S1 and S2 | S1 13.87% / S2 9.34% | 15.61% / 11.35% | **fail** (both) |
| H5 | H1 at 1x costs (calibrated) | 14.77% | 17.60% | **fail** |
| | H1 under base financing (2x) | 14.94% | 18.00% | **fail** |
| | H1 at 1x, base | 15.39% | 18.24% | **fail** |

Bootstrap medians on P, report-only: base financing 19.90% against 20.46%;
1x costs 19.77% against 20.07%. GOLD is behind in both.

## Sub-periods and stress (P unless stated; 2x costs, calibrated)

| Period | CASH | GOLD | BILLS |
|---|---:|---:|---:|
| 1975–1980 (gold bull) CAGR | 20.17% | 14.62% | 21.05% |
| 1981–1998 (gold bear) CAGR | 16.45% | 14.22% | 18.60% |
| S2: 1975–1980 / 1981–1998 | 3.56% / 14.07% | 3.67% / 11.29% | 5.14% / 15.62% |
| 1987 calendar year | +14.4% | +19.5% | +15.7% |
| October 1987 | −11.6% | −10.4% | −11.4% |
| S1 October 1987 | −17.3% | −19.4% | −17.2% |
| S2 1987 calendar year | +40.8% | +46.5% | +42.3% |

- **Gold's 1975–1980 bull market did not help.** On P, 19 risk-off episodes
  fall in 1975–1980. Gold rose in only 6 of them; chained over all 19, gold
  returned −22.6%. Gold's big gains (1978–80) came while the Nasdaq was in an
  uptrend and the arm held equities. On S2, the 1975–1980 risk-off episodes
  netted gold +6.1%, which barely moved the S2 result (+0.1 pp).
- **Where GOLD's drawdown comes from:**
  - Every arm's worst drawdown on P runs from 1983-06-24 to 1984-12-27.
  - Gold fell 11.4% inside the 1983-10 → 1984-08 risk-off episode.
  - GOLD's drawdown is 57.9% against CASH's 44.3%. GOLD did not recover
    until 1991-11; CASH recovered in 1986-03.
  - On S2, gold fell 20.6% in the 1981-08 → 1982-05 risk-off episode.
- **The 1987 crash.**
  - The trend exit came on 1987-10-16 for P and S2, and 1987-10-19 for S1.
  - Gold was flat to down over the following risk-off months: −3.2% on P,
    −10.9% on S1, 0.0% on S2.
  - GOLD helped the 1987 calendar year on P and S2 only through earlier
    episodes.
- **Risk-off episodes:**
  - P: 60 episodes, 25.7% of sessions; gold rose in 21.
  - S1: 43 episodes, 22.1% of sessions; gold rose in 18.
  - S2: 78 episodes, 24.8% of sessions; gold rose in 33.
  - Chained over every episode, the GOLD arm against CASH is −44% on P
    (log −0.58), −14% on S1 and −31% on S2.
- **Alignment sensitivity** (report-only): using the same-date fixing, as
  `leverage_frontier_v1` did, gives 14.15% / 58.6% DD on P, 14.37% on S1 and
  8.73% on S2. That is the same conclusion.

## Combined picture with `leverage_frontier_v1` (report-only)

| Window | GOLD | CASH | Difference |
|---|---:|---:|---:|
| 1975–1998, ^IXIC price (this campaign, calibrated) | 14.32% | 17.37% | −3.05 pp |
| 2000-08 → 2026-09, QQQ total return (`leverage_frontier_v1`, calibrated) | 16.39% | 14.12% | +2.27 pp |
| Chained, about 50 years | 15.39% | 15.66% | −0.27 pp |
| Chained, base financing | 15.99% | 16.27% | −0.27 pp |

- The two windows point in opposite directions. Over about 50 years, the
  gold leg roughly breaks even on CAGR. It cost drawdown in both windows:
  +13.6 pp here and +4.3 pp in 2000–2026.
- The 2000–2026 gain was the 2001–2011 gold bull market, a single regime.
  Before 1999, gold mostly fell while equities were below trend.
- The chained figure mixes indices and dividend treatments, and leaves out
  1999 → 2000-08.

## Caveats

- **Price-only indices.** Dividends are missing. This does not bias the arm
  comparison, because the in-trend leg is identical in every arm.
- **Frankfurt, not London.** The gold price is the Frankfurt fixing, not the
  London fix. The monthly gap is 0.34%; daily timing noise remains.
- **1999 not covered.** The window ends 1998-12-30.
- **Fills at the previous close.** Most fills, in every arm, are at the
  previous close because the opens are flat (see Data validation).
- **Synthetic leverage.** There was no 2x product before 2006.
- **Overlapping indices.** S1 and S2 overlap heavily with P.
- **No taxes.**
- **T-bills.** The BILLS result (+0.8 to +1.8 pp, lower DD) reflects the
  high real T-bill yields of 1979–1998. It is report-only, and it does not
  reopen `paper_config_v2`'s 2x-cost decision. That decision was made on
  2017–2026 data with a different definition (a sweep of all idle cash).

## Decision

Gold risk-off fails out of sample on every gate. Under the pre-registered
rule, it is **closed and will not be revisited**. VT35 remains the PAPER
identity, unchanged. No code, configuration or runtime changed.
