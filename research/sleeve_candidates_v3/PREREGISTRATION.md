# Sleeve candidates v3 — pre-registration

Status: frozen before any candidate outcome below was computed (2026-09-25).
Research-only; no production, config, risk or PAPER/LIVE change.

## Why this campaign

Sleeve candidates v2 (see its `RESULTS.md`) and ETF diversifier v1 tested
passive US core, defensive factors, risk parity, GTAA-5, defensive sectors,
global equity, a T-bill sweep, a SPY/TLT/GLD/BIL permanent portfolio and a
five-asset trend sleeve. Earlier campaigns tested ETF dual momentum, equity-ETF
trend, sector/factor ETF momentum rotation, SPY timing overlays and
turn-of-month SPY. None of those is repeated. This campaign screens a broad
set of further, literature-standard static or single-rule sleeves with
parameters fixed here (no tuning, no grid).

Honest scope: the 2017–2026 window is the one earlier campaigns used, so this
is not fresh out-of-sample evidence; with twelve candidates the Holm-adjusted
bootstrap diagnostic below matters more than any single gate margin.

## Family S — substitution sleeves (V3 0.55 → 0.40, sleeve 0.15, crypto 0.05)

Monthly (first bar of month), long-only, weights inside the sleeve sum to 1
and are scaled to 0.15 of the account; the shipped per-name cap 0.10 applies.
Where one exposure is wanted, it is split across two equivalent ETFs only so
that no name exceeds the cap; those pairs skip G8 (marked †).

| ID | Name | Rule |
|---|---|---|
| S7 | `all_weather_v1` | SPY 0.30, TLT 0.40, IEF 0.15, GLD 0.075, DBC 0.075 |
| S8 | `balanced_60_40_v1` | SPY 0.60, AGG 0.40 |
| S9 | `gold_v1` † | GLD 1/2, IAU 1/2 |
| S10 | `equal_weight_us_v1` † | RSP 1/2, EUSA 1/2 |
| S11 | `dividend_growth_v1` | SCHD, VIG, DGRO 1/3 each |
| S12 | `hedged_equity_v1` | SPY 1/2, BTAL (anti-beta market-neutral) 1/2 |
| S13 | `covered_call_v1` | XYLD 1/2, QYLD 1/2 |
| S14 | `global_min_vol_v1` | USMV, EFAV, EEMV 1/3 each |
| S15 | `momentum_quality_v1` | MTUM 1/2, QUAL 1/2 (held statically, not rotated) |
| S16 | `small_cap_v1` | IJR 1/2, IWM 1/2 |
| S17 | `treasury_trend_v1` † | TLT/VGLT halves if TLT close > its 210-bar SMA, otherwise BIL/SHV halves |

## Family X — idle-cash parking (additive; V3 0.55 + crypto 0.05 unchanged)

| ID | Name | Rule |
|---|---|---|
| X2 | `short_treasury_sweep_v1` | SHY 0.10 + VGSH 0.10 (1–3 year Treasuries), held permanently, restored monthly |

Same hypothetical cash-equivalent policy as v2 X1 (excluded from gross, cash
buffer and slots; 0.20 true-cash floor). Unlike T-bills, 1–3 year Treasuries
carry duration risk (2022), which is exactly what this candidate measures.

## Data

Alpaca SIP daily split-adjusted bars plus explicit cash dividends for every
listed ETF, 2015-01-01 → 2026-07-31 (`download_data.py`, manifest with
SHA-256). Evaluation 2017-01-03 → 2026-07-30; development ≤ 2021-12-31,
validation ≥ 2022-01-03; 2022 stress year. V3 sleeve curves (shipped config,
0.55, cap 8, 1x/2x/3x) and the crypto causal replay A are reused unchanged
from v2 / main.

## Stage A — summed sleeve screen

Identical method and gates to sleeve candidates v2 Stage A (G1–G9 for S, XG1–XG7
for X; G8 skipped only for † candidates). Bootstrap diagnostic: stationary
block bootstrap, mean block 20 sessions, 2,000 resamples, seed 20260926,
Holm-adjusted across all twelve candidates.

## Stage B — shared ledger confirmation

At most the two Stage A family-S passers with the highest full 1x Sharpe, plus
X2 if it passes, go to the v2 Stage B shared-ledger harness (composite equity
engine, additive ETF slots, pin-only sleeve symbols hidden from V3, invariance
arm I must reproduce arm A exactly) with the v2 Stage B gates. A candidate
whose ETFs are in V3's own universe cannot pass S1 and is reported
inconclusive, as in v2.

Verdict per candidate: `PASS` only if every Stage A and Stage B gate holds.
A pass authorizes a production-integration design, never a production change.
