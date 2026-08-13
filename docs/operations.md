# ParayaBuck operations

## Persistent state

Keep `data/` on durable storage. SQLite state, active-universe snapshots, fill
lineage, high-water marks, and runtime decisions live there. Backtest artifacts
are written to `reports/`.

Before upgrades, stop the process cleanly and copy both directories. Restore by
putting them back at the same paths before starting the new version. Never copy
an SQLite database while the process is writing unless the storage layer's
snapshot mechanism guarantees consistency.

## Paper recovery

On restart the runtime reconciles Alpaca before enabling decisions, pins open
positions with portfolio-order lineage into the active universe, restores the
high-water/risk state, warms every configured strategy timeframe, and then
resumes. A stale universe can be refreshed from Alpaca; an API failure preserves
the previous successful snapshot or uses the configured fallback.

If the safety lock opens, inspect `/api/status`, broker positions/orders, and
SQLite state. The Web UI reset action succeeds only after the event queue drains,
data gaps clear, and broker reconciliation passes.

## Incident reconciliation

Run the read-only reconciliation with the equity observed at the incident start.
The command downloads paginated Alpaca deposits, withdrawals, cash ACATS, and
cash journals automatically; `--net-transfers` is only an optional adjustment:

```bash
.venv/bin/python scripts/reconcile_paper_incident.py \
  --database data/runtime/parayabuck.sqlite3 \
  --after 2026-08-01T00:00:00Z \
  --starting-equity 100000 \
  --starting-unrealized-pnl 0 \
  --net-transfers 0
```

The report exposes realized PnL, starting/ending/change in unrealized PnL, fees,
transfers, expected ending equity, actual broker equity, and an unexplained
residual. It also writes an anonymized replay fixture.

## Docker

Build and run with Compose (builds the existing Dockerfile: python:3.12-slim,
non-root `parayabuck` user, locked requirements, `SIGTERM` stop signal):

```bash
docker compose up -d --build
docker compose logs -f parayabuck
docker compose down
```

The compose service publishes the Web UI to loopback only
(`127.0.0.1:8000:8000`), loads environment from `.env` (credentials are never
part of the command line or the compose file, so they cannot appear in
`docker ps` or the container logs), and mounts the Dockerfile `VOLUMEs`:
`./config`, `./data` and `./reports` on the host. Runtime settings saved from
the operator UI, the SQLite runtime ledger, universe state and backup/backtest
artifacts all survive container recreation and upgrades. An empty `/app/config`
volume is seeded from the image's default settings on first start.

### Restart policy

`restart: unless-stopped` keeps the container running across daemon restarts
and host reboots while still honoring an explicit `docker compose stop`. The
container is only restarted when the process actually dies — a broken config
fails the app at startup, so a crash loop is loud in `docker compose logs`
rather than silently healthy.

### Healthcheck semantics: alive vs ready

- `/healthz` — process liveness only. Returns `200 {"status": "ok"}` whenever
  the Web process can serve the route, regardless of configuration, credentials
  or engine state. This is the container health probe (Dockerfile `HEALTHCHECK`
  and the compose `healthcheck` both hit it).
- `/readyz` — layered deployment readiness: `config_valid` AND (trading is not
  expected OR the expected runtime is actually healthy). A process can be
  alive but not ready (auto-start expected but the engine ended in an error
  state: `/healthz` stays 200, `/readyz` is 503).

Container health deliberately does NOT depend on `/readyz`: in a paper setup
credentials may legitimately be absent or the engine intentionally stopped, and
an alive control plane must never be restarted for that. `docker compose ps`
shows `healthy`/`unhealthy` from `/healthz`; readiness is inspected with
`curl -s http://127.0.0.1:8000/readyz` (or `/api/status`, which stays private).

### Log rotation

The app streams structured logs (`timestamp | LEVEL | logger | message`) to
stdout; Docker's `json-file` driver rotates them per container:
`max-size: 10m`, `max-file: 3`. No API secrets are ever logged: the app never
logs credential values, and the container command line carries none.

## Backup

The runtime decision database is WAL-mode SQLite that stays open while the
process runs, so backups use the sqlite3 online backup API (a consistent
snapshot of all committed transactions, never a raw file copy):

```bash
.venv/bin/python -m parayabuck.cli --config config/settings.yaml backup
# or, with the console script:
parayabuck backup
```

Options and defaults (from the `backup:` block in `config/settings.yaml`):
- `path` — backup directory (default `data/backups`);
- `retention_days` — delete backups older than this, oldest first (default 7);
- `--retention-days N` on the CLI overrides the configured value for one run.

Each run writes a timestamped `parayabuck-<UTC>.sqlite3` snapshot, verifies it
with `PRAGMA integrity_check`, and prunes expired backups. Snapshots are
normalized to standalone rollback-journal databases (no `-wal`/`-shm`
sidecars) and are directly openable for restore. Schedule it with cron or a
systemd timer; run it while the process is live — it does not require stopping
the engine.
