# Disaster Recovery — ParayaBuck

> **Scope**: PAPER-only runtime. `REPLACE:` placeholders and `.env` values are install-specific; never commit secrets. No LIVE trading is covered or enabled by this guide. Loopback-only deployment (`127.0.0.1:8000` unless `PARAYABUCK_CONTAINER=YES`). State recovery is always **fail-closed, reconcile-to-broker**, never blind overwrite.

Related docs: `docs/deployment.md` (systemd, backup CLI), `docs/operator_runbook.md` (live diagnostics), `docs/operational_observability.md` (journal/snapshot internals), `docs/runtime_architecture.md`, `docs/failure_semantics.md`, `docs/paper_incident_taxonomy.md`.

**Principle — never recommend deleting state as first fix.** Deleting `parayabuck.sqlite3`, peak sidecars, universe snapshots, or journals destroys forensic evidence and lineage. Every recovery path below verifies first, restores to a temporary location, and only then — with operator confirmation — promotes.

---

## 1. State Inventory

All paths are relative to `WorkingDirectory` (repo root / `/opt/parayabuck`). `config.settings.yaml` uses `runtime.decision_db_path = data/runtime/parayabuck.sqlite3` (override via `DEFAULT_DECISION_DB_PATH`), `backup.path = data/backups`, `observability.journal_path = data/runtime/observability.sqlite3`, `observability.snapshot_path = data/runtime/health_snapshot.json`.

