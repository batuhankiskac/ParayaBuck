# SIP recent-data clamp v1 (2026-09-30)

Code fix, not a strategy change. Stage B, strategy parameters, risk limits and
the PAPER/LIVE boundary are unchanged.

## Finding (confirmed by reading the code)

- `config/settings.yaml` sets `alpaca.stock_historical_feed: sip`.
- The hourly split-basis verifier (`TradingRuntime.verify_equity_signal_basis`)
  and the runtime warmup call `AlpacaHistoricalData.get_bars_batch` with
  `end = now`. `_fetch_window` sent that end unchanged on the SIP feed (only
  the IEX path had a 15-minute clamp).
- Alpaca's Basic data plan rejects SIP bar queries that reach into the last
  15 minutes ("subscription does not permit querying recent SIP data").
- `_is_entitlement_error` matched that text through its `\bsip\b` pattern,
  so the first such error switched the **whole process** to the IEX
  historical feed for good (log "SIP historical entitlement unavailable",
  `feed_status()["sip_fallback_active"] = True`).

Not verified against the live API (no network or credentials in this
session). On the VM, the log line above or `sip_fallback_active` in
`/api/status` shows whether the fallback fired in the running build.

## Change

1. `AlpacaHistoricalData` takes `sip_embargo_minutes` (keyword). Every SIP
   bar request end is clamped to `now - embargo` (plain and incremental
   paths, shared `_clamp_recent_end`). The runtime and the backtest CLI wire
   it from `signal_cross_check.sip_embargo_minutes`, the one setting for
   this data-plan property (0 = real-time SIP subscription, no clamp). The
   IEX clamp is unchanged.
2. `_is_entitlement_error` returns False for the recent-data restriction
   (`recent ... data`). Such an error now propagates like any provider
   failure (cache fallback or fail closed, retried on the next hourly
   pass) and no longer downgrades the feed.

Daily bars are stamped at 00:00 ET, so an end 15 minutes in the past still
includes the current session's bar: verifier coverage is unchanged.

## Tests

`tests/test_historical_data.py`:
`test_recent_sip_data_restriction_is_not_an_entitlement_error` (fake client
raising the recent-data error: no fallback, feed stays `sip`) and
`test_sip_request_end_is_clamped_to_now_minus_embargo` (end clamped; older
ends untouched; embargo 0 disables the clamp). The existing entitlement
fallback test still passes.
