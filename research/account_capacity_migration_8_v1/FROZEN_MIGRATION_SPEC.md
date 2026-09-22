# Frozen account-capacity migration specification v1

**Freeze boundary:** This file and the adjacent JSON must be committed and hashed before any migration economic output is generated. No 6/8 historical replay has been run at freeze. After the freeze, implementation may only make the specified shared-account replay executable and correct; a change to any arm input, period, metric, cost, ordering, or acceptance rule invalidates this freeze and stops the study. No search over other position caps is permitted.

## Question and two arms

The hypothesis is that a shared account cap of eight may remove count-caused equity/crypto capacity mismatch while every other control remains fixed. Control A has `risk.max_positions = 6`; treatment B has `risk.max_positions = 8`. These are the only two arms and the only differing configuration field. No performance improvement is required or used to select the treatment. The treatment is the minimum global count that could hold six equity and two crypto names under the audited target geometry.

Source is new clean main `478b15bd7c56d75735de0d94f456a2b9b9b491e8`; the outcome-blind audit is archived at `da26dda53777042b58aba28af05779a2bcdd2449` under tag `archive/research/account-capacity-architecture-audit-v1-2026-09`. Active `config/settings.yaml` SHA256 is `414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`.

## Fixed incumbents and policy

Equity is `equity_residual_momentum_v3` v3.0.0, 0.55 maximum allocation, `top_n=8`, 0.10 strategy and overlay per-name caps, positive formation gate, raw multi-horizon rank consensus, IVOL rejection, inverse-vol sizing without residual redistribution, weekly schedule. Crypto is `crypto_low_variance_hysteresis_v1` v1.0.0, 0.05 maximum allocation, `top_n=2`, held-name hysteresis at rank four, 96-hour schedule, equal raw weights. All scanner, liquidity, reserved-role, lifecycle, corporate-action, and order rules are pinned to the main source/config. Global gross is 0.60, minimum target cash 0.40, crypto name cap two, daily loss 0.005, drawdown halt 0.05, rebalance band 0.005, and order floor $10. No incumbent parameter, signal, rank, timing, or strategy identity may change to make B executable.

## Pinned datasets and causal period

| Input | SHA256 | Role |
| --- | --- | --- |
| `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` | Equity PIT bars and warmup |
| `data/historical/equity_entity_lifetimes_full_v5.csv` | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` | Equity PIT entity membership/lifecycle |
| `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` | Equity corporate actions |
| `data/historical/crypto_expanded_1h.csv` | `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e` | Crypto hourly scanner and UTC-complete daily signal bars |

Hashes were independently recomputed on local files before freeze. The equity evaluation window is the established V3 window, 2017-01-03 through the final decision at 2026-07-30 23:59:59.999999 UTC; 2016 observations are warmup. Crypto cannot enter before its canonical 2021-04-01 00:00 UTC start; its sleeve remains cash before then. The joint final valuation may use **causally available** pinned data through 2026-07-31 23:59:59 UTC for next-open settlement and marking. Remaining holdings are marked at their last eligible prices, with no forced terminal liquidation; orders lacking a later fill bar are canceled unfilled and incur no trade cost. This retains earlier causal equity history rather than truncating it to crypto availability. The existing equity development/validation boundary is 2021-12-31 / 2022-01-03; any 2022 onward joint comparison is labeled an equity-boundary diagnostic because there is no separately canonical crypto split. No new favorable partition is permitted.

## Shared account and execution

Use one account ledger, one cash balance, and shared positions/orders/gross/count for both markets. Merge by **availability time**, not raw CSV labels: equity daily labels are New York midnight labels and become available only after that trading session closes; crypto UTC daily buckets become available only after 24 contiguous completed hours. Equal-time decisions run crypto before equity, matching current runtime sequencing. Feed only completed bars and point-in-time eligibility; later-market decisions see same-cycle executable BUY reservations and unresolved orders. Use the production risk overlay, target planner, and account ownership semantics rather than summing isolated equity/crypto curves. Next tradable open fills are a causal historical execution approximation: sells resolve before dependent buys, with persistent nonterminal claims, partial fills, fresh-account replanning, quantity/increment and cash constraints. Historical OHLCV cannot reveal broker rejections or failed exits; cover them with prespecified synthetic parity tests, not fabricated historical events. Mark to market from the one ledger.

Starting cash is $100,000 for both arms. Freeze 2 bps adverse slippage, 2 bps full spread (half on each fill side), zero fixed commission, 25 bps crypto taker fee, effective-dated equity regulatory fees, 1.0 cost multiplier, 10% maximum volume participation, and 20-bar liquidity lookback. Enforce configured daily-loss/drawdown entry gates with no discretionary operator reset. If a fidelity limitation in this execution or risk-epoch model dominates, return `ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE` rather than adjust an arm. No production runtime/config change is allowed.

## Measures and attribution

Measure every structural metric listed in JSON for each arm: decision and eligible-entry counts by strategy; slot, gross, sleeve, per-name and pending-order constraints; account/equity/crypto count distributions and days at cap; requested versus achievable sleeves and count-caused target/cash shortfall; target and realized gross/cash; orders, turnover, concentration/HHI, and both directions of crypto/equity crowd-out with event dates and duration. The attribution must separate insufficient eligible names, signal, IVOL gate, liquidity, per-name, sleeve, gross, crypto occupancy, and global count. For cap attribution, compare the pre-count eligible target to the post-count target under the same fixed account state; do not label any generic uninvested cash count-caused. Report worst-case complete-replacement order counts of 12 for A and 16 for B plus actual replay counts/request load.

After this committed freeze and fidelity tests, calculate economics exactly once per arm: total return, CAGR, annualized volatility, Sharpe, Sortino, max drawdown, Calmar, turnover, costs, ending NAV, and the predeclared validation-period diagnostic where supported. Do not change parameters after seeing any figure. Economic differences may expose defects or an unintended risk regime, but no return improvement or invented Sharpe/drawdown non-inferiority number is an acceptance condition.

## Hard gates and decision

G1 only cap 6 versus 8; G2 repeated deterministic results per arm; G3 unchanged exposure/cash/sleeve/per-name and entry-latch enforcement; G4 no new lookahead; G5 no production-code dependency; G6 verified broker/API/runtime feasibility for the eight-slot order/state load, including account-specific uncertainty; G7 a genuine shared ledger; G8 treatment materially reduces genuine incumbent count-cap blocking when such demand exists. Publish every binding event and magnitude without inventing a cutoff after viewing it. If no meaningful >6 demand exists, operational necessity is unproved.

Accept only if all gates pass and no fundamentally unintended risk regime is observed. Reject if a hard gate fails or eight fails the targeted structural issue. Use inconclusive when data, replay, or operational evidence prevents a defensible judgment. No other cap or candidate is tested. Even acceptance does not authorize production config changes; it leads only to `PROCEED_TO_PRODUCTION_PARITY_CHANGE_REVIEW`.
