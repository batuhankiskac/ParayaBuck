# Rejected Strategy Archive

Status: **FROZEN** · 2026-08-18 · Final legacy cleanup.

Historical evidence for every strategy family that was implemented, evaluated
and REJECTED for the PAPER product. The implementations were removed from
`src/parayabuck/portfolio/` on 2026-08-18; nothing here runs at runtime.

Documentation only. Nothing at runtime reads this file.

The PAPER product strategy surface is exactly:

    equity_residual_momentum_v1
    equity_residual_momentum_v2
    equity_residual_momentum_v3
    crypto_low_variance_v1
    crypto_low_variance_v2

`portfolio_strategy_from` raises `Unsupported portfolio strategy: <name>` for
every name below (no silent fallback, no substitution).

Canonical evidence (kept, linked below): `docs/crypto_bakeoff_validation.md`,
`docs/crypto_reversal_validation.md`,
`docs/multi_market_strategy_validation.md`, `docs/strategy_validation_v2.md`,
`docs/strategy_diagnosis.md`, `docs/release_validation.md` (historical).

---

## EQUITY

### equity_dynamic_trend_1d_v1

NAME: `equity_dynamic_trend_1d_v1` (module `equity_dynamic_trend.py`, removed)
HYPOTHESIS: positive absolute trend (close > SMA150) plus relative 60-bar
momentum on the scanner universe, inverse-vol sized with a volatility target
and pairwise correlation floor; weekly or monthly rebalance.
MARKET: US equities
TIMEFRAME: 1d
PARAMETERS: trend_window 150, momentum_window 60, volatility_window 20,
top_n 6, max_single_weight 0.10, annual_volatility_target 0.10,
correlation_floor 0.25, rebalance_frequency weekly, maximum_allocation 0.50
TEST WINDOW: 2019-05-28 .. 2026-07-30 (1,194 symbols, production scanner
policy, PIT universe; walk-forward folds from 2017 extension 2016-2026)
RESULT: full-period economics negative OOS; with the best TRAIN parameters
run once: return -5.84% (1x) / -5.89% (2x) / -5.93% (3x), CAGR -3.76%,
Sharpe -0.934 .. -0.951. Trend and vol sizing components value-negative in
ablation.
VERDICT: rejected (economics negative OOS)
WHY REJECTED: no net positive edge after costs; root-cause diagnosis showed
the trend gate and vol sizing destroyed value; superseded by
`equity_residual_momentum_v1`.
EVIDENCE: `docs/strategy_diagnosis.md`, `docs/release_validation.md`
(historical release record; the "CURRENT production strategy" claims there
describe the state at that release and are NOT current).

### equity_long_horizon_momentum_1d_v2

NAME: `equity_long_horizon_momentum_1d_v2` (module
`equity_long_horizon_momentum.py`, removed)
HYPOTHESIS: classic 12-month formation / 1-month skip cross-sectional
momentum (Jegadeesh & Titman 1993), each stock gated by its own SMA200
trend, the whole book gated by SPY's SMA200 regime, monthly rebalance,
inverse-vol / volatility-target sizing (research ablation flags M0-M3).
MARKET: US equities
TIMEFRAME: 1d
PARAMETERS: trend_window 200, momentum_window 252, momentum_skip 21,
volatility_window 20, top_n 6, max_single_weight 0.10,
annual_volatility_target 0.10, correlation_floor 0.25, monthly; component
flags use_stock_trend / use_market_regime / use_vol_target default true
TEST WINDOW: walk-forward on 2016-2026 data, folds from 2017-01-02
(development ablation baseline M1 = 2020-01-02..2024-12-31)
RESULT: walk-forward weak: median fold Sharpe 0.39, +1.6% total; SPY regime
gate and vol sizing ablation value-negative on development evidence.
VERDICT: NOT VALIDATED (family closed)
WHY REJECTED: no robust positive edge; the stock-momentum family was closed
in favor of market-residual momentum (v1).
EVIDENCE: `docs/strategy_validation_v2.md` (harness `scripts/strategy_diagnose.py`
retired 2026-08-18; evidence frozen).

---

## CRYPTO

### crypto_cross_sectional_momentum_1h_v1

NAME: `crypto_cross_sectional_momentum_1h_v1` (module
`crypto_cross_sectional.py`, removed)
HYPOTHESIS: 1h long-only spot momentum — rank execution-supported pairs by
relative momentum (24h/7d/30d or composite), hold the strongest names
inverse-vol weighted, go to cash when the BTC regime turns negative;
rebalances limited to 6/12/24h.
MARKET: crypto
TIMEFRAME: 1h
PARAMETERS: momentum_scheme 7d, rebalance_hours 24, top_n 3,
volatility_window 72, btc_regime_lookback_hours 168,
max_annualized_volatility_pct 250.0, max_single_weight 0.5
TEST WINDOW: 2021-04-01 .. 2026-07-31 (bake-off) and 2024-12-01 .. 2026-07-31
(multi-market)
RESULT: -6.54% (1x, bake-off window); -8.8% (multi-market window).
VERDICT: family closed (verdict C)
WHY REJECTED: negative net economics on both windows; the crypto
cross-sectional momentum family was closed by the 2026-08-12 bake-off.
EVIDENCE: `docs/crypto_bakeoff_validation.md`,
`docs/multi_market_strategy_validation.md`.

### crypto_time_series_momentum_v1

