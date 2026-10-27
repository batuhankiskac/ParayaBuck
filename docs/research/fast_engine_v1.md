# Numba research engine (fast_engine_v1, 2026-10-16)

> Evidence and code: `research/fast_engine/` is not on `main`; it is preserved in tag
> `archive/research/fast-engine-v1-2026-10` (branch `research/fast_engine_v1`, commit `ed616dc6`).
> Runs in the separate research venv `~/.venvs/pb-research` (numba 0.68, numpy 2.5.2, pandas 3.0.5 —
> the project's numpy/pandas versions); the production code in `src/` and the project venv are
> untouched.

**Verdict: the research harness is 40–60× faster and bit-identical.** The engine re-implements
the `band_h2_combo_v1` harness (the K control: E2 + R2 early leg + vol target, next-open fills,
0.5 pp band, A1u gain band on a FIFO lot book, H2 December deferral, synthetic QLD*, costs,
financing, optional cash yield) and the corrected Turkish after-tax engine (CBRT FX, FIFO,
Yİ-ÜFE indexation, progressive tariff, instalments, liquidation) as Numba kernels, with the
block bootstrap parallelised over paths.

| job | old (pure Python) | new | speed-up |
|---|---|---|---|
| harness | 63.4 s | 1.08 s | 59× |
| one 1,000-path after-tax bootstrap | 31.8 s | 0.52 s | 61× |
| study-sized run | 392 s | 9.8 s | 40× |
| JIT compile (cached after the first run) | — | 6.0 s cold / 0.6 s warm | — |

**Parity.** `parity.py` runs the old code and the new engine side by side: 1,411 NAV/trace/dict
cells identical to the last bit (no fastmath, operation order preserved), and 5,360 published
`band_h2_combo_v1` cells (pre-tax, R-USD, after-tax windows at $10k–$1M, m = 4, OOS, 21 ETFs,
E1/E2/E3/E5, B1/B2 bootstraps) within 5e-7, i.e. the rounding of `RESULTS.json` itself. Rerun by
the orchestrator (`python -m research.fast_engine.parity --quick`): PASS in 11 s. The
harness is not on `main`: check out the tag in a separate worktree first
(`git worktree add <dir> archive/research/fast-engine-v1-2026-10`) and run the command
there with the research venv (`~/.venvs/pb-research`), never from the main checkout.

**Why earlier speed-ups did not help research.** Commits `c4943f7b` and `88e0c97c` (2.3×)
accelerated the production backtest in `src/`; every research study copied its own pure-Python
loop, so research never got faster. From now on every research study must build on
`research/fast_engine` (orchestrator rule).

**Limitations.** A study still writes its own target-weight arrays (numpy) and, for a new
execution mechanism, a kernel change plus a parity cell. Studies started before 2026-10-16
(`tax_budget_v1`, `exit_timing_v1`) keep their own harness.
