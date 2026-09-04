# Equity V3 profit-protection research — 2026-09 R2

**Mission:** `equity_profit_protection_2026_09_r2`<br>
**Supersedes:** `equity_profit_protection_2026_09` (preserved archived blocked attempt)<br>
**Decision:** `KEEP_CURRENT_V3_AFTER_VALID_COMPARISON`

## 1. Provenance and boundaries

- Experiment parent / clean Commit A pin: `6b0cabe5a03d8d3c02173ebfce5b6430252b12a1`.
- Runner implementation commit: `05837c33e06cf3f548f2b41f8633c3a4128f05e7`.
- Runner SHA-256: `42247e32c5c0d081d9c3aa32a52779a57a1e59dc0016377b51f29ab2c060f7ec`.
- The experiment ran in a clean detached worktree. The final result commit is the child of the Commit A pin; its self-reference is intentionally recorded by the final Git handoff rather than embedded in its own object.
- Current configuration digest: `1833407d7c42fcef45b1f2aef200a3fd14e344d6cdcc9b03c578f8e494152a3e`.
- Current V3: `equity_residual_momentum_v3`, version `3.0.0`, allocation `0.55`, T1 terminal policy, weekly scheduled decisions, current effective-dated fees/slippage/spread, prepared inputs, and numeric history.
- No files under `src/parayabuck/` or `config/settings.yaml` were changed by this mission. No PAPER/LIVE/Alpaca activity, production registration, promotion, threshold change, replacement, or large dataset commit occurred.
- Concurrent user-owned working-tree changes were left unstaged and were not included in the R2 commits.
- The old R1 runner, manifest, report, and history remain unchanged.

The R2 runner uses the current-parity machinery in `research_v3_current_parity_control.py`, `research_v3_equity_roadmap.py`, and `run_symmetric_v5_validation.py`; it does not import or use the old exit-latency runner or `equity_1200_2016_2026.csv`.

## 2. Dataset verification

| Input | SHA-256 | Result |
|---|---|---|
| `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` | verified |
| `data/historical/equity_entity_lifetimes_full_v5.csv` | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` | verified |
| `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` | verified |

The corporate-action audit found 143,446 rows across 5,683 symbols: 141,908 cash dividends, 61 stock dividends, 270 forward splits, and 1,207 reverse splits. Cash dividends are applied once by the current loader. Split/stock-dividend rows are audited but not re-applied because the executable V5 bars are already split-adjusted.

## 3. E0 parity gate

The current-parity reference produced 1,890 fills, 495 rebalance decisions, 12 terminal events, and zero incomplete strategy batches. Both independent R2 E0 replays matched the reference exactly:

| Component | SHA-256 |
|---|---|
| Equity curve | `9d08f9c3b3a6959189fb0a482c7de6d294fd4a72e1cec3ae7fe7f79d6f2e02fb` |
| Fill sequence | `dff4000e6c71074bcd7ffde96c98a209eb9b54c74233ddb264b7fb151d1b0b63` |
| Target sequence | `b3b39c78d7731776d9dcc90a9eff2352b2e18007848bb14d23f68027d77f15c9` |
| Terminal events | `8fd87474a7aaf9a7c5977dcb6c5d0cd02b09af0254d9fdefe81ba9ca0c766c95` |
| Universe | `d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011` |

Full-window E0 metrics were return `0.764538`, Sharpe `0.571342`, max drawdown `0.203809`, CAGR `0.055197`, fees `5835.61`, and turnover `149.724817`. The 2022-present E0 metrics were return `0.292385`, Sharpe `0.609706`, and max drawdown `0.127255`.

## 4. Integration tests

The focused R2 engine-level suite passed: **12 passed**. It covers current-parity replay determinism; scheduled/non-scheduled +5% crossing; activation-bar suppression; next-open and gap-through execution; E3 two-timestamp confirmation; duplicate replay; partial-fill pending quantity; segmented restart equivalence; scheduled signal precedence; pending SELL versus rebalance; no intra-week BUY/replacement; external/manual exclusion; actual split adjustment; T1 terminal handling; and the no-intermediate-exposure-increase invariant.

Static validation also passed: `py_compile`, Ruff check, Ruff format check, and `git diff --check`.

## 5. Stage One results

Each frozen candidate was run independently on the full current-parity window and on the 2022-present window. No result was promoted to robustness.

| Candidate | Full return / Sharpe / max DD | Recent return / Sharpe / max DD | Full incomplete batches | Net protection value | Survivor |
|---|---:|---:|---:|---:|---:|
| `E1_TP5_CLOSE` | `0.738592 / 0.589697 / 0.227592` | `0.340302 / 0.732500 / 0.130643` | 42 | `-8286.959939` | no |
| `E2_PROFIT_LOCK_50` | `0.540491 / 0.462265 / 0.215606` | `0.205530 / 0.479556 / 0.127003` | 42 | `-15802.572189` | no |
| `E3_PROFIT_ALPHA_CONFIRMED` | `0.782813 / 0.587047 / 0.204829` | `0.297283 / 0.628703 / 0.118283` | 42 | `-6534.673655` | no |

All candidates passed control parity, observation idempotency, no intermediate exposure increase, and no intra-week BUY/replacement. All failed the positive net-protection gate. E1 and E2 also failed the full max-drawdown gate; E2 failed return and Sharpe gates. The full candidate runs also failed the zero-incomplete-batches gate. E3 passed the comparison metric gates but was not a Stage One survivor because its net protection value was negative and its full run was incomplete.

## 6. Robustness and final classification

`stage_one_survivors` is empty. Walk-forward, temporal-window, cost-stress, bootstrap, exclusion, and jackknife robustness were therefore **not run**, as required by the frozen survivor-only stop condition.

The result is `KEEP_CURRENT_V3_AFTER_VALID_COMPARISON`: the current V3 baseline is valid and parity-verified, while no frozen protection candidate is eligible for PAPER or a separate architecture review. This is not a claim that profit protection can never work; it is the result under the frozen candidates, data, costs, state machine, execution, and gates.

## 7. Result artifacts

| Artifact | SHA-256 |
|---|---|
| `data/research/equity_profit_protection_2026_09_r2/E0_CONTROL.json` | `fe709d5f1bb19000a69c348e612f55421375532d2df0d9eb6f2ab4cf5de0e065` |
| `data/research/equity_profit_protection_2026_09_r2/E1_TP5_CLOSE.json` | `0ca5bc65e5e94a264541603f72e26eda692d0ef042064c2ac50519d13546c25a` |
| `data/research/equity_profit_protection_2026_09_r2/E2_PROFIT_LOCK_50.json` | `77e1347d3fc4d839e647ee9be676fe1c73507256fa06607ce0594f056b14b63d` |
| `data/research/equity_profit_protection_2026_09_r2/E3_PROFIT_ALPHA_CONFIRMED.json` | `27e099d6db94c204cdaab107134d9011818bb8ee4b431071e998f780e248e021` |
| `data/research/equity_profit_protection_2026_09_r2/stage_one.json` | `58f12fc34e3de5766c93c5bd430afd90f3c376b9d88e3a8c73bec13db798df4a` |
| `data/research/equity_profit_protection_2026_09_r2/artifact_hashes.json` | `1cb63dbfc084c3f2796f549eef8d5d7a1f9742c79187d06bdd0d53a4a6b47b6f` |
| `data/research_receipts/equity_profit_protection_2026_09_r2_current_control.json` | `9692288e5319334321227d9a1da5a0c2e89e566fc9d19c21b9df438c35b1b1a1` |

No economic KEEP, PAPER eligibility, or production promotion is authorized for E1, E2, or E3.
