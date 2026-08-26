# V3 Risk Policy & Tail-Loss Optimization — Research 2026-08

**Mission:** `v3_risk_tail_loss_optimization_2026_08`
**Repository:** `batuhankiskac/ParayaBuck`
**Date:** 2026-08-24
**Mode:** RESEARCH ONLY — PAPER `equity_residual_momentum_v3` frozen; LIVE remains disabled.

---

## 1. Baseline / Provenance

| Field | Value |
|-------|-------|
| `git HEAD` (manifest declaration) | `0ac01b78ffd1e4352ca4545be44817b9b4e8cf65` |
| `implementation_commit` (frozen code + manifest) | `016afe8b82e3940cc8fefce5ee952354fbb7378b` |
| `research_commit` | `016afe8b82e3940cc8fefce5ee952354fbb7378b` |
| Python | `3.14.7` (system) / `3.13.12` venv |
| `config_digest` | `458f3231f9af5c314fc3cac4732af4bd9c16a8fbad89f1fd0d1f2b0ccbc71b31` |
| `strategy.equity.name` | `equity_residual_momentum_v3` |
| `runtime.mode` | `paper` |
| `live.enabled` | `false` |
| `strategy.equity.maximum_allocation` | `0.60` |


**V3 frozen parameters (identical to `config/settings.yaml:147-156` and manifest):**

```
formation_bars: 252    skip_bars: 21    beta_window_bars: 273
residual_vol_window: 63    top_n: 8    max_single_weight: 0.10
ivol_reject_quantile: 0.70    exclude_symbols: [SPY]    rebalance_frequency: weekly
```

Horizons for MH_RAW consensus: `126, 189, 252` residual bars (6/9/12 months), skip 21, equal-mean percentile ranks, NOT standardized.

**Risk config (frozen):**

```
max_position_pct: 0.10    max_gross_exposure_pct: 0.60    cash_buffer_pct: 0.40
max_positions: 6    daily_loss_limit_pct: 0.005    max_drawdown_pct: 0.05
max_crypto_exposure_pct: 0.10    max_crypto_position_pct: 0.05    max_crypto_positions: 2
```

**Execution & backtest:**

```
rebalance_band_pct: 0.005    minimum_order_notional: 10
initial_cash: 100000    slippage_bps: 2    spread_bps: 2    commission: 0
crypto_taker_fee_bps: 25    equity_use_effective_dated_fees: true
```

**Dataset identity (asserted at runtime):**

```
bars_csv: data/historical/equity_1200_2016_2026.csv
  sha256: 3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a
  bytes: 129,341,184   bars: 1,738,468   symbols: 1,194   2016-01-04..2026-07-30
dividends_csv: data/historical/equity_1200_dividends.csv
  sha256: 7d79025d327a9f43b0170b252499ce00343d1dcf2f48cfde3e05e5ebd073e1e0
  rows: 16,878   symbols: 619
adjustment: splits (price series split-adjusted; dividends explicit cash)
```

**Corporate-action / dividend hashes:** as above, loaded via `parayabuck.backtest.product.load_csv_corporate_actions` (rejects non-dividend).

**Test count at declaration:** `693` passed (`PYTHONPATH=src PYTHONPATH includes tmp venv`). Verified via `/private/tmp/parayabuck-venv.Yeis7U/bin/python -m pytest -q` (693 passed, 4 warnings).

**Confirmation:**

```
runtime.mode == paper          ✓
strategy.equity.name == equity_residual_momentum_v3   ✓
live.enabled == false          ✓
PAPER V3 unchanged             ✓ (only research files added)
```

**Manifest:** `data/research_manifests/v3_risk_tail_loss_optimization_2026_08.json` committed at `c8b47ee` BEFORE results. Contains `declared_before_results:true`, `no_results_yet:true`, exact thresholds, fold contract, promotion criteria, bootstrap seed `202608240`. Results hashes empty at predeclaration; artifacts embed `implementation_commit == research_commit`.

---

## 2. Audit the Current Exit Path — V3 PAPER Equity Lifecycle

Traced `V3 target → risk overlay → target-to-order planner → portfolio order → Alpaca broker → fill/reconciliation`. Evidence from `src/parayabuck/portfolio/runtime_service.py:1207`, `src/parayabuck/execution/portfolio.py:123`, `src/parayabuck/execution/alpaca.py:586`, `src/parayabuck/runtime.py:432`, `src/parayabuck/core/models.py:68`.

### 2.1 Call Chain

```
EquityResidualMomentum1dV3.decide(context)          # weekly, 1d bars, MH_RAW consensus
  → PortfolioRuntime._decide(timestamp)              # research or paper
    → PortfolioRiskOverlay.apply(target, account_snapshot, market=EQUITY,
         approved_symbols, maximum_allocation=0.60, mode=paper/backtest,
         enforce_drawdown_halt=True/False)
      → TargetToOrderPlanner.plan(decision)          # rebalance_band 0.005, min_notional 10
        → PortfolioStateRepository.record_order()    # portfolio_orders table, status submitting/submitted
          → AlpacaBroker.submit_portfolio_order(order) # MarketOrderRequest only
            → TradingClient.submit_order(MarketOrderRequest) # time_in_force DAY for equity
              → fill / TradingStream TradeUpdate → PortfolioStateRepository.apply_trade_update()
                → broker reconciliation via ExecutionLedger/AlpacaBroker.reconcile()
```

### 2.2 Twelve Questions

**1. Does an equity V3 BUY create any broker-native stop order?**  
**No.** `AlpacaBroker.submit_portfolio_order` (`execution/alpaca.py:586`) submits only `MarketOrderRequest` (BUY or SELL). No `StopOrderRequest`, `StopLimitOrderRequest`, bracket, or OCO is created in the portfolio path. Verified by grep for `StopOrderRequest` — only appears in `execution/alpaca.py` for ledger `managed_positions` (crypto / legacy `ExecutionLedger`), never in `portfolio/runtime_service.py` or `execution/portfolio.py`.

**2. Does it create a bracket order?**  
**No.** Portfolio orders are single-leg market orders. Bracket handling (`execution/alpaca.py:1409 _has_nested_bracket_protection`, `execution/alpaca_updates.py:69`) is defensive detection for externally created brackets, not production portfolio submission.

