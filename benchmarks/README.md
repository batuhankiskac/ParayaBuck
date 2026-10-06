# Benchmarks

Executable benchmark scripts use deterministic synthetic fixtures or committed
fixtures and never participate in runtime decisions. Outputs are disposable
diagnostics.

`bench_backtest.py` times the VT35 product backtest on
`tests/fixtures/vt35_golden_bars.csv` (QQQ/QLD/SPY 2019-2022, a subset of the
committed stress dataset). Result parity is not a benchmark concern: it is
locked by `tests/test_vt35_golden.py` on the same bars. The former
`parity.py` and `bench_backtest_core.py` ran on the pruned
`equity_1200_2016_2026.csv` (no QLD, in no tag) and were deleted on
2026-10-06.

Run a benchmark from the repository root with its documented CLI or `--help`.
