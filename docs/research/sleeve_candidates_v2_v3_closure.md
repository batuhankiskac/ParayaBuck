# Sleeve candidates v2 and v3 — closure (2026-09-25)

Decision: **v2 — no candidate passes. v3 — `dividend_growth_v1` and
`gold_v1b` pass every pre-registered Stage A and Stage B gate.** A pass
authorizes a production-integration *design* only; the shipped V3 0.55 +
crypto 0.05 configuration is unchanged. Full evidence (pre-registrations,
code, data manifests, curves, shared-ledger runs, detailed `RESULTS.md` files)
is preserved at annotated tag `archive/research/sleeve-candidates-v2-v3-2026-09`
(commit `094c7fb7b1a7723c2c116bfb7fc5e1f85ff71c15`); only this summary is on main.

## Question

Successor to ETF diversifier v1: which 15% ETF sleeves (funded from V3 0.55 →
0.40, crypto 0.05 unchanged), or additive idle-cash parking sleeves, improve
the combined account? Mechanisms tested earlier (ETF dual momentum,
equity-ETF trend, sector/factor rotation, SPY timing, turn-of-month, the v1
static and trend sleeves) were not repeated. Evaluation 2017-01-03 →
2026-07-30 (development ≤ 2021, validation ≥ 2022), costs 1x/2x/3x, Stage A
summed-sleeve screen, then Stage B on the accepted shared-ledger physical-clock
harness (one cash balance, account-wide 0.5% daily-loss latch, cap 8).

## Results

Stage B control A (shipped): CAGR 6.69%, Sharpe 0.675, max DD 19.4%, 2022
−15.3%, ending NAV 185.8k per 100k.

| Campaign | Candidate | Stage B CAGR | Sharpe | Max DD | Verdict |
|---|---|---:|---:|---:|---|
| v2 | risk parity (SPY/TLT/IEF/GLD/DBC) | 5.84% | 0.727 | 18.8% | FAIL (CAGR guard) |
| v2 | defensive sectors (XLP/XLU/XLV) | 6.17% | 0.714 | 18.4% | FAIL (overlaps V3 universe) |
| v2 | defensive factor (USMV/QUAL/SPLV) | 6.36% | 0.709 | 19.0% | FAIL (Sharpe uplift) |
| v2 | global equity (SPY/EFA/EEM) | 6.83% | 0.738 | 20.1% | FAIL (drawdown above A) |
| v2 | T-bill sweep (additive) | 6.77% | 0.682 | 19.1% | FAIL (uplift +0.08 pp < +0.25) |
| v3 | **dividend growth (SCHD/VIG/DGRO)** | 7.00% | 0.767 | 18.2% | **PASS** |
| v3 | **gold (GLD/OUNZ)** — `gold_v1b` | 7.28% | 0.858 | 18.1% | **PASS** |

Stage A-only failures: passive US core (2022 gate), GTAA-5 (CAGR guard),
60/40, hedged equity (SPY/BTAL), covered calls, momentum+quality, small caps,
Treasury trend, 1–3 year Treasury sweep. Also Stage A passes not selected for
Stage B: all-weather, equal-weight US, global minimum volatility.

## Caveats

- Holm-adjusted bootstrap p ≈ 0.15 for both passers; about twenty sleeves
  were screened on the same window across v1–v3, so the passes are not
  statistically established.
- Gold's return edge is concentrated in 2025; outside 2025 its return equals
  A. Its durable contribution is diversification: lower drawdown and a
  smaller 2022 loss. It is a single-asset sleeve.
- `gold_v1b` is a declared post-hoc structural variant: `gold_v1` (GLD/IAU)
  failed only the invariance gate because IAU is inside V3's observed
  universe; OUNZ replaces IAU with identical exposure.
- One historical path; shared-ledger runs move by roughly ±$1–4k through
  path dependence. Broker cash interest is modeled as zero.

## Harness lessons for successors

- Pinning sleeve symbols into V3's stream leaks them into V3's scoring pool
  (changes its volatility threshold and picks) even when V3 never targets
  them; pin-only symbols must be hidden from V3, and any sleeve ETF that V3
  itself observes (IAU, XLP/XLU/XLV) cannot pass the invariance gate.
- Cash-equivalent sleeves must offset only reserved sleeve orders when
  freeing slots; counting held positions too gave crypto 10 slots.
- Shared-ledger runs need ~6 GB each; run at most two in parallel on a 16 GB
  machine.
