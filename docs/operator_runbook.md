# Operator runbook

Symptom-by-symptom playbooks for the PAPER bot on the VM. Diagnostics are
read-only. **Do not bypass risk controls, edit the DB by hand, or start a
second engine.** When unsure, collect a bundle first.

```bash
parayabuck ops status
parayabuck ops doctor                 # exit 0 ok, 1 degraded, 2 critical
parayabuck ops events --since 2h
parayabuck ops bundle --output /tmp/pb_bundle.zip   # sanitized; share status.json + recent_events.jsonl first
curl -s http://127.0.0.1:8000/readyz | python3 -m json.tool
sudo journalctl -u parayabuck --since "1 hour ago" --no-pager | tail -n 100
```

Safety resets are operator actions in the dashboard (or
`POST /api/engine/reset-safety-lock`, `POST /api/safety/risk-epoch`). A reset
succeeds only after the queue drains, data gaps clear and reconciliation
passes, and it clears only the reason it verified.

## Intent alarm (`/readyz` 503 with `degraded_reasons`)

- `equity_decision_missing` — no decision for the last NYSE session by
  16:00 ET + 45 min. Check `ops explain latest`, `ops status` (Workers,
  Adjustment, Market halts) and the journal for `Ignored unfinished equity daily
  bar`, warmup, stream or signal-adjustment errors.
- `equity_position_under_target` — the target is positive but QLD held is
  below half of it 30 min after the next open. Check open orders/fills
  (`ops timeline --decision <id>`), `order_rejected` / `ambiguous_order`
  events, entry latches and the broker account.

Alarms clear by themselves (`intent_recovered`) once reality matches intent.

## Bot is not trading

`ops explain latest` names the reason:

| Reason | Meaning | Action |
|---|---|---|
| `NO_TARGET_CHANGE` | Target unchanged | Normal |
| `REBALANCE_BAND` / `MIN_NOTIONAL` | Drift under 0.5% or order under $10 | Normal; do not lower the thresholds to force trades |
| `ENTRY_LATCH` | Daily-loss or drawdown latch, or `signal_adjustment_unverified` | Wait for recovery; drawdown needs a risk-epoch reset after review |
| `ACCOUNT_STALE` | Account snapshot > 30 s old | See below |
| `SELL_PHASE_PENDING` | Previous rebalance still selling | `ops timeline --decision <id>` |
| `AMBIGUOUS_ORDER` / `REBALANCE_BLOCKED` | Broker outcome unknown | Manual, see below |

## Runtime appears dead

`ops status` shows heartbeat stale (> 90 s) or `UNKNOWN`.

- Process gone: `systemctl status parayabuck`; systemd restarts on failure.
  `sudo systemctl restart parayabuck` if needed.
- Process alive, snapshot stale: the snapshot writer failed; check the
  `observability` check and the journal.
- `stop_failed`: workers did not stop; a process restart is required.

Do not delete `health_snapshot.json` to "fix" status.

## Account stale

Snapshot older than `engine.maximum_account_age_for_entry_seconds` (30 s):
entries blocked, sells allowed. Events `account_became_stale` →
`account_recovered`. If stale for > 30 min with `reconciliation_failure`,
check Alpaca reachability (`parayabuck doctor` without `--offline`), then
restart.

## Reconciliation failing

`reconcile` check CRITICAL after 5 min without success. With a
`circuit_breaker` (`reconciliation_failed`) the runtime halts entries. Compare
broker positions/orders (app.alpaca.markets/paper) with `ops status` and the
bundle, then reset from the dashboard once a clean reconcile has run. Never
clear `safety_incidents` by hand, and never flatten broker positions to "fix"
drift.

## Ambiguous order

An order's broker outcome is unknown; dependent BUYs are blocked.
`ops timeline --order <client_order_id>`, verify on the Alpaca dashboard. The
next reconciliation usually settles it (a local order unknown to Alpaca is
canceled after 10 min). Never resubmit manually — idempotency is by
`client_order_id`.

## Rebalance stuck

A rebalance in `sell_pending` / `buy_ready` / `buy_pending` for over 1 h
(`ops doctor` → `stuck_rebalance`). Check broker order status and
reconciliation. Do not edit `portfolio_rebalance_executions`.

## Circuit breaker active

Trading state `CIRCUIT_BREAKER`, runtime `halted`, incident persisted across
restarts. Review `active_incidents.json` in the bundle and the broker account,
then reset. Restarting does not clear it.

## Crypto in the account

`risk_incident` at startup naming crypto symbols: the shared Alpaca account
holds crypto. The bot never trades, stops or streams it; close it manually in
the Alpaca dashboard if unwanted. Startup is not blocked.

## Webhook alerts not arriving

`ops status` → `alerts` check shows failed/dropped deliveries.

- Is `PARAYABUCK_ALERT_WEBHOOK_URL` set in the unit's `EnvironmentFile` and was
  the service restarted? (`alerting.webhook_url` must be empty or it wins.)
- Is the kind in `alerting.events`?
- `journalctl -u parayabuck | grep "webhook delivery failed"` — the URL is
  scrubbed from the message.

Events are still journaled: `ops events --since 1h`.

## Database issue

`ops doctor` reports `integrity_check` ≠ ok or foreign-key violations. Stop the
service and restore a verified backup (`docs/operations.md`, State, backup and
restore). Do not delete the DB.
