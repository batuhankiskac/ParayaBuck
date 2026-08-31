# Equity V3 alpha-roadmap execution report

Date: 2026-08-31
Status: research-only execution record; no strategy was registered, promoted, or wired into PAPER/LIVE.

This report records the local execution of the missions in the GPT Pro equity-return roadmap. A result marked `headline` is a valid same-run screen under the current parity implementation, but is not a promotion receipt until the complete validation contract and a clean committed rerun are available.

The clean deterministic current-parity control generated after this report is
tracked in `docs/equity_v3_current_parity_control_2026_08.md` (source commit
`34a9a3f...`).

The exact M5 shrinkage-ERC arm subsequently received a clean kill check and
remained below the predeclared Sharpe gate; see
`docs/equity_v3_m5_clean_kill_2026_08.md`.

## Receipt boundary

- Repository at execution start: `HEAD=97209e759d77ccc38c7225522adb9ac08f07e648`; the worktree was dirty because the parity implementation and research runners were the changes under test.
- Canonical bars: `data/historical/equity_pit_symmetric_2016_2026_v5.csv`, SHA-256 `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`, 11,686,832 bars, 9,010 lifetimes, 141,908 corporate-action records.
- Evaluation end: `2026-07-30`; terminal policy: `T1`; active equity allocation: `0.55`; historical feed: SIP; configured live stock stream: IEX.
- The old frozen `+113.642%` receipt and the later `+100.9935%` receipt are not interchangeable: they use different allocation/date/source provenance. The current-parity control below is a new comparator, not a replacement frozen receipt.
- Current roadmap artifact hashes: `control.json` `f8800154540b11bddad6a9a44b334517aea0c96d31457d13e3a50b281befb1ba`; `path.json` `be597954a710f89e3d21df216580ebef44d56277e425d7851e3ea06b00d0eca6`; `fold_deltas.json` `405167d0c8ffb1ed8de7276dc97d6b726e1f149e1ffc99665400ed988ba87746`; `summary.json` `1f688b7bf7fd35b81e039b118f26fa1c3998df85d798b7a6b11a3aafef58f911`.

## Gate 0A — control receipt reconciliation

| Receipt | Allocation/date | Return | Sharpe | Status |
| --- | --- | ---: | ---: | --- |
| Frozen canonical v3 | 0.60, through 2026-07-31 | +113.642% | 0.639346 | historical reference only |
| Later roadmap control | 0.55, through 2026-07-30 | +100.9935% | 0.625935 | not equivalent to frozen receipt |
| Current-parity same-run control | 0.55, through 2026-07-30 | +76.4538% | 0.571342 | valid within this execution; headline scope |

The third receipt uses runtime-sized observation contexts and terminal-aware lifecycle filtering. It is materially lower than the old receipts, so no candidate delta is presented as an absolute-return guarantee and no re-freezing is authorized.

## Gate 0B — observation/context/feed parity

The pre-change synthetic check reproduced different targets for the same decision when V3 saw the full eligible pool versus the runtime-sized 30-slot context (five of eight names changed). The cause was that V3's cross-sectional ranks use the histories visible in `PortfolioContext`, while the historical context was not carrying the exact runtime observation membership.

Implemented and tested before the final run:

- ordered PIT observation membership, capacity, pins, and strategy identity are persisted and included in universe hashes;
- historical product construction uses the configured strategy's shared observation ranker on bars strictly before each snapshot;
- backtest contexts and completeness gates are restricted to the runtime-sized observation membership, while managed positions/open orders reserve capacity;
- terminal symbols remain available on their last bar, then leave the active observation set on the next decision;
- the numeric-history cache cannot widen a scoped context back to the broad pool.

The first two large-data runs before the terminal lifecycle fix produced approximately 967 incomplete batches and are invalid/non-comparable. The final current-parity roadmap and follow-up runs produced zero incomplete batches and 495 weekly rebalances (990 for twice-weekly). This establishes runtime integrity for the reported screens; it does not prove economic equivalence to the old frozen receipt.

## M1 — residual path momentum

Identity: `equity_residual_path_momentum_v1`; locked formula `0.75 * V3 MH_RAW consensus + 0.25 * path-continuity score`. No coefficient retuning was performed.

Current-parity headline:

