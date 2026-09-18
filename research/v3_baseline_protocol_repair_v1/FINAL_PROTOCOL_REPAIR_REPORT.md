# V3 Baseline Protocol Repair Report

## 1. Final decision

**`CORRECTED_V3_BASELINE_VALID`**

The decision is valid under the repaired, temporally frozen V2 protocol. It is
not a production promotion decision.

## 2. Scope and invariants

Audit branch: `research/v3-baseline-protocol-repair-v1`.

Production reference verified unchanged at:
`bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`.

The audit changed no production source, strategy parameter, allocation, risk
limit, runtime drawdown behavior, crypto behavior, PAPER state, or LIVE state.
`runtime.mode=paper`, `live.enabled=false`, and no live authorization remain in
force. The original corrected replay branch and its large evidence artifact
remain unchanged.

## 3. Original G5 classification

Original G5 was:

> full-period 1x offline maximum drawdown <= `risk.max_drawdown_pct` (5%).

Classification: **`G5_MISSPECIFIED`**.

The value is an account-level operational control:

```text
drawdown_pct = max(0, (peak_equity - current_account_equity) / peak_equity)
```

In PAPER/LIVE, a breach persists `portfolio_drawdown_halt`, blocks new entries,
and allows risk-reducing SELL-only decisions. It does not automatically
liquidate. Recovery requires successful reconciliation, a fresh authoritative
account snapshot, and an explicit operator risk-epoch reset that rebases the
high-water mark. The incident is visible across restart until recovery.

The offline engine passes `enforce_drawdown_halt=False` by default. A
drawdown-only breach remains in `risk_overlay_reasons` and
`drawdown_halt_shadow_events`, but the target is not capped and the alpha NAV
continues. Normal end-of-sample liquidation is a separate terminal lifecycle,
not a drawdown response.

Therefore, the 5% runtime value is not a direct bound on offline alpha NAV
maximum drawdown.

## 4. Pre-existing governance evidence

The pre-replay repository record contained no universal absolute offline alpha
maximum-drawdown threshold equal to the 5% runtime latch.

* `docs/product_foundation.md` froze the separation between operational
  drawdown incidents and offline `drawdown_halt_shadow_events`.
* `docs/strategy_architecture.md` explicitly states “halts entries” in
  production while backtest records shadow events and keeps trading.
* `docs/strategy_robustness.md` reported a frozen equity strategy's offline
  full-period maxDD of 25.45% and described documented risks as diagnostics,
  not gates.
* `docs/capital_utilization_cadence_verdict.md` used relative maxDD comparison
  in cadence acceptance and separately reported drawdown-halt shadow days; the
  accepted weekly equity candidate had 19.68% maxDD.
* The 5% comparison existed only in the task-local corrected-baseline protocol
  that was frozen before that replay's output, not as an independent historical
  strategy-acceptance rule.

Option A was selected: report offline maxDD, retain positive Calmar and
validation/cost gates, and require correct shadow-event semantics without an
absolute 1x alpha maxDD cap. No historical absolute threshold, relative
candidate rule, or invented operator-reset schedule was substituted.

## 5. Frozen protocol and provenance

Protocol:
`research/v3_baseline_protocol_repair_v1/V3_BASELINE_PROTOCOL_V2.md`

Protocol SHA-256:
`b8a09e2f430c9ff68b707d79e17e4d7154b44e44ac51732716ba51d85eecd8b2`

The hash receipt was persisted before the persisted economic output was
opened. The V2 protocol retained the original identity, data, execution, role,
and cost-stress controls. It replaced only the invalid normal 1x drawdown
comparison with a semantics-and-reporting gate.

## 6. Persisted-result reuse decision

The existing corrected result was reused; the full replay was not repeated.
This was allowed because the independent verifier confirmed:

* current `HEAD`, `main`, and `origin/main` all resolve to the production SHA;
* the strategy/config identity and role boundary match;
* bars, lifetimes, and corporate-action hashes match the frozen contract;
* structural old-vs-corrected evidence is present and pre-economic;
* persisted equity curves, holdings, fills, terminal events, and rebalances
  are sufficient to recompute the V2 metrics and shadow events.