| Artifact | Path(s) | Classification | Backed up? | Restore? | Corruption behavior | Reconstructible from broker? |
|---|---|---|---|---|---|---|
| **Portfolio decision DB** (SQLiteStore + ExecutionLedger + PortfolioStateRepository + warmup_replay) | `data/runtime/parayabuck.sqlite3` (+ `-wal`/`-shm` while running) | **CRITICAL** | **YES** — `parayabuck backup` (online backup API, verified) | YES — restore verified backup to temp, integrity_check, then promote | `StorageInitializationError` on pragma mismatch; `PRAGMA integrity_check != ok` → `doctor` CRITICAL; runtime halts (`halted`, `circuit_breaker_reason`) | Partial: broker holds current positions/orders/account; decisions, `configuration_digest`, watermarks, rebalance phases are **not** reconstructible |
| **Execution ledger (inside decision DB)** | Tables: `portfolio_decisions`, `portfolio_orders`, `portfolio_rebalance_executions`, `risk_state`, `safety_incidents`, `reconcile_watermarks`, etc. | **CRITICAL** (subset of decision DB) | YES (same file) | same | `foreign_key_check` violations → `doctor` CRITICAL; orders fail-closed | No — ledger is source of `managed_for_account` ownership; broker cannot tell bot-owned vs external |
| **Observability DB** | `data/runtime/observability.sqlite3` | **RECONSTRUCTIBLE** | NO | NO (regenerated) | Journal writer fails → `event_journal_failed` counter increments, trading continues; stale snapshot → `heartbeat_stale` UNKNOWN, never halts trading | N/A — emits derived from runtime; history lost, trading unaffected |
| **Peak sidecar** | `data/runtime/paper_equity_peak.json` (or `live_equity_peak.json`; `resolve_peak_state_path()`; overridden by `peak_state_path`/`runtime_state_dir`) `{"account_id","peak_equity","epoch"}` | **CRITICAL** (drawdown latch) | **YES** if `data/runtime/` or `data/backups` captured; explicitly included in `deployment.md` tarball (`data/runtime/paper_equity_peak.json`) | YES — restore sidecar + DB together; `RiskPeakService.ensure_reconciled()` reconciles sidecar vs `portfolio_high_water`/`risk_state` max-wins | Missing → bootstrap `0.0` (new account). Malformed/truncated/non-finite/negative → `RiskStatePersistenceError` — **fail-closed, never falls back to 0.0** (would erase watermark) | No — peak is max historical equity; broker only gives current equity |
| **Universe artifact** | `data/runtime/active_crypto_universe.json` + `.history.jsonl` (equity has none — `universe.equity.symbols` is static config) | **RECONSTRUCTIBLE** | NO (but bundle includes snapshot hash) | No restore needed — rescanned from provider; stale check `universe.crypto.active_universe_max_age_hours` (12h) → degraded, entries blocked unless `allow_static_fallback_trading` | Missing/corrupt → `universe stale` → crypto entry gate blocked, sells still allowed; `doctor` shows `universe crypto active` REFRESH_NEEDED/WARN and `universe equity static` | No — the point-in-time crypto universe is derived from scanner + corporate actions |
| **Warmup cache** | `data/cache/` (historical bars, `scope_meta` with `config_digest`), `data/historical/` CSVs | **RECONSTRUCTIBLE** | NO | NO — refetched via `AlpacaHistoricalData` with `config_digest` validation; digest mismatch → full refetch (fail-closed) | Missing → bounded single retry, then fail-closed; never serves wrong-config bars | No — cache is provider-derived |
| **Config** | `config/settings.yaml` | **CRITICAL** | **YES** — deployment tarball (`tar czf ... config/ .env data/runtime/active_crypto_universe.json`) | YES — versioned in git; restore tarball, `diff` vs HEAD, `parayabuck doctor --strict` | Invalid/missing → `StorageInitializationError` at startup; `doctor` CRITICAL; `backup.retention_days` validated `>0 finite` | No — config defines the static equity universe, strategy, allocation, retention |
| **.env (secrets)** | `.env` (mode 600, `EnvironmentFile` in systemd) `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `PARAYABUCK_OPERATOR_APPROVAL_KEY`, `PARAYABUCK_ALERT_WEBHOOK_URL` | **CRITICAL** | **YES** (operator tarball, minus git) | YES — restore from vault/tarball; never commit | Missing → startup fails (paper needs keys); wrong keys → `auth_invalid` / `401` non-retryable | No — secrets are operator-supplied |
| **Receipts** | `data/runtime/paper_receipts/`, `data/runtime/ai_decisions/paper/` | **EPHEMERAL** (audit trail) | NO (but durable on disk; include in host backup if required) | Optional — not needed for trading; forensic only | Missing → no impact on runtime | No |
| **Backups** | `data/backups/parayabuck-*.sqlite3` (rollback-journal, no sidecars) | **CRITICAL** | SELF (are the backups) | Source for DB restore; `latest_verified_backup()` scans `mtime` + `verify_backup()` | Partial file from crash → `verify_backup() == False` → deleted, previous backups preserved, `BackupVerificationError` | No |
| **Health snapshot** | `data/runtime/health_snapshot.json` (atomic `temp+replace`, 30s cadence, ~2KB) | **EPHEMERAL** | NO | NO — regenerated every `snapshot_interval_seconds` (30s) | Missing/stale → `ops status` UNKNOWN, `snapshot_age_seconds`; `doctor` UNKNOWN; trading still runs if process alive | No — derived from in-memory health |
| **Reports** | `reports/` (generated backtest and diagnostic outputs) | **RECONSTRUCTIBLE** | NO | Regenerate with the current backtest/report commands | Missing → no runtime impact | No |

**Backup coverage — what IS included vs what is NOT:**

- **Included** (via `parayabuck backup` + deployment tarball):
  - `runtime.decision_db_path` (`data/runtime/parayabuck.sqlite3`) — all ledger/repos — via `sqlite3.Connection.backup()` (see § Backup safety below).
  - Peak sidecar (`data/runtime/paper_equity_peak.json`) — via tarball step in `docs/deployment.md` §3.
  - Config + secrets (`config/`, `.env`) — via same tarball (operator-managed, host backup recommended).
- **NOT included** (reconstructed or ephemeral):
  - `observability.sqlite3`, `health_snapshot.json`, `data/cache/`, `data/historical/` cache, and `reports/` — all regenerable.
  - `active_crypto_universe.json` — rescanned; history `.jsonl` is append-only log for forensics, not required for restart. The equity universe is configuration, not an artifact.

---

## 2. Backup Implementation — Evidence, Not Folklore

Source of truth: `src/parayabuck/backup.py` (241 lines), `src/parayabuck/cli.py:_run_backup`, `src/parayabuck/config.py:BackupConfig`.

### 2.1 Online SQLite safety — why not `cp`

```python
# src/parayabuck/backup.py:92
source_connection.backup(destination_connection)  # online backup API
destination_connection.execute("PRAGMA journal_mode=DELETE")
destination_connection.commit()
```

- **WAL-safe**: runtime DB runs `journal_mode=WAL, synchronous=NORMAL, busy_timeout=5000` (`src/parayabuck/storage.py:79-82`). A raw `cp`/`cp -a` would miss `-wal` frames and copy a half-committed transaction. The **online backup API** copies a consistent snapshot of committed transactions at backup-start time, retrying on transient locks while the runtime holds `store.lock` (`BEGIN IMMEDIATE`). The docstring is explicit:

  > "The snapshot contains every committed transaction at the instant the backup runs and excludes any in-flight uncommitted work. … it never contains a half-committed decision+order transaction."

- **Sidecar normalization**: the backup copies the header verbatim, so it would otherwise spawn `-wal`/`-shm` on next open. It is normalized to `journal_mode=DELETE` (standalone rollback-journal, no sidecars) — portable, restorable without WAL recovery.
- **Durability**: file `fsync` + directory `fsync` (`os.fsync` on both). File fsync failure → delete new file + sidecars, preserve previous verified backups, raise `BackupDurabilityError` (pruning NOT performed). Directory `fsync` errors `EINVAL/ENOTSUP/EOPNOTSUPP` treated best-effort (notably **not** `EPERM/EACCES/EBADF` which are real permission failures → `BackupDurabilityError`).

### 2.2 Verify-before-prune invariant

```python
# src/parayabuck/backup.py:150-166
if not verify_backup(destination):
    destination.unlink()  # delete corrupt new file
    raise BackupVerificationError  # previous backups preserved, no prune
