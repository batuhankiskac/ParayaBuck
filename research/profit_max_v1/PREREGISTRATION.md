# Profit maximization v1 — pre-registration (2026-09-25)

Operator goal (2026-09-25): maximize long-run ending wealth; interim drawdowns
are acceptable. The bar is therefore **SPY buy-and-hold total return**, not the
previous bot. Seen before this file was written: buy-and-hold returns of the
campaign ETFs (`buy_hold.py`, `BUY_HOLD.json`). Data: Alpaca SIP split-adjusted
daily bars, cash dividends restated to post-split shares (fixes the 3x SCHD
pre-2024 dividend inflation in the earlier sleeve campaigns).

## Stage A (close-to-close screen, `stage_a.py`)

Window 2017-01-03 → 2026-07-30. Signal on the completed close, position change
at the next open, dividends reinvested, cash earns 0, cost per side 4 bps × m
(m = 1, 2, 3). Fixed parameters, no tuning:

| ID | Rule |
|---|---|
| S0 | SPY buy-and-hold (benchmark) |
| S1 | QQQ buy-and-hold |
| L1–L4 | SSO, UPRO, QLD, TQQQ buy-and-hold |
| T0 | SPY while SPY close > its 200-session SMA, else cash |
| T1 | SSO while SPY > SMA200, else cash |
| T2 | UPRO while SPY > SMA200, else cash |
| T3 | QLD while QQQ > SMA200, else cash |
| T4 | TQQQ while QQQ > SMA200, else cash |

Stage A pass (finalist for Stage B): at 2x costs, CAGR above S0 over the full
window **and** in both 2017–2021 and 2022–2026. Drawdown is reported, not
gated (operator tolerance). Stage B re-tests finalists inside the production
shared-ledger harness together with V3 full-investment arms.

## Stage B (production shared-ledger harness) — added after Stage A

Stage A finalists by the pre-registered rule: S1, L1, L2, L3, T3, T4. Stage B
tests the production strategy `equity_leveraged_trend_v1` (daily decisions,
signal on the completed close, next-open execution through the product engine)
at equity 0.93 + the unchanged crypto sleeve 0.05, cash buffer 0.02, gross
0.98, per-asset cap 0.93, daily-loss latch 0.20, drawdown halt 0.75 (shadow in
backtests). V3 at full investment (`V3full`, 8.34% CAGR) confirmed V3 is not a
candidate.

The designated candidate is **Q2 = QLD while QQQ > SMA200** (T3). 3x variants
are reported but not eligible: with Nasdaq-100 volatility (~22–25%/yr) the
growth-optimal leverage is about 2x, so 3x is fragile outside this bull
window. Arms: H_VOO (VOO buy-and-hold control through the same engine), Q2,
Q2BH (QLD unfiltered control), Q3 (TQQQ trend, report only).

Note: H_VOO 1x and Q2 1x were run before this section was written (the arms
and gates below were fixed in the Stage A text; only the Stage B wording is
new).

Gates for Q2 against H_VOO: accounting invariants; streams ≤ 30; CAGR above
H_VOO at 1x, 2x and 3x costs; CAGR above H_VOO in 2017–2021 and in 2022–2026.
Drawdown is reported, not gated.

## Stage A2 — risk "sweet spot" (operator request 2026-09-25, after Stage B)

Question: give up some return to cut the worst loss. Rule fixed before the
screen: among the candidates below, keep those whose full-window max drawdown
is at most S0's (SPY buy-and-hold) and whose CAGR at 2x costs beats S0 over
the full window and both halves; select the highest full-window CAGR. If none
qualifies, Q2 stays the proposal. Signal everywhere: QQQ close vs its 200-session
SMA (daily, next open). Downtrend = cash unless stated. Rebalance to targets
when any asset drifts more than 0.5 pp (production band). Costs 4 bps × m per
traded notional.

| ID | Uptrend holdings | Other |
|---|---|---|
| M25 | 25% QLD + 75% QQQ (≈1.25x) | |
| M50 | 50% QLD + 50% QQQ (≈1.5x) | |
| M75 | 75% QLD + 25% QQQ (≈1.75x) | |
| M100 | 100% QLD (= Q2 / T3) | reference |
| VT25 / VT35 | QLD at min(1, target / 20-session realized QLD vol), rest cash | vol targets 25% / 35% |
| G30 | 70% × M100 rule + 30% GLD held always | |
| G30M50 | 70% × M50 rule + 30% GLD held always | |
