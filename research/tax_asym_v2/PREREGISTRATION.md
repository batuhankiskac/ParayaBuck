# tax_asym_v2 pre-registration

## Question
Is candidate A2 (downward-only vol resizes) robustly better than production E2
after Turkish (progressive, inflation-indexed) tax and under simple 20% flat tax,
using clean bootstraps that no longer produce negative liquidation values?

## Diagnosis (why tax_asym_v1 T1 broke)
In tax_asym_v1, the bootstrap resampled 10-year QQQ sessions by block, then
attached each sampled source date's USD/TRY and an assessment year to that path.
The tariff model records realized gains in TRY using the FX at buy vs sell:
cost is `units * buy_price * FX_buy`, proceeds `units * sell_price * FX_sell`.
Because blocks jump across different calendar years/decades, FX_buy and FX_sell
can differ by orders of magnitude; a buy in a strong-TRY year and sell in a
weak-TRY year creates an enormous TRY gain and tariff tax that can exceed the
whole portfolio, liquidating to a negative value. The 643/1000 negative paths
were then silently dropped, biasing the surviving 357. On the real (monotonic
depreciation) history all FX pairs obey chronological order, so this never
occurs; the mechanism is an artifact of randomizing calendar order.

## Arms (fixed)
- C0 = production E2 (vol20 target, 0.5 pp band).
- A1 = E2 with no vol target (weight = CAP when signal) — context.
- A2 = E2 with downward-only vol resizes (trim only when target < 50% held, or
  trend exit); k=0.5, trim=0.

## Data / mechanics
- USD market series: QQQ open/close/dividend from stress bars; financing DTB3.
- Synthetic QLD* 2x daily-reset, base financing (0.95% + DTB3).
- E2 signal on QQQ price closes (SMA200, 252-ret>0, donchian); vol20.
- Cost 2 sides * m=2 (8 bp).
- Bootstrap: stationary block-20, seed 20261011, 1000 paths, 10 measured years
  (2520 sessions), 3-session back-extension, WARM=250 for vol/SMA.
- Market returns (QQQ+DTB3) resampled jointly by date; USD/TRY and Yi-UFE NOT
  resampled.

B1 (Turkish model, real calendar): two variants
- recent: fixed calendar 2015-09 -> 2026-09 (TRY window 2016-09..2026-09 on
  measured part), USD/TRY + Yi-UFE actual path unchanged on every path.
- early: fixed calendar 2005-09 -> 2016-09 (TRY window 2006-09..2016-09).
Only market returns resampled. Tax = Turkish progressive, inflation-indexed
regime (tariff 2026 brackets, Yi-UFE anchoring), instalments Mar 31/Jul 31.

B2 (simple flat tax): 20% on each calendar year's net realized USD gain, losses
offset within year and carried forward indefinitely, paid at year end from the
portfolio, liquidation at end; no currency or inflation; recent calendar only.

## Gates
G-like after-tax gate (pre-registered): CONFIRMED if, in B1 (both calendars)
AND B2, median Δ(A2-C0) after-tax CAGR >= +0.50 pp AND share Δ > 0 >= 65%.
Also report median/mean/p05/p95 Δ CAGR, median Δ max DD, and the same for
A1 vs C0.

Hard assertion: no path may have non-positive terminal liquidated USD value;
if any occurs, the run fails loudly.

## Selection rule
No selection among arms; report A2 (downward-only) as candidate. A1/A2
survive only if gate passes.