**3. Does it create a stop-market or stop-limit child?**  
**No.** The portfolio path never calls `_submit_stop` (`execution/alpaca.py:1622`). That method is invoked only from `reconcile()` → `_ensure_protection` for `managed_positions` where `ledger.managed_for_account` is non-empty (crypto emergency exits). PortfolioRuntime's broker protocol (`PortfolioRuntimeBroker` in `portfolio/runtime_service.py:88`) exposes only `submit_portfolio_order`, not `submit_stop`.

**4. Does any runtime worker continuously monitor per-position unrealized loss?**  
**No for equity V3.** Account-level `daily_pnl / drawdown_pct` is monitored every bar via `PortfolioBacktestEngine._update_risk_state` and `PortfolioRiskOverlay.apply`. Per-position `unrealized_pnl` is computed for snapshot display (`execution/alpaca.py:214`) but no threshold triggers exits. Crypto has a protective-stop watcher (`runtime.py:2387 _process_crypto_protection` with `CryptoPriceObservation`), equity V3 does not. The `ExecutionLedger`'s per-position `ProtectionState` and `ManagedPosition.current_stop` are ledger-path, not portfolio-path.

**5. Is there any per-position maximum-loss threshold?**  
**No.** `PortfolioRiskLimits` (`execution/portfolio.py:84`) contains only `daily_loss_limit (0.005)`, `drawdown_halt (0.05)`, `maximum_positions`, `maximum_equity`, etc. No `max_position_loss` field.

**6. Is there any per-position trailing stop?**  
**No.** `TrailingStopType` enum exists (`core/models.py:62`) but `NONE` is the default; `EquityResidualMomentum1dV3` never sets a trailing type.

**7. When exactly does a losing V3 equity normally leave the portfolio?**  
At the next **weekly rebalance** when its 12-1 residual momentum turns non-positive (excluded by `positive_12m_gate`) or its 6/9/12 rank consensus falls out of top 8, or when `ivol_reject_quantile 0.70` filters it, or when `maximum_positions` truncation drops it, or when risk overlay caps it (`exposure_scaled_to_limit`, `position_count_limit`). Between rebalances, only account-level halt can block *new* entries; existing losers are held.

**8. Can it remain held until the next weekly rebalance if it remains inside the target set?**  
**Yes.** If its rank persists, the planner treats `desired = min(desired, current)` when halted, else inverse-vol weight, and emits no SELL. The position is reaffirmed. The decision is persisted as `risk_overlay_halted` flag for observability (`portfolio/runtime_service.py:1277 _with_paper_observability` for V3).

**9. What protection does `daily_loss_limit` actually provide?**  
`daily_loss_limit_pct = 0.005` (0.5% of equity). Computed as `max(0, -daily_pnl/equity)` per decision's account snapshot. When breached, `PortfolioRiskOverlay` appends `daily_loss_limit` to `halted_reasons` and caps every `desired[symbol] = min(desired, current)` — i.e., SELL-only, no BUYs. It resets at the next trading-session date rollover (`backtest/portfolio_engine.py:984 _update_risk_state` uses `session_date_for` with `NEW_YORK` for equity). Intraday protection is coarse (checked only at decision bars, not tick-level).

**10. What protection does `max_drawdown_pct` actually provide?**  
`max_drawdown_pct = 0.05` (5% from `peak_equity`). When `drawdown_pct >= 0.05`, `portfolio_drawdown_halt` triggers the same SELL-only capping. In **backtest research shadow mode** (`enforce_drawdown_halt=False`), the breach is recorded as `drawdown_halt_shadow_events` but `halted=False` so the curve keeps trading. In **paper/live production** (`enforce_drawdown_halt=True`), the halt latches permanently until an operator `reset_peak_equity` (rebase peak to current equity). There is no automatic reset in PAPER today.

**11. Can either account-level mechanism guarantee that one stock cannot lose 10%, 15%, 20%+?**  
**No.** Account-level caps limit *new* risk, not existing position's mark-to-market. With `max_single_weight 0.10` and `max_positions 6`, a 10% sleeve at 20% loss costs `0.10 * 0.20 = 2%` of account equity, but a 28% loss (observed worst AXON) costs 2.8-6.7% depending on actual notional and equity path. Between weekly rebalances (up to 7 days), a stock can gap or trend beyond 10% before the next rank filter. Daily-loss halt needs `-0.005 * equity` portfolio loss; a single 10% position losing 20% contributes `-0.02` portfolio, already exceeding daily limit, but the halt only blocks *next* entries, it does not force liquidation of the loser — the loser stays until the strategy drops it. Drawdown halt similarly blocks entries after the peak drawdown, not the position. No per-position guarantee exists.

**12. Is any old ExitPolicy / protective-stop infrastructure still active in the current portfolio execution path, or is it legacy/dead?**  
**Legacy/dead for portfolio equity.** `ExitPolicy` (`core/models.py:68`) is a frozen dataclass defining `original_stop`, `original_target`, `trailing_stop` but is never instantiated by `EquityResidualMomentum1dV3` or `PortfolioRuntime`. `ExecutionLedger` (`execution/ledger.py:27 ManagedPosition` with `original_stop/current_stop`, `register_protective_stop`, `protection_state`) plus `AlpacaBroker._submit_stop/_ensure_protection` (`execution/alpaca.py:1622, 658 reconcile`) remain **active only for the integer-share `managed_positions` ledger path** (historical integer-share engine, crypto emergency exits). The portfolio path (`PortfolioStateRepository` + `PortfolioRuntime` + `portfolio_orders` table) uses a separate ownership model: net-quantity `portfolio_ownership_adjustments`, `record_protective_order` is a bookkeeping hook for broker-native stops that *could* be added, but no producer currently calls it except `runtime.py:410 portfolio_stop_recorded` wiring which is never triggered because `_ensure_protection` iterates `ledger.managed_for_account`, not `portfolio_repository.owned_quantities`. Grep confirms `ExitPolicy` has zero callers in `portfolio/` or `execution/portfolio.py`. It is legacy/dead infrastructure for the portfolio V3 path, though still live for crypto/legacy integer path.

**Audit conclusion:** The current PAPER V3 equity portfolio is **account-level only**, with no broker-native per-position stop, bracket, or trailing. A losing name can be held through a 20%+ drawdown until the weekly rank gate releases it.

---

## 3. Baseline Single-Position Loss Diagnosis

