# dd_budget_v1: a drawdown risk budget on the QQQ signal (audit item 55)

## Verdict

**No arm passes. K stays.** Three drawdown-budget arms were tested on the underlying QQQ path (not on the account
equity, unlike IC49):

- U1: Ulcer Index budget, updated daily.
- U2: CDaR 95 % budget (mean of the worst 5 % of depths), updated daily.
- U3: U1 with the budget updated only at each month end.

All three lower K's after-tax CAGR in every window (−0.40 to −2.82 pp), lose both after-tax bootstraps (the
arm beats K in only 43–47 % of paths against the 55 % bar, with negative medians), and fail the OOS and pooled
gates. The one real effect is drawdown: max DD falls from 46.7 % to 40.2 % (U3), 40.4 % (U1) or 43.8 % (U2), at a
pre-tax CAGR cost of 0.8 to 1.2 pp. That is the same kind of trade as a flat lower vol target (vt_level_k_v1 quotes
about 0.4 pp/yr after tax per pp of drawdown). It is not a free improvement, and it does not beat K after tax.

## Method

- Control K and reference R2 exactly as in `slow_vol_blend_v1`. The harness reproduces K's pre-tax CAGR (15.76 %),
  calibrated CAGR (15.18 %), max DD (46.67 %), after-tax CAGR (10.75 / 15.24 / 23.97 %) and DSR at N = 365 (0.711)
  to 0.01 pp; `research.fast_engine.parity --quick` passes.
- Budget on QQQ closes, over the trailing 252 closes (depth from the running peak inside the window):
  Ulcer Index = sqrt(mean depth^2); CDaR 95 % = mean of the worst 13 depths.