prune_backups(target_dir, retention_days=retention, now=stamp)
```

`verify_backup(path)` opens read-only (`file:...?mode=ro`, `uri=True`) and runs `PRAGMA integrity_check;` — `True` only when `row[0] == "ok"` (`backup.py:176`). A backup interrupted by crash/power loss leaves a partial file → verification fails → file removed, `BackupVerificationError` raised, previous verified backups untouched. Pruning only happens after verification succeeds. `prune_backups()` orders by `st_mtime` (not filename), oldest-first, deleting only `mtime < cutoff` (`cutoff = now - retention*86400`).

### 2.3 Retention & storage

`BackupConfig` (`config.py:606`): `path: str = "data/backups"`, `retention_days: float = 7.0` (validated finite `>0`). `config/settings.yaml:227-232` ships `retention_days: 7`.

- **Frequency**: expected `parayabuck-backup.timer` every 6h (see `deploy/systemd/` and `docs/deployment.md` unit comment), plus pre-deploy `parayabuck backup` (deployment.md §3). Operators may increase to daily minimum; the timer is not enforced by the binary — host `systemctl list-timers` is the check.
- **Retention window**: **7 days default**, operator-tunable `7–30` days (`--retention-days` CLI overrides config for one run). `prune_backups` never deletes the just-verified file (its `mtime` is now).
- **Storage estimate** (measured 2026-08-29):
  - `parayabuck.sqlite3` = **2.7 MB** (2.8 MB observed; WAL 132 KB, SHM 32 KB not copied — backup is ~2.7 MB).
  - 6-hour cadence → **4/day** → 28 files / week → **~76 MB/week**.
  - Daily cadence → 7 files / week → **~19 MB/week**.
  - 30-day retention at 6h cadence → **~120 files → ~324 MB**.
  - Dominant cost is history; enable host-level compression or `BackupConfig` tuning if host disk <10 GB free (see Disk full §11).

### 2.4 CLI surface

```bash
# Create and verify a new backup, prune expired (uses config `backup.retention_days`)
parayabuck backup
parayabuck backup --retention-days 14           # one-shot override, still validates >0

# Verify-only — no new file, no prune, read-only integrity_check
parayabuck backup --verify-only data/backups/parayabuck-20260829T091500000000Z.sqlite3
# → stdout: "Backup OK: <path>"   exit 0
# → stderr: "Backup CORRUPT: <path>" exit 1