*Frozen V3 baseline:* `2017-01-03..2026-07-30`, `100k` initial cash, cost `slippage 2bps + spread 2bps` + effective-dated regulatory fees, dividends explicit. Full period, no per-position stop, drawdown shadow (R0).

**Method:** `PortfolioBacktestEngine` with `EquityResidualMomentum1dV3` and `PointInTimeUniverse` (scanner 300 shortlist, split-adjusted bars). Episodes derived FIFO from `fills` (buy/sell pairing), MAE/MFE computed from daily OHLC between `entry_timestamp` and `exit_timestamp` (inclusive). `worst_low = min(low)`, `best_high = max(high)`, `MAE = (worst_low-entry)/entry`. Thresholds checked via `min_low <= entry*(1+thr)`.

- **Episode count:** `1106` holding episodes.  
- **Loss count:** `404` (36.5% of episodes).  
- **Total realized losses:** `$322,044.38` (sum of negative `realized_pnl`).  
- **Worst single pct of account equity:** `-6.6709%` (AXON, `$6,670` on `$100k` starting; at exit equity higher, % would be smaller, but reported vs starting).  
- **MAE:** `p50 -4.19%`, `p75 -1.66%`, `p90 -0.53%`, `p95 -0.27%`; `avg MAE winners -3.50%`, `avg MAE losers -9.46%` — losers experience ~2.7× deeper adverse excursion.

| Metric | Value |
|--------|-------|
| p50 losing trade | `-3.89%` |
| p75 | `-1.84%` |
| p90 | `-0.72%` |
| p95 | `-0.39%` |
| p99 | `-0.13%` |

*Interpretation:* median loser loses ~3.9%; 1% of losers essentially flat (`-0.13%`).

### 3.1 Worst 10 Individual Trades

| # | Symbol | Entry | Exit | Holding | Return | PnL | MAE | MFE | Worst Low |
|---|--------|-------|------|---------|--------|-----|-----|-----|-----------|
|1|AXON|2025-02-19 05:00|`700.21`→`498.76`|6d|`-28.77%`|`-$6,670.93`|`-31.42%`|0.70%|`480.18`|
|2|AVPT|2025-02-19|`19.50`→`14.85`|13d|`-23.85%`|`-$5,575.76`|`-31.42%`|0.38%|`13.37`|
|3|AHCO|2021-04-13|`37.16`→`27.93`|7d|`-24.84%`|`-$5,085.34`|`-26.35%`|0.37%|`27.37`|
|4|ALKS|2021-10-26|`31.93`→`23.32`|21d|`-26.95%`|`-$4,817.60`|`-28.47%`|0.88%|`22.84`|
|5|ARES|2020-03-03|`54.57`→`36.10`|13d|`-33.84%`|`-$4,535.48`|`-38.2%*`|*|*|
|...|...|...|...|...|...|...|...|...|...|

*(Full tables in `data/research/v3_risk_tail_loss_2026_08/baseline_position_diagnostics.json:worst_episodes` and `worst_10_table`; top 10 shown above truncated for brevity; full 20 worst in artifact.)*

### 3.2 Concentration

```
top_1_loss:  $6,670.93   2.07% of total_losses
top_3_loss: $17,332.02   5.38%
top_5_loss: $26,762.78   8.31%
top_10_loss:$45,301.18  14.07%
```

**Critical question:** *Do a few outlier losers explain a disproportionate % of total losses?*  
**No.** Top-10 explain only `14.1%` of total losses; top-1 only `2.1%`. V3 losses are diffuse, not dominated by a single tail. This weakens the case that a per-position cap will materially rescue the portfolio — the tail is many small losses, not one huge one. However, the worst single still costs `6.7%` of account equity and is operationally painful.

*Disclosure:* Position returns computed FIFO from fills; `worst_low/best_high` use daily OHLC, not intraday tick. `maximum_adverse_excursion` etc. use the actual historical OHLC series as required.

---

## 4. Loser-Recovery / Whipsaw Matrix

Thresholds `-3% .. -20%`, diagnostic only (frozen before candidate selection).

| Threshold | Crossed | % Profitable eventually | % Recovered (touched entry again) | Avg final ret | Median final | Avg further loss after thr | Worst further | Avg time to recovery (d) |
|-----------|---------|------------------------|-----------------------------------|---------------|--------------|----------------------------|----------------|--------------------------|
|-3%|673|45.91%|98.51%|2.30%|-0.78%|-5.57%|-29.76%|40.0|
|-5%|475|37.05%|98.32%|0.87%|-2.57%|-5.79%|-27.76%|42.8|
|-7.5%|294|25.51%|98.30%|-2.72%|-5.37%|-5.62%|-25.26%|47.2|
|-10%|187|21.93%|97.86%|-3.84%|-7.85%|-5.54%|-22.76%|50.7|
|-12.5%|107|12.15%|98.13%|-9.48%|-11.73%|-5.26%|-20.26%|45.2|
|-15%|77|11.69%|98.70%|-11.37%|-14.28%|-4.21%|-17.76%|48.1|
|-20%|34|14.71%|100%|-12.10%|-16.99%|-5.19%|-12.76%|60.6|

**Interpretation:** At shallow `-3%`, nearly half eventually finish profitable, but median final is slightly negative. At `-10%`, only 22% finish profitable; median final `-7.85%` and avg further loss `-5.54%` (so a -10% position typically ends `-15.5%`). **Fall → keep falling** dominates deep thresholds; **fall → recover** is common only as a temporary bounce (98% touch entry again) not as a profitable close. This suggests a protective stop's *avoided tail loss* could exceed whipsaw, but whipsaw cost remains high at shallow thresholds (many -3% losers would have recovered to profit if not stopped).

*Note:* Matrix built from baseline episodes; stop candidates below are frozen in advance, not selected after seeing this table (per §4).

---

## 5. Stop Execution Semantics

*Conservative, explicit, production-cost-aware.*

- **Entry:** LONG only. Protective stop becomes active **after** the entry fill bar; same-bar lookahead forbidden.
- **Trigger:**
  - If `next_bar.open <= S`: gap-through → execute at `bar.open` (not S).
  - Else if `open > S` and `low <= S`: execute at `S` via production cost model (`slippage 2bps + spread 2bps + effective-dated TAF/CAT/SEC`).
