# Deployment — systemd on Google Cloud

ParayaBuck runs as a single process (`parayabuck web`) that serves the
operator console AND runs the PAPER engine in-process. All commands below
were verified against this release (`parayabuck web` == `parayabuck` with no
arguments; `cli.py` routes both to `web.app.main()`; engine auto-start on
paper mode with credentials). `REPLACE:` values are install-time.

## Launch facts

- Entry point: `parayabuck web` — uvicorn on `HOST`/`PORT` (default
  `127.0.0.1:8000` from `config/settings.yaml`). Loopback-only unless
  `PARAYABUCK_CONTAINER=YES` + `HOST=0.0.0.0` (Docker only).
- `WorkingDirectory` MUST be the repo root: `CONFIG_PATH=./config/settings.yaml`
  and all `data/runtime/...` paths are relative to the process cwd.
- Health: `GET /healthz` → `200 {"status":"ok"}` (liveness);
  `GET /readyz` → JSON `{ready, process_alive, config_valid,
  trading_expected, trading_ready, engine_state, runtime_expected, error}`,
  `200` when ready, `503` otherwise. A `503` during warmup
  (`engine_state=warming_up`) is expected behavior.
- Env (`.env`, mode 600): `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (required,
  paper), `PARAYABUCK_ENABLE_LIVE=NO`, `CONFIG_PATH`,
  `PARAYABUCK_OPERATOR_APPROVAL_KEY` (live-only), `PARAYABUCK_ALERT_WEBHOOK_URL`
  (optional), `LOG_LEVEL`, `OPEN_BROWSER=0` (headless).
- Backup: `parayabuck backup [--retention-days N]` — consistent SQLite online
  backup to `data/backups/` with integrity check (safe while running).

## systemd unit (`/etc/systemd/system/parayabuck.service`)

Track actual hardened templates in `deploy/systemd/`:
`parayabuck.service` (non-root, loopback, `EnvironmentFile`, `Restart=on-failure`, `TimeoutStopSec=35`, `UMask=0077`, `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, `ProtectHome=true` with `ReadWritePaths` for `/opt/parayabuck/{config,data,reports}`, `ExecStartPre=parayabuck doctor --strict --paper --offline`), plus `parayabuck-backup.service`/`parayabuck-backup.timer` every 6h with verified backups.

```ini
[Unit]
Description=ParayaBuck Paper Trading Engine
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=REPLACE:non-root-user
Group=REPLACE:non-root-group
WorkingDirectory=REPLACE:/opt/parayabuck
EnvironmentFile=REPLACE:/opt/parayabuck/.env
ExecStartPre=REPLACE:/opt/parayabuck/.venv/bin/parayabuck doctor --strict --paper --offline
ExecStart=REPLACE:/opt/parayabuck/.venv/bin/parayabuck web
Restart=on-failure
RestartSec=5
TimeoutStopSec=35
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=REPLACE:/opt/parayabuck/data REPLACE:/opt/parayabuck/config REPLACE:/opt/parayabuck/reports

[Install]
WantedBy=multi-user.target
```

