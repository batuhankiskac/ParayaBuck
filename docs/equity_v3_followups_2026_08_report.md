# Equity V3 follow-up headline report (M5-M11)

Date: 2026-08-31

Status: research only. No strategy was registered, promoted, or wired into PAPER/LIVE.

## Receipt and runtime integrity

- Control and every runnable candidate used the same canonical symmetric-PIT-v5 bars, T1 terminal policy, `2026-07-30` evaluation end, current `0.55` equity allocation, costs, engine, and prepared inputs.
- The run loaded 11,686,832 bars, 9,010 lifetimes, and 141,908 corporate actions.
- Every reported arm recorded `incomplete_strategy_batch_count = 0`; the final run was traceback-free.
- Result SHA-256: `9a07b3b55f0d1450687b0f25aabea2d9d41d69140499240b422d7340acc6676b`.
- This is a headline screen only. It does not include 3x costs, candidate jackknife, folds, or annual robustness and cannot support promotion.

## Same-run headline

| Arm | Net return | Sharpe | Max drawdown | Turnover | Delta return | Delta Sharpe | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Control | 76.4538% | 0.571342 | 20.3809% | 149.724817 | - | - | Comparator |
| M5 shrinkage ERC | 76.6425% | 0.602380 | 19.1964% | 146.191627 | +0.1887 pp | +0.031038 | Small headline improvement; robustness required |
| M6 correlation clusters | 60.6301% | 0.493733 | 20.4470% | 153.943050 | -15.8237 pp | -0.077609 | Reject this arm |
| M8 factor ETF | 75.0610% | 0.682870 | 14.9662% | 47.303789 | -1.3928 pp | +0.111528 | Interesting risk profile, but independence gate fails |
| M9 twice weekly | 58.5118% | 0.468362 | 25.2910% | 223.996999 | -17.9420 pp | -0.102980 | Reject this arm |

M5 used 126-session residual covariance, deterministic constant-correlation shrinkage, and a long-only cyclic-coordinate ERC solution. Its average pre-overlay exposure was 52.1560%, average shrinkage intensity 0.857255, and average effective weight bets 5.693537.

M6 formed an average 17.086869 connected components from the 20-name shortlist and required no refills on average, but its headline economics were materially worse.

M8 used only the canonical-data ETFs available for the full sample: `SPY`, `QQQ`, and the ten sector ETFs `XLB` through `XLY` listed in the manifest. `IWM`, `MTUM`, `QUAL`, `VLUE`, and `USMV` are absent, so this is not a complete size/style factor panel. Its active-return correlation to control was 0.693842, above the predeclared `< 0.60` independence gate.

## Gated or blocked missions

M7 dispersion did not reach a trading backtest. All 494 usable diagnostics landed in the predeclared high-gap bucket; low and medium had zero observations, no six-month fold was eligible, and high-bucket five-session forward alpha was -0.1015%. The diagnostic gate therefore failed.

M10 PEAD is blocked. Repository search found no announcement timestamp, actual EPS, pre-announcement consensus EPS, earnings-surprise schema, revision/restatement lineage, or typed `PointInTimeFeatureView`. The existing roadmap manifest independently records that the typed view and commercial feed are absent. Without those fields, an as-of event signal cannot be made point-in-time safe.

M11 composite was not run. The factor-ETF leg failed the `< 0.60` active-return-correlation gate, and its 3x-cost, jackknife, and multi-fold incrementality gates were not run. Decision: `DO_NOT_RUN_COMPOSITE`.

## Clean M5 kill check (2026-08-31)

The exact predeclared M5 shrinkage-ERC arm was rerun once from a clean tracked
checkout at commit `16c9426...` using `--missions m5`. It reproduced the
current control (`76.4538%`, Sharpe `0.571342`) and M5 (`76.6425%`, Sharpe
`0.602380`) with zero incomplete batches and zero fallback decisions across
495 decisions. The Sharpe delta `+0.031038` remains below the frozen `+0.04`
gate (`0.611342` required), so the identity is rejected and no full contract
was run. Details and the result hash are in
`docs/equity_v3_m5_clean_kill_2026_08.md`.

## Files

- Frozen declaration: `data/research_manifests/equity_v3_followups_2026_08.json`
- Research-only runner: `scripts/research_v3_equity_followups.py`
- Dedicated tests: `tests/test_equity_v3_followups.py`
- Ignored result receipt: `data/research/equity_v3_followups_2026_08/headline_results.json`

Verification:

```text
.venv/bin/python -m py_compile scripts/research_v3_equity_followups.py
.venv/bin/ruff check scripts/research_v3_equity_followups.py tests/test_equity_v3_followups.py
.venv/bin/pytest -q tests/test_equity_v3_followups.py
PYTHONUNBUFFERED=1 .venv/bin/python scripts/research_v3_equity_followups.py --print-json
```

The first three checks passed; the dedicated suite reported `5 passed`. The final command completed successfully with zero incomplete-batch skips.