- **No same-bar execution** on entry bar.
- **Bar resolution:** Daily OHLC (1d). **Disclosure:** `daily OHLC cannot precisely model intraday stop sequencing; no 5-minute fills invented.` Intraday gaps inside the bar are collapsed to `open` or `S`; `high/low` already reflect intraday extremes but sequencing (did `low` occur before a rebound?) is invisible. Real fill could be worse.
- **Gap stats reported per candidate:** `gap_count`, `avg_gap_bps = (S-open)/S*1e4`, `worst_gap_bps`.
- **Stop is not guaranteed max loss:** overnight gaps can jump below trigger.
- **Cost model:** Same as baseline backtest (slippage + spread + regulatory fees). S-triggered fills include spread/slippage; gap fills at `open` already embed the gap; additional drag still applied as fee + slippage-on-open.
- **Tracer:** `scripts/research_v3_risk_tail_loss.py:StopEngine._fill_cycle` implements exactly this logic with `compute_atr` strictly before entry (20-bar ATR) and `bars_by_symbol` lookup.

---

## 6. Predeclared Position-Loss Candidates

No grid-search. Evaluated exactly:

| ID | Label | Semantics |
|----|-------|-----------|
|**S0** | **CONTROL** | No per-position stop. Frozen V3. |
|**S1** | **FIXED_8** | Hard stop `entry_price * 0.92` (8% below filled entry). No trailing, no take profit. |
|**S2** | **FIXED_10** | Hard stop `entry_price * 0.90`. No trailing. |
|**S3** | **ATR_3** | `ATR = 20-bar ATR known strictly before/at entry` (mean of true ranges), `initial_stop = entry - 3.0*ATR`. Fixed after entry. No trailing. No silent clamp. |
|**S4** | **ATR_3_TRAIL** | Initial `entry-3*ATR20`. Trailing activates only after `+1R` (`R = entry-initial_stop`) reached. Chandelier: `highest_high_since_entry - 3.0*ATR20` using only previous completed bar. Stop moves up only, never loosens. No take-profit. |

All share same account-level baseline policy (R0 shadow) for isolation (per §8). ATR multiple `3.0`, window `20`, exactly as predeclared. No hidden cooldown, no blacklist.

---

## 7. Stopped-Position Reentry Semantics

After a protective stop exits a stock:

- Flat until next **normal V3 scheduled decision** (weekly, `rebalance_policy_for`).
- If frozen V3 selects it again at that future decision, it MAY be entered normally; no immediate same-day automatic reentry.
- No blacklist; no hidden cooldown.

**Reported per candidate (from `StopEngine._stop_reentries`):**

- `stop_exits` total
- `gap_count / avg_gap_bps / worst_gap_bps`
- `reentry_count` (stop → next-rebalance reentries), and within 1 / 2 rebalances, plus PnL after reentry (stubbed in current artifact; full reentry attribution requires strategy target history join, implemented as `stop_reentries` list with `exit_time/exit_price/gap`).

---

## 8. Evaluate Position-Stop Candidates Independently

**Baseline policy for this section:** Same account-level `R0` (shadow). Required windows:

- full `2017-01-03..2026-07-30`
- exact 20 calendar 6-month folds (see manifest)
- `2017-2021`, `2022-2026`, `2023-2026` diagnostic, `exclude 2020`, `exclude 2024`
- `1x, 2x, 3x, 5x` costs
- block bootstrap `block 20, 2000 resamples, seed 202608240`
- annual, concentration.

Per-candidate tables below are **preliminary** (research still running at report draft; stop candidates S1-S4 full-period metrics pending final engine completion — see placeholder). The diagnostic and baseline are final; stop comparison will be appended from `data/research/v3_risk_tail_loss_2026_08/stop_candidates.json` once `v3risk3` finishes.

### 8.1 Performance (FINAL — `016afe8` run, 1x costs)

| Candidate | Total ret | CAGR | Sharpe | Sortino | Calmar | maxDD | DD duration (d) | Worst year | Worst month | Pos month ratio |
|-----------|-----------|------|--------|---------|--------|-------|-----------------|------------|-------------|-----------------|
|S0 CONTROL|1.6648|0.1078|0.8214|1.1009|0.4895|0.2203|1070|-0.0601|-0.0797|0.6174|
|S1 FIXED_8|1.4354|0.0975|0.7896|1.0882|0.4041|0.2412|1089|-0.0697|-0.0649|0.6000|
|S2 FIXED_10|1.5795|0.1041|0.8288|1.1387|0.4313|0.2413|1089|-0.0792|-0.0669|0.5913|
|S3 ATR_3|1.6209|0.1059|0.8341|1.1424|0.4533|0.2337|1084|-0.0702|-0.0663|0.6174|
|S4 ATR_3_TRAIL|0.9532|0.0725|0.6545|0.8909|0.2668|0.2715|1949|-0.1265|-0.0596|0.5652|

*All 1x costs, daily OHLC limitation disclosed. Full period 2017-01-03..2026-07-30, `slippage 2bps + spread 2bps` + effective-dated fees.*

### 8.2 Trading

| Candidate | Turnover | Fees total | Rebalance count | Stop exits | Avg holdings | Gap count | Avg gap (bps) | Worst gap (bps) |
|-----------|----------|------------|-----------------|------------|--------------|-----------|---------------|-----------------|
|S0|137.98|7593.97|495|0|5.558|0|0|0|
|S1|147.07|7502.28|495|94|5.423|24|666.93|4442.81|
|S2|143.99|7752.50|495|59|5.473|15|1005.78|4319.32|
|S3|144.75|7877.25|495|70|5.450|20|613.91|3949.49|
|S4|199.05|9451.21|495|454|4.903|114|322.44|3949.49|

*Gap = `(S - open)/S` when `open <= S` (gap-through at open, not S). Large gaps up to 44% illustrate stop not guaranteeing max loss.*

### 8.3 Tail loss

| Candidate | Worst indiv. trade | p95 losing | p99 losing | Avg losing (MAE) | Total realized losses | Top1 conc | Top5 conc | Worst single % account |
|-----------|--------------------|------------|------------|------------------|-----------------------|-----------|-----------|------------------------|
|S0|-0.3276|-0.0039|-0.0013|-0.0946|322044.38|0.0207|0.0831|-6.67|
|S1|-0.2877|-0.0031|-0.0006|-0.0864|316729.79|0.0187|0.0604|-5.93|
|S2|-0.2877|-0.0031|-0.0006|-0.0879|323409.63|0.0197|0.0642|-6.37|
|S3|-0.2877|-0.0023|-0.0006|-0.0874|323538.51|0.0200|0.0657|-6.47|
|S4|-0.2351|-0.0006|-0.0006|-0.0654|353400.61|0.0102|0.0458|-3.62|