# Python API (read-only)
from parayabuck.backup import backup_database, verify_backup, latest_verified_backup, prune_backups, BackupVerificationError
verify_backup("data/backups/parayabuck-20260829T091500000000Z.sqlite3")  # bool
latest_verified_backup("data/backups")  # Path | None — most recent verified by mtime
prune_backups("data/backups", retention_days=7.0)  # list[Path] removed
```

`latest_verified_backup()` iterates backups newest-first (by `st_mtime`, reverse), returns first `verify_backup() == True`, else `None`. Use it in restore drill.

---

## 3. Scenario Playbook

Every scenario uses the same shape: **Prevent | Detect | Recover | Operator action**. Recovery never deletes state first.

### 3.1 Process crash (SIGKILL, OOM, panic)

**Prevent**: `systemd` `Restart=on-failure RestartSec=5`, `TimeoutStopSec=35` (graceful `SIGTERM` → ledger close, socket release; see `deployment.md`). Bounded queue (`event_queue_size 2048`), `journal_mode=WAL synchronous=NORMAL` avoids per-commit stalls. `NoNewPrivileges`, bounded log (10 MB × 5).

**Detect**: `systemctl status parayabuck` (exit signal), `journalctl -u parayabuck -n 200`, `health_snapshot.json` missing `session_id` or stale `written_at`, `ops status` → `Heartbeat stale >90s`, `ops doctor` → `snapshot` UNKNOWN but `process_alive` false. Observability `EventJournal` records `runtime_stopped_unexpected` with `payload: {unexpected: true}`.

**Recover**: systemd restarts automatically. Engine startup replays idempotently:
- `Storage` reopens `parayabuck.sqlite3` — WAL auto-recovers committed frames; `backup_database` never left half-committed decision.
- Ledger `INSERT OR IGNORE` on `(client_order_id, event_type, broker_timestamp)` prevents double-count fill recovery.
- `reconcile()` (7-bucket, idempotent) runs on startup — second call with same broker truth yields same buckets, no duplicate fills/stops (see §8).
- `RiskPeakService.ensure_reconciled()` reconciles sidecar vs persisted peak max-wins; account change → fail-closed `RuntimeError`.

**Operator action**:
```bash
systemctl status parayabuck
journalctl -u parayabuck --since "10 min" | tail -n 100
parayabuck ops status --json | jq .snapshot_age_seconds
parayabuck ops events --since 30m --kind runtime_stopped_unexpected
# If restart loop: collect bundle before intervention
parayabuck ops bundle --output /tmp/parayabuck_bundle.zip
```
Do **not** delete DB/snapshot. If process loops with `DATABASE_ERROR` `critical` (3 generic write fails → `bug_suspected` halt), see §3 corruption.

### 3.2 Machine reboot (host power loss, kernel panic, GC VM preemption)

**Prevent**: WAL + `fsync` durability (backup and runtime); systemd `WantedBy=multi-user.target`; `ExecStartPre=parayabuck doctor --strict --paper --offline` (ref: `deployment.md`).

**Detect**: `uptime`, `journalctl --list-boots`, `systemctl is-active parayabuck`, stale snapshot timestamp jumps.

**Recover**: Same as process crash — `WAL` recovery on next open; backups survive because verified files are `fsynced` + `DELETE` mode (no WAL sidecars). An interrupted backup leaves partial file → next `backup --verify-only` returns 1; `latest_verified_backup()` skips it.

**Operator action**:
```bash
sudo systemctl status parayabuck
ls -l data/runtime/health_snapshot.json data/backups/
parayabuck backup --verify-only data/backups/parayabuck-*.sqlite3  # spot-check
parayabuck ops doctor  # read-only preflight, no orders
# Only if DB integrity fails: restore drill §7
```

### 3.3 SQLite corruption (`PRAGMA integrity_check`, `foreign_key_check`)

This is the most severe local-data failure. Every `backup.verify_backup` and `doctor`/`bundle` database check uses exactly:

```sql
PRAGMA integrity_check;      -- expect single row 'ok'
PRAGMA foreign_key_check;    -- expect zero rows
```

Evidence: `src/parayabuck/backup.py:176` (`row[0] == "ok"`), `src/parayabuck/monitoring/diagnostic.py:258-263` and `:308-319` (same two pragmas, same semantics), `src/parayabuck/storage.py:81` (`foreign_keys=ON` required, validated).

**Prevent**: `WAL` + `synchronous=NORMAL` + `busy_timeout=5000` + `store.lock` serialization; `PRAGMA foreign_keys=ON` enforced at open (abort if `fk_val !=1`); stable filesystem (avoid NFS for `data/`); verified backups.

**Detect**:
```bash
parayabuck ops doctor                          # → decision_db CRITICAL / foreign_keys CRITICAL
sqlite3 data/runtime/parayabuck.sqlite3 "PRAGMA integrity_check;"
# → "ok" or list of errors
sqlite3 data/runtime/parayabuck.sqlite3 "PRAGMA foreign_key_check;"
# → (empty) or violating rows
sqlite3 data/runtime/parayabuck.sqlite3 "PRAGMA foreign_key_check;" | wc -l
parayabuck ops events --since 24h --kind database_error
```
Bundle includes `database_checks: {integrity_check, foreign_key_violations}` (redacted, safe to share).

**Recover**:
- **Do not delete** `parayabuck.sqlite3`. Treat corruption as evidence.
- Run restore drill §7 (verify → restore to temp dir → integrity checks → promote only on success, with operator confirmation).
- If only `foreign_key_check` fails (no `integrity_check` error), the DB is readable but referentially inconsistent — still halt entries (fail-closed) and restore; do not `PRAGMA foreign_keys=OFF` to paper over.
- Startup path `storage.py:58` treats `integrity_check` failure as transient read that degrades rather than corrupts; persistent failure → restore verified backup (`backup_database` produces rollback-journal snapshots safe to copy).

**Operator action**: see Restore drill §7 step-by-step; after restore, `parayabuck ops doctor` must show `decision_db OK`, `foreign_keys OK`.

### 3.4 Missing peak sidecar — bootstrap vs fail-closed

Evidence: `src/parayabuck/execution/risk_peak.py:26-64` (`_parse_sidecar_file` — Missing → `None`; malformed/truncated/non-finite/negative → `RiskStatePersistenceError`; caller must halt fail-closed, never fall back to 0.0), `RiskPeakService.__init__` and `ensure_reconciled` (account-scoped, `max(sidecar, persisted)`, legacy rewrite).

**Prevent**: `atomic_write` (temp+replace) for sidecar, ledger `risk_state`/`portfolio_high_water` as fallback, bound `account_id` stored with peak.

**Detect**:
```bash
ls -l data/runtime/paper_equity_peak.json
cat data/runtime/paper_equity_peak.json | python3 -m json.tool
# expect {"account_id":"...","peak_equity":..., "epoch":0}
parayabuck ops status | grep -i drawdown
parayabuck ops events --since 1h --kind risk_incident
journalctl -u parayabuck | grep -i "Peak.*mismatch\|peak sidecar"
```

| Condition | Classification | Behavior |
|---|---|---|
| File missing | **Bootstrap** | `None` → `_peak_equity=0.0` initialized; `ensure_reconciled()` bootstraps from `query_persisted_peak()` or `current_equity`; new sidecar written on next `save_peak_equity()` |
| File present, valid | **Authoritative** | `max(sidecar.peak, ledger.persisted_peak)` wins; epoch-aware; account-bound rewrite if legacy/mismatch |
| File present, **malformed** (bad JSON, non-finite, negative, missing `peak_equity`, `account_id` not string) | **Fail-closed** | `RiskStatePersistenceError` raised; trading halts (drawdown latch cannot be trusted); operator must restore or reconcile |
| File present, account mismatch (`sidecar.account_id != current account_id`) | **Fail-closed** | `RuntimeError: Account identity changed ... fail closed; peak is account-scoped and must not cross accounts` |

**Recover**:
- Missing: no manual creation needed — running `ensure_reconciled(account_id, current_equity)` bootstraps correctly. Verify with `cat paper_equity_peak.json` after one successful loop.
- Corrupt: do **not** delete and restart (would erase watermark and hide drawdown). Instead:
  ```bash
  cp data/runtime/paper_equity_peak.json /tmp/peak.corrupt.$(date +%s).json
  sqlite3 data/runtime/parayabuck.sqlite3 "SELECT account_id, equity, epoch FROM portfolio_high_water;"
  sqlite3 data/runtime/parayabuck.sqlite3 "SELECT payload FROM risk_state WHERE account_id='<id>';"
  # Restore sidecar from backup tarball or from max(persisted_peak) observation
  cat data/runtime/paper_equity_peak.json  # after ensure_reconciled rewrite
  ```

**Operator action**: always preserve corrupt file for forensics; restore from `latest_verified_backup` + ledger state, never hand-craft `peak_equity: 0`.

### 3.5 Ownership ambiguity (`OwnershipDriftError`, dual lineage)

Evidence: `src/parayabuck/execution/alpaca_models.py:30` (`OwnershipDriftError`), `src/parayabuck/execution/alpaca.py:443-494` and `alpaca_account.py:166-217`, wrapped in `runtime.py:1766` / `:2612` / `:3570` (catch → `halted`, `circuit_breaker_reason ownership_drift`).

Two variants:

**(a) Dual lineage**: `ledger.managed_qty >1e-12` **and** `repo.managed_qty >1e-12` for same canonical symbol → both claim ownership. Fail-closed. Message: `"Dual ownership lineage for {symbol}: ledger managed qty={ledger} and portfolio managed qty={repo} are both positive; reconciliation is required"`.

**(b) Drift**: `repository managed qty exceeds broker position qty` (after `managed_qty = min(|raw|, total_owned)`, `external_qty`, `qty_available`, fee-reserve adjustment for crypto) → `Ownership drift for {symbol}: repository managed qty={owned} exceeds broker position qty={raw}`. SELL/shortfall exemption: an **over-sized** SELL where `fill_activities_reconciled == False` must not trip drift against pre-fill qty; fee-adjusted crypto (`CRYPTO_TAKER_FEE_RESERVE`) accounted.

**Prevent**: Single lineage per symbol (ledger OR repo, never both); fee-reserve; `ctx.is_flat`-aware position reconciliation; `broker.reconcile()` idempotent.

**Detect**:
```bash
parayabuck ops status  # Reconcile CRITICAL, trading CIRCUIT_BREAKER
parayabuck ops events --since 2h --kind reconciliation_failure
parayabuck ops doctor | grep -i "reconciliation\|ownership"
journalctl -u parayabuck | grep -i "OwnershipDriftError\|ownership drift"
sqlite3 data/runtime/parayabuck.sqlite3 "SELECT canonical_symbol, owned_qty FROM portfolio_positions LIMIT 20;"
# Compare to broker:
# https://app.alpaca.markets/paper → positions + open orders
```

**Recover**: Automated recovery is **NOT** permitted — `ProtectionReconciliationError`/`OwnershipDriftError` are not retried (`runtime.py:108-111`, `TransientStartupError` excludes them). Operator must:
1. Inspect broker dashboard — record positions/qty/open orders.
2. Decide lineage: if manual order created the extra position → external; if ledger fill mismatched → ledger correction.
3. Use `reset_safety_lock` only after `broker.reconcile()` proves healthy (infra `automate_incident_recovery` + `load_safety_incidents` + `load_risk_state` parsable).

**Operator action**:
```bash
parayabuck ops bundle --output /tmp/ownership_bundle.zip
# Share status.json + recent_events.jsonl, DO NOT clear safety_incidents table
# After external reconciliation, restart and verify:
parayabuck ops status
parayabuck ops events --since 30m --kind reconciliation_recovered
```
Never cancel/flatten broker positions as first step — reconciliation re-adopts open positions.

### 3.6 Restore from backup (verified, no overwrite)

> **Never overwrite the production DB in place without verifying the backup first and restoring to a temp location.**

**Prevent**: verified backups (`integrity_check == ok` before prune); `latest_verified_backup()` helper; host filesystem backup of `data/backups/` off-machine.

**Detect**: `doctor` CRITICAL on DB; `verify_backup()` returns `False`; restore is operator-initiated (not automatic) — see paper_incident_taxonomy `STATE_RESTORE_FAILED` → `backup.verify_backup, restore WAL snapshot`.

**Recover — canonical drill** (run exactly in this order):

```bash
# 1. Stop trading (avoid WAL write race) and snapshot current state for forensics
sudo systemctl stop parayabuck
cp -a data/runtime/parayabuck.sqlite3 /tmp/parayabuck.corrupt.$(date +%Y%m%dT%H%M%SZ).sqlite3 2>/dev/null || true
cp data/runtime/paper_equity_peak.json /tmp/peak.$(date +%Y%m%dT%H%M%SZ).json 2>/dev/null || true

