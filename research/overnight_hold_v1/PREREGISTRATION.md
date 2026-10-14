# overnight_hold_v1

Question: does holding the E2 position only overnight (buy at close auction, sell at next open),
or tilting exposure toward overnight, beat production after realistic costs?

Arms (fixed):
- C0 = production E2 (control): vol20 E2 QLD total return close-to-close.
- N1 = E2 on -> hold production weight overnight only (buy close auction t, sell open auction t+1), cash intraday; E2 off -> cash.
- N2 = E2 on -> production weight overnight, half weight intraday.
- N3 = report-only unconditional QQQ 1x overnight-only.
Costs: auction slippage s per side; evaluate s=1,2,4,8 bps; gate uses s=4 (production
model) for G1-G6 and requires G1 also pass at s=8.
Gates: reuse ewma G1-G8 (G9 replaced by cost stress). Financing full day cost conservative.

Data:
- Main: stress_robustness_v1_daily.csv QQQ open/close/dividend; QLD synth pre-2006.
- OOS: overnight_hold_v1_{spy,dia,iwm}.csv downloaded from Yahoo chart API (ETF real opens).