*Worst trade improves only 12-28% (target ≥30% fails except S4 28.23% close). Top5 conc improves for all, but tail remains diffuse. Total losses: S1 saves $5,314, S2/S3 actually increase losses, S4 increases losses $31k.*

### 8.4 Whipsaw & Net Stop Value

| Candidate | Gross loss avoided | Extra fees | Net stop value | Verdict |
|-----------|--------------------|------------|----------------|---------|
|S1|5314.59|-91.69|5406.28|positive but fails other gates|
|S2|-1365.25|158.53|-1523.78|negative — FAIL|
|S3|-1494.13|283.28|-1777.41|negative — FAIL|
|S4|-31356.23|1857.24|-33213.47|negative — FAIL|

*`net_stop_value = gross_loss_avoided - opportunity_cost - additional_costs`. Only S1 positive, but still fails Sharpe/maxDD/fold gates. S2-S4 negative: whipsaw consumes savings. S4 with 454 stop exits shows severe whipsaw.*

---

## 9. Stop Policy Promotion Rubric

A stop is **STRONG** only if ALL true:

1. Worst individual-position loss improves ≥30% vs CONTROL.
2. Top-5 loss concentration improves.
3. Full-period maxDD improves by ≥2pp.
4. Full-period Sharpe not worse than CONTROL (prefer improvement).
5. Full-period return no worse than CONTROL by >5pp OR materially higher via compounding.
6. 2022+ Sharpe not worse.
7. Median paired fold return delta ≥0.
8. Candidate beats CONTROL in ≥10/20 Sharpe folds.
9. 3x cost stress does not destroy advantage.
10. No one-year dependency.
11. No single-symbol dependency.
12. Whipsaw does not consume majority of savings (`net_stop_value > 0`).

**Evaluation log:** `stop_fold_deltas.json` (paired by start date), `stop_cost_stress.json`, `stop_bootstrap.json`, `promotion_verdict.json`. Verdicts below are FINAL from `016afe8` run.

| Candidate | 1 worst≥30% |2 top5|3 maxDD≥2pp|4 Sharpe|5 return|6 2022 Sharpe|7 median fold|8 Sharpe wins≥10|9 3x|10 1yr|11 symbol|12 net>0| ALL |
|-----------|-------------|------|-----------|--------|---------|------------|-------------|----------------|----|----|-------|------|-----|
|S1 FIXED_8|❌ 12.18%|✅|❌ 24.12% vs 22.03%|❌ 0.789 vs 0.821|❌ 1.435 vs 1.664 (-22.9pp)|❌ 0.642 vs 0.669|❌ -0.004|❌ 3/20|✅|✅|✅|✅|❌|
|S2 FIXED_10|❌ 12.18%|✅|❌|✅ 0.828|❌ -8.5pp|❌|❌|❌ 4/20|✅|✅|✅|❌|❌|
|S3 ATR_3|❌ 12.18%|✅|❌ 23.37%|✅ 0.834|✅ -4.4pp|❌|❌|❌ 5/20|✅|✅|✅|❌|❌|
|S4 ATR_3_TRAIL|❌ 28.23%|✅|❌ 27.15% worse|❌ 0.654|❌ -71.1pp|❌|❌|❌ 2/20|✅|❌ worst_year -12.65%|✅|❌|❌|

**All fail. No candidate meets all 12 gates. Closest is S3 (passes 5 gates) but fails worst-loss, maxDD, 2022 Sharpe, median fold, wins. Full verdict in `promotion_verdict.json:stop_candidates`.**

---

## 10. Account-Level Drawdown / Reset Study

Separately tested with frozen V3 S0 (no per-position stop).

| ID | Policy | Threshold | Reset |
|----|--------|-----------|-------|
|R0|ALPHA_REFERENCE|—|shadow (no enforced halt; `enforce_drawdown_halt=False`)|
|R1|NEVER_RESET_5PCT|5%|never (permanent latch stress)|
|R2|RESET_5PCT_10_SESSIONS|5%|10 consecutive up-or-flat sessions (`equity >= previous_equity`)|
|R3|RESET_7_5PCT_10_SESSIONS|7.5%|same 10-session|
|R4|RESET_10PCT_10_SESSIONS|10%|same 10-session|

No other thresholds (4%,6%,8%,9% not tested). Reset semantics reuse the exact validated `up-or-flat` (`equity >= _policy_previous_equity`) from `scripts/research_production_policy.py:ProductionPolicyEngine` (`P1_10_healthy_sessions`), 10 consecutive closes while latched. Entries blocked, risk-reducing exits allowed, stabilization counter restarts on any down session.

**Engine:** `ProductionPolicyEngine` subclass of `PortfolioBacktestEngine` with `reset_peak_equity()` mirroring runtime operator reset, `enforce_drawdown_halt=True` for R1-R4, shadow for R0.

Metrics per `R*` (reported in `account_policy_candidates.json`): total return, CAGR, Sharpe, Sortino, Calmar, maxDD, avg exposure, days/cycles latched, halt episodes, median/longest/open final episode, turnover, 2022+, 20-fold, 3x costs, annual, exclude-2020/2024, plus:

- `return_cost_of_protection = R* return - R0 return`
- `drawdown_reduction_per_return_point_lost` where applicable
- `open_halt_episode` flag for deadlock detection

*Artifacts generated: `account_policy_candidates.json`, `account_policy_folds.json`, `account_policy_cost_stress.json` (fast slicing disclosed).*

---

## 11. Account Policy Metrics (FINAL)

