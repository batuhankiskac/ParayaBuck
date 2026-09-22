# Episode and measurement contract

An episode is flat → positive inventory → continuously positive inventory through every add, partial sale, target change and rebalance → flat. Symbol-local ordinal IDs preserve repeated entries. Natural exits (55) are the primary retention cohort; two open horizon positions are forcibly settled and reported separately as censored, never as strategy exit decisions. The accounting union contains all 57 episodes.

Economic P&L = net cumulative sale proceeds − gross cumulative purchase outlays + remaining quantity × canonical completed-bar mark. Equivalently it is realized net P&L **plus** current unrealized P&L. The isolated `*` in the supplied equation is interpreted as addition, consistent with economic accounting and the stated total-P&L objective. Buy fees enter average-cost basis; sell fees reduce proceeds. Slippage/spread are already in fill prices and are not charged twice. Costs and partial fills come directly from the verified canonical replay.

The fixed normalization denominator is each episode's **total gross purchase outlay including buy fees across all adds**, determined ex post. It is a conservative, turnover-sensitive economic normalization, not raw coin-price return, initial-position return, time-weighted return or an online tradable trigger. A partial sale's realized P&L remains in the episode. All dollar paths and denominators are persisted so percentages can be audited.

MFE/MAE are max/min economic P&L of active-inventory completed daily marks, using the first timestamp for ties. Intraday HIGH/LOW and flat exit settlement are excluded. Marks reproduce canonical account snapshots, including carried last completed close when a symbol has no new bar. No artificial zero is inserted. Final settlement is measured separately: retention >1 and negative giveback are valid when the later actual fill improves upon the best active close; MAE can be positive. Nonpositive MFE has null retention/giveback, never a clipped ratio. A tiny positive MFE can make negative retention arbitrarily large, so aggregate means alone are misleading.

The daily labels are UTC bucket-start labels, with completed-bucket information used in canonical sequence. Duration calculations use these labels and should not be interpreted as tick-level execution times. Terminal liquidation executes after the last snapshot (2026-07-31); its raw source-price event is labeled 2026-07-30. Censored episode `exit_timestamp` is the horizon snapshot, while `terminal_price_timestamp` and `exit_event.timestamp` preserve the original price label. This avoids closing inventory before its final mark or producing negative final duration.

Time below peak and still-positive time use left-observation occupancy between completed marks, ending at the actual exit/horizon label. They are discrete descriptive durations, not proof the condition held continuously intraday. Profit erosion records the first later completed mark or actual settlement at 25/50/75/100% loss of the hindsight peak, with observation type explicit. No peak is supplied to the strategy.

The +5/+10/+20/+30/+40/+50% groups are nested descriptive first-crossing groups under the fixed denominator. Near-flat means abs(final normalized return) <=1% for reporting only. Retention bins are disjoint: >=75%, [50,75)%, [25,50)%, (0,25)%, <=0%. Top10% means ceil(episode count ×0.10); full-account net P&L includes terminal costs and both censored episodes. Annual reports use natural **exit-year** cohorts; entry-year data in the independent interpretation review are separately labeled.

Rank is observed only at actual strategy decisions. MFE rank is the last decision at or before the MFE label, with its age and contemporaneity flag; it is not an invented fresh daily rank. Held rank <=4 is conditional on eligibility and the two-name retained capacity. The 96-hour rule is an epoch-aligned decision cadence, not an entry-age lock or automatic expiry. A legal decision opportunity is not a guaranteed replacement. Initiating zero-target intent, final fill source decision, and actual exit are distinct during participation-limited liquidation.

Every completed daily NAV reconciles with closed-episode plus active-episode P&L. Every episode, including all large winners, large givebacks, longest holdings and round trips, is independently reconstructed with a separate average-cost implementation and stdlib rank calculation. The independent verifier imports neither primary diagnostics nor production accounting/strategy code. Scanner causality/membership remains the pinned baseline contract; the independent replay verifier states this scope explicitly.

## Reproduction

From repository root, with existing pinned data and dependencies:

```sh
.venv/bin/python research/crypto_exit_giveback_audit_v2/canonical_replay.py --universe-timeframe 1h --out research/crypto_exit_giveback_audit_v2/CANONICAL_RAW.json.gz
.venv/bin/python -m research.crypto_exit_giveback_audit_v2.build_diagnostics
.venv/bin/python -m pytest -q tests/test_crypto_giveback_v2.py tests/test_crypto_giveback_v2_hysteresis.py tests/test_crypto_giveback_v2_verifier.py
```

The builder first fails closed on canonical parity, then on independent replay and episode verification. Reports interpret the persisted observations; no counterfactual engine or exit search is present.