- Budget ratio = clip(expanding median of the measure / current measure, 0.6, 1.2). Target = 0.35 x ratio.
- Weight = K's sizing with that target: on x factor x min(1, target / vol20), cap 0.98. K's vote, vol20, warm-up
  (first 252 sessions keep K's weight) and K's execution layer (A1u gain band, H2 December deferral, 0.5 pp band)
  are unchanged.
- U3 holds the ratio from each month's last session until the next month end.
- Data, bootstraps (block 20, seed 20261011, 5,000 pre-tax paths, B1-recent and B2 after-tax on 1,000 paths) and
  OOS windows as in `slow_vol_blend_v1`. Preregistration: `research/dd_budget_v1/PREREGISTRATION.md`.

## Results (pre-tax main window, base financing unless stated; after tax at $100k liquidated)

| arm | pre CAGR base / cal | max DD base / cal | after tax 2000 / 2010 / 2017 | Δ after tax vs K (pp) | sells/yr (pre-tax) | exposure, vote-on (all / pre-2010) |
|---|---|---|---|---|---|---|
| K (control) | 15.76 / 15.18 | 46.67 / 46.79 | 10.75 / 15.24 / 23.97 | — | 10.2 | 1.754 / 1.603 |
| R2 (reference) | 15.48 / 14.90 | 46.76 / 46.88 | 10.16 / 14.71 / 22.89 | −0.59 / −0.53 / −1.08 | 29.6 | 1.754 / 1.603 |
| U1 Ulcer, daily | 14.99 / 14.42 | 40.43 / 40.53 | 10.35 / 13.90 / 21.71 | −0.40 / −1.33 / −2.27 | 9.1 | 1.728 / 1.563 |
| U2 CDaR95, daily | 14.58 / 14.01 | 43.81 / 43.93 | 10.02 / 13.67 / 21.15 | −0.74 / −1.56 / −2.82 | 8.9 | 1.718 / 1.584 |
| U3 Ulcer, monthly | 14.92 / 14.35 | 40.19 / 40.29 | 10.26 / 13.72 / 21.29 | −0.49 / −1.52 / −2.68 | 9.2 | 1.723 / 1.553 |

Sells per year after tax (2000-2026 window): K 10.7, U1 9.8, U2 9.5, U3 9.9. The budget cuts sells only modestly
(pre-tax 10.2 for K against 8.9–9.2 for the arms), because K's A1u gain band and H2 deferral already removed most of
the trims. The tax saving is too small to offset the lower exposure.

Budget ratio on the main window: mean 1.03; at the 1.2 cap (target 0.42) on 55 % of days (U1), at the 0.6 floor
(target 0.21) on 13 % of days. The rule raises exposure in calm regimes more often than it cuts it in stress,
and the net exposure on vote-on days is slightly lower than K.

Bootstraps and OOS:

| arm | pre-tax boot median Δ (base) / share > 0 | B1 median Δ / share > 0 | B2 median Δ / share > 0 | OOS median Δ CAGR (6 windows) | pooled G8 (pp/yr, share ≥ 0) |
|---|---|---|---|---|---|
| U1 | −0.38 / 41 % | −0.13 / 46 % | −0.25 / 43 % | −0.83 pp | −0.66 (0.25 %) |
| U2 | −0.27 / 42 % | −0.09 / 47 % | −0.20 / 44 % | −0.83 pp | −0.68 (0.50 %) |
| U3 | −0.39 / 41 % | −0.17 / 45 % | −0.26 / 43 % | −0.82 pp | −0.64 (0.45 %) |

After-tax halves (Δ vs K, pp): pre-2020 +0.23 / +0.02 / +0.22 (U1 / U2 / U3); post-2020 −2.23 / −2.87 / −2.55.
Pre-tax CAGR of the sub-periods P1 / P2 / P3: U1 beats K in one of the three (P1 only).

DSR at N = 378 (375 + 3 arms), pre-tax / after-tax: K 0.707 / 0.420; U1 0.666 / 0.396; U2 0.645 / 0.372;
U3 0.663 / 0.391. None reaches the 0.95 bar.

## Gates (pre-registered; an arm must pass all)

| gate | U1 | U2 | U3 | note |
|---|---|---|---|---|
| G1 CAGR ≥ K or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp (both financings) | fail | fail | fail | CAGR −0.77 pp; Calmar ratio 1.10 for U1 (threshold 1.1 — misses at 1.097) |
| G2 max DD ≤ K + 1 pp | pass | pass | pass | DD −6.2 / −2.9 / −6.5 pp |
| G3 2017-end CAGR ≥ K − 1 pp | fail | fail | fail | |
| G4 CAGR above K in ≥ 2 of 3 sub-periods | fail | fail | fail | 1 of 3 each |
| G5 pre-tax bootstrap median Δ ≥ 0 (both) | fail | fail | fail | see table |
| G6 G1 at m = 1 | fail | fail | fail | |
| G7 OOS (six windows): median Δ ≥ 0; Δ ≥ −0.5 in ≥ 5; DD ≤ +3 in ≥ 5 and never > +8 | fail | fail | fail | Δ ≥ −0.5 holds in only 2 of 6 windows |
| G8 reduced pooled M1-M5 block bootstrap, median ≥ 0 | fail | fail | fail | |
| G9 turnover ≤ 2 × K | pass | pass | pass | |
| A1 after-tax CAGR above K in all three windows | fail | fail | fail | 2000 window −0.40 / −0.74 / −0.49 |
| A2 B1 median ≥ 0 and share ≥ 55 % | fail | fail | fail | |
| A3 B2 median ≥ 0 and share ≥ 55 % | fail | fail | fail | |
| A4 pre-2020 and post-2020 after-tax Δ both > 0 | fail | fail | fail | post-2020 negative |

Selection rule: an arm replaces K only if every gate passes. No arm does. No production configuration changes.

## Caveats

- One path. The after-tax windows overlap (2000 and 2010 share history). The bootstraps and OOS windows are the
  robustness checks, and they agree with the main window.
- Expanding median: the main median starts at the first defined session (about 2000-03), so the early ratios
  rest on little history. Bootstrap paths start their own expanding median, and U3's month structure follows the
  fixed bootstrap calendar, as the tax engine does.
- Amendment A1 (in PREREGISTRATION.md): the first run attempt crashed on a bootstrap month-index shape mismatch
  after computing the in-memory blocks. Nothing was written, printed or inspected before the fix. Arms, parameters
  and gates are unchanged.
- DSR uses N = 378 (375 + 3) as instructed. slow_vol_blend_v1 recorded its ledger as 369 + 3 = 372. The N used here
  is stated for each figure.
- The harness wrote `research/fast_engine/PARITY.json` (timings only). It was restored before commit.

## Reproduction

```
python -m research.dd_budget_v1.screen --harness   # HARNESS PASS on 2026-10-27
python -m research.dd_budget_v1.screen --run       # RESULTS.json (byte-identical on 2 runs, sha256 6489f517...), SUMMARY.txt (18 s wall clock)
```

Evidence: `research/dd_budget_v1/RESULTS.json` (provenance stamp with SHA-256 of `screen.py`, `ddbudget.py`,
`PREREGISTRATION.md` and the seven inputs), `research/dd_budget_v1/SUMMARY.txt`. Branch `research/dd_budget_v1`.
