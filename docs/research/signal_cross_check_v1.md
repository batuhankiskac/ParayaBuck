# Signal cross-check v1 (2026-09-30)

Roadmap item: "cross-check the signal with a second data source; if the two
sources disagree on the trend, don't trade."

**Answer.** Implemented as a runtime-only guard (`signal_cross_check:` in
`config/settings.yaml`, on by default). Each scheduled equity session-close
decision is recomputed on a second bar for the same session: the Alpaca
**consolidated SIP daily bar**, fetched after the close. The live decision bar
is still the one built from IEX 1-minute stream bars. The guard compares the
two decisions:
- If they agree, the decision path is unchanged.
- If they disagree on the trend, the position is held for that session.
- If the SIP bar is missing, the decision proceeds on the IEX bar with a
  warning.

History says rule (b) would almost never fire:
- Alpaca SIP vs Yahoo official closes: **0 trend disagreements in 2,406
  sessions** (2017-01-03 → 2026-07-30).
- Modelled IEX-vs-official close noise of 5–10 bps: **about 0.1–0.2 holds per
  year**.

The guard is meant to catch data faults, not market noise. The 2026-09-29
labelling bug is the kind of fault it targets.

The Stage B replay is unchanged: ending NAV **1,173,537.61**, with identical
decision and fill hashes.

Deployed: no.

## 1. Behaviour

The primary source is the IEX stream bar (the last IEX minute, flushed at
about 16:00:02 ET). The secondary source is the Alpaca SIP daily bar, split
adjusted, for the same New York session. Both decisions use the same
`EquityLeveragedTrendV1.decide` on the same stored history. The only
difference is the session-close bar of QQQ (trend) and QLD (vol-target
weight). A same-session provider tail is replaced, not duplicated, exactly as
`_append_signal_bar` does.

| Case | Condition | Orders | Decision record | Alert (`signal_source_mismatch`, warning) | Watchdog / health |
|---|---|---|---|---|---|
| (a) agree | Same trend state | As today, byte-identical | `metadata.signal_cross_check.status = agree` | None; INFO log only | Nothing new |
| (b) trend mismatch | Trend states differ | **None this session**; target = currently held weights (even a risk-cap trim is suppressed) | Persisted with status `signal_source_hold`, `signal_source_hold: true`, both source decisions and close gaps; no-trade journal reason `signal_source_mismatch` | `category=trend_mismatch` | Intent-watchdog alarm `equity_signal_source_hold` (the true reason, replacing `equity_position_under_target` for that decision), in `degraded_reasons` and `/readyz`; clears at the next non-hold decision |
| (c) second source unavailable | No SIP bar for QQQ and QLD within the retry window (error, empty or wrong session) | As today, on the IEX bar | `status = secondary_unavailable`, with the last error | `category=secondary_unavailable` | `health.signal_cross_check.last` |
| (d) close divergence | \|IEX/SIP − 1\| > `close_divergence_bps` for QQQ or QLD, trend agrees | As today | `close_divergence: true`, with the symbols | `category=close_divergence` | `health.signal_cross_check.last` |

`RuntimeHealth.signal_cross_check` (and `/api/status`) shows the last
finished check and any decision still waiting for the SIP bar (attempts, next
attempt, deadline, last error).

A hold decision is persisted as usual. Because of that:
- `equity_decision_missing` does not fire;
- the next session decides normally;
- the watchdog reads the flag through
  `PortfolioStateRepository.latest_decision_cross_check`.

The hold targets the held weights, so the under-target check would not fire
anyway. For hold decisions it is skipped all the same, so the alarm reports
the real reason.

## 2. Timing: deferred, never blocking, never missing a session

- **First attempt:** at `max(decision time, NYSE close + sip_embargo_minutes)`.
- **Retries:** every `retry_interval_seconds` until `retry_window_minutes`
  after the first attempt.
- **After the window:** rule (c) applies.
- **Worst case:** the decision lands at close + 25 min with the defaults. That
  is inside the watchdog's 45-minute decision grace. `Config` rejects
  settings where embargo + window ≥ grace.

While it waits, the batch is **deferred**. The decision loop keeps it and
re-runs `_process_portfolio_timestamp` when the retry is due, so the
`pb-decisions` worker, its heartbeat and the event queue are never blocked.
Re-running also re-checks readiness and the account snapshot at the moment of
the decision.

The delay does not change execution. VT35's DAY market orders queue for the
next open in either case, so a 16:25 decision fills exactly like a 16:00 one.

The request is `GET bars` with timeframe 1Day, `feed=sip`, `adjustment=split`,
start at 00:00 ET of the session and **end at the session close**. It goes
through `AlpacaHistoricalData.get_session_daily_bars`, which is deliberately
uncached. It also bypasses dedup and the sticky SIP→IEX entitlement fallback
of `get_bars_batch`: a failed cross-check can never switch the history feed.

