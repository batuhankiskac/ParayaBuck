# riskoff_after_tax_v1 — riskoff_rotation L2 after Turkish income tax

> Evidence for this report (`research/riskoff_after_tax_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/riskoff-after-tax-v1-2026-10`.

Verdict: **No meaningful edge.** L2's pre-tax edge over C0 (+0.82 pp/yr, main
window; +0.88 pp/yr 2017+) collapses after Turkish income tax to about 0 to
+0.4 pp/yr, and at a $1M account L2 is *worse* by 0.32 pp/yr (2000 window)
and only +0.23 pp/yr better (2017 window). The extra ~11 sells/yr and the
bond/gold defensive positions generate taxable gains that consume most of the
pre-tax advantage. Do not promote L2 on after-tax grounds.

## Reconciliation

The extended multi-instrument model reproduces the production control exactly
when only QLD* trades: pre-tax CAGR 14.67% (m=2, base financing), max DD 49.56%;
after-tax CAGR 11.05% at $10k and 9.53% at $100k in the 2000 window, byte for
byte against `after_tax_tr_v1`.

## After-tax results (liquidated, progressive tariff)

**2000-03-09 → 2026-09-29**

| Capital | Arm | Pre-tax CAGR | After-tax CAGR | Max DD | Tax paid (USD) | Trades/yr |
|---|---|---:|---:|---:|---:|---:|
| $10k | C0 | 14.67% | 11.05% | 49.56% | 63,795 | 58.8 |
| $10k | L2 | 15.48% | 11.33% | 47.38% | 70,987 | 62.6 |
| $100k | C0 | 14.67% | 9.53% | 49.56% | 646,780 | 59.1 |
| $100k | L2 | 15.48% | 9.57% | 49.81% | 690,084 | 62.2 |
| $1M | C0 | 14.67% | 8.54% | 49.56% | 5,767,265 | 59.1 |
| $1M | L2 | 15.48% | 8.22% | 51.12% | 5,839,630 | 63.2 |

L2 − C0 after-tax delta: **+0.28 pp ($10k), +0.04 pp ($100k), −0.32 pp ($1M)**.

**2017-01-03 → 2026-09-29**

| Capital | Arm | Pre-tax CAGR | After-tax CAGR | Max DD | Tax paid (USD) | Trades/yr |
|---|---|---:|---:|---:|---:|---:|
| $10k | C0 | 32.26% | 25.83% | 35.89% | 28,527 | 74.2 |
| $10k | L2 | 33.14% | 26.45% | 38.02% | 30,584 | 77.4 |
| $100k | C0 | 32.26% | 22.69% | 37.61% | 352,698 | 74.2 |
| $100k | L2 | 33.14% | 23.12% | 39.68% | 377,738 | 77.4 |
| $1M | C0 | 32.26% | 21.54% | 38.11% | 3,667,300 | 74.2 |
| $1M | L2 | 33.14% | 21.77% | 40.18% | 3,956,236 | 77.5 |

L2 − C0 after-tax delta: **+0.62 pp ($10k), +0.43 pp ($100k), +0.23 pp ($1M)**.

## Where the tax difference comes from

Realized TRY gain by instrument (2000 window, $100k, after indexation):

| Instrument | C0 (TRY m) | L2 (TRY m) |
|---|---:|---:|
| QLD* | 50.96 | 50.44 |
| GOLD* | — | 4.98 |
| BOND10* | — | −0.08 |
| **Total** | **50.96** | **55.34** |

- L2's extra realized gain is **~4.90m TRY**, essentially all from the defensive
  leg (GOLD* realization). QLD* realized gains fall by ~0.52m TRY, so the extra
  tax is **not** paid on extra QLD trims; it is paid on the bond/gold rotation.
- The net tax increment is +43,304 USD at $100k (690,084 vs 646,780 USD) and
  +52,449 USD at $10k; at $1M it is +72,365 USD. Because the investor is in
  the 40% (or 35%) bracket at the margin, every 1m TRY of defensive gain costs
  ~0.35m TRY of tax.
- Tax difference is concentrated in 2024–2025 (1.0m TRY difference at $100k),
  with smaller contributions in 2020–2023.

## Budget / pre-tax context

L2 keeps its pre-tax edge (+0.82 pp main window; +0.88 pp 2017+) and better
drawdown pre-tax (44.22% vs 49.56%), but after tax the drawdown advantage in
the main window almost vanishes (49.81% vs 49.56%) and the gain in 2017 turns
into a worse drawdown (39.68% vs 37.61%). turnover grows from 1543 to 1642
strategy trades/27 yr (about +3.7 sells/yr).

## Caveats

- Report-only: no statistical gates were run; gate comparison is pre-tax and
  published previously.
- Single account, annual assessment, liquidation view; the unliquidated view
  (deferred tax) is not shown but follows the same direction.
- USD→TRY conversion, Yİ-ÜFE indexation, FIFO matching all carry the
  uncertainties noted in `after_tax_tr_v1`.
- 2017 window runs to 2026-09-29, not the 2026-07-30 end used in the original
  after-tax study.