`SIGTERM` (systemd's default kill signal) triggers a graceful engine stop
(ledger close, broker socket release); do not set `TimeoutStopSec` below ~30s.

```bash
sudo cp parayabuck.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now parayabuck
sudo systemctl status parayabuck
sudo journalctl -u parayabuck -f
```

## Google Cloud update procedure (current PAPER leveraged-trend release)

1. **Stop safely**

   ```bash
   sudo systemctl stop parayabuck
   curl -s -m 3 http://127.0.0.1:8000/healthz || echo "port closed"
   ```

2. **Inspect the broker PAPER account** (authoritative external view):
   positions and open orders on https://app.alpaca.markets/paper. Record them
   for post-restart comparison.

3. **Back up runtime state**

   ```bash
   cd /opt/parayabuck
   sudo -u parayabuck .venv/bin/parayabuck backup
   sudo -u parayabuck tar czf ~/backups/parayabuck-config-$(date +%Y%m%dT%H%M%SZ).tgz \
     config/ .env data/runtime/active_equity_universe.json data/runtime/active_crypto_universe.json
   ```

4. **Pull the release**

   ```bash
   sudo -u parayabuck git fetch --tags origin
   sudo -u parayabuck git checkout <RELEASE_TAG>     # e.g. parayabuck-paper-v1.0.0
   git -C . diff HEAD -- config/settings.yaml .env  # confirm no tracked drift
   ```

5. **Install dependencies**

   ```bash
   sudo -u parayabuck .venv/bin/pip install -r requirements.lock
   sudo -u parayabuck .venv/bin/pip install -e . --no-deps
   ```

6. **Offline tests** (no broker/network)

   ```bash
   cd /opt/parayabuck
   sudo -u parayabuck .venv/bin/python -m pytest -q \
     tests/test_cadence_v2_identities.py tests/test_strategy_spec_freeze.py \
     tests/test_dual_market_startup.py tests/test_release_v2_integration.py
   ```

7. **Start**

   ```bash
   sudo systemctl start parayabuck
   ```

8. **Verify**

   ```bash
   curl -s -m 5 http://127.0.0.1:8000/healthz                      # 200 {"status":"ok"}
   curl -s -m 5 http://127.0.0.1:8000/readyz | python3 -m json.tool # eventually ready=true
   sudo systemctl status parayabuck
   sudo journalctl -u parayabuck -n 200 --no-pager
   ```

   Dashboard (`http://127.0.0.1:8000/` via SSH tunnel) must show: PAPER
   badge, **Leveraged Index Trend v1** (`equity_leveraged_trend_v1`: holds QLD,
   the 2x Nasdaq-100 ETF, while QQQ's completed close is above its 200-session
   SMA, sized `min(1, 0.35 / 20-session annualized realized QLD volatility)`,
   inside the 0.93 equity maximum, cash otherwise), and
   unchanged `crypto_low_variance_hysteresis_v1` (96h, lookback 22, top2, retention rank
   4, allocation 0.05) cards, engine running, catch-up status per card. See the
   [profit maximization v1 evidence](research/profit_max_v1.md).
9. **Verify the persisted catch-up decision** (fresh account only):

   ```bash
   sqlite3 data/runtime/parayabuck.sqlite3 \
     "SELECT market, decision_timestamp, strategy_name,
             json_extract(target_json, '$.metadata.decision_origin') AS origin
      FROM portfolio_decisions ORDER BY decision_timestamp DESC LIMIT 8;"
   ```

   Expect `origin='startup_catch_up'` for the newest rows; current equity rows
   use `strategy_name='equity_leveraged_trend_v1'`, and
   later scheduled decisions carry `'scheduled'`. An account that already holds
   managed positions shows catch-up status `already_invested` (positions stay
   managed; the first scheduled equity boundary rebalances them normally) —
   never a duplicate order set. The equity target is the ordinary daily
   QQQ-trend decision: QLD at the volatility target while QQQ is above its
   200-session SMA, cash otherwise.

## Rollback

1. Stop: `sudo systemctl stop parayabuck`
2. Snapshot the CURRENT state (backup CLI + config tarball) in case the
   rollback itself must be reverted.
3. `git checkout <PREVIOUS_RELEASE_TAG>`; re-apply the pre-deploy config/env
   snapshot (config is operator state — `git checkout` must NOT clobber it).
   Reverting the 2026-09-25 equity identity switch additionally restores
   `config/settings.yaml` and the removed modules from commit `ec4e2df`
   (main before the switch), e.g.
   `git checkout ec4e2df -- config/settings.yaml src/parayabuck`;
   then run
   `pip install -r requirements.lock && pip install -e . --no-deps`.
4. Restore the runtime DB only when the new release corrupted/partially
   migrated it (loud schema errors on startup). A healthy new ledger is kept:
   it contains fills the broker already executed.
5. Broker reconciliation is authoritative: the engine reconciles the ledger
   TO the current Alpaca paper account on every start and halts loudly on
   mismatch (`reconciliation_failure`). Never cancel/flatten broker positions
   as part of a rollback; startup reconciliation re-adopts open positions.
6. Restart and verify (`systemctl status`, `journalctl`, `/healthz`,
   `/readyz`, dashboard).

## Docker (alternative deployment)

`docker-compose.yml` runs `parayabuck:local` (non-root, `CMD ["parayabuck","web"]`,
`HEALTHCHECK /healthz`) with `./config`, `./data`, `./reports` bind-mounted,
`env_file: .env`, `restart: unless-stopped`. Container update: `docker compose
pull`-style rebuild, `docker compose up -d`. The systemd unit is the
recommended Google Cloud deployment; Docker is for evaluation/containers.
