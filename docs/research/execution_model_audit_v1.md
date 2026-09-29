# VT35 execution model audit v1 (2026-09-29)

Question: does the Stage B backtest behind VT35 (`equity_leveraged_trend_v1`,
28.98% CAGR / 29.2% max DD, 2017-01-03 → 2026-07-30, `profit_max_v1.md`) model
decision time, fill time and fill price the way the PAPER runtime trades? How
much does fill timing matter, and could the two live signal bugs fixed today
(ebcae9c) have touched the backtest?

**Answer.** The backtest and live runtime use the same execution model:
- Both decide on the completed session close.
- Both size a share quantity at that close.
- Both fill at the next session's open.
The backtest is the cleaner of the two. Neither live bug can occur in it.
Filling at the next open does **not** flatter the backtest. On this window,
filling at the decision close would have been slightly worse. The live-only
gaps are in the inputs, not the execution model:
- the IEX last-minute close;
- missed sessions;
- the entry latch that was always on before today's fix;
- a startup catch-up path that can decide on a forming daily bar.

No bug was found in the backtest's execution model. No result was changed.

Evidence is in `research/execution_model_audit_v1/`:
- the fill-timing simulation;
- the instrumented Stage B replay;
- the replay summary.

## 1. Side by side

`B` = backtest (engine `src/parayabuck/backtest/portfolio_engine.py` plus the
harness from the archive tags below). `L` = live runtime. Line numbers are
this commit's (b976749). Harness files:
- `run_shared.py` is at tag `archive/research/profit-max-v1-2026-09`.
- `shared_replay.py`, `timing.py` and `run_stage_b.py` are at tag
  `archive/research/sleeve-candidates-v2-v3-2026-09`.

| Aspect | Backtest (Stage B shared ledger) | Live PAPER runtime |
|---|---|---|
| Signal bars | SIP split-adjusted daily bars labeled 00:00 ET. QQQ/SPY come from canonical v5 and QLD from the campaign file (`run_shared.py:118-125`). QQQ is byte-identical in both files. | 199 provider SIP split-adjusted daily bars (00:00 ET), plus today's bar aggregated from **IEX 1-minute** stream bars and labeled 09:30 ET (`aggregator.py:92-99`, `runtime_service.py:649-676`). The provider overrides stored bars per session (`adjustment.py:120-176`). |
| "Completed" bar | Availability equals the XNYS session close from an exchange calendar, including half-days (`timing.py` `equity_daily_available_at`, `run_shared.py:159`). The replay asserts no history is newer than the decision (`shared_replay.py:658-676`). | The 1d bucket is flushed at 16:00:02 ET (`runtime.py:3159-3172`, `aggregator.py:114-122`). The close is the **last IEX minute**, not the official closing auction. 16:00 is hard-coded: on half-days the bar ends at 13:00 but the decision still happens at 16:00 ET. |
| Decision time | Physical session close: 20:00 / 21:00 UTC, or 17:00 / 18:00 on half-days. There are 2,406 decisions and every session is decided (`shared_replay.py:651-685`). | ~16:00:02 ET via `_emit_finalized_bar` → `_process_portfolio_timestamp` (`runtime.py:2877-2884, 3254`). The basis latch runs first (`runtime.py:3360`). |
| Decision rule | `EquityLeveragedTrendV1.decide` (`equity_leveraged_trend.py:108-155`). QQQ close > SMA of the last 200 closes. Weight = `min(1, 0.35 / vol)`. | The same class and the same code. |
| QLD volatility | Sample standard deviation of the last 20 close-to-close QLD returns × √252 (`equity_leveraged_trend.py:92-106`). All inputs are SIP closes. | Same formula. The last return is the IEX close over the prior SIP close. |
| Allocation | The strategy weight × `maximum_allocation` 0.93 via the risk overlay, on the shared (equity + crypto) NAV. | The same overlay (`runtime_service.py:1047-1061`), on account equity. |
| Sizing price | `qty = w · equity / last_price`, where `last_price` is the **decision close** (`portfolio_engine.py:1280-1289`). | `target_qty = w · equity / price`, where `price` is the history tail (the decision close, IEX) (`portfolio.py:508`, `runtime_models.py:165`). |
| Rebalance band | Skip when \|target − current\| < 0.005 (`portfolio_engine.py:1287`, set from `execution.rebalance_band_pct`). | The same (`portfolio.py:514`). The minimum notional is $10 in both. |
| Order | A synthetic order with `created_at` = physical close (`shared_replay.py:681-685`). | A quantity-based `MarketOrderRequest`, `TimeInForce.DAY`, submitted just after the close (`alpaca_orders.py:119-125`). Alpaca queues it for the next open. |
| Fill time | The next session's open. It needs `timestamp > created_at` and uses `_physical_open_bar` (`shared_replay.py:527-590, 1296-1307`). Measured: **585 of 585** QLD fills were exactly one session after the decision, at 13:30 / 14:30 UTC. | The next session's open or shortly after, as Alpaca paper fills a queued DAY market order. This is inferred from broker behavior; the code has no explicit OPG or MOO order type. |
| Fill price | `open × (1 ± (slippage 2 bps + spread 2/2 bps))` = open ± **3 bps** at 1x costs (`portfolio_engine.py:211, 943-947`). Measured: every fill was exactly 3.000 bps adverse to that session's open. | Broker fill price, stored in `portfolio_fills.price`. |
| Fees | Effective-dated SEC / TAF / CAT (`regulatory_fees.py`), $4.8k over the run. | Broker fees. |
| Partial fills / capacity | Capped at 10% of the prior 20-bar average volume (`portfolio_engine.py:934`). Never binding here: 0 multi-fill decisions. | Broker. |
| Sell before buy | Shared-ledger buys wait for pending sells (`shared_replay.py:547-592`). Never delayed an equity fill (all lags were 1 session). | Sell phase first, then deferred buys (`runtime_service.py:1172-1246`). VT35 trades one symbol, so a decision is either a buy or a sell. |
| Missed session | Impossible. | If the service is down or restarting at 16:00 ET, no decision is made for that session. There is no catch-up for a running deployment (`runtime_service.py:551-567`). The next decision is at the next close. |
| Duplicate session / label basis | Impossible. One label convention and strictly increasing appends (`portfolio_engine.py:749-755`). | Fixed today in ebcae9c (see §2). |