## 7. V2 evaluation

| Gate | Result | Meaning |
|---|---|---|
| G1 validity/parity | PASS | Identity, hashes, lifecycle/PIT, execution, NAV/cash, structural and role evidence verified. |
| G2 full 1x return/CAGR positive | PASS | Recomputed from persisted NAV. |
| G3 validation 1x return/CAGR positive | PASS | Recomputed from persisted NAV. |
| G4 full 1x Sharpe/Sortino/Calmar positive | PASS | Calmar remains the drawdown-aware economic gate. |
| G5 drawdown semantics/reporting | PASS | No 1x alpha DD cap; maxDD recomputed, shadow semantics verified, runtime latch preserved. |
| G6 cost robustness | PASS | Existing 2x/3x pre-result stress controls pass. |
| G7 portfolio/role controls | PASS | Position, weight, gross, cash, and zero-reserved-target controls pass. |

Selected recomputed metrics:

| Cost | Period | Net return | CAGR | Sharpe | Sortino | Max DD | Calmar |
|---|---|---:|---:|---:|---:|---:|---:|
| 1x | Development | 60.9781% | 10.0015% | 0.8770 | 1.0681 | 15.5323% | 0.6439 |
| 1x | Validation | 22.7203% | 4.5736% | 0.4672 | 0.6410 | 18.9593% | 0.2412 |
| 1x | Full | 97.5529% | 7.3714% | 0.6880 | 0.8841 | 20.4359% | 0.3607 |
| 2x | Full | 87.8348% | 6.8071% | 0.6412 | 0.8240 | 21.3095% | 0.3194 |
| 3x | Full | 78.6259% | 6.2476% | 0.5945 | 0.7644 | 22.1282% | 0.2823 |

The 20.4359% value is reported and recomputed; it is not compared with the
5% operational latch under V2.

Shadow-event verification found 185, 189, and 198 drawdown-reason records at
1x, 2x, and 3x. Of these, 145, 148, and 157 were drawdown-only events; each
had `risk_overlay_halted=false` and a subsequent curve point. The remainder
co-occurred with daily-loss enforcement and were correctly treated as halted
by the daily-loss rule rather than as evidence that offline drawdown semantics
should be changed.

## 8. Independent verification

Artifact:
`research/v3_baseline_protocol_repair_v1/V2_INDEPENDENT_VERIFICATION.json`

Verified true:

* protocol hash;
* production/main/origin SHA identity;
* corrected role fix and V3 identity;
* all three dataset hashes;
* 5% account-latch semantics;
* offline shadow behavior;
* temporal provenance;
* metric recomputation;
* all V2 gates.

Required anti-post-hoc answer:

```text
Did the observed 20.4359% result influence V2 gate definition? NO.
```

The persisted verification explicitly records `anti_post_hoc_answer: NO` and
`observed_20_4359_percent_influenced_v2_gate_definition: NO`.

## 9. Test evidence

Focused semantic tests passed:

* `test_high_water_between_rebalances_feeds_later_drawdown`
* `test_drawdown_breach_persists_incident_and_epoch_reset_recovers`
* `test_entry_risk_latch_evaluates_from_account_poll`

The normal suite ran under the sandbox with 1068 passed, 1 skipped, and 6
localhost-socket tests blocked by sandbox permission. Those six tests were
rerun with host permission and all passed. The initial `pytest` command itself
therefore is recorded as sandbox-limited, not silently relabeled as a clean
full-suite pass.

## 10. Artifact inventory

* `DRAWDOWN_SEMANTICS_AUDIT.md`
* `PREEXISTING_GOVERNANCE_EVIDENCE.md`
* `G5_AUDIT.json`
* `V3_BASELINE_PROTOCOL_V2.md`
* `PROTOCOL_HASH_RECEIPT.txt`
* `SHADOW_SEMANTICS_TEST_RECEIPT.json`
* `evaluate_v2.py`
* `V2_EVALUATION.json`
* `V2_INDEPENDENT_VERIFICATION.json`
* `FINAL_DECISION.md`

No large replay output was copied into the repair branch.