NAME: `crypto_time_series_momentum_v1` (module `crypto_time_series.py`,
removed)
HYPOTHESIS: per-asset time-series momentum (Moskowitz, Ooi & Pedersen 2012)
— asset vs its OWN history: 30d return > 0 AND close > own 15d SMA;
inverse-vol sized with per-asset cap; weekly decisions (168h); validated 4h
and 1d variants with identical calendar lookbacks.
MARKET: crypto
TIMEFRAME: 1d (validated 4h and 1d)
PARAMETERS: lookback_days 30, trend_days 15, volatility_window_days 15,
skip_bars 1, top_n 3, max_single_weight 0.5, rebalance_hours 168
TEST WINDOW: 2024-12-01 .. 2026-07-31 (19 months) and 2021-04-01 .. 2026-07-31
RESULT: 1d 1x -12.2% (Sharpe -1.64, PF 0.41, WF 1/5), 1d 2x -13.9%,
1d 3x -15.5%, 4h 1x -7.7% (WF 1/5); +0.20% over the longer bake-off window.
Cut drawdown vs BTC buy-and-hold but NO positive edge: CAGR < 0, Sharpe < 0,
PF < 1 in both variants, 1/5 folds positive; fees ~1.6-1.8% of equity (not
the cause — no gross edge).
VERDICT: C — time-series momentum family NOT VALIDATED
WHY REJECTED: no positive edge in any variant; per the strategy-hopping stop
rule no further crypto momentum variants were evaluated.
EVIDENCE: `docs/multi_market_strategy_validation.md`,
`docs/crypto_bakeoff_validation.md`.

### crypto_cross_sectional_reversal_v1

NAME: `crypto_cross_sectional_reversal_v1` (module
`crypto_cross_sectional_reversal.py`, removed)
HYPOTHESIS: 10-week cross-sectional reversal, long losers only (DeBondt &
Thaler 1985 applied to crypto): strictly-negative trailing 70-bar return
qualifies, most-negative first, top-2 equal weight, weekly, no RSI/MACD/vol
overlays, no regime conditioning.
MARKET: crypto
TIMEFRAME: 1d
PARAMETERS: formation_bars 70, top_n 2, rebalance_hours 168
TEST WINDOW: 2021-04-01 .. 2026-07-31 (warmup 2021-01-01..03-31)
RESULT: +3.56% net 1x; 2x-cost net return NEGATIVE (-0.24%); walk-forward
majority positive FAILS (5/10); ETH = 90.1% of total net PnL (+$3,206 of
+$3,560; drop ETH -> flat); top-1 share of positive PnL 25.2% (DOGE), top-2
49.3%, top-3 67.0%.
VERDICT: C — NOT VALIDATED; the crypto cross-sectional reversal family is
CLOSED
WHY REJECTED: fails 2x costs, fails walk-forward majority, concentration
fragile (single-asset dependence).
EVIDENCE: `docs/crypto_reversal_validation.md`.

### crypto_high_proximity_v1

NAME: `crypto_high_proximity_v1` (implementation removed before 2026-08-18)
HYPOTHESIS: anchoring / nearness-to-high (George & Hwang 2004; Jia et al.
2026): close_t / max(close_{t-363..t}), 364-bar window, weekly 168h, top-2
equal weight.
MARKET: crypto
TIMEFRAME: 1d
PARAMETERS: 364-bar proximity window, rebalance_hours 168, top_n 2
TEST WINDOW: 2021-04-01 .. 2026-07-31 (bake-off, production harness)
RESULT: return -11.14% (1x) / -12.90% (2x) / -14.61% (3x), Sharpe -0.385,
PF 0.636, walk-forward 5/10 (coin flip), 100% of positive PnL from BTC with
ETH the largest loss, negative edge in both BTC regimes.
VERDICT: C — FAMILY NOT VALIDATED
WHY REJECTED: net negative at every cost level, walk-forward coin flip,
single-symbol PnL dependence.
EVIDENCE: `docs/crypto_bakeoff_validation.md`.

### crypto_low_disagreement_v1

NAME: `crypto_low_disagreement_v1` (implementation removed before 2026-08-18)
HYPOTHESIS: abnormal volume / disagreement, low side (GKM 2001 interval):
mean(vol t-5..t-1, nonzero) / mean(vol t-50..t-6, nonzero), zero-volume
gates, weekly 168h, top-2 equal weight.
MARKET: crypto
TIMEFRAME: 1d
PARAMETERS: 5d vs 45d volume ratio, zero-volume gates, rebalance_hours 168,
top_n 2
TEST WINDOW: 2021-04-01 .. 2026-07-31 (bake-off, production harness)
RESULT: return +5.51% (1x) / -1.53% (2x) / -9.61% (3x), Sharpe 0.171,
PF 1.072, walk-forward 4/10, positives all inside the 2023-24 bull
(one-regime dependence), drop-SOL kills profitability (-2.41%).
VERDICT: C — FAMILY NOT VALIDATED
WHY REJECTED: fails 2x costs, walk-forward minority, one-regime dependence,
top-contributor fragility.
EVIDENCE: `docs/crypto_bakeoff_validation.md`.

---

## Retired research harnesses

Removed 2026-08-18 with the rejected implementations:

- `scripts/validate_strategy_candidates.py` — multi-candidate bake-off runner
  for the rejected crypto/equity candidates. Shared research helpers were
  preserved in `scripts/research_helpers.py`.
- `scripts/strategy_diagnose.py` — equity strategy research harness
  (baseline/alpha/incidents/exposure/signal/benchmarks modes) for the
  rejected equity candidates. Current-strategy diagnostics are covered by
  `scripts/strategy_robustness.py` and the cadence-mission scripts
  (`research_equity_cadence.py`, `research_crypto_cadence.py`,
  `research_robustness_audit.py`, `research_deployment_start.py`,
  `research_equity_staleness.py`).

Historical artifact JSON survives on disk for the canonical evaluation
windows: `data/research/strategy_validation_v1/`, `strategy_validation_v2/`,
`strategy_validation_v3/`, `strategy_validation_v4/`,
`cadence_research_2026_08/`.
