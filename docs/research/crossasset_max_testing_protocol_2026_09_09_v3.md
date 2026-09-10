# Crossasset maximum-testing protocol V3

Frozen before V3 outcomes. This research-only protocol covers the measurable
X01, X05, and X06 retrospective branches. The existing X02, X03, and X04
gates remain unchanged and blocked by their exact missing causal fields.

The cutoff is `2026-07-31T23:59:59Z`. Signals use only exact event date and
asset joins. The allowed lags are 1, 3, and 7 days. Missing `available_at`
keeps every branch `causal_admissible=false` and `economic_eligible=false`.

Frozen formulas:

- X01: `(10y-2y)/max(abs(2y),1e-9)`. A negative slope selects TLT, a positive
  slope selects SHY, and zero selects IEF. Yahoo adjusted close is used only
  as the explicitly labeled retrospective total-return outcome; its bars and
  distributions are not a substitute for the curve signal.
- X05: at each exact five-minute timestamp, compute the cross-sectional
  z-score of `log(sum_open_interest_value_usd)` across BTCUSDT and ETHUSDT;
  select the asset with negative z-score. OI is joined to Coin Metrics PriceUSD
  by exact asset and UTC calendar date for the next daily outcome.
- X06: at each UTC date, compute the cross-sectional z-score of the mean
  eight-hour funding rate across BTCUSDT and ETHUSDT; select the asset with
  negative z-score. Funding is joined to Coin Metrics PriceUSD by exact asset
  and UTC date for the next daily outcome.

Each measurable branch runs standalone and fixed `95% V3 + 5% candidate`
overlays for lags 1/3/7, costs 1x/2x/3x, full/recent/true-holdout windows,
turnover, drawdown, CVaR05, regime means, bootstrap, permutation placebo,
and capacity-aware V3-preserved overlays. The capacity-aware sleeve preserves
V3 weights and receives only dated spare capacity under equity 55%, crypto 5%,
gross 60%, cash 40%, max-two-crypto, and six-total-position caps. It cannot
alter V3 or promote a candidate. X01/X05/X06 remain retrospective diagnostics
because all required source rows lack `available_at`; borrow, financing,
margin, settlement, liquidation, and revision fields are also absent where
required for economic eligibility.

No economics, production, PAPER, LIVE, trade, configuration, or automatic
promotion is authorized by this protocol.