The 15-minute embargo is there because Alpaca's Basic data plan rejects SIP
queries that reach into the most recent 15 minutes. With end = close and
now ≥ close + 15 min, the request stays outside that range. An account with a
real-time SIP subscription can set the embargo to 0.

The embargo behaviour was not verified against the live API; this session had
no network or credentials. The failure mode is safe: the worst case is a daily
`secondary_unavailable` warning, never a missed or blocked session. **Check
the first PAPER sessions after deploy** in the `Signal cross-check` log lines
or `health.signal_cross_check`.

## 3. Why the second source is Alpaca SIP, not an independent vendor

- **Independent where it matters.** The live primary is one exchange's (IEX)
  last 1-minute print. The SIP daily bar is the consolidated tape, and its
  close is normally the primary-listing closing auction. That is a different
  feed and a different aggregation, and the one the backtest used. A
  stream-side fault shows up as a disagreement:
  - aggregation, labels, a stale or missing minute;
  - the 13:00 half-day bar;
  - an IEX-only print.
- **What it does not protect against.** Both sources come from Alpaca. An
  Alpaca-wide outage leaves one source (rule c), and a shared split-adjustment
  error would affect both. The 199 prior bars are provider SIP bars in any
  case, so a bad provider history is outside what any tail cross-check can
  see. The hourly basis verifier covers that part.
- **Why not Yahoo (or another public endpoint) in production.**
  `query1.finance.yahoo.com` is unauthenticated and undocumented. It has no
  SLA, no versioning promise, and can rate-limit (HTTP 429) or block cloud IPs
  at will. Yahoo's terms limit its data to personal, non-commercial use.
  - Adding it would put a third-party web dependency with no contract on the
    decision path of a trading bot.
  - Rule (c) would then fire whenever Yahoo misbehaves, which trains the
    operator to ignore the alert.
  - Section 4 shows Yahoo and Alpaca SIP official closes agree to 0.03 bps on
    average for QQQ, so Yahoo would add almost no information over SIP.
  - It remains a research tool (`stress_robustness_v1` uses it offline).
- **Operational fit.** The SIP bar needs no new credentials or dependencies,
  since the REST client already exists. It is the basis the backtest was
  validated on.

## 4. Historical frequency

Script: `research/signal_cross_check_v1/analyze.py`. Results are in
`RESULTS.json`. It is offline and deterministic.

**(i) Two real vendors, official close vs official close.** For every Stage B
session (2017-01-03 → 2026-07-30), QQQ's trend state is computed from 199
prior Alpaca SIP closes plus that session's close taken from either Alpaca SIP
or Yahoo.

| Measure (2,406 sessions) | Result |
|---|---|
| Trend-state disagreements (rule b holds) | **0** |
| QQQ close gap, mean / p99 / max | 0.03 / 0.77 / 9.3 bps |
| QLD close gap, mean / p99 / max | 1.2 / 6.2 / 433 bps |
| Sessions with a close divergence > 50 bps (rule d) | **1**: 2020-03-16 QLD, SIP 9.563 vs Yahoo 9.149 (−433 bps). One vendor's QLD close is wrong on that day; which one was not investigated. Rule (d) would have alerted, and the trend (QQQ) agreed. |

**(ii) Proxy for the live IEX last-minute close.** No historical IEX minute
bars are stored. The IEX-vs-official close gap is therefore modelled as
zero-mean noise of σ bps. The expected number of trend flips is the sum over
sessions of Φ(−|close/SMA − 1| / σ). The distance counts give the model-free
bound: a close error of that size can flip at most that many sessions.

| Sample | Sessions | QQQ close within 5 / 10 / 25 / 50 bps of SMA200 | Expected holds, σ = 5 bps | σ = 10 bps | σ = 20 bps |
|---|---:|---|---:|---:|---:|
| SIP 2017-01 → 2026-07 | 2,406 | 3 / 6 / 16 / 33 | 1.0 (0.10/yr) | 2.1 (0.22/yr) | 4.5 (0.47/yr) |
| Yahoo 1999-03 → 2026-09 | 6,733 | 11 / 25 / 87 / 174 | 4.8 (0.18/yr) | 10.9 (0.41/yr) | 24.8 (0.93/yr) |

About 10 bps is a reasonable size for the gap between the last IEX minute and
the closing auction on QQQ. At that size the guard would hold about once
every 2–5 years.

The cost of a hold is at most one session's delay, and only on the rare
sessions where the two sources straddle the SMA. Such a session has near-zero
signal content: the close is within noise of the SMA. The audit's
"(d) next close" cost (−1.2 pp CAGR) assumes every decision is one session
late. Here it applies to about one session every few years, so the expected
cost is negligible.

