# Rule variants v1 (2026-09-29)

**None of the screened rule changes beats VT35 under the pre-registered gate,
so nothing goes to Stage B.** The closest miss is parking idle cash in T-bills
(F-BIL / F-SGOV). It passes at 1x costs but loses 2017–2021 by 0.13 pp at 2x
costs. Gold diversification (E-GLD) cuts drawdown by about 3 pp and raises
Sharpe, but it gives up 3–5 pp of CAGR, which conflicts with the max-profit
goal.

Close-to-close screen, 2017-01-03 → 2026-07-30, equity budget 0.93 (rest cash),
1x costs (4 bps per unit traded). Gate columns (1x / 2x costs) are: CAGR or
Sharpe up · DD ≤ VT35 + 2 pp · wins 2017–21 · wins 2022–26.

| Arm | CAGR | Sharpe | Max DD | 2017–21 | 2022–26 | Switches | Turnover/yr | CAGR 2x | Gate 1x / 2x |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| VOO hold (0.93) | 14.16% | 0.871 | 32.0% | 17.01% | 10.97% | 1 | 0.1 | 14.16% | ref |
| **VT35** | **27.67%** | **1.052** | **26.4%** | **34.86%** | **19.85%** | 35 | 4.6 | 27.45% | ref |
| A2 exit buffer 2% | 26.94% | 1.023 | 28.0% | 36.23% | 17.09% | 23 | 3.6 | 26.76% | nYYn / nYYn |
| A3 exit buffer 3% | 27.00% | 1.020 | 31.1% | 37.57% | 15.97% | 19 | 3.4 | 26.83% | nnYn / nnYn |
| B5 min hold 5 | 26.11% | 1.007 | 32.3% | 32.82% | 18.77% | 25 | 3.9 | 25.92% | nnnn / nnnn |
| B10 min hold 10 | 25.87% | 1.003 | 29.5% | 33.92% | 17.23% | 23 | 3.8 | 25.69% | nnnn / nnnn |
| C3 staged thirds | 27.66% | 1.050 | 27.5% | 35.52% | 19.18% | 35 | 3.6 | 27.48% | nYYn / YYYn |
| D5 QLD>5% / QQQ 0–5% | 23.63% | 0.964 | 28.0% | 28.88% | 17.75% | 121 | 19.7 | 22.67% | nYnn / nnnn |
| E-GLD-80 | 24.32% | 1.114 | 23.1% | 28.25% | 19.91% | 35 + 80 | 5.7 | 24.04% | YYnY / YYnY |
| E-GLD-70 | 22.49% | 1.146 | 21.7% | 24.88% | 19.77% | 35 + 80 | 6.2 | 22.19% | YYnn / YYnn |
| E-TLT-80 | 22.08% | 1.052 | 21.7% | 29.09% | 14.65% | 35 + 104 | 6.0 | 21.79% | nYnn / nYnn |
| E-TLT-70 | 19.23% | 1.048 | 19.4% | 26.15% | 12.00% | 35 + 104 | 6.7 | 18.91% | nYnn / nYnn |
| E-IEF-80 | 22.19% | 1.053 | 21.7% | 28.49% | 15.33% | 35 + 104 | 6.0 | 21.90% | YYnn / nYnn |
| E-IEF-70 | 19.38% | 1.051 | 19.4% | 25.25% | 13.00% | 35 + 104 | 6.7 | 19.06% | nYnn / nYnn |
| F-BIL idle cash | 27.92% | 1.060 | 26.5% | 34.89% | 20.30% | 35 | 7.9 | 27.52% | YYYY / YYnY |
| F-SGOV idle cash | 27.95% | 1.061 | 26.5% | 34.89% | 20.36% | 35 | 7.9 | 27.55% | YYYY / YYnY |

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
  - 27.67% CAGR, 26.4% DD, Sharpe 1.052 at 0.93 budget (−1.31 pp CAGR, −2.8 pp DD).
- **Why Stage B sits between the two.** Stage B also runs the 0.05 crypto
  sleeve, uses engine fills, applies regulatory fees and has a 0.93 per-asset
  cap. The candidates are judged only against the screen's own VT35 at 0.93.

## Verdicts

- **a. Exit buffer (A2, A3): reject.** The buffer halves whipsaw (35 → 23 / 19
  switches) and helps 2017–2021, but 2022–2026 loses 2.8–3.9 pp. Staying in
  longer during the 2022 decline costs more than the saved whipsaws. A3 also
  raises max DD to 31.1% (+4.7 pp).
- **b. Minimum hold (B5, B10): reject.** Worse on every gate criterion. A
  cooldown also delays the exit that follows a false re-entry.
- **c. Staged thirds (C3): reject.** It is neutral overall: CAGR ±0.03 pp,
  1.1 pp more drawdown and 22% less turnover. It fails 2022–2026 by
  0.6–0.7 pp.
- **d. Leverage by trend strength (D5): reject.** Holding QQQ in the 0–5% band
  gives up about 4 pp of CAGR. The 5% boundary causes 121 switches and 19.7x
  turnover a year.
- **e. Second sleeve: reject.**
  - GLD is the only useful diversifier. E-GLD-70 has the best Sharpe in the
    screen (1.146) and a 21.7% DD, and E-GLD-80 roughly ties VT35 in 2022–2026.
    Both give up 6.6–10 pp in 2017–2021 and 3.4–5.2 pp of full-window CAGR.
  - TLT and IEF trend sleeves lose on return in both halves. Bonds were a poor
    diversifier in the 2022 rate shock.
  - These arms would qualify only under a risk-first objective, which is not the
    operator's.
- **f. Idle cash in BIL / SGOV: reject by the letter of the gate (near miss).**
  - At 1x costs it passes everything: +0.25 / +0.28 pp CAGR, Sharpe
    1.060 / 1.061, DD unchanged.
  - At 2x costs it loses 2017–2021 by 0.13 pp. T-bill yields were about 0–2%
    then, and each regime switch adds an extra BIL round trip (turnover
    4.6 → 7.9x).
  - The benefit comes from 2022–2026 (+0.45–0.5 pp) and depends on the rate
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