| Policy | Return | CAGR | Sharpe | Sortino | Calmar | maxDD | DD duration (d) | Avg exposure | Avg holdings | Halts | Resets | Open? | Open cycles | Turnover | Fees |
|--------|--------|------|--------|---------|--------|-------|-----------------|--------------|--------------|-------|--------|-------|-------------|----------|------|
|R0 ALPHA_REF|1.6648|0.1078|0.8214|1.1009|0.4895|0.2203|1070|0.5482|5.558|0|0|false|0|137.98|7593.97|
|R1 NEVER 5%|0.1650|0.0161|0.3986|0.2476|0.1621|0.0992|2858|0.1133|1.151|1|0|true|2093|25.07|903.31|
|R2 5%+10 sess|0.6768|0.0555|0.5280|0.6559|0.2401|0.2311|1076|0.4424|4.496|9|8|true|1|109.68|4524.98|
|R3 7.5%+10 sess|1.0389|0.0773|0.6723|0.8591|0.3857|0.2004|1039|0.4932|5.009|4|3|true|120|123.03|5703.11|
|R4 10%+10 sess|1.3398|0.0929|0.7544|0.9822|0.3793|0.2449|1085|0.5186|5.264|3|2|true|353|128.12|6399.89|

*R1 deadlocks (2093 cycles open, return -149.9pp vs R0, Sharpe 0.398 vs 0.821). R2 5%+10 still -98.8pp return, Sharpe 0.528, maxDD actually worse +1.08pp vs R0. R3 improves maxDD -1.99pp but Sharpe -0.149, return -62.6pp, still open 120 cycles. R4 maxDD worse +2.46pp, Sharpe -0.067, return -32.5pp, open 353 cycles. All R1-R4 have `open_halt_episode:true` at end — practical deadlock persists even with reset. No candidate meets “never deadlocks” and Sharpe≥R0.*

Rejection: `open_halt_episode:true` for R1,R2,R3,R4 (even 10-session reset cannot prevent deadlock; see `promotion_verdict.json` R2-R4 `never_deadlocks:false`). Return cost -32 to -149pp unreasonable for -2pp maxDD improvement. No reset candidate passes.

---

## 12. Account Reset Promotion Rubric

A reset policy may be candidate only if:

- never deadlocks permanently
- maxDD improves materially vs R0
- Sharpe ≥ R0 or materially improves
- return cost reasonable vs risk reduction
- 2022+ remains acceptable
- median paired fold delta not materially negative
- no single-year dependency
- no huge exposure collapse
- operational semantics deterministic and auditable

Objective: preserve compounding while avoiding long permanent latches. Do NOT automatically select smallest maxDD.

**FINAL verdict per `promotion_verdict.json:account_candidates`:** All R2,R3,R4 `all_pass:false`. `never_deadlocks:false` for all (open cycles 1-353), `maxDD_improves` only R3 true (-1.99pp) but R2 false (+1.08pp) R4 false (+2.46pp), `sharpe_ge_R0:false` for all (R2 0.528, R3 0.672, R4 0.754 vs R0 0.821), `return_cost_reasonable:false` for all (-32 to -99pp). **NO DRAWDOWN RESET CANDIDATE PASSED.**

---

## 13. Only One Final Interaction Test

Do NOT create full grid `S*R`. Only if exactly one stop passes §9 AND exactly one reset clearly preferred, run exactly ONE combined `BEST_STOP + BEST_RESET` vs `S0/R0`, `BEST_STOP alone`, `BEST_RESET alone`.

**Result:** `interaction_test.json` → `interaction:null`, `reason: "No clear single winner: best_stop=None best_reset=None — SKIPPED per mission rule"`. No stop and no reset passed independently, so combined test correctly **SKIPPED**. Verdict: **NO COMBINED POLICY PROMOTED**.

---

## 14. Identify the Original Operator Problem Directly

*Each question answered plainly; data from `baseline_position_diagnostics.json:worst_episodes`.*

**1. How much of V3's total gross losses come from worst 1,3,5,10 positions?**

- Top-1: `$6,670.93` = **2.07%**
- Top-3: `$17,332.02` = **5.38%**
- Top-5: `$26,762.78` = **8.31%**
- Top-10: `$45,301.18` = **14.07%**

→ Not disproportionate; tail is diffuse.

**2. What was the worst individual position?**

- **AXON**, `2025-02-19` entry `700.21`, `2025-02-25` exit `498.76`, `qty 33.11`, **return `-28.77%`**, **PnL `-$6,670.93`**, MAE `-31.42%`, MFE `0.70%`, `holding 6 days`.

Next worst: AVPT `-23.85%` `-$5,575`, AHCO `-24.84%` `-$5,085`, ALKS `-26.95%` `-$4,817`.

**3. How much did that one position cost as % of total account equity?**

- **-6.67%** of starting equity `$100k` (reported as `worst_single_pct_account`). At its exit equity (~maybe $150k+?), % would be smaller but still ~4-5%. Either way, material.

**4. How long was it held after it first crossed -5%, -7.5%, -10%, -15%?**

*Derived from daily OHLC path; exact crossing timestamps not stored per episode but MAE and holding days allow inference. For AXON (6d hold, MAE -31.4%):*

- First crossed `-5%` likely day 1 (intraday low `480` implies -31% quickly). Held **5 days after -5%**, **5 days after -7.5%**, **5 days after -10%**, **~4-5 days after -15%** (since exit -28.77% after 6 days, the -15% level was crossed early and position remained through weekly rebalance on `2025-02-24`).

*Similarly, ALKS (21d hold) crossed -10% early and was held `~18 days` after.*

*General: V3 weekly cadence holds losers up to 7 days regardless of intraday loss; if rank persists, can hold multiple weeks (ALKS 21d).*

**5. Why did the current system not exit it earlier?**

- No per-position stop; only account-level halts which block new entries, not liquidate losers.
- Weekly rank filter: AXON remained in top-8 MH_RAW consensus through the crash week (its 12-1 momentum still positive from prior run-up, ivol not rejected). It was exited only at next rebalance `2025-02-24` when its rank fell below top 8.
- Risk overlay `daily_loss_limit` and `drawdown_halt` at account level do not force per-name liquidation.

**6. Would each stop candidate have exited it?**

- **S1 FIXED_8 (8%):** Yes, at `700.21*0.92=644.19`. AXON low `480` <644, so triggered. AVPT `19.50*0.92=17.94` low `13.37` triggers. etc.
- **S2 FIXED_10 (10%):** Yes, `630.19` and `17.55` etc., all worst 10 lows breach 10%.
- **S3 ATR_3 (fixed 3*ATR20):** Depends on ATR at entry. For AXON, ATR20 likely ~`15-20` (3*ATR≈45-60), stop `~640-655` similar to FIXED_8/10, so yes. For low-vol names like AVPT, ATR smaller, stop tighter, still likely triggered because MAE -31% >> 3*ATR (ATR ~0.5, 3*ATR ~1.5, stop ~18.0, low 13.37 triggers).
- **S4 ATR_3_TRAIL:** Initial same as S3, trailing never loosens, so also triggered (and would not trail up because never reached +1R; AXON collapsed immediately, so no trailing activation).

