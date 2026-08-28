# Benchmark Artifact Labels — Mission 4 Audit

Mission 4 requires current-vs-historical metadata be unambiguous. This file labels every parity/benchmark artifact so future agents know which is current and which is frozen historical.

| Artifact | Label | HEAD / Hash | Note |
|---|---|---|---|
| `backtest_core_before.json` | current baseline — Mission 3 final (1.30s 3y, 0.24s 6m, batch4 5.32s) HEAD 89e87c7 — NOT frozen, current vs historical must be explicit | — | exists |
| `backtest_core_after.json` | frozen baseline — Mission 1 after (2.20s 3y) — legacy parity fixture, do not compare as current | — | exists |
| `backtest_core_after_mission1.json` | frozen baseline — Mission 1 after (2.20s) — legacy, identical to backtest_core_after.json | — | exists |
| `backtest_core_mission2_before.json` | frozen baseline — Mission 2 before (2.20s) — historical, before numeric history acceleration | — | exists |
| `backtest_core_mission2_after.json` | historical baseline — Mission 2 after (1.33s 3y, 0.25s 6m) — intermediate, before Mission 3 research platform | — | exists |
| `mission1_parity_before.json` | frozen baseline — Mission 1 parity fixture (curve hash 3c3bfd...) at HEAD 63b7c72 — legacy, do not regenerate | — | exists |
| `parity_guard.json` | frozen canonical parity fixture — symmetric PIT 2016-2026 v5 at HEAD a69cbc3 — legacy parity fixture, canonical dataset sha256 bb7aaae... | — | exists |
| `paper_runtime_baseline.json` | historical baseline — PAPER runtime baseline 2026-08-26 at commit 8a00ff7 — frozen, for startup/weekly decision comparison | — | exists |
| `post_research_runtime_baseline.json` | historical baseline — post-research runtime baseline 2026-08-26 — frozen parity before/after | — | exists |
| `research_acceleration_baseline.json` | frozen baseline — research acceleration baseline 2026-08-27 (single 3.23s, batch4 13.28s) — historical | — | exists |
| `research_acceleration_after.json` | historical baseline — research acceleration after (single 4.49s, batch4 11.62s) — historical, after mission 0A | — | exists |

## Rules
- **Do NOT rewrite history**: frozen baselines retain old strategy names (e.g., `equity_residual_momentum_v2`), old HEAD hashes, and old ending_equity hashes. They are parity fixtures.
- **Current baseline** for Mission 4 is `backtest_core_before.json` (HEAD 89e87c7, Mission 3 final, 1.30s). New measurements after Mission 4 must be compared to this, not to frozen `backtest_core_after.json` (2.20s).
- **Canonical dataset** for parity is `parity_guard.json` (sha256 bb7aaae...) — frozen, expensive to regenerate. Keep as is.
- If regenerating benchmarks, write new file `backtest_core_mission4_after.json` and label it `current`, never overwrite frozen files.

Generated: 2026-08-29 Mission 4 audit
