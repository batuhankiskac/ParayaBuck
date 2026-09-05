# High-proximity364 trace review — 2026-09-05

## Review status

The native-context fixture tests pass (`3 passed`) with the current source. They use `PortfolioBacktestEngine._ingest` and `_build_context`, a real `PointInTimeUniverse`, and causal daily bars; they do not run an economic backtest or alter frozen/native state.

## Confirmed implementation issue

The frozen `HighProximity364Adapter` inherits `minimum_history_bars = 23` from the incumbent low-variance strategy, while its score requires a consecutive 364-price window. `PortfolioBacktestEngine._build_context` sets `trim = max(strategy.minimum_history_bars, 274)` and exempts BTC/USD from that trim. Consequently, with causal BTC and ETH histories both containing 364 bars, the original context exposes 364 BTC bars but only 274 ETH bars; the original scorer returns BTC/USD only. This is a structural observation-path bug, not evidence of a cash or regime benefit.

The research-only `HighProximity364HistoryFixedAdapter` declares `minimum_history_bars = 364`. The same fixture then exposes 364 bars for both BTC and ETH, keeps the future ETH bar out of the context, and ranks both symbols with the inherited P364 score and HYST4/top-2/96h policy. Its identity is deliberately distinct and it is not registered as a product strategy.

The fixture also carries raw universe membership for BTC, ETH and SOL while the observed stream contains BTC and ETH. The native context correctly preserves only the observed stream, so raw-ready membership must not be reported as observed eligibility.

## Acceptance boundary for the forwarding trace

The forwarding observer is acceptable for the one full/1x trace only when its source hash is recorded and the trace demonstrates, at decision level:

- original strategy return values, state transitions, strategy identity, cadence and HYST4 policy remain unchanged;
- provided history counts, first/last timestamps, daily contiguity, requested lookback and raw-versus-observed membership are visible;
- preflight/support/incomplete-history skips are distinct from not-due cycles and zero-target decisions;
- the original trace reproduces the BTC-only consequence, while the corrected adapter is evaluated separately with the explicit 364-bar requirement;
- whole-result parity is checked separately for NAV, fills, fees, cash, terminal marks and exposure.

The current fixture proves the first structural distinction and causal no-future-bar boundary. It does not claim that the full historical cash interval has a single cause; the full trace must provide that evidence.

## Source pins reviewed

- `scripts/research/portfolio_expansion_crypto_screen_2026_09.py` — frozen P364 scorer and inherited policy.
- `src/parayabuck/backtest/portfolio_engine.py` — native context trim and BTC benchmark exemption.
- `src/parayabuck/portfolio/crypto_low_variance_hysteresis.py` — inherited minimum-history and HYST4/top-2 decision path.
- `scripts/research/high_proximity364_trace_2026_09_05.py` — forwarding observer, reviewed at the owner-provided SHA before the full trace.
- `scripts/research/high_proximity364_history_fixed_2026_09_05.py` — research-only 364-bar adapter.