| Metric | Path | Same-run control | Delta |
| --- | ---: | ---: | ---: |
| Net return | +109.2968% | +76.4538% | +32.8430 pp |
| CAGR | 7.2376% | 5.5197% | +1.7179 pp |
| Sharpe | 0.716564 | 0.571342 | +0.145222 |
| Max drawdown | 26.6559% | 20.3809% | +6.2750 pp |
| Turnover | 149.061729 | 149.724817 | -0.663088 |
| Active-return correlation | 0.946661 | — | — |

Full contract rerun (20 folds, annual windows, 2x/3x/5x costs, jackknife, bootstrap) completed under the same current-parity control. Path full-period values are unchanged; recent return is +34.9144% (control +29.2385%), recent Sharpe 0.691608 (control 0.609706), but recent max drawdown is 15.5198% (control 12.7255%), +2.7953 pp and above the +2 pp promotion limit. The path won 13/20 folds on both return and Sharpe, median fold return 4.5298% (control 2.3821%), bootstrap P(return > control)=0.9485 and P(Sharpe > control)=0.9250; 3x-cost return remained +91.6453% and top-1 positive-P&L share was 4.7158%. Decision: keep fenced; the max-drawdown gate fails despite attractive return/Sharpe evidence. Do not promote or retune.

## M2 — deployable six equal weight

Identity: `equity_residual_momentum_deployable6_equal_v1`; exact V3 MH_RAW rank, top six, equal 10% pre-overlay targets, with the normal risk overlay unchanged.

Current-parity headline:

| Metric | Deployable six | Same-run control | Delta |
| --- | ---: | ---: | ---: |
| Net return | +73.8230% | +76.4538% | -2.6308 pp |
| CAGR | 5.3698% | 5.5197% | -0.1499 pp |
| Sharpe | 0.557382 | 0.571342 | -0.013960 |
| Max drawdown | 20.2478% | 20.3809% | -0.1331 pp |
| Turnover | 149.463047 | 149.724817 | -0.261770 |
| Active-return correlation | 0.999559 | — | — |

Scope: full-window headline only. Decision: reject/deprioritize this exact arm; its tiny drawdown benefit does not compensate for lower return/Sharpe and near-identical daily returns. A full contract was not spent on an arm already below the same-run control on both return and Sharpe.

## M3 — proxy multifactor residual momentum

Identity: `equity_proxy_multifactor_residual_momentum_v1`; fixed factors are SPY, `IWM-SPY`, and `QQQ-SPY` with the V3 gate/consensus/ivol/selection/sizing pipeline unchanged.

Decision: blocked fail-closed, not a negative alpha result. The canonical dataset has 2,658 SPY bars and 2,658 QQQ bars but zero IWM bars. IWM is also not a configured equity reference; QQQ/IWM live reference reservation and a new immutable IWM historical backfill would be required. No fabricated or substituted reference was used.

## M4 — volume-persistence residual momentum

Identity: `equity_residual_momentum_volume_persistence_v1`; score `0.80 * V3 consensus + 0.20 * percentile_rank(-log(median recent volume / median prior volume))`, with fixed windows `[-84:-21]` and `[-273:-84]`.

Recent window (`2022-01-01`–`2026-07-30`) headline:

| Metric | M4 | Same-run control | Delta |
| --- | ---: | ---: | ---: |
| Net return | +25.9791% | +29.2385% | -3.2594 pp |
| CAGR | 5.1867% | 5.7767% | -0.5900 pp |
| Sharpe | 0.546425 | 0.609706 | -0.063281 |
| Max drawdown | 14.3682% | 12.7255% | +1.6427 pp |
| Turnover | 74.322843 | 72.347666 | +1.975177 |

Volume diagnostics: 6,680 evaluations, 6,679 valid, one missing/zero case, median ratio 0.999828. Decision: reject the recent headline and keep promotion blocked independently because the canonical historical volume is SIP while the live stock stream is IEX; no historical IEX panel or SIP/IEX rank-overlap evidence exists.

## M5–M9 follow-up screens

All arms used the same current-policy control, canonical bars, T1 terminal handling, and reported `incomplete_strategy_batch_count=0` (`runtime_valid=true`, `comparability=COMPARABLE`). The result receipt is `data/research/equity_v3_followups_2026_08/headline_results.json`, SHA-256 `9a07b3b55f0d1450687b0f25aabea2d9d41d69140499240b422d7340acc6676b`.

