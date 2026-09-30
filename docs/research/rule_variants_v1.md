# Rule variants v1 (2026-09-29)

**None of the screened rule changes beats VT35 under the pre-registered gate,
so nothing goes to Stage B.** The closest miss is parking idle cash in T-bills
(F-BIL / F-SGOV). It passes at 1x costs but loses 2017–2021 by 0.13 pp at 2x
costs. Gold diversification (E-GLD) cuts drawdown by about 3 pp and raises
Sharpe, but it gives up 3–5 pp of CAGR, which conflicts with the max-profit
goal.

Close-to-close screen, 2017-01-03 → 2026-07-30, equity budget 0.98 (rest cash),
1x costs (4 bps per unit traded). Gate columns (1x / 2x costs) are: CAGR or
Sharpe up · DD ≤ VT35 + 2 pp · wins 2017–21 · wins 2022–26.

| Arm | CAGR | Sharpe | Max DD | 2017–21 | 2022–26 | Switches | Turnover/yr | CAGR 2x | Gate 1x / 2x |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| VOO hold (0.98) | 14.87% | 0.870 | 33.4% | 17.91% | 11.48% | 1 | 0.1 | 14.87% | ref |
| **VT35** | **29.37%** | **1.053** | **27.9%** | **37.14%** | **20.94%** | 35 | 4.7 | 29.13% | ref |
| A2 exit buffer 2% | 28.55% | 1.023 | 29.7% | 38.58% | 17.98% | 23 | 3.7 | 28.37% | nYYn / nYYn |
| A3 exit buffer 3% | 28.62% | 1.020 | 32.9% | 40.04% | 16.77% | 19 | 3.5 | 28.45% | nnYn / nnYn |
| B5 min hold 5 | 27.67% | 1.007 | 34.1% | 34.91% | 19.77% | 25 | 4.0 | 27.47% | nnnn / nnnn |
| B10 min hold 10 | 27.42% | 1.003 | 31.1% | 36.12% | 18.11% | 23 | 3.9 | 27.23% | nnnn / nnnn |
| C3 staged thirds | 29.36% | 1.050 | 29.1% | 37.87% | 20.22% | 35 | 3.6 | 29.18% | nYYn / YYYn |
| D5 QLD>5% / QQQ 0–5% | 24.93% | 0.962 | 29.6% | 30.56% | 18.63% | 121 | 20.7 | 23.90% | nYnn / nnnn |
| E-GLD-80 | 26.06% | 1.112 | 24.7% | 30.36% | 21.25% | 35 + 80 | 6.0 | 25.76% | YYnY / YYnY |
| E-GLD-70 | 24.10% | 1.144 | 23.1% | 26.76% | 21.09% | 35 + 80 | 6.6 | 23.78% | YYnY / YYnY |
| E-TLT-80 | 23.71% | 1.052 | 23.1% | 31.32% | 15.68% | 35 + 104 | 6.4 | 23.40% | nYnn / nYnn |
| E-TLT-70 | 20.65% | 1.048 | 20.7% | 28.14% | 12.86% | 35 + 104 | 7.1 | 20.31% | nYnn / nYnn |
| E-IEF-80 | 23.82% | 1.053 | 23.2% | 30.67% | 16.41% | 35 + 104 | 6.3 | 23.51% | YYnn / nYnn |
| E-IEF-70 | 20.81% | 1.051 | 20.8% | 27.19% | 13.90% | 35 + 104 | 7.1 | 20.47% | nYnn / nYnn |
| F-BIL idle cash | 29.63% | 1.060 | 28.0% | 37.17% | 21.42% | 35 | 8.2 | 29.21% | YYYY / YYnY |
| F-SGOV idle cash | 29.66% | 1.061 | 28.0% | 37.17% | 21.49% | 35 | 8.2 | 29.25% | YYYY / YYnY |

**Correction (2026-10-01, sizing convention).** This table was originally
produced with the budget multiplied into the strategy weight
(`budget × min(1, 0.35 / vol)`) at a 0.93 budget. Production never multiplies:
the risk overlay caps the strategy weight at `maximum_allocation`, so the
equity weight is `min(strategy_weight, allocation)`. The whole screen was
re-run with `BUDGET = 0.98` and `min(weight, budget)` in every arm
(`python3 research/rule_variants_v1/screen.py`), regenerating
`RESULTS.json`. Every verdict is unchanged: nothing is promoted, and the
relative ordering of the arms is the same.

Switches are trend-regime changes (entries + exits), counted from an initial
cash state. E arms show the QLD sleeve's switches plus the second sleeve's.
Turnover is traded notional as a multiple of NAV per year.

## Reproduction against Stage B

- **This screen matches Stage A2 exactly.** At 1.0 budget and 2x costs, the
  screen's VT35 gives 29.42% CAGR and 28.2% max DD, identical to
  `RESULTS_STAGE_A2.json`.