A missed session carries the same one-session delay for sure, whatever the
signal content. That is why rule (c) fails open: an unavailable checker must
never cost a session.

**What would make (b) fire in practice is a data fault, not noise.** Examples
are a mislabelled or partial stream bar, a stale last minute, or a half-day
bar built to 13:00 but compared at 16:00. A cross-check exists to catch
exactly those faults.

## 5. Unchanged behaviour

- **Stage B / backtest.** The backtest has one source. The guard lives only in
  `EngineRuntime` (`runtime.py`) and in an optional `signal_cross_check`
  argument of `PortfolioRuntime.process_closed_bars` (default `None` = today's
  path). `portfolio_engine.py` does not reference it. Replay of the adopted
  config with `research/paper_config_v2/run_stage_b.py` at 1x costs:

  | Run | Ending NAV | Fills | Decisions | Decision hash | Fill hash (no client ids) |
  |---|---|---|---|---|---|
  | crypto_removal_v1 (after deletion) | 1,173,537.61 | 615 | 2,406 | `c9adec34…4829` | `8c00d14f…a9f0` |
  | this branch | 1,173,537.61 | 615 | 2,406 | `c9adec34…4829` | `8c00d14f…a9f0` |

  Every payload field is identical except `config`, `config_sha256` (the new
  YAML block) and `elapsed_seconds`, including the session NAV series and
  fees of 193.53.
- **Configuration digest.** It is unchanged. The digest hashes strategy,
  mode, allocation, universe, risk limits and execution policy, not this
  block, so no new soak window is needed for identity reasons.
- **Startup catch-up.** It is not cross-checked. It decides on provider SIP
  bars (the forming session is pruned), so its input already is the second
  source.
- **Entry latch.** It is independent of the guard. A hold overrides it for
  that session, since there are no orders at all.

## 6. Configuration (`signal_cross_check:`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch (runtime only) |
| `sip_embargo_minutes` | `15` | Minutes after the NYSE close before the first SIP request (Basic-plan 15-minute SIP restriction; 0 with a real-time SIP subscription) |
| `retry_window_minutes` | `10` | Retry span after the first allowed attempt, before deciding on IEX alone |
| `retry_interval_seconds` | `60` | Spacing between attempts |
| `close_divergence_bps` | `50` | Rule (d) threshold, QQQ or QLD |

The new alert kind `signal_source_mismatch` (warning) is in the default
webhook events and in `alerting.events` of `config/settings.yaml`.

## 7. Open items and limits

- The first live sessions must confirm that the SIP request succeeds at
  close + 15 min on this account's data plan (see §2).
- Related finding, fixed in `sip_recent_data_clamp_v1.md` (SIP requests are
  now clamped to now − embargo and the recent-data error no longer counts as
  a missing entitlement). Original note: the hourly basis verifier requests
  splits-adjusted daily bars with `end = now` on the configured SIP feed. On
  a Basic plan, a request that reaches into the last 15 minutes raises an
  error. That error matches `_is_entitlement_error` (`\bsip\b`), which
  switches the **whole process** to the IEX historical feed permanently.
  - To check, look at `feed_status()` (`sip_fallback_active`) or the log line
    "SIP historical entitlement unavailable" on the VM.
  - If the fallback is active, the "199 SIP bars" of the audit are IEX daily
    bars in live. The cross-check still compares against real SIP, because
    its request bypasses that fallback.
- If the runtime stops while a decision is deferred (≤ 25 min after close),
  that session is not decided. This is the same exposure as a restart
  spanning 16:00 today.
- Holds are evaluated on the trend state only. Weight differences from the
  QLD close show up through rule (d) (a 50 bps QLD gap moves the 20-day
  volatility, and so the weight, only marginally).

## 8. Tests

`tests/test_signal_cross_check.py` has 22 tests. They are deterministic and
use synthetic bars, pinned clocks, fake fetches and `tmp_path` DBs. They cover:
- (a) the agree path trades byte-identically to no check;
- (b) blocked entry and held position, with no orders, a persisted flag and
  the repository read-back;
- (c) the bounded retry to `secondary_unavailable`, plus the runtime alert;
- (d) the divergence flag and alert;
- the embargo timing and the request end at the close;
- the deferral and re-run in the decision loop;
- the watchdog reporting `equity_signal_source_hold` instead of under-target;
- config validation, the alert vocabulary, and the explicit-SIP-feed client
  method.

Full normal suite: 1,443 passed, 3 skipped, 0 failed. ruff is clean on the
touched files, and mypy reports no issues in `src`.