The instrumented replay also recomputed all 2,406 Stage B decisions, from the
trend state and volatility, straight from the campaign closes. The mismatch
count was 0. The replay reproduces the corrected 2026-09-28 result
(ending NAV 1,141,851).

## 2. Could the backtest have had today's two bugs?

No. **The backtest was cleaner than live.**

- **Timestamp basis (session vs label instant).** The backtest never compares
  provider and stream labels. It has one bar source per symbol with one label
  convention (00:00 ET), and the decision label is that bar's own timestamp.
  In live, before ebcae9c, the latch compared instants (09:30-ET decision
  label vs 00:00-ET provider watermark). As a result, **every scheduled
  decision was entry-latched with `signal_adjustment_unverified`**:
  - Only risk-reducing (sell) orders could flow.
  - Live could not open or increase QLD, whatever the backtest would have
    done.
- **Duplicate same-session bar.** In the backtest, `_append_history` appends
  only strictly newer timestamps, and every symbol has a single label per
  session. Live, before the fix, could keep both [D@00:00 provider, D@09:30
  stream]. Replaying that distortion on 2017–2026 SIP data:
  - The SMA200 counts today twice and drops the oldest session. This flipped
    the trend state on **1 of 2,406** sessions.
  - The volatility window replaces one real return with a ~0 same-session
    return. This raises the strategy weight by 0.009 on average (median 0,
    90th percentile 0.028, max 0.279).
  - Because the latch blocked entries anyway, the practical effect was
    distorted `target_json` metadata and larger-than-intended caps only on
    reductions.

## 3. Fill-timing effect

Script: `research/execution_model_audit_v1/fill_timing.py`, with results in
`RESULTS_FILL_TIMING.json`. It is equity only, with no crypto sleeve. It uses
the archived campaign bars and restated dividends and applies exactly the
VT35 rule and 0.005 band. Costs are per side on the fill price (3 bps = Stage
B 1x). Four fill variants:
- **(a) same close:** trade at the decision close. This is not achievable
  live.
- **(b′) next open, re-weighted:** trade at the next open, with the weight
  re-sized at the open.
- **(b / c) next open, quantity sized at close:** what Stage B and live both
  do.
- **(d) next close:** trade at the next session's close. This models a missed
  or one-session-late decision.

| Variant (alloc 0.93, 3 bps) | CAGR | Max DD | Sharpe | 2017–21 | 2022–26 |
|---|---:|---:|---:|---:|---:|
| (a) same close | 26.95% | 28.80% | 1.033 | 34.63% | 18.66% |
| (b′) next open, re-weighted | 27.72% | 26.38% | 1.054 | 34.87% | 19.92% |
| **(b/c) next open, qty at close (Stage B = live)** | **27.75%** | **26.15%** | **1.055** | 34.93% | 19.93% |
| (d) next close | 26.53% | 29.30% | 1.014 | 34.29% | 18.15% |

At 100% allocation the same ordering holds:
- (a) 28.84% CAGR / 30.70% max DD;
- (b/c) 29.74% / 27.89%, which matches Stage A2's 29.4% / 28.2% at 8 bps;
- (d) 28.42% / 31.22%.

At 6 and 9 bps the variants shift in parallel by about −0.2 pp CAGR each.

