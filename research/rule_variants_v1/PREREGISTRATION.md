# Rule variants v1 — pre-registration (2026-09-29)

Written before any candidate was simulated. Seen beforehand: the profit_max_v1
evidence (Stage A/A2/B results for VT35, Q2, M-mixes, G30 gold overlays on the
archived data). No result for any candidate below has been computed.

## Question

Does any simple rule change to the PAPER identity VT35
(`equity_leveraged_trend_v1`: QLD while QQQ's completed close > its 200-session
SMA, weight `min(1, 0.35 / 20-session realized QLD vol)`, else cash; daily;
equity budget 0.93) improve it enough to justify a Stage B shared-ledger replay?

## Data

- QQQ, QLD, SPY, VOO, GLD: `data/historical/profit_max_v1_daily_split_sip.csv`
  and `..._corporate_actions.csv` recovered byte-identical (SHA-256 checked
  against the archived manifest) from tag `archive/research/profit-max-v1-2026-09`.
- TLT, IEF, BIL, SGOV (and GLD again, as a cross-check): new download with the
  same Alpaca SIP split-adjusted daily bars and the same dividend restatement to
  post-split shares (`download_data.py`, `DATA_MANIFEST.json`). GLD pays no
  dividends. A per-symbol trailing dividend-yield sanity check is part of the
  manifest.

## Simulator (`screen.py`, Stage A2 convention)

Window 2017-01-03 → 2026-07-30. Signals on the completed close of day t-1,
trades at the open of day t. Dividends reinvested on the ex-date (open).
Target weights are fractions of NAV; the whole portfolio is re-set to targets
when any asset drifts more than 0.5 pp (production band). Cost 4 bps × m per
unit of traded notional (m = 1, 2). Uninvested cash earns 0 (except
candidate f). Equity budget B = 0.93 of NAV (crypto sleeve not modelled; the
0.07 remainder is cash). Metrics: CAGR (252-session years), Sharpe (daily,
rf = 0, ×√252), max drawdown, CAGR in 2017–2021 and 2022–2026, number of
trend-regime switches of the QLD sleeve (entries + exits), annual turnover.

## Arms (fixed; no tuning)

| ID | Rule |
|---|---|
| VT35 | baseline as above |
| A2 / A3 | exit only when QQQ < SMA200 × (1 − b), b = 0.02 / 0.03; entry unchanged (QQQ > SMA200) |
| B5 / B10 | after any regime switch, no further regime switch for 5 / 10 sessions (vol-target resizing inside a regime still allowed) |
| C3 | regime exposure moves toward 1 (up) or 0 (down) by 1/3 per session instead of at once |
| D5 | QQQ/SMA200 − 1 > 5 %: QLD at VT35 size; 0 to 5 %: QQQ at min(1, 0.35 / 20-session QQQ vol); ≤ 0: cash |
| E-X-80 / E-X-70 | X ∈ {GLD, TLT, IEF}: 80/20 or 70/30 split of B between VT35 (scaled) and an independent sleeve holding X while X's close > its own SMA200, else cash |
| F-BIL / F-SGOV | when the QLD regime is off, the equity budget B sits in BIL (F-SGOV: SGOV from its first bar, BIL before) instead of cash |

## Gate ("promote to Stage B")

Against VT35 on the same simulator, a candidate is promoted only if **all** hold
at both 1x and 2x costs:

1. full-window CAGR **or** Sharpe is higher than VT35's;
2. full-window max drawdown is not more than VT35's + 2.0 pp;
3. CAGR is higher than VT35's in 2017–2021 **and** in 2022–2026.

Otherwise the verdict is "reject". Switch count and turnover are reported, not
gated. If several candidates pass, all are listed; none is combined or tuned in
this campaign.

## Reproduction check

The screen's VT35 is compared with the Stage B result (28.98 % CAGR, 29.2 %
max DD, 1x costs, 2017-01-03 → 2026-07-30). The gap is reported, not gated;
candidates are judged only relative to the screen's own VT35.