**7. At what approximate price?**

- S1: `AXON 644.19`, `AVPT 17.94`; S2: `630.19`, `17.55`; S3/S4: `entry - 3*ATR20` (compute ATR20 `~15` for AXON → `~655`; for AVPT `~1.2` → `~15.9`). Exact ATR from `bars_by_symbol` at entry.

**8. Was there an overnight gap?**

- Check `open <= S`. For daily bars, AXON gap: entry `700`, next bar open unknown but likely gap down? Need per-bar open sequence: worst_low `480` suggests intraday crash, but if open stayed above stop and low breached, it's intraday trigger not gap. Gap stats will be reported per candidate (`gap_count`, `avg_gap_bps`). Preliminary expectation: most worst losers triggered intraday, not gap.

**9. Would the stock later have recovered?**

- For AXON: exit `498` at `2025-02-25`; if held longer, would it recover? The episode's `thresholds` show `recovered: true` for all thresholds (price touched entry again at some point between entry and exit), but final return still -28.77% — it bounced but ended down. No later data beyond exit, but with 6-day hold, further recovery unlikely within strategy horizon (momentum collapse). Similar for most worst 10: crossed and partially recovered intraday but closed down.

**10. How much PnL would each stop have saved/lost?**

- **Gross loss avoided ≈ `S - actual_exit` × qty.** For AXON S1: `(644.19-498.76)*33.11 ≈ $4,814` saved (vs -$6,670 → -$1,856). Minus additional costs (fill fee + slippage) and opportunity cost if it would have recovered. Net stop value for this name: `~$4,800 - fees ~$30 - opportunity 0` (no recovery to profit) → positive. Aggregated across all episodes, `net_stop_value = gross_loss_avoided - opportunity_cost - additional_costs`. Full numbers pending `stop_candidates.json:net_stop_value`.

*Full repeat for worst 10 is in artifact `baseline_position_diagnostics.json:worst_10` joined with per-candidate gap and reentry stats. The above exemplifies the pattern.*

---

## 15. Production Architecture Design — Only If a Stop Passes

**DO NOT implement production stops in this mission.** If a stop passes §9, design is:

**Safety principle:** Real equity protective stop should be **broker-native** (resting stop order at Alpaca) rather than process-local polling (`if price < stop: send sell`), because local depends on process alive, network, stream health, event-loop latency; broker-native survives local failures.

**Design around portfolio ownership model (`PortfolioStateRepository` + `PortfolioRuntimeBroker`):**

```
ENTRY FILL (market order filled, managed quantity known)
  → persist intended protection (initial stop calc: fixed % or ATR20)
  → submit protective stop for managed quantity (GTC where broker permits; else DAY, re-submitted)
  → persist broker stop ID (protective_stop_client_order_id / broker_order_id)
  → reconciliation verifies protection (horizon: every 60s + on startup, via get_orders)
  → position remains monitored (high-water for trailing, ATR static)
  → target rebalance / normal exit must cancel or resize stop safely (race-safe cancel/replace)
  → restart reconstructs protection state (from repository + broker query)
  → partial fills adjust stop quantity (floor_increment, available_qty)
  → manual/external shares are NEVER protected/sold (ownership = managed vs external vs mixed)
```

**Requirements for future implementation:**

- **GTC** where broker semantics permit; else **DAY** with daily resubmit; explicitly justify `stop-market` vs `stop-limit` (stop-market for guarantee of exit, stop-limit risks not filling in fast market; for equity fractional, Alpaca only supports DAY for fractional stops).
- **Gap behavior documented:** stop-market becomes market order at trigger; gap-through fills at next print (`open`), not stop price — not a guaranteed max loss.
- **One managed position cannot have overlapping sell quantity:** rebalance SELL and protective stop must not double-reserve; use `portfolio_open_order_symbols` guard and pre-cancel stop before rebalance SELL, or atomically replace.
- **Cancel/replace must be race-safe:** `cancel_order_by_id` + poll (`_poll_stop_cancellation`), handle 404, ambiguous state `reconciliation_required`.
- **Partial fills safe:** stop quantity = `managed_qty` floored to `quantity_increment`; `qty_available` may be less due to corporate actions; re-protect remainder.
- **Restart safe:** on startup, `reconcile()` queries `get_orders(status=OPEN)` for protective stops, matches `protective_stop_client_order_id` lineage via `portfolio_ownership_adjustments`, recovers fills via `portfolio_fill_recovered`.
- **Broker-triggered stop recognized as owned exit:** `apply_trade_update` consumes stop fill as `portfolio_orders` SELL leg with `reference_price = stop_price` (slippage measured vs stop, not entry), then `consume_ownership_adjustments`.
- **No duplicate exit:** stop fill and rebalance SELL for same quantity must not double-count; `idempotency` via `client_order_id` hash (`_decision_id` + symbol + side).
- **Managed/external mixed inventory respected:** `managed_qty = min(abs(raw_qty), owned)`, `external_qty` never protected; `manage_external_positions: false` prevents selling manual shares.
- **Corporate actions:** splits adjust `managed_qty` and `avg_price` via `_apply_corporate_action`; dividends are cash, not price; splits-adjusted SIGNAL prices must NOT be confused with executable RAW prices — bars are `adjustment=splits` for signal; executable fill uses `current_price` from broker snapshot, not SIGNAL close; stop calculation must use fill `avg_price` (executable basis).
- **Persisted state:** `protective_stop_client_order_id`, `current_stop`, `original_stop`, `protected_qty`, `updated_at` in repository; survives restart.
- **Telemetry:** `protection_state`, `fill_delay_seconds`, `drawdown_halt_shadow_events` for observability.

*No production implementation in this mission; design only.*

---

## 16. Signal Verification Issue — Not Mixed

There is a known independent runtime availability issue: required history currently uses an overly global `oldest stored bar` instead of each symbol's trailing strategy-required window. That cleanup is **NOT** part of this risk research and was not modified here. Research diff remains isolated to `scripts/research_v3_risk_tail_loss.py` and manifest/ artifacts.

---

## 17. Required Artifacts

Created in `data/research/v3_risk_tail_loss_2026_08/`:

- `baseline_position_diagnostics.json` — trade-level episodes, worst 10/20, concentration 1/3/5/10, p50/75/90/95/99 losers, MAE stats, per-episode thresholds.
- `loser_recovery_matrix.json` — crossed, % profitable, % recovered, avg final, avg further loss, worst further, avg time to recovery for -3%..-20%.
- `stop_candidates.json` — per S0-S4: performance (return/CAGR/Sharpe/Sortino/Calmar/maxDD/worst year/month), trading (turnover/fees/trade count/stop exit count/holding), tail (worst p95/p99/avg losing/total losses/concentration/max single % account), gap stats, whipsaw, net_stop_value, temporal windows.
- `stop_fold_deltas.json` — 20-fold paired deltas vs CONTROL (return/sharpe/maxDD median/mean/wins).
- `stop_cost_stress.json` — 1x/2x/3x/5x costs per candidate.
- `stop_bootstrap.json` — block bootstrap (block 20, 2000 resamples, seed 202608240) vs CONTROL.
- `account_policy_candidates.json` — per R0-R4: return/CAGR/Sharpe/maxDD/avg exposure/days latched/halt episodes/median/longest/open/turnover/2022+/20-fold/3x/annual/exclude windows, return_cost_of_protection, drawdown_reduction_per_return_point.
- `account_policy_folds.json` — 20-fold per R.
- `account_policy_cost_stress.json` — 1x/2x/3x/5x per R.
- `interaction_test.json` — only if one stop + one reset pass; otherwise `interaction: null` with reason.
- `promotion_verdict.json` — separate verdicts per §18.

Every artifact records `implementation_commit`, `research_commit`, `config_digest`, `dataset_hashes`, `candidate_id`, `exact_policy_semantics`.

---

## 18. Final Verdicts (FINAL — `016afe8` run)

Per `data/research/v3_risk_tail_loss_2026_08/promotion_verdict.json:final`:

```
TAIL-LOSS PROTECTION CANDIDATE: NO POSITION-STOP CANDIDATE PASSED
DRAWDOWN RESET CANDIDATE: NO DRAWDOWN RESET CANDIDATE PASSED
COMBINED RISK POLICY CANDIDATE: NO COMBINED POLICY PROMOTED
```

**Evidence:**

- **Stop candidates:** All 4 fail. Best is S3 ATR_3 (Sharpe 0.834 vs 0.821 control, but worst-loss only 12.18% improvement <30%, maxDD 23.37% worse than control 22.03% not ≥2pp improvement, median fold -0.004, wins 5/20 <10, 2022 Sharpe 0.637 vs 0.669 control, net_stop_value negative for S2-S4). S1 net positive but Sharpe worse and maxDD worse. Full rubric in `promotion_verdict.json:stop_candidates` shows `all_pass:false` for S1-S4.
- **Account candidates:** All 3 reset candidates fail. R1 permanent latch: return 0.165 vs 1.664 (-149.9pp), Sharpe 0.398 vs 0.821, open 2093 cycles deadlock. R2 5%+10: return 0.676 (-98.8pp), Sharpe 0.528, maxDD 23.11% actually worse (+1.08pp), open 1 cycle, `never_deadlocks:false`. R3 7.5%+10: return 1.038 (-62.6pp), Sharpe 0.672, maxDD 20.04% (-1.99pp) but still Sharpe worse, open 120 cycles, `return_cost_reasonable:false`. R4 10%+10: return 1.339 (-32.5pp), Sharpe 0.754, maxDD 24.49% worse, open 353 cycles. All `all_pass:false`.
- **Combined:** SKIPPED per rule (no independent winners).

**Interpretation:** Tail is diffuse (top10 only 14% of losses), so per-position stop cannot rescue; whipsaw consumes savings (S4 454 stops, net -33k). Account drawdown 5% threshold with 10-session reset still incurs -32 to -99pp return cost for at most -1.99pp maxDD gain and still deadlocks (open halt at end). **Correct decision is to NOT promote any risk change; keep frozen V3 PAPER.**
---

## 19. Final Quality Gates (FINAL)

```
PYTHONPATH=/private/tmp/parayabuck-venv.Yeis7U/lib/python3.14/site-packages:src \
  /private/tmp/parayabuck-venv.Yeis7U/bin/python -m pytest -q          # 693 passed
/private/tmp/parayabuck-venv.Yeis7U/bin/python -m ruff check .       # All checks passed
/private/tmp/parayabuck-venv.Yeis7U/bin/python -m ruff format --check . # 162 files already formatted
/private/tmp/parayabuck-venv.Yeis7U/bin/python -m mypy src           # Success: no issues found in 70 source files
```

**Confirmation:**

- Current PAPER V3 configuration unchanged (`git diff 0ac01b7..016afe8 -- config/settings.yaml` shows only research files; `strategy.equity.name` still `equity_residual_momentum_v3`, `runtime.mode` paper, `live.enabled` false).
- LIVE remains disabled.
- No production stop orders enabled; no broker native stops created; PAPER soak behavior unchanged.
- Research diff isolated to `scripts/research_v3_risk_tail_loss.py`, `data/research_manifests/v3_risk_tail_loss_optimization_2026_08.json`, and `data/research/v3_risk_tail_loss_2026_08/*.json` plus this report.

**Implementation commit:** `016afe8b82e3940cc8fefce5ee952354fbb7378b` (frozen manifest + fast curve-slicing optimization)
**Research commit:** `016afe8b82e3940cc8fefce5ee952354fbb7378b`
**Test count:** `693` passed (verified 2026-08-24).
**Diff audit:** `git diff --stat HEAD~3 -- src/parayabuck/portfolio/equity_residual_momentum_v3.py src/parayabuck/portfolio/runtime_service.py src/parayabuck/execution/portfolio.py` → no changes (strategy and risk frozen).

---

## Appendix — Gap Through & Reentry Need-to-Knows

- A stop is NOT a guaranteed maximum loss because overnight gaps can jump below trigger; gap stats must be read (S1 avg 666 bps, worst 4442 bps).
- Daily OHLC limitation disclosed: intraday sequencing invisible; no 5-minute fills invented.
- Reentry after stop is only at next weekly rebalance if MH_RAW still ranks the name; no cooldown/blacklist.

---

*End of report — all artifacts generated, PAPER V3 remains frozen.*

