# Operator Runbook — ParayaBuck

Practical, read-only diagnostics. **Do not bypass risk controls.** When in doubt, collect a bundle and escalate.

**One-command flow**:

```bash
parayabuck ops status
parayabuck ops doctor
parayabuck ops events --since 2h
parayabuck ops bundle
```

---

## Bot is not trading

**What it means**: scheduled decision produced no orders or risk latch blocks entries, but runtime is alive.

**Check**:

```bash
parayabuck ops status
parayabuck ops explain latest
parayabuck ops events --since 2h | grep -E "decision_summary|rebalance"
```

**Common reasons**:
- `NO_TARGET_CHANGE` — strategy target unchanged (normal weekly).
- `REBALANCE_BAND` / `MIN_NOTIONAL` — delta $7 vs $10 minimum (normal).
- `ENTRY_LATCH` — `risk` check shows `portfolio_drawdown_halt` or `daily_loss_limit`.
- `ACCOUNT_STALE` — `Account` check degraded, reconciliation failing.
- `SELL_PHASE_PENDING` — previous rebalance still in `SELL_PENDING` (see `ops timeline --decision <id>`).
- `AMBIGUOUS_ORDER` — manual intervention required.

**When human intervention needed**: `AMBIGUOUS_ORDER` / `REBALANCE_BLOCKED` with `reconciliation_required`. Otherwise, wait for next cadence or risk recovery.

**What NOT to do**: do not manually edit DB to clear latch/orders; do not lower `minimum_order_notional` to force trades.

---

## Runtime appears dead

**What it means**: `ops status` shows `Heartbeat stale >90s` or `UNKNOWN` snapshot.

**Check**:

```bash
parayabuck ops status --json | jq .snapshot_age_seconds
parayabuck ops doctor
ls -l data/runtime/health_snapshot.json
ps aux | grep parayabuck
journalctl -u parayabuck --since "1h" | tail -n 50
```

**If snapshot stale but process alive**: writer may have failed — check `observability` check: `journal failed >5`.

**If process dead**: `snapshot_age` grows, `sessions` shows `unclean` previous session (no `ended_at` with newer session). Restart via systemd: `systemctl restart parayabuck`.

**When to intervene**: if `stop_failed` state, process restart required (`Runtime workers did not stop; process restart required`).

**What NOT to do**: do not delete snapshot to “fix” status.

---

## Account stale

**What it means**: broker account snapshot older than `maximum_account_age_for_entry_seconds` (30s). Entries blocked, but sells still allowed.

**Check**:

```bash
parayabuck ops status  # Account line
parayabuck ops events --since 30m --kind account_became_stale
parayabuck ops events --since 30m --kind account_recovered
```

Events show `account_became_stale` → `account_recovered` transitions (deduplicated, not per-poll spam).

**When human intervention**: if stale >30m with `reconciliation_failure` events, check broker connectivity (`alpaca_paper` reachable via `ops doctor`), then restart.

**What NOT to do**: do not disable `account_snapshot_stale` gate.

---

## Universe stale

**What it means**: active universe file older than `active_universe_max_age_hours` (equity 36h, crypto 12h) or refresh failed.

**Check**:

```bash
parayabuck ops status  # Universe line
parayabuck ops doctor  # universe_equity/crypto checks
parayabuck ops events --since 24h --kind scanner_refresh_failure
cat data/runtime/active_equity_universe.json | jq .generated_at
```

**When human intervention**: if `refresh_failed` but `recovered` never appears and age > max, check network, then `parayabuck` will retry bounded (retry_at in snapshot). Do not manually edit universe file.

**What NOT to do**: do not set `allow_static_fallback_trading=true` to hide failure unless intended.

---

## Reconciliation failing

**What it means**: broker vs local ledger drift. `Reconcile` check `CRITICAL` if no success >`account_poll_interval*20` (5m).

**Check**:

```bash
parayabuck ops status
parayabuck ops events --since 1h --kind reconciliation_failure
parayabuck ops events --since 1h --kind reconciliation_recovered
```

Recovery should appear after `account_recovered`. If `circuit_breaker` active with `reconciliation_failed`, runtime is `halted`.

**When human intervention**: `circuit_breaker` requires operator to verify broker positions vs DB, then `reset_safety_lock` (future mission) — do not auto-clear.

