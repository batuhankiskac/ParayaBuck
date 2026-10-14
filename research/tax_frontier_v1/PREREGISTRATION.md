# tax_frontier_v1 — which E2-family rule maximizes after-tax return

Research-only. Uses the extended multi-instrument after-tax model from
riskoff_after_tax_v1 (Turkish income tax, CBRT rates, YI-UFE indexation, FIFO,
progressive tariff, instalments March/July, liquidated view at end) unchanged.

## Question
Which E2-family rule maximizes after-tax CAGR for a Turkish resident?

## Control
C0 = production E2 (`equity_leveraged_trend_v1` 1.1.0): QLD* (2x QQQ total
return, base financing) at weight min(1, 0.35/vol20), cap 0.98, while >=2 of 3
QQQ votes hold; decide at close, fill at next open, 0.5 pp resize band,
m=2 costs (8 bp/traded side).

## Arms (fixed)
- C0: as above.
- A1: no vol target: QLD* at 0.98 whenever E2 on; trades only on E2 trend
  entries/exits (no drift/vol resizes).
- A2: vol target 0.35; downward resizes only (a) at trend exit, or (b) when
  target weight < 50% of held weight; upward resizes as production (0.5 pp band).
- A3: vol target 0.35, 0.5 pp band, but resizes only on the first session of
  each quarter; E2 entries/exits trade daily.
- A4: E2 trend on QQQ 1x (no leverage, no vol target): QQQ* at 0.98 while E2 on,
  0.5 pp production band.
- A5: QQQ buy-and-hold (benchmark).
- A6: SPY buy-and-hold (benchmark; owner benchmark).

A5/A6 and A1 do not resize for drift (hold except trend flip for A1, none for
buy-and-hold). Tax funding sales are taxable and FIFO.

## Data
CBRT USD buying rates, YI-UFE index, QQQ/SPY bars, synthetic QLD*/QQQ*, as
recorded in DATA_MANIFEST.json. Windows:
- 2000-03-09 -> 2026-09-29
- 2010-01-01 -> 2026-09-29
- 2017-01-03 -> 2026-09-29
Capital levels: $10k, $100k, $1M.

## Gates / selection
The arm with highest after-tax CAGR at $100k on the 2000 window whose
max DD (pre-tax path) <= C0 + 5 pp and whose after-tax CAGR beats C0 in ALL
three windows at $100k. If none, verdict FAIL.

## Robustness (report-only) for selected arm only
- Pre-tax block-20 bootstrap, seed 20261011, 5,000 paths, median delta vs C0.
- Six OOS windows' pre-tax CAGR and max DD using ewma_band_v1 harness.

Tax inputs do not exist for OOS windows; hence report-only.

## Cost model
m=2: 4 bp per side * 2 = 8 bp per unit of traded notional.
