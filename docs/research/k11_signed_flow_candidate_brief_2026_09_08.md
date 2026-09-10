# K11 signed order flow candidate brief — 2026-09-08

This is a frozen research brief for a spot-only K11 observation. It does not
authorize derivative execution, PAPER/LIVE changes, configuration changes,
strategy registration, or an economic result from the one-day pilot.

The native observation is Binance USD-M aggregate trades, identified by venue,
instrument and aggregate-trade ID. A buyer-maker trade is a seller aggressor;
therefore signed notional is `-price * quantity` when
`is_buyer_maker=true`, and `+price * quantity` otherwise. The order-flow sign
is fixed as continuation: a positive imbalance produces a long spot target and
a non-positive imbalance produces cash.

At each completed UTC hour, calculate

`imbalance_24h = sum(signed_notional) / sum(price * quantity)`

over exactly the preceding 24 complete, consecutive UTC hours. The signal is
consumed only when the frozen external expected raw-row set for the symbol and
UTC event day is present. That contract is
`data/research_manifests/k11_signed_flow_archive_expectations_2026_09_08.json`;
it records the immutable archive SHA-256, member identity and CRC/size, row
count, and aggregate-trade ID contract. The signal then requires every
preceding hourly row from that set and every included trade row to have
`available_at <= decision_time`; one late or missing availability row blocks
the entire signal. A missing hour also blocks. A
strict `imbalance_24h > 0` threshold is the sole long rule. The signal is
shifted to the first strictly later spot bar open, which must have a causal
`available_at <= execution_timestamp`; same-time, missing, late or ambiguous
bars block execution. No threshold, lookback, sign, smoothing, symbol set, or
holdout is chosen from returns.

The intended causal universe is BTCUSDT and ETHUSDT on Binance USD-M, with
full coverage from 2021-01-01 through 2026-07-31 and an untouched recent
window from 2024-01-01 through 2026-07-31. The current official archive has
only BTCUSDT on 2026-07-30, with a server `Last-Modified` bound of
2026-07-31T07:02:44Z. It therefore establishes a causal one-day pilot and
cannot support a full/recent return run. The missing historical ETH and BTC
coverage is a data gate, not permission to substitute OHLCV or infer depth
imbalance. The frozen expected set proves only the one archived BTCUSDT day;
it cannot prove completeness for another symbol/day or for rows omitted from
the external archive itself.

The shared diagnostic contract remains fixed at 5% crypto allocation, gross
exposure at most 60%, cash at least 40%, 96-hour decision cadence, and no
automatic promotion. Economic diagnostics, if a complete archive later
arrives, must be frozen separately for full/recent windows and 1x/2x/3x cost
stress against the existing low-variance incumbent.
