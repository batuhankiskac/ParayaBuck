# Sleeve candidates v2 — pre-registration

Status: frozen before any candidate outcome below was computed (2026-09-25).
Research-only; no production, config, risk or PAPER/LIVE change.

## Why this campaign

ETF diversifier v1 (tag `archive/research/etf-diversifier-v1-stage-b-fail-2026-09`)
showed that a sleeve funded from V3 fails mainly on the CAGR guard: defensive
assets return less than V3. The repository inventory of earlier work also shows
that ETF dual momentum, equity-ETF time-series momentum, sector/factor ETF
momentum, SPY timing overlays and turn-of-month SPY were already tested and
failed or were blocked; they are **not** repeated. This campaign tests
mechanisms not previously tested, in two families, with literature-standard
parameters fixed here (no tuning, no grid).

Honest scope: the 2017–2026 window is the same one earlier campaigns used, so
nothing here is fresh out-of-sample evidence; the multiple-testing diagnostic
below is reported for that reason.

## Family S — substitution sleeves (V3 0.55 → 0.40, sleeve 0.15, crypto 0.05)

All monthly (first bar of month), long-only, weights inside the sleeve sum to
1 and are scaled to 0.15 of the account; the shipped per-name cap 0.10 applies.

| ID | Name | Rule |
|---|---|---|
| S1 | `passive_us_core_v1` | SPY 1/2, QQQ 1/2 |
| S2 | `defensive_factor_v1` | USMV, QUAL, SPLV 1/3 each |
| S3 | `risk_parity_v1` | SPY, TLT, IEF, GLD, DBC weighted by inverse 63-bar daily-return volatility |
| S4 | `gtaa5_sma_v1` | SPY, EFA, IEF, DBC, VNQ: each 1/5 if close > 210-bar SMA, otherwise that fifth in BIL (Faber 10-month rule) |
| S5 | `defensive_sectors_v1` | XLP, XLU, XLV 1/3 each |
| S6 | `global_equity_v1` | SPY 1/2, EFA 1/4, EEM 1/4 |

S3 and S4 are relatives of the failed v1 candidates (different rule and asset
set); they are included once, flagged as such.

## Family X — idle-cash parking (additive; V3 0.55 + crypto 0.05 unchanged)

| ID | Name | Rule |
|---|---|---|
| X1 | `tbill_sweep_v1` | BIL 0.10 + SHV 0.10 of the account (T-bill ETFs), held permanently, restored monthly |

X1 models a **hypothetical policy** in which T-bill ETFs count as cash
equivalents: they do not count toward gross exposure, the cash buffer or
position slots, and at least 0.20 of the account stays in true cash. Adopting
it would require an explicit risk-policy decision by the owner; this campaign
only measures it. Broker cash interest is modeled as zero, as everywhere else.

## Data

Alpaca SIP daily split-adjusted bars plus explicit cash dividends for all
listed ETFs, 2016-01-01 → 2026-07-31 (`download_data.py`, manifest with
SHA-256). Evaluation 2017-01-03 → 2026-07-30; development ≤ 2021-12-31,
validation ≥ 2022-01-03; 2022 stress year. V3 sleeve curves (shipped config,
0.55, cap 8, 1x/2x/3x) and the crypto causal replay A are reused unchanged
from the v1 archive / main.

## Stage A — summed sleeve screen

Each candidate runs standalone through the production `PortfolioBacktestEngine`
(shipped risk limits, execution policy and cost model; costs 1x/2x/3x;
allocation 0.15, or 0.20 for X1). Account daily return = V3 return × (0.40/0.55
for S, 1 for X) + sleeve return + crypto return. Controls: **A** shipped
(V3 0.55 + crypto), **B** matched cash (V3 × 0.40/0.55 + crypto).

Family S gates (all must hold; identical to v1 plus the cash control):
G1 validity (100% coverage from first eligible decision, fills after
decisions); G2 full 1x Sharpe ≥ A + 0.05 and max DD ≤ A; G3 CAGR ≥ A − 0.50
pp; G4 validation Sharpe ≥ A and DD ≤ A + 1 pp; G5 development Sharpe ≥ A −
0.05; G6 2022 return ≥ A; G7 3x Sharpe ≥ A at 3x; G8 largest ETF share of
|P&L| ≤ 60% (not applied to S1, whose two holdings are both broad US indices);
G9a full Sharpe ≥ B + 0.05; G9b Calmar ≥ B; G9c validation Sharpe ≥ B.

Family X gates: X-G1 validity; X-G2 full CAGR ≥ A + 0.25 pp; X-G3 max DD ≤ A +
0.25 pp; X-G4 full Sharpe ≥ A; X-G5 validation CAGR ≥ A; X-G6 2022 return ≥ A
− 0.25 pp; X-G7 3x CAGR ≥ A at 3x.

Diagnostics (reported, not gates): per-calendar-year return versus A;
correlation with V3; stationary block bootstrap (mean block 20 sessions, 2,000
resamples, seed 20260925) of the one-sided p-value for Sharpe(candidate) >
Sharpe(B) (family S) or mean daily return > A (X1), Holm-adjusted across all
seven candidates.

## Stage B — shared ledger confirmation

At most the two Stage A family-S passers with the highest full 1x Sharpe, plus
X1 if it passes, go to the accepted physical-clock shared-ledger harness with
the v1 Stage B method (composite equity engine, ETF slots additive, invariance
arm). Family S uses the v1 Stage B gates (S1–S4 structural, G2–G9 economic).
X1 uses the X gates on shared-ledger NAV plus S1–S4, with T-bill holdings
excluded from gross, cash-buffer and slot accounting in every overlay and a
0.20 true-cash floor. Arm A and B outputs from the v1 archive are reused only
if a fresh arm A 1x run reproduces its decision and fill hashes exactly.

Verdict per candidate: `PASS` only if every Stage A and Stage B gate holds.
A pass authorizes a production-integration design, never a production change.