# 2. Verify backups — pick latest verified, never assume newest file is good
parayabuck backup --verify-only data/backups/parayabuck-*.sqlite3
# Or programmatically:
python3 -c "from parayabuck.backup import latest_verified_backup, verify_backup; \
  p=latest_verified_backup('data/backups'); print(p, verify_backup(p) if p else None)"

# 3. Restore to TEMP dir — never overwrite production directly
mkdir -p /tmp/parayabuck-restore
BACKUP=$(python3 -c "from parayabuck.backup import latest_verified_backup; print(latest_verified_backup('data/backups'))")
cp "$BACKUP" /tmp/parayabuck-restore/parayabuck.sqlite3

# 4. Integrity checks on the temp copy (both pragmas)
sqlite3 /tmp/parayabuck-restore/parayabuck.sqlite3 "PRAGMA integrity_check;"
# expect: ok
sqlite3 /tmp/parayabuck-restore/parayabuck.sqlite3 "PRAGMA foreign_key_check;"
# expect: (no output)
sqlite3 /tmp/parayabuck-restore/parayabuck.sqlite3 "PRAGMA foreign_keys; PRAGMA journal_mode;"
# expect: 1, wal  (will be used after promote; backup file itself is DELETE mode, rewrites to WAL on open)

# 5. Cross-check row counts vs bundle/broker for sanity (optional)
sqlite3 /tmp/parayabuck-restore/parayabuck.sqlite3 "SELECT count(*) FROM portfolio_decisions; SELECT count(*) FROM portfolio_orders;"
sqlite3 data/runtime/parayabuck.sqlite3 "SELECT count(*) FROM portfolio_decisions;" 2>&1 | head