Findings:
- Next-open execution is what the backtest models. On this window it is
  **+0.80 pp CAGR and −2.7 pp max DD better** than filling at the decision
  close. The published Stage B figure therefore does not rely on an
  unachievable same-close fill.
- The sign reflects 2017–2026 overnight behavior after signal days, which is
  noise-level and regime-specific. Treat the ±0.8 pp gap as fill-timing
  uncertainty, not as an edge.
- Sizing the share quantity at the prior close (b/c) vs at the open (b′)
  differs by 0.03 pp. It is immaterial.
- A one-session delay (d) costs about **−1.2 pp CAGR and +3.2 pp max DD** if
  it applied to every decision. A missed live session costs a small part of
  that.
- Stage B's 28.98% / 29.2% is the whole shared ledger, including the 0.05
  crypto sleeve. The equity-only rows above are therefore about 1.2 pp lower
  in CAGR.

## 4. Live-only risks (documented, not changed)

1. **Signal close source.** Live uses the last IEX 1-minute close, not the
   official closing auction.
   - Only 6 of 2,406 sessions had QQQ within 10 bps of its SMA200, and 33
     within 50 bps. So trend flips from this are rare.
   - The volatility input takes one noisy return.
   - The comparison report (§5) measures `signal_close_gap_bps` per session.
2. **Startup catch-up on a forming bar.** This is the known risk, confirmed by
   the code trace below; it was not reproduced.
   - The monitor thread's first verification fetches provider bars up to
     `now` (`runtime.py:4008`). `merge_provider_authority` then adopts
     today's **still-forming** SIP bar as the history tail (`adjustment.py:162`).
     `covers_session` accepts a forming bar by design (`adjustment.py:59-70`).
   - `startup_catch_up` then decides on `series[-1]` with no completed-session
     check (`runtime_service.py:617-625`). Only `seed` checks closedness
     (`runtime_service.py:445-446`).
   - The exposure is limited to a first decision for a
     (account, configuration digest, market). Examples are a first deployment
     or any config-digest change while the equity sleeve is flat
     (`runtime_service.py:551-586`).
   - **Suggested fix (not applied, since it changes production behavior):** in
     `startup_catch_up`, drop equity tail bars for which
     `equity_day_bar_is_closed(bar.timestamp, now)` is false before deciding.
   - The scheduled 16:00 path is safe: `_append_signal_bar` replaces a
     same-session provider tail with the stream bar (`runtime_service.py:668-674`).
3. **Missed sessions.** A restart spanning 16:00 ET skips that session's
   decision; the backtest never does. Variant (d) gives the cost per delayed
   decision.
4. **Half-days.** The 16:00 ET hard-code only delays the half-day decision
   from 13:00 to 16:00. It is still before the next open, so the result is
   unchanged.

## 5. Backtest vs live comparison report

`scripts/backtest_live_comparison.py`, with tests in
`tests/test_backtest_live_comparison.py`. It is read-only: the DB is opened
with `mode=ro`, and it makes no network calls.

**Inputs:**
- the runtime DB (`data/runtime/parayabuck.sqlite3`):
  - `portfolio_decisions`: `decision_timestamp`, and `target_json` with
    `weights` and `metadata.{trend_state, signal_close, signal_sma,
    realized_volatility, entry_risk_latch, decision_origin}`;
  - `portfolio_orders` joined to `portfolio_fills`: `occurred_at`,
    `quantity`, `price`;
- an official daily-bar CSV for QQQ/QLD (SIP, split-adjusted, in
  `load_csv_bars` format);
- the config, for strategy parameters, `maximum_allocation` and the band.

**Per live session** (matched by New York session date, not label):
- **Reference decision:** the production `EquityLeveragedTrendV1.decide` on
  official closes through that session. It yields the trend state, close,
  SMA, volatility and expected target weight (× 0.93).
- **Reference fill:** the next session and its official open.
- **Live side:** the persisted values, the QLD target weight, and the fill
  VWAP and session.
- **Derived measures:**
  - `weight_gap`;
  - `signal_close_gap_bps` (live IEX close vs official close);
  - `cost_vs_open_bps` (signed, positive = worse than the modeled open).

**Flags:**
- `entry_latched`
- `trend_state_mismatch`
- `target_weight_gap` (≥ band)
- `fill_not_next_session`
- `missing_reference_bar`

The report also lists **sessions with no live decision** between the first
and last live decision, and gives flag counts.

**Usage:**

    python scripts/backtest_live_comparison.py --db data/runtime/parayabuck.sqlite3 \
        --bars <daily_sip.csv> --json-output report.json

The VM's DB must be copied locally first. Do not run it against a live
writer's path on the VM without `mode=ro` (the script already uses it).

**Not covered:**
- Decisions that were never persisted (e.g. a crash before recording) show up
  only as sessions without a live decision.
- Journal `portfolio_no_trade` events (`observability.sqlite3`) are not
  joined. That is a possible follow-up for the no-decision reasons.