- **The Stage B gap is small.** Stage B VT35 made 28.98% CAGR, 29.2% max DD
  and Sharpe 1.068 at 1x costs. The screen gives:
  - 29.67% CAGR, 28.1% DD, Sharpe 1.053 at 1.0 budget (+0.69 pp CAGR, −1.1 pp DD);
  - 29.37% CAGR, 27.9% DD, Sharpe 1.053 at 0.98 budget (+0.39 pp CAGR, −1.3 pp DD).
- **Why Stage B sits between the two.** Stage B also runs the 0.05 crypto
  sleeve, uses engine fills, applies regulatory fees and, at the time, had a
  0.93 per-asset cap. The candidates are judged only against the screen's own
  VT35 at 0.98.

## Verdicts

- **a. Exit buffer (A2, A3): reject.** The buffer halves whipsaw (35 → 23 / 19
  switches) and helps 2017–2021, but 2022–2026 loses 3.0–4.2 pp. Staying in
  longer during the 2022 decline costs more than the saved whipsaws. A3 also
  raises max DD to 32.9% (+5.0 pp).
- **b. Minimum hold (B5, B10): reject.** Worse on every gate criterion. A
  cooldown also delays the exit that follows a false re-entry.
- **c. Staged thirds (C3): reject.** It is neutral overall: CAGR −0.01 pp,
  1.2 pp more drawdown and 23% less turnover. It fails 2022–2026 by
  0.7 pp.
- **d. Leverage by trend strength (D5): reject.** Holding QQQ in the 0–5% band
  gives up 4.4 pp of CAGR. The 5% boundary causes 121 switches and 20.7x
  turnover a year.
- **e. Second sleeve: reject.**
  - GLD is the only useful diversifier. E-GLD-70 has the best Sharpe in the
    screen (1.144) and a 23.1% DD, and E-GLD-80 beats VT35 in 2022–2026
    (+0.3 pp). Both give up 6.8–10.4 pp in 2017–2021 and 3.3–5.3 pp of
    full-window CAGR.
  - TLT and IEF trend sleeves lose on return in both halves. Bonds were a poor
    diversifier in the 2022 rate shock.
  - These arms would qualify only under a risk-first objective, which is not the
    operator's.
- **f. Idle cash in BIL / SGOV: reject by the letter of the gate (near miss).**
  - At 1x costs it passes everything: +0.26 / +0.29 pp CAGR, Sharpe
    1.060 / 1.061, DD unchanged.
  - At 2x costs it loses 2017–2021 by 0.14 pp. T-bill yields were about 0–2%
    then, and each regime switch adds an extra BIL round trip (turnover
    4.7 → 8.2x).
  - The benefit comes from 2022–2026 (+0.29–0.36 pp) and depends on the rate
    level. The long-history check by another agent should decide this.
  - 4 bps per side is conservative for BIL/SGOV, whose spreads are about 1 bp.

**Promoted to Stage B: none.** No config diff is proposed.

## Data

- **QQQ, QLD, SPY and VOO.** The archived profit_max_v1 CSVs, recovered
  byte-identical from tag `archive/research/profit-max-v1-2026-09` with
  `research/rule_variants_v1/recover_inputs.sh`. The SHA-256 is checked.
- **GLD, TLT, IEF, BIL and SGOV.**
  - A new Alpaca SIP split-adjusted daily download, saved in
    `research/rule_variants_v1/data/` and listed in `DATA_MANIFEST.json`.
  - Cash dividends are restated to the post-split basis. The only split in
    the window is BIL's 1:2 reverse split on 2017-11-30; its earlier rates are
    doubled.
  - The manifest records calendar-year dividend yields as a sanity check, for
    example TLT at 2.4% in 2017 and 4.3% in 2024, and BIL at 4.9–5.0% in
    2023–24.
  - The GLD bars are identical to the archived file, and GLD pays no dividends.

## Caveats

- **Same window as profit_max_v1.** 2017–2026 was a strong Nasdaq decade, and
  every variant is tested on only one path. The two halves are the only
  robustness check.
- **Model differences from production.** It is a close-to-close model: next-open
  fills, no crypto sleeve, cash earns 0 except in the F arms, and a flat
  4 bps × m cost.
- **Second-sleeve design.** The E arms use one fixed design: trend-filtered, no
  vol targeting, splits fixed at 80/20 and 70/30. Other gold designs were not
  explored, by design.
- **F-SGOV fallback.** Before SGOV's first bar (2020-05-28), F-SGOV uses BIL.

Files: `research/rule_variants_v1/` (PREREGISTRATION.md, screen.py,
download_data.py, recover_inputs.sh, DATA_MANIFEST.json, RESULTS.json, data/),
`tests/test_rule_variants_v1_screen.py`.