| Mission / identity | Net return | Sharpe | Max DD | Turnover | Same-run delta / decision |
| --- | ---: | ---: | ---: | ---: | --- |
| M5 `equity_residual_momentum_shrinkage_erc_v1` | 76.6425% | 0.602380 | 19.1964% | 146.191627 | +0.1887 pp / +0.031038 / -1.1845 pp; small headline win, robustness still required |
| M6 `equity_residual_momentum_clustered_v1` | 60.6301% | 0.493733 | 20.4470% | 153.943050 | -15.8237 pp / -0.077609 / +0.0661 pp; reject |
| M7 `equity_residual_momentum_dispersion_v1` | not run | not run | not run | not run | diagnostic gate failed: 494 high-gap, 0 medium, 0 low; high-bucket 5-session alpha -0.1015%; 0 eligible folds |
| M8 `equity_factor_momentum_etf_v1` | 75.0610% | 0.682870 | 14.9662% | 47.303789 | -1.3928 pp / +0.111528 / -5.4147 pp; active correlation 0.693842 > 0.60 gate; panel incomplete |
| M9 `equity_residual_momentum_twice_weekly_v1` | 58.5118% | 0.468362 | 25.2910% | 223.996999 | -17.9420 pp / -0.102980 / +4.9101 pp; reject |

M8 used only the available fixed panel (`SPY`, `QQQ`, `XLB`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLRE`, `XLU`, `XLV`, `XLY`); `IWM`, `MTUM`, `QUAL`, `VLUE`, and `USMV` are absent. M8 is therefore an interesting risk-profile diagnostic, not an independent second leg. No M5–M9 result has full folds, annual robustness, 3x costs, or jackknife evidence yet.

## M10 — PEAD

Decision: blocked. The repository has no point-in-time announcement timestamp/session mapping, actual EPS, pre-announcement consensus EPS, earnings-surprise field, revision/restatement lineage, or typed `PointInTimeFeatureView`. Starting this mission would require a new licensed PIT fundamentals feed and feature infrastructure; no lookahead-prone proxy was run.

## M11 — composite / ensemble

Decision: `DO_NOT_RUN_COMPOSITE`. Only one production equity strategy is first-class today, so a second leg must be represented by one explicitly named composite strategy. The only promising independence screen (M8) failed the predeclared active-return-correlation `<0.60` gate (`0.693842`) and lacks 3x-cost, jackknife, and multi-fold evidence. No composite was registered or auto-promoted.

## Governance items deliberately not run

The following remain closed by invariant and were documented rather than tested as silent product changes: raising equity allocation or lowering the cash buffer/gross policy; increasing position count or changing caps merely to chase return; mutating V3 in place; parallel equity runtimes without a composite strategy; using scanner liquidity/completeness metrics as alpha; shorting, leverage, options, or derivatives; and automatic research-to-PAPER/LIVE promotion.

## Verification

- Focused parity/snapshot/product/V3/terminal tests after the terminal fix: `68 passed`.
- Follow-up and M3/M4 dedicated tests: `29 passed` plus `5 passed` for follow-ups and `3 passed` for M3/M4 in their separate runs.
- Follow-up and M3/M4 runners: `py_compile` and Ruff checks passed; final large-data runs exited 0 without tracebacks and with zero incomplete batches where runnable.
- After the compatibility fixes, the repo-wide suite with the six sandbox-only localhost-bind tests deselected finished `1147 passed, 11 skipped, 6 deselected` (3 warnings). The focused changed-surface suite independently finished `84 passed`. The un-deselected socket tests require host networking permission; the permissioned full run also exposed unrelated live-stream/webhook teardown noise and is not used as a product failure signal.

## Current decision

No mission is promotion-ready. The best next research order is: (1) obtain a clean committed current-policy control receipt, (2) keep path fenced because its full contract fails the max-drawdown gate, (3) run full robustness for ERC only if its +0.031 Sharpe headline survives cost/fold/jackknife gates, (4) obtain IWM and feed-parity data before M3/M4 reconsideration, and (5) leave M6/M9/dispersion/PEAD/composite closed unless new evidence changes their blockers.