**What NOT to do**: do not clear safety_incidents table manually.

---

## Ambiguous order

**What it means**: broker order outcome unknown (accepted but no ack). Dependent buys blocked.

**Check**:

```bash
parayabuck ops status  # Execution line
parayabuck ops doctor  # ambiguous_orders
parayabuck ops events --since 1h --kind ambiguous_order
parayabuck ops timeline --order <client_order_id>
```

**When human intervention**: required. Verify broker order via Alpaca dashboard, reconcile, then restart or manual resolve. Bundle shows `ambiguous_orders.json`.

**What NOT to do**: do not submit duplicate order; idempotency is by `client_order_id`.

---

## Rebalance stuck

**What it means**: rebalance execution remains `SELL_PENDING`/`BUY_READY`/`BUY_PENDING` >1h.

**Check**:

```bash
parayabuck ops doctor  # stuck_rebalance
parayabuck ops events --since 2h --kind rebalance_phase_changed
parayabuck ops timeline --decision <decision_id>
```

`timeline` shows `sell_pending` → `buy_ready` → `buy_pending` → `complete`. If stuck, no transition event.

**When human intervention**: if `submitting` orders >10m, check broker and reconciliation. Do not force phase transition via DB.

**What NOT to do**: do not update `portfolio_rebalance_executions` phase manually.

---

## Circuit breaker active

**What it means**: `Trading` = `CIRCUIT_BREAKER`, `runtime_state` = `halted`, risk incident persisted.

**Check**:

```bash
parayabuck ops status
parayabuck ops events --since 2h --kind circuit_breaker
parayabuck ops doctor | grep -i incident
```

**When human intervention**: mandatory. Review `active_incidents.json` in bundle, verify positions, then authorized reset.

**What NOT to do**: do not restart to clear breaker — it is persisted until verified recovery.

---

## Webhook alerts not arriving

**What it means**: `Alerts` check `DEGRADED` with `failed/attempted` ratio, or `dropped`.

**Check**:

```bash
parayabuck ops status  # Alerts line
parayabuck ops events --since 1h --kind runtime_stopped_unexpected  # should still be journaled
# Check stats
python -c "from parayabuck.monitoring.alerts import alert_stats; print(alert_stats())"
```

Webhook queue is bounded (256) with 3 retries; `webhook_dropped` indicates overflow.

**When human intervention**: check `alerting.webhook_url` in `sanitized_config.json` (redacted but presence visible), network, and `throttle_seconds` (60s).

**What NOT to do**: do not set `throttle_seconds=0` in production to “fix” missed alerts — it increases spam.

---

## Database issue

**What it means**: `ops doctor` shows `sqlite integrity_check` != `ok` or `foreign_key_violations>0`.

**Check**:

```bash
parayabuck ops doctor
sqlite3 data/runtime/parayabuck.sqlite3 "PRAGMA integrity_check;"
sqlite3 data/runtime/parayabuck.sqlite3 "PRAGMA foreign_key_check;"
```

**When human intervention**: restore latest verified backup from `data/backups/` via `parayabuck backup --verify-only`.

**What NOT to do**: do not delete DB to “reset”.

---

## How to collect diagnostic bundle

```bash
parayabuck ops bundle --output /tmp/parayabuck_bundle.zip
ls -lh /tmp/parayabuck_bundle.zip
unzip -l /tmp/parayabuck_bundle.zip
```

Bundle is sanitized (no `api_key`, `webhook_url`, `approval_key`). Share `status.json` + `recent_events.jsonl` first.

**Size**: <5MB, recent 200 events, no raw CSVs/DB copy.

---

## Exit codes

`ops doctor`:

- `0` healthy
- `1` degraded/warnings (e.g. `UNKNOWN` snapshot, stale universe with fallback)
- `2` critical (integrity_fail, ambiguous orders, heartbeat stale)

Use in scripts: `parayabuck ops doctor || echo "needs attention"`.

---

## Core questions answered

After `ops status` + `ops events --since 1h` you should know:

1. what failed, 2. when, 3. what trading was blocked, 4. why blocked, 5. whether orders sent, 6. whether alerts reached operator, 7. when recovered — all from durable evidence, without opening ten SQLite tables.
