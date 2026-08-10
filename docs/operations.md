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

Build with `docker build -t parayabuck .`. Publish only to loopback and mount
durable state:

```bash
docker run --name parayabuck --restart unless-stopped \
  --env-file .env -p 127.0.0.1:8000:8000 \
  -v "$PWD/config:/app/config" -v "$PWD/data:/app/data" \
  -v "$PWD/reports:/app/reports" \
  parayabuck
```

`/app/config`, `/app/data` and `/app/reports` are volumes: runtime settings
saved from the operator UI, the SQLite runtime database, universe state and
backtest reports persist across container recreation and upgrades. An empty
`/app/config` volume is seeded from the image's default settings on first
start. The image installs the same locked dependencies CI tests against and
runs as a non-root user, handles SIGTERM through Uvicorn lifespan shutdown,
and exposes `/healthz` for liveness plus `/readyz` for readiness.
`/api/status` contains detailed runtime readiness and should remain private.
