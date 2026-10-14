# tax_hedge_v1 — hedge instead of trimming under Turkish income tax

Verdict: **FAIL for H1.** H1's after-tax CAGR is below C0's in the 2000 window
at $100k (9.09% vs 9.53%, −0.44 pp/yr), so it does not beat C0 in all three
windows. H2 (report-only) does beat C0 in all three windows at $100k
(9.76% / 14.62% / 23.18% against 9.53% / 14.50% / 22.14%), but it achieves
this only by trading less and still realizing about the same TRY gain; it is not
the prescribed H1 design and should not be promoted on these numbers alone.

## Method

- Same multi-instrument Turkish tax model as riskoff_after_tax_v1: CBRT USD/TRY
  conversion, Yİ-ÜFE indexation, 2026 GVK 103 tariffs deflated by Yİ-ÜFE, FIFO lots
  per leg, losses net within the calendar year with no carry-forward, 20% US
  dividend withholding credited, tax funding sales at the open, whole portfolio
  liquidated at the window end; m = 2 (8 bps/side).
- Pre-tax: base financing (DTB3), main window only.
- H1 long leg is synthetic TQQQ* (3x, 0.84% expense, financing (L−1)×DTB3); hedge
  leg is synthetic SQQQ* (−3x, 0.95% expense, (|L|−1)×DTB3). Entry long weight =
  (2/3)× production target; hedge buys only happen when cash is short, via trims.
- H2 uses synthetic QLD* (2x) / QID* (−2x), entry long weight = 0.98× target, no
  cash reserve, so hedges are always funded by trims.
- A 0.5 pp net-exposure gap is required before a hedge adjustment; flips, entries
  and exits always execute.

### Pre-tax, main window 2000-03 → 2026-09, base, m = 2

| Arm | CAGR | Max DD | Both legs held | Sells/yr |
|---|---:|---:|---:|---:|
| H0 / C0 (QLD*) | 14.67% | 49.6% | 0% | 59 |
| H1 (TQQQ* + SQQQ*) | 14.02% | 48.76% | 69.7% | 168 |
| H2 (QLD* + QID*) | 14.35% | 48.62% | 56.4% | 74 |

The 0.65 pp pre-tax loss for H1 and the 0.32 pp loss for H2 are the price of
co-holding long and inverse legs through the same market: the hedge legs rebalance
daily, but the inverse leg still suffers volatility decay whenever both are held
at once (≈70% and ≈56% of sessions respectively).

### After-tax, $100k, liquidated

| Window | C0 | H1 | H2 | H1 − C0 | H2 − C0 |
|---|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | 9.53% | 9.09% | 9.76% | −0.44 pp | +0.23 pp |
| 2010-01 → 2026-09 | 14.50% | 14.05% | 14.62% | −0.45 pp | +0.12 pp |
| 2017-01 → 2026-09 | 22.14% | 21.61% | 23.18% | −0.53 pp | +1.04 pp |

### After-tax by capital, 2000-03 → 2026-09

| Capital | C0 | H1 | H1 − C0 | H2 − C0 |
|---|---:|---:|---:|---:|
| $10k | 11.05% | 10.57% | −0.48 pp | +0.07 pp |
| $100k | 9.53% | 9.09% | −0.44 pp | +0.23 pp |
| $1M | 8.54% (≈) | 7.99% | −0.55 pp | +0.33 pp |

C0 $1M cell is the 8.54% figure from after_tax_tr_v1 (2000 window). L2's
after-tax 2000 cells at $10k/$100k/$1M are 11.33 / 9.57 / 8.22 for comparison.

### Tax burden and trading

| Window | Arm | Tax paid/yr ($100k) | Sells/yr |
|---|---|---:|---:|
| 2000-03 → 2026-09 | H1 | $22,767 | 168.4 |
| 2000-03 → 2026-09 | H2 | $23,202 | 75.9 |
| 2010-01 → 2026-09 | H1 | $31,122 | 192.2 |
| 2010-01 → 2026-09 | H2 | $29,274 | 79.6 |
| 2017-01 → 2026-09 | H1 | $34,013 | 189.3 |
| 2017-01 → 2026-09 | H2 | $33,422 | 88.4 |

For reference, C0 pays $19,331/yr and sells ≈59/yr in the 2000 window at $100k.
H1's hedge round-trips add ≈110 strategy trades a year on top of C0, and those
trades are themselves taxable (their realized gain on the hedge is booked when
it is closed at a profit). The design's hope — that closing a hedge at a *loss*
offsets trim gains — is not realized in these paths: the inverse leg that gets
bought when the target falls and sold when it rises again is more often closed at
a loss, but the realized gain on the long leg that had to be trimmed when cash
ran out, plus hedge funding sales, leaves H1 with a *higher* realized gain base
than C0 in the 2000 window.

### Realized TRY gain by instrument (2000 window, $100k)

| | Realized gain (TRY, after indexation) |
|---|---:|
| H1 long (TQQQ*) | 49.15m |
| H1 hedge (SQQQ*) | −1.46m |
| H1 total | 47.69m |
| H2 long (QLD*) | 51.79m |
| H2 hedge (QID*) | −1.06m |
| H2 total | 50.73m |
| C0 (QLD*) | 50.96m |

Hedge losses do appear (the SQQQ* and QID* legs book losses on most exits), but
they are small relative to the long-leg gains and do not offset the trend-exit
and funding-sale gains.

## Caveats

- No statistical gates were run (this is report-only by pre-registration). The
  verdict rests on the three $100k after-tax windows.
- The 3x inverse path is synthetic and highly path-dependent; inverse ETF
  terminal levels are not validated against real SQQQ data (no 2000–2026
  SQQQ history exists). Base financing is used even for inverses.
- Daily hedging of a continuous target generates both high turnover (H1) and,
  for H2, realization of inverse gains/losses that still add to the tax base in
  profitable years (2024–2025 TRY inflation).
- H2's after-tax edge comes with much less volatility-exit trading and a higher
  buy-and-hold tilt (fewer hedge trims, more deferred long-leg gain), not
  from the prescribed "leave winning lots alone" mechanic.

## Commit

Pre-registration commit `219518b`; results commit follows.
