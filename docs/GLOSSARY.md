# Glossary

The short names used across the README, the operations docs and the research
reports. Each entry points to the doc that defines it in full.

## Strategy identity

| Term | Meaning |
|---|---|
| **QQQ / QLD / SPY** | QQQ tracks the Nasdaq-100 and is the signal. QLD is the 2x daily-leveraged Nasdaq-100 ETF and the only holding. SPY is the benchmark. |
| **VT35** | The base rule: hold QLD at weight `min(1, 0.35 / 20-session annualized QLD volatility)` while QQQ is in trend, cash otherwise. "VT" = volatility target, 35 = 0.35. [`validated_strategies.md`](validated_strategies.md) |
| **E2** | The trend vote (since 2026-10-12): QQQ is in trend when at least 2 of 3 hold: close > 200-session SMA (**SMA**), 252-session return > 0 (**TSM**), close > midpoint of the 126-session high/low (**DON**). [`research/e2_integration_v1.md`](research/e2_integration_v1.md) |
| **R2** | The early re-entry path (since 2026-10-14): while the vote is off, hold half the weight when QQQ is above SMA50, calm, and up over 20 sessions. Strategy version 1.2.0. [`research/fast_reentry_integration_v1.md`](research/fast_reentry_integration_v1.md) |
| **A1u** | Gain-scaled downsize band: a small downward resize is skipped unless the drift is large enough for the position's embedded gain, to avoid realising taxable gains for little risk change. [`research/gain_band_integration_v1.md`](research/gain_band_integration_v1.md) |
| **H2** | December deferral: a downsize that would fill in the last 10 NYSE sessions of December waits for January. Exits and risk-driven cuts are never deferred. |
| **K** | The current production composition: VT35 + E2 + R2 + A1u + H2 (`equity_leveraged_trend_v1` 1.2.0 plus the two execution rules). Research reports compare every candidate against K. Deployed 2026-10-15. |

## Running the bot

| Term | Meaning |
|---|---|
| **PAPER / LIVE** | Alpaca's simulated account / a real-money account. The bot runs PAPER only; LIVE is disabled and needs a separate multi-step authorization (README, "Enabling live"). |
| **Trading host** | The one machine allowed to trade (`PARAYABUCK_TRADING_HOST=1`), the GCP VM. Every other machine refuses to start the engine. [`deployment.md`](deployment.md) |
| **Decision** | The once-a-day target computed after the NYSE close on the completed daily bar. Orders fill at the next open. [`operations.md`](operations.md) |
| **Startup catch-up** | A fresh deployment decides once on the latest completed bars instead of waiting a session. |
| **Risk latch / risk epoch** | Catastrophe brakes: a 20 % daily-loss and a 75 % drawdown latch. A drawdown halt persists until an operator resets the risk epoch. [`operator_runbook.md`](operator_runbook.md) |
| **Soak** | A monitored PAPER period on one configuration digest. Any config change starts a new soak session. [`operations.md`](operations.md) |
| **Intent watchdog** | Compares what each decision should have produced (orders, fills, positions) with what happened, and alerts on a mismatch. |

## Research

| Term | Meaning |
|---|---|
| **pp** | Percentage points: 15.8 % → 15.3 % is −0.5 pp. |
| **After tax** | Returns after Turkish capital-gains tax on a $100k account (2026 tariff, Yİ-ÜFE indexation, FIFO lots). The owner's real outcome, so most verdicts are judged on it. |
| **Pre-registration** | The arms, windows and pass/fail gates are committed before any candidate is simulated, so results cannot steer the test. |
| **Arm** | One candidate variant inside a study. |
| **Gates (G1, G2, …)** | A study's pre-registered pass/fail criteria (return, drawdown, sub-periods, bootstrap, turnover…). Numbering is per study. See each report. |
| **Bootstrap** | Re-sampled return paths used to check that a win is not one lucky history. "Clean" bootstrap = the variant used across recent reports. |
| **OOS** | Out of sample: markets or periods not used to design the rule. |
| **Stage B** | Replaying a research candidate on the production engine (real order planner, ledger and costs, 2017–2026) before it is deployed. e.g. [`research/k_stage_b_v1.md`](research/k_stage_b_v1.md) |
| **Fast engine** | The Numba research backtester that new studies run on. Its code lives in a tag, not on `main`. [`research/fast_engine_v1.md`](research/fast_engine_v1.md) |
| **Archive tag** | `archive/research/<name>-<yyyy-mm>`: each study's code, data and results. `main` keeps only the reports. |