# 6. Promote only on operator confirmation (keep corrupt file)
cp -a /tmp/parayabuck-restore/parayabuck.sqlite3 data/runtime/parayabuck.sqlite3
# Sidecar: if peak sidecar was corrupt/missing, ensure_reconciled will rewrite on next start.
# If you have a tarball with peak sidecar, restore it beside the DB before start:
# tar xzf ~/backups/parayabuck-config-*.tgz -C /tmp/restore-config
# cp /tmp/restore-config/data/runtime/paper_equity_peak.json data/runtime/paper_equity_peak.json  # only after verify

# 7. Start and verify
sudo systemctl start parayabuck
sleep 5
parayabuck ops doctor               # must show decision_db OK, foreign_keys OK
curl -s http://127.0.0.1:8000/readyz | python3 -m json.tool
parayabuck ops events --since 10m --kind reconciliation_recovered
# Compare broker dashboard positions to DB holdings — should MATCH after reconcile()
```

Failure modes during drill:
- `latest_verified_backup() is None` → no verified backup exists → restore from host-level filesystem backup or treat as fresh install (new decisions from this point; broker positions remain but ledger starts without history — operator must acknowledge lineage gap).
- Temp copy fails `integrity_check` → try next-newest verified; if all fail → host backup or fresh.
- `Borrowed` backup file is `DELETE` mode — after promotion it becomes `WAL` on first `SQLiteStore` open (`storage.py:79`), expected.

### 3.7 Broker / local mismatch — reconciliation (7 buckets)

Evidence: `src/parayabuck/execution/alpaca_reconciliation.py:1-15, 327-564` — 7-bucket, idempotent, fail-closed. `ReconciliationService.reconcile()` returns `dict[str, list[str]]` with keys `MATCH, LOCAL_STALE, BROKER_STALE, QUANTITY_MISMATCH, UNKNOWN_ORDER, MISSING_BROKER_ORDER, MISSING_LOCAL_ORDER`. Flow: `account() → _reconcile_fill_activity_fees() (watermarked 24h, paginated, deduped by activity.id, watermark only on success) → ledger.managed_for_account → holdings 7-bucket → _reconcile_stale_portfolio_ownership → _reconcile_crypto_exit_state → _reconcile_stale_ledger_entries → protective-stop (re)submission`.

Buckets (holdings layer):
- `MATCH` — `|local_qty - broker_qty| <= tol` (where `tol = max(1e-9, local*1e-8)`) and both `>1e-12`.
- `QUANTITY_MISMATCH` — both sides positive but difference exceeds tolerance.
- `LOCAL_STALE` + `MISSING_BROKER_ORDER` — local positive, broker zero.
- `BROKER_STALE` + `MISSING_LOCAL_ORDER` — broker positive, local zero.
- `UNKNOWN_ORDER` — broker `OPEN` `client_order_id` not in `portfolio_open_orders()`.
- `MISSING_LOCAL_ORDER` — actually `MISSING_BROKER_ORDER` duplicate (repo open id not in broker OPEN) — upstream bug, treated same; reported in both keys for compatibility.

**Prevent**: Watermarked fill-activity backfill (incremental, unbounded history without rescan); dedup by `activity.id`; `BEGIN IMMEDIATE` transactions; `INSERT OR IGNORE`.

**Detect**:
```bash
parayabuck ops status  # Reconcile line; healthy vs CRITICAL (>account_poll_interval*20 ≈5m no success)
parayabuck ops events --since 1h --kind reconciliation_failure --kind reconciliation_recovered
# Programmatic:
python3 -c "from parayabuck.execution.alpaca import AlpacaBroker; b=...; print(b.reconcile())"
```

**Recover**:
- Idempotent — second call with same broker snapshot yields same buckets; no duplicate fills/stops.
- `LOCAL_STALE` → `_reconcile_stale_portfolio_ownership` attempts fill recovery; if fails → `portfolio_flat_reconciled` clears stale portfolio ownership.
- `QUANTITY_MISMATCH` / `BROKER_STALE` → `ProtectionService` re-submits or cancels protective stops; `fee-adjusted` crypto qty (`* (1 - CRYPTO_TAKER_FEE_RESERVE)`) accepted, otherwise `ProtectionReconciliationError` + `manual_intervention` ledger mark.
- Observability emits but never controls trading; monitoring failure does not affect reconciliation decisions.

**Operator action**: no manual DB writes expected. If `QUANTITY_MISMATCH` persists across 3 reconciliations:
```bash
parayabuck ops bundle --output /tmp/recon_bundle.zip
# Inspect status.json `reconcile` categories, compare brokerQty vs ledgerQty
# If manual broker trade caused it: let next reconcile clear or use portfolio_flat_reconciled flow
# If ledger bug: escalate with bundle
```

### 3.8 Disk full (`ENOSPC`)

**Prevent**: Bounded logs (`RotatingFileHandler` 10 MB ×5), `prune_backups` by `retention_days`, `EventJournal` retention (`event_retention_days 30`, `event_max_events 50000`, cleanup every 100 writes), `reports/` gitignored, host disk alerts. Keep `data/` on durable volume with ≥5 GB headroom.

**Detect**:
```bash
df -h
df -i                          # inodes
du -sh data/runtime/* data/backups/* data/cache/* | sort -h
parayabuck ops doctor | grep -i "disk\|permission\|writable"
journalctl -u parayabuck | grep -E "No space|ENOSPC|disk|I/O error|database.*disk"
sqlite3 data/runtime/parayabuck.sqlite3 "PRAGMA integrity_check;" 2>&1
```

**Recover**:
- DB write failure with `ENOSPC` → `sqlite3.Error` → `StorageInitializationError` abort, or `DATABASE_ERROR` incident `critical` on write.
- Backup durability `fsync` fails with `ENOSPC` → `BackupDurabilityError`, new backup removed, previous backups preserved.
- Free space by: pruning old reports `reports/equity_*_oos` hash dirs (regenerable), host log rotation (`journalctl --vacuum-size=500M`), reducing `backup.retention_days` or cadence, **not** by deleting `parayabuck.sqlite3` or sidecar.

**Operator action**:
```bash
sudo journalctl --vacuum-size=500M
# Remove only confirmed-derived report outputs after checking retention needs.
# Reduce retention if needed:
parayabuck backup --retention-days 7
python3 -c "from parayabuck.backup import prune_backups; print(prune_backups('data/backups', retention_days=7.0))"
df -h
parayabuck ops doctor
```

### 3.9 Network / broker outage

**Prevent**: Bounded retry with backoff, no unbounded hammering. Startup `TransientStartupError` for network-class failures (connection/timeout, HTTP 5xx/429, DNS, `ConnectionError`/`TimeoutError`) up to 5 attempts; daily provider fetch single bounded retry; crypto universe scanner bounded `historical_requests` budget. Web `auto_start` retry `[5,15,30,60]s` capped at 60s.

**Detect**:
```bash
parayabuck ops status  # Account stale, Universe stale, Reconcile CRITICAL
parayabuck ops events --since 1h --kind account_became_stale --kind scanner_refresh_failure
curl -s http://127.0.0.1:8000/readyz | python3 -m json.tool  # trading_expected vs trading_ready
parayabuck ops doctor | grep -i "alpaca.*reachable\|network transient"
# Distinguish transient vs permanent:
journalctl -u parayabuck | grep -E "timeout|timed out|connection|unreachable|dns|network|401|403|429|5xx"
```
Permanent 4xx (auth/config/entitlement) are **not** retried; 5xx/429 are bounded retry.

**Recover**:
- Transient → runtime stays alive, entries blocked (`ENTRY_LATCH`, `ACCOUNT_STALE`), sells still allowed; auto-recovers when `account_recovered` / `scanner_recovered` events appear. Equity entries are unaffected by a universe refresh — there is none.
- `TransientStartupError` → caller discards runtime and may retry startup; nothing persisted as permanent.
- Offline preflight (`--offline`) classifies stale universe as `REFRESH_NEEDED` (non-blocking) so first boot without network does not fail `ExecStartPre`.

**Operator action**:
```bash
# Check Alpaca status externally
curl -s https://status.alpaca.markets  # or https
# If creds/entitlement issue → fix .env, do NOT restart with wrong keys
parayabuck ops doctor --strict --paper   # online check (fails on non-transient)
parayabuck ops doctor --strict --paper --offline  # offline proof of local health
# No DB delete needed; wait for provider recovery or fix network/creds
```

### 3.10 Backup failure (`BackupVerificationError`, `BackupDurabilityError`)

**Prevent**: `fsync` durability, verify-before-prune, retention validation `finite >0`.

**Detect**:
```bash
parayabuck backup  # exit non-zero with BackupVerificationError / BackupDurabilityError
parayabuck backup --verify-only data/backups/parayabuck-*.sqlite3; echo $?
ls -lh data/backups/  # partial file already removed on failure
journalctl -u parayabuck-backup --since "24h"  # timer unit logs
```

**Recover**:
- `BackupVerificationError` → new invalid file deleted, previous backups preserved, **no prune**. Retry `parayabuck backup` (transient I/O). If persistent, check `PRAGMA integrity_check` on live DB — corruption → restore drill §7.
- `BackupDurabilityError` (file fsync `ENOSPC`/`EIO` or directory `EPERM`/`EBADF`) → new file removed, previous backups preserved. Check `df -h`, permissions (`UMask 0077`, `ProtectHome`/`ProtectSystem`), remediate, retry.
- `FileNotFoundError` (`Runtime database not found`) → fresh install; DB will be created on next engine start.

**Operator action**:
```bash
parayabuck backup --verify-only "$(python3 -c "from parayabuck.backup import latest_verified_backup; print(latest_verified_backup('data/backups') or '')")"
ls -lt data/backups/ | head -20
python3 -c "from parayabuck.backup import verify_backup; from pathlib import Path; \
  [print(p.name, verify_backup(p)) for p in Path('data/backups').glob('parayabuck-*.sqlite3')]"
```

---

## 4. Operational Checklists

### 4.1 Pre-deploy (every release — `docs/deployment.md` §72-131)

1. `sudo systemctl stop parayabuck; curl -m 3 http://127.0.0.1:8000/healthz || echo closed`
2. Record Alpaca paper positions/orders dashboard
3. `parayabuck backup` (verify `Backup OK`)
4. `tar czf ~/backups/parayabuck-config-$(date +%Y%m%dT%H%M%SZ).tgz config/ .env data/runtime/active_crypto_universe.json data/runtime/paper_equity_peak.json`
5. `git fetch --tags && git checkout <RELEASE_TAG>`
6. `pip install -r requirements.lock && pip install -e . --no-deps`
7. Offline tests (`pytest` identity/startup/integration) → `parayabuck doctor --strict --paper --offline`
8. `sudo systemctl start parayabuck; curl /healthz; curl /readyz | jq; journalctl -n 200`
9. Verify catch-up `startup_catch_up` vs `already_invested` in `portfolio_decisions`

### 4.2 Backup/restore drill (quarterly, or after any corruption)

Execute §7 verbatim on a **non-production clone** (copy `data/backups` + `config/` + `.env` to `/tmp/drill/` and run `parayabuck backup --verify-only` + temp-restore steps). Record:

```json
{
  "drill_at": "2026-08-29T09:00:00Z",
  "latest_verified_backup": "data/backups/parayabuck-20260829T090000000000Z.sqlite3",
  "verify_backup": true,
  "integrity_check": "ok",
  "foreign_key_check_rows": 0,
  "retention_days": 7,
  "backup_count": 28,
  "storage_estimate_mb": 76,
  "restore_to_temp": true,
  "promote_test": "not executed on production (drill only)",
  "operator": "REPLACE:operator"
}
```

Keep drill receipt in `reports/` or `data/backups/drill_receipt_*.json`.

### 4.3 Health & bundle quick refs

```bash
parayabuck ops status
parayabuck ops doctor
parayabuck ops events --since 2h
parayabuck ops bundle --output /tmp/parayabuck_bundle.zip
python3 - <<'PY'
from parayabuck.backup import verify_backup, latest_verified_backup
from pathlib import Path
b = Path("data/backups")
print("latest:", latest_verified_backup(b))
for p in sorted(b.glob("parayabuck-*.sqlite3"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
    print(p.name, verify_backup(p))
PY
sqlite3 data/runtime/parayabuck.sqlite3 "PRAGMA integrity_check; PRAGMA foreign_key_check;"
ls -lh data/runtime/parayabuck.sqlite3 data/runtime/observability.sqlite3 data/backups/
```

---

## 5. Design Rationale & Non-Goals

- **Broker is authoritative for current holdings**, but **NOT for history** — decisions, watermarks, incidents, peak high-water, and rebalance phases live only in `parayabuck.sqlite3`. Recovery reconciles TO broker idempotently; it never deletes broker positions to match local.
- **Observability never controls trading** — journal/snapshot failures increment counters, never halt entries. See `docs/operational_observability.md` §D.
- **No automatic LIVE enable** — `PARAYABUCK_ENABLE_LIVE=NO` default, HMAC operator approval required; this DR doc covers PAPER only.
- **No unbounded retry** — scanner/broker/provider paths use bounded single-retry or `[5,15,30,60]s` backoff; persistent failure is fail-closed with `reconciliation_required` / `manual_intervention`, never log-spam.
- **Never delete first** — every red recovery path preserves the corrupt/missing artifact under `/tmp/...` before any write, retaining evidence for `ops bundle` sharing.

---

## 6. Related Code References (audit trail)

| Concern | Source | Lines / symbol |
|---|---|---|
| Online backup API + fsync + verify-before-prune | `src/parayabuck/backup.py` | `backup_database`, `verify_backup`, `prune_backups`, `latest_verified_backup`, `BackupVerificationError`, `BackupDurabilityError` |
| WAL/foreign_keys/busy_timeout enforcement | `src/parayabuck/storage.py` | `SQLiteStore.__init__: PRAGMA journal_mode=WAL, foreign_keys=ON, busy_timeout=5000`, `StorageInitializationError` |
| Peak sidecar missing vs corrupt | `src/parayabuck/execution/risk_peak.py` | `_parse_sidecar_file`, `RiskPeakService.ensure_reconciled`, `RiskStatePersistenceError`, `resolve_peak_state_path` |
| Dual lineage / drift | `src/parayabuck/execution/alpaca_models.py` | `OwnershipDriftError` |
| 7-bucket reconciliation | `src/parayabuck/execution/alpaca_reconciliation.py` | `ReconciliationService.reconcile` → categories `MATCH | LOCAL_STALE | BROKER_STALE | QUANTITY_MISMATCH | UNKNOWN_ORDER | MISSING_BROKER_ORDER | MISSING_LOCAL_ORDER` |
| Broker quantity resolution (fee reserve, qty_available) | `src/parayabuck/execution/alpaca.py` | `managed_qty = min(|raw|, total_owned)`, `CRYPTO_TAKER_FEE_RESERVE` |
| Fail-closed incident taxonomy | `src/parayabuck/monitoring/paper_incident.py`, `incident.py` | `db_integrity_fail_closed`, `IncidentCategory.DATABASE_ERROR / STATE_RESTORE_FAILED` |
| Doctor dual-pragma checks | `src/parayabuck/monitoring/diagnostic.py` | `collect_bundle_data`, `_run_doctor_checks: PRAGMA integrity_check + foreign_key_check` |
| Config retention validation | `src/parayabuck/config.py` | `BackupConfig: retention_days finite >0` |
| CLI verify-only + retention override | `src/parayabuck/cli.py` | `_run_backup: --verify-only, --retention-days` |
