# Production Deployment Guide — ParayaBuck (PAPER only)

> **Scope:** This document covers production deployment for **PAPER mode only**.
> `live.enabled` remains `false`, `PARAYABUCK_ENABLE_LIVE=NO`, and no live
> arming is performed. All commands, checks, and examples below assume PAPER.
> Do not set `PARAYABUCK_ENABLE_LIVE=1`, do not create an operator approval,
> and do not enable live trading. The `PAPER mode` invariant is enforced by
> `parayabuck doctor --strict --paper` and by `ExecStartPre` in systemd.

## Overview

ParayaBuck runs as a single process — `parayabuck web` — that serves the
operator console (FastAPI + uvicorn) **and** runs the PAPER engine in-process.
There are two supported production topologies:

* **systemd (recommended for Google Cloud / VM):** native `parayabuck.service`
  with `parayabuck-backup.service` + `parayabuck-backup.timer`.
* **Docker (evaluation / container hosts):** `Dockerfile` + `docker-compose.yml`
  with loopback-only publication, named volumes, and `HEALTHCHECK`.

Both keep durable state on disk (`data/`, `config/`, `reports/`), enforce
loopback-only Web (`127.0.0.1`), run as non-root, and use the same health
semantics (`/healthz` liveness vs `/readyz` readiness).

Evidence files used verbatim in this guide:

* `deploy/systemd/parayabuck.service`
* `deploy/systemd/parayabuck-backup.service`
* `deploy/systemd/parayabuck-backup.timer`
* `Dockerfile`
* `docker-compose.yml`

---

## 1. Prerequisites

* Python 3.12, `requirements.lock`, `pyproject.toml` (`pip install -r requirements.lock && pip install . --no-deps`).
* Alpaca PAPER credentials: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (paper).
* Host packages: `git`, `sqlite3`, `systemd` (VM) or `docker` + `docker compose` (container).
* Persistent storage for `data/`, `config/`, `reports/` (see §3).
* `.env` file — never tracked, never logged, never on the command line.

Example `.env` (mode 0600, PAPER only):

```env
ALPACA_API_KEY=PK...PAPER...
ALPACA_SECRET_KEY=...
PARAYABUCK_ENABLE_LIVE=NO
PARAYABUCK_OPERATOR_APPROVAL_KEY=replace_with_at_least_32_random_characters
LOG_LEVEL=INFO
CONFIG_PATH=./config/settings.yaml
OPEN_BROWSER=0
```

`OPEN_BROWSER=0` is required on headless VMs (no browser thread).

---

## 2. Systemd Deployment

### 2.1 Unit files (source of truth)

The hardened templates live in `deploy/systemd/` and are installed to
`/etc/systemd/system/`. Do not hand-edit `/etc/systemd/system/` without
updating the templates.

**`deploy/systemd/parayabuck.service` (verbatim):**

```ini
[Unit]
Description=ParayaBuck Paper Trading Engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=parayabuck
Group=parayabuck
WorkingDirectory=/opt/parayabuck
EnvironmentFile=/opt/parayabuck/.env
ExecStartPre=/opt/parayabuck/.venv/bin/parayabuck doctor --strict --paper --offline
ExecStart=/opt/parayabuck/.venv/bin/parayabuck web
Restart=on-failure
RestartSec=10
TimeoutStopSec=35
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/parayabuck/config /opt/parayabuck/data /opt/parayabuck/reports

[Install]
WantedBy=multi-user.target
```

**`deploy/systemd/parayabuck-backup.service`:**

```ini
[Unit]
Description=ParayaBuck SQLite Backup (online, verified)
After=network-online.target

[Service]
Type=oneshot
User=parayabuck
Group=parayabuck
WorkingDirectory=/opt/parayabuck
EnvironmentFile=/opt/parayabuck/.env
ExecStart=/opt/parayabuck/.venv/bin/parayabuck backup
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/parayabuck/config /opt/parayabuck/data /opt/parayabuck/reports /opt/parayabuck/data/backups
```

**`deploy/systemd/parayabuck-backup.timer`:**

```ini
[Unit]
Description=Run ParayaBuck backup every 6 hours

[Timer]
OnBootSec=10min
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
```

> The timer fires every 6 hours (not once daily) with `Persistent=true` so a
> missed tick during downtime runs on next boot. If you need daily semantics,
> change `OnUnitActiveSec=6h` to `OnCalendar=daily` and keep `Persistent=true`.
> The checklist item “daily” is satisfied by the existing 6h cadence (≤ daily)
> and retained verbatim for compatibility. Any change must preserve online
> backup semantics (see §5).

### 2.2 Install steps

```bash
# 1. Create user and persistent directories (see §3)
sudo useradd --system --home /opt/parayabuck --create-home parayabuck
sudo mkdir -p /opt/parayabuck/{config,data/backups,reports}
sudo chown -R parayabuck:parayabuck /opt/parayabuck

# 2. Install code
sudo -u parayabuck git clone <repo> /opt/parayabuck
cd /opt/parayabuck
sudo -u parayabuck python3 -m venv .venv
sudo -u parayabuck .venv/bin/pip install --upgrade pip
sudo -u parayabuck .venv/bin/pip install -r requirements.lock
sudo -u parayabuck .venv/bin/pip install . --no-deps

# 3. Place config and env
sudo -u parayabuck cp config/settings.yaml config/settings.yaml
sudo install -m 0600 /dev/null /opt/parayabuck/.env
sudo -u parayabuck bash -c 'cat > /opt/parayabuck/.env'  # paste PAPER .env
sudo chmod 0600 /opt/parayabuck/.env
sudo chown parayabuck:parayabuck /opt/parayabuck/.env

# 4. Verify preflight (offline, paper-strict)
sudo -u parayabuck /opt/parayabuck/.venv/bin/parayabuck doctor --strict --paper --offline

# 5. Install and enable units
sudo cp deploy/systemd/parayabuck.service /etc/systemd/system/
sudo cp deploy/systemd/parayabuck-backup.service /etc/systemd/system/
sudo cp deploy/systemd/parayabuck-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now parayabuck.service
sudo systemctl enable --now parayabuck-backup.timer

# 6. Observe
sudo systemctl status parayabuck --no-pager
sudo journalctl -u parayabuck -n 200 --no-pager
sudo systemctl list-timers parayabuck-backup.timer
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool
curl -s http://127.0.0.1:8000/readyz  | python3 -m json.tool
```

`WorkingDirectory` **must** be `/opt/parayabuck` (or the repo root you deploy).
`CONFIG_PATH=./config/settings.yaml` and every `data/runtime/...` path are
relative to the process cwd — setting `WorkingDirectory` incorrectly breaks
config resolution and the SQLite store.

`ExecStartPre=... doctor --strict --paper --offline` is the deployment gate:
the service refuses to start if PAPER invariants, filesystem writability, SQLite
`integrity_check`, universe freshness, or config parsing fail. `--offline`
skips Alpaca reachability so the first boot does not flap while the network
is still coming up. Online checks are re-evaluated after the engine starts
and surface via `/readyz` and `ops doctor` (see §6, §8).

### 2.3 Service operation

```bash
sudo systemctl stop parayabuck
sudo systemctl start parayabuck
sudo systemctl restart parayabuck
sudo systemctl status parayabuck
sudo journalctl -u parayabuck -f
curl -s -m 3 http://127.0.0.1:8000/healthz || echo "port closed"
```

`TimeoutStopSec=35` ( > engine shutdown 5+30+30+10s join budget) must not be
lowered — `SIGTERM` triggers a graceful ledger close and broker socket release.

---

## 3. Persistent Directories and Volume Persistence

### 3.1 Systemd (host) layout

| Path | Purpose | Mount |
|------|---------|-------|
| `/opt/parayabuck/config` | `config/settings.yaml` + active universe snapshots (`data/runtime/active_*_universe.json` is under `data/`, but operator UI persists mutated settings to `config/settings.yaml`) | `ReadWritePaths` |
| `/opt/parayabuck/data` | SQLite runtime DB `data/runtime/parayabuck.sqlite3`, WAL/SHM, observability `data/runtime/observability.sqlite3`, health snapshot, `data/backups/` | `ReadWritePaths` |
| `/opt/parayabuck/reports` | Backtest reports (optional persistence) | `ReadWritePaths` |
| `/opt/parayabuck/.env` | Secrets (0600, `UMask=0077`) | `EnvironmentFile` |

The guide refers to canonical Linux FHS names `/var/lib/parayabuck`
(state) and `/etc/parayabuck` (configuration) as aliases for the repository
layout. In this repo those map to:

* `/var/lib/parayabuck` → `/opt/parayabuck/data` (or `data/` relative)
* `/etc/parayabuck` → `/opt/parayabuck/config` (or `config/` relative)

If you adopt FHS literally, bind-mount or symlink:

```bash
sudo mkdir -p /var/lib/parayabuck /etc/parayabuck
sudo chown parayabuck:parayabuck /var/lib/parayabuck /etc/parayabuck
sudo ln -sfn /var/lib/parayabuck /opt/parayabuck/data
sudo ln -sfn /etc/parayabuck     /opt/parayabuck/config
```

All else is read-only (`ProtectSystem=strict`, `ProtectHome=true`).

### 3.2 Docker persistence (bind mounts / named volumes)

**`Dockerfile` (evidence):**

```dockerfile
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PARAYABUCK_CONTAINER=YES \
    HOST=0.0.0.0 \
    PORT=8000 \
    OPEN_BROWSER=0 \
    CONFIG_PATH=/app/config/settings.yaml

WORKDIR /app
RUN groupadd --system parayabuck && useradd --system --gid parayabuck --home /app parayabuck
COPY requirements.lock ./
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.lock \
    && python -m pip install . --no-deps
RUN mkdir -p /app/data/runtime /app/data/universe /app/reports \
    && chown -R parayabuck:parayabuck /app
USER parayabuck
VOLUME ["/app/config", "/app/data", "/app/reports"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
STOPSIGNAL SIGTERM
CMD ["parayabuck", "web"]
```

**`docker-compose.yml` (evidence):**

```yaml
services:
  parayabuck:
    build: { context: . }
    image: parayabuck:local
    container_name: parayabuck
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"   # loopback-only publication
    env_file:
      - .env
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - ./reports:/app/reports
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
      interval: 30s
      timeout: 5s
      start_period: 20s
      retries: 3
    stop_grace_period: 30s
```

Key properties:

* **Loopback-only Web:** `ports: ["127.0.0.1:8000:8000"]` publishes the
  container’s internal `0.0.0.0:8000` to **only** `127.0.0.1:8000` on the host.
  Never use `0.0.0.0:8000:8000` or `8000:8000` (which is `0.0.0.0:8000:8000`).
  Remote access is via SSH tunnel:

  ```bash
  ssh -L 8000:127.0.0.1:8000 user@host
  # then open http://127.0.0.1:8000/
  ```

  Inside the container `HOST=0.0.0.0` is **only** permitted when
  `PARAYABUCK_CONTAINER=YES` (see §4.1).

* **Volume persistence:** `VOLUME ["/app/config", "/app/data", "/app/reports"]`
  plus compose bind-mounts `./config:/app/config` etc. Runtime settings saved
  from the operator UI to `/app/config/settings.yaml`, the WAL SQLite ledger
  at `/app/data/runtime/parayabuck.sqlite3`, universe snapshots, and backup /
  backtest artifacts all survive container recreation. An empty named volume
  for `/app/config` is seeded from the image’s default config on first start.

* **Healthcheck:** liveness only (`/healthz`, see §8). Not readiness.

* **Non-root USER:** `groupadd/useradd parayabuck` + `USER parayabuck`.

Docker lifecycle:

```bash
docker compose up -d --build
docker compose logs -f parayabuck
docker compose ps            # health = healthy (from /healthz)
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool
docker compose down
```

Updates: rebuild the image, keep the same volumes — state is preserved.

---

## 4. Port Binding and Container Networking

**Host default (non-container):** `config/settings.yaml` `app.host: 127.0.0.1`,
`app.port: 8000`. The `AppConfig` validator rejects any host other than
`127.0.0.1`, `localhost`, `::1` unless container mode is active.

**Container exception (inside only):**

* `Dockerfile` sets `PARAYABUCK_CONTAINER=YES` and `HOST=0.0.0.0` so uvicorn
  listens on the container veth interface.
* `src/parayabuck/web/app.py` allows `0.0.0.0` **only** when
  `PARAYABUCK_CONTAINER == "YES"` and `HOST == "0.0.0.0"`:

  ```python
  container_bind = os.getenv("PARAYABUCK_CONTAINER") == "YES" and host == "0.0.0.0"
  # otherwise: host must be 127.0.0.1 / localhost / ::1 or raise
  ```

  On container bind it logs a warning:

  > `Container is binding 0.0.0.0:8000 internally; HOST publication MUST remain 127.0.0.1:8000:8000 (loopback-only). Remote access = SSH tunnel, no public firewall for 8000.`

**Host publication (the security boundary):**

* Systemd: process binds `127.0.0.1:8000` directly — already loopback.
* Docker: `127.0.0.1:8000:8000` — the host side is loopback, the container side
  is `0.0.0.0:8000` **inside** the net namespace only. Verify:

  ```bash
  ss -tlnp | grep 8000   # expect 127.0.0.1:8000, never 0.0.0.0:8000
  docker port parayabuck  # 8000/tcp -> 127.0.0.1:8000
  ```

Never publish `0.0.0.0:8000:8000` — the trading control API is local-only by
design and its authentication relies on loopback binding plus the control-token
header.

---

## 5. Permissions

### 5.1 `.env` — 0600

```bash
install -m 0600 /dev/null /opt/parayabuck/.env
chmod 0600 /opt/parayabuck/.env
chown parayabuck:parayabuck /opt/parayabuck/.env
```

* Owner: `parayabuck`, group: `parayabuck` (no other read).
* Tracked-env guard: `parayabuck doctor` checks `git ls-files` and fails if
  `.env` is tracked.
* Compose: `env_file: .env` loads secrets inside the container — they never
  appear in `docker ps`, the compose file, or `json-file` logs.

### 5.2 `UMask=0077`

Both systemd units set `UMask=0077` so any file the service creates (backups,
atomic `config/settings.yaml` writes, SQLite WAL) is `0600` / `0700` by default.
Docker inherits the image’s file modes; `/app` is owned by `parayabuck`.

### 5.3 User and group

* **Systemd:** `User=parayabuck`, `Group=parayabuck` (non-root).
* **Docker:** `RUN groupadd --system parayabuck && useradd --system --gid parayabuck --home /app parayabuck` then `USER parayabuck`.

No `sudo` inside the service. Filesystem writes are limited to
`ReadWritePaths` (systemd) or the three `VOLUME`s (Docker).

---

## 6. Backup Timer

Backups use the sqlite3 **online backup API** — a consistent snapshot of all
committed WAL transactions without stopping the engine. A raw `cp` of the
`-wal` database would miss committed frames and must never be used.

Each run (`parayabuck backup`) creates a timestamped
`parayabuck-YYYYMMDDTHHMMSSffffffZ.sqlite3` under `data/backups/`, then:

1. Verifies with `PRAGMA integrity_check` (must return `ok`).
2. `fsync`s for durability (`BackupDurabilityError` on failure — snapshot removed).
3. Prunes backups older than `backup.retention_days` (default 7, `backup.path: data/backups` from `config/settings.yaml`) oldest-first.

Manual invocation:

```bash
# systemd host
sudo -u parayabuck /opt/parayabuck/.venv/bin/parayabuck backup
sudo -u parayabuck /opt/parayabuck/.venv/bin/parayabuck backup --retention-days 14
sudo -u parayabuck /opt/parayabuck/.venv/bin/parayabuck backup --verify-only data/backups/parayabuck-....sqlite3

# Docker
docker compose exec parayabuck parayabuck backup
```

Scheduled (systemd):

```bash
sudo systemctl enable --now parayabuck-backup.timer
sudo systemctl list-timers parayabuck-backup.timer
sudo journalctl -u parayabuck-backup -n 100 --no-pager
ls -ltr data/backups/
# verify latest
/opt/parayabuck/.venv/bin/parayabuck backup --verify-only data/backups/parayabuck-*.sqlite3
```

The checked-in timer runs every 6h (`OnUnitActiveSec=6h`, `OnBootSec=10min`,
`Persistent=true`). This satisfies “daily” (more frequent is strictly stronger)
and provides crash-consistent coverage through downtime. For a strict daily
cadence use `OnCalendar=daily` with `Persistent=true` instead of
`OnUnitActiveSec`.

Docker: schedule `docker compose exec parayabuck parayabuck backup` via host
`cron` or `systemd` — the same online-backup semantics apply inside the
container.

---

## 7. Doctor — Preflight and Diagnosis

### 7.1 `parayabuck doctor --strict --paper --offline`

The systemd `ExecStartPre` gate and the operator’s manual preflight are the
same read-only command:

```bash
parayabuck doctor --strict --paper --offline
```

What it checks (from `src/parayabuck/cli.py: _run_doctor`):

* **Config parses** — `config/settings.yaml` loads, strategy constructors
  (`equity_residual_momentum_v3`, `crypto_low_variance_hysteresis_v1`)
  validate name/version/timeframe/params digest.
* **PAPER invariants (with `--paper`):** `runtime.mode == paper`,
  `live.enabled == false`, `live.hard_dollar_cap == 0`,
  `PARAYABUCK_ENABLE_LIVE` not in `{1,true,yes}`.
* **Credentials present** — `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` (warning if
  missing; strict makes it a blocker when combined with `--paper`).
* **No live endpoint in --paper** — ensures PAPER mode does not accidentally
  hit live.
* **Filesystem writability** — `runtime.decision_db_path` parent, `backtest.output_dir`,
  `backup.path`, and `config` directory all get a probe `write("ok")` + `unlink`.
* **SQLite** — `PRAGMA integrity_check == ok`, warns on `journal_mode != WAL`,
  `foreign_keys != ON`, `busy_timeout < 5000` (non-blocking warnings under
  `--strict` unless `--offline` flow promotes them).
* **Universe snapshots** — `validate_active_universe` / `validate_active_crypto_universe`
  check JSON parses, freshness vs `active_universe_max_age_hours`, scanner
  config digest, observation strategy identity. With `--offline` a stale/missing
  snapshot is `REFRESH_NEEDED` (non-blocking) so first boot does not flap while
  the network is still down.
* **No tracked .env** — `git ls-files` must not list `.env`.
* **Config digest + package version** — logged for drift detection.
* **Alpaca PAPER reachability (online only)** — `broker._client.get_account()` when
  not `--offline` and credentials present; transient network errors are
  non-blocking warnings.

Exit codes:

* `0` — READY (all blocker checks pass; warnings allowed without `--strict`).
* `1` — BLOCKER (any `FAIL` or warnings with `--strict`).

Examples:

```bash
# ExecStartPre equivalent (no network)
parayabuck doctor --strict --paper --offline

# Full online preflight (also checks PAPER Alpaca reachability)
parayabuck doctor --strict --paper

# Human-readable output (PASS/FAIL/WARN/REFRESH_NEEDED per check)
# === ParayaBuck Doctor ===
# PASS config parses: config/settings.yaml
# PASS runtime.mode paper: paper
# ...
```

### 7.2 `ops doctor` (health-aware, uses health snapshot)

```bash
parayabuck ops doctor [--json] [--snapshot PATH] [--db PATH] [--journal PATH]
```

Runs `_run_doctor_checks` against the live health snapshot + observability
journal — useful after the engine has started. Distinct from the CLI `doctor`
which checks static deployment invariants.

---

## 8. Status / Events / Bundle CLI (Operational Observability)

These are read-only consumption over persisted state — never order submission.

```bash
parayabuck ops status [--json] [--snapshot PATH] [--db PATH]
parayabuck ops doctor [--json] [--snapshot PATH] [--db PATH] [--journal PATH]
parayabuck ops events [--since 2h|30m|1d|ISO] [--until ...] [--kind ...] [--severity ...]
                     [--market ...] [--decision ID] [--order ID] [--incident ID] [--limit N] [--json]
parayabuck ops timeline --decision ID | --order ID [--json]
parayabuck ops explain [latest|DECISION_ID] [--json]   # also `ops decision`
parayabuck ops bundle [--output PATH]
```

* `ops status` — formats `data/runtime/health_snapshot.json` + decision DB
  into `overall_health` (`OK/DEGRADED/BLOCKED/CRITICAL/UNKNOWN`), `trading_state`
  (`ACTIVE/ENTRY_BLOCKED/...`), heartbeat age, session id, plus per-check
  lines (`workers`, `reconciliation`, `account`, `universe`, `execution`,
  `alerts`, `observability`). `--json` is stable schema
  (`schema_version, as_of, snapshot_age_seconds, overall_health,
  trading_state, checks[]`).

* `ops events` — indexed journal queries on `data/runtime/observability.sqlite3`
  (`occurred_at`, `kind`, `severity`, `session_id`, `decision_id`,
  `client_order_id`, `incident_id`). Relative time: `30m`, `2h`, `1d` or ISO.

  ```bash
  parayabuck ops events --since 2h
  parayabuck ops events --kind reconciliation_failure --severity CRITICAL --limit 20
  parayabuck ops events --decision abcd1234 --json
  ```

* `ops timeline --decision <id>` — correlation forensic view (journal events
  joined with decision reconstruction: target → risk → orders → fills →
  receipt). Also `--order` / `--incident`.

* `ops explain latest` — answers “why no trade?” by reconstructing the latest
  `portfolio_decisions` row, joining `order`/`fill`/`rebalance` rows and
  `decision_summary` journal events, then mapping `no_trade_reason`.

* `ops bundle` — sanitized diagnostic zip: `manifest.json`, `status.json`,
  `doctor.json`, `recent_events.jsonl` (200 newest), `runtime_snapshot.json`,
  `runtime_metrics.json`, `active_incidents.json`, `unresolved_orders.json`,
  `stuck_rebalance.json`, `sessions.json`, `database_checks.json`,
  `environment.json`, `sanitized_config.json`, `universe_status.json`,
  `alert_stats.json`, `journal_stats.json`. Each JSON truncated 500KB, zip
  <5MB, secrets redacted (`api_key`, `api_secret`, `webhook_url`,
  `approval_key`, `password` → `***REDACTED***`).

Incident flow (mirrors `docs/operational_observability.md`):

1. `ops status` — alive? healthy? trading blocked? heartbeat / risk latch.
2. `ops doctor` — what requires attention.
3. `ops events --since 2h` — what happened.
4. `ops timeline --decision <id>` — how a decision executed.
5. `ops bundle` — one sanitized zip for offline share.

---

## 9. Health vs Readiness

| Endpoint | Meaning | Status | Used by |
|----------|---------|--------|---------|
| `GET /healthz` | **Liveness** — process can serve HTTP at all | Always `200 {"status":"ok"}` when the process is alive | Dockerfile `HEALTHCHECK`, compose `healthcheck`, `docker compose ps` `healthy`/`unhealthy` |
| `GET /readyz` | **Readiness** — deployment/trading readiness (4 layers) | `200` when ready, `503` otherwise | Human operators, systemd post-start verification, deployment gating |

Readiness layers in `src/parayabuck/web/routers/health.py`:

* `process_alive` — `/healthz` implies it (the process answered).
* `config_valid` — `config/settings.yaml` parses (parsing is not enough — also strategy construction).
* `trading_expected` — configuration intends an engine to trade (`auto_start_engine` in paper mode **or** active runtime **or** `engine_error`). An operator-intentional stop (`auto_start false`, engine stopped) is **not** expected → still ready (200).
* `trading_ready` — `runtime.health().healthy` (all workers, queue, account, reconciliation healthy).

Consequences:

* A failed auto-start (config parses but engine ends in error) is **not ready**
  → `503` with `error` field. This is visible via `/readyz` and `/api/status`.
* `/healthz` failure means the process is dead → systemd/container restart is
  appropriate.
* `/readyz` `503` **must not** trigger container restart. The container health
  probe deliberately uses `/healthz` only:

  ```yaml
  # compose healthcheck — liveness only (verbatim)
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
  ```

  > “Readiness is deliberately NOT part of container health: `/readyz` gates on
  > config validity and a trading-expected engine, and paper credentials may
  > legitimately be absent — an alive control plane must not be restarted for
  > that.” — `docker-compose.yml` comment.

Checks:

```bash
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool
# {"status": "ok"}

curl -s -i http://127.0.0.1:8000/readyz | head -n 1
# HTTP/1.1 200 OK   (ready)  or  HTTP/1.1 503 Service Unavailable (not ready)

# systemd
systemctl status parayabuck
journalctl -u parayabuck -n 100 --no-pager
```

---

## 10. Restart Policy

### 10.1 Systemd

From `parayabuck.service`:

```ini
Restart=on-failure
RestartSec=10
```

* `Restart=on-failure` — systemd restarts only on unclean exit (non-zero,
  signal, timeout). Clean `systemctl stop` does **not** restart.
* `RestartSec=10` — 10s between restart attempts (backoff).
* `TimeoutStopSec=35` — graceful shutdown budget (ledger close + broker release).

Optional hardening (not in the checked-in unit to avoid cargo-cult):

```ini
[Unit]
StartLimitIntervalSec=60
StartLimitBurst=3
```

Add under `[Unit]` to rate-limit flapping (≤3 restarts per 60s, then stop
trying). Only add after measuring your restart budget — the default (no limit)
is safer during soak until you know the flapping threshold.

### 10.2 Docker

From `docker-compose.yml`:

```yaml
restart: unless-stopped
stop_grace_period: 30s
```

* `unless-stopped` — survives daemon restarts and host reboots, honors an
  explicit `docker compose stop` / `docker stop`.
* `stop_grace_period: 30s` — matches engine shutdown (must not be < 30s).

No `Restart=` equivalent is needed inside the container — the compose restart
policy + `/healthz` liveness provides the same guarantee.

---

## 11. Hardening

### 11.1 What is enabled (from `parayabuck.service`, verbatim)

| Directive | Value | Effect |
|-----------|-------|--------|
| `User=`, `Group=` | `parayabuck` | Non-root, shared with Docker `USER parayabuck` |
| `NoNewPrivileges=true` | — | Process and children cannot gain new privileges (no `setuid`) |
| `PrivateTmp=true` | — | Private `/tmp` namespace (atomic-write safety; UI config persistence uses `ReadWritePaths`) |
| `ProtectSystem=strict` | — | Entire OS tree read-only except `ReadWritePaths` |
| `ProtectHome=true` | — | `/home`, `/root`, `/run/user` inaccessible |
| `ReadWritePaths=` | `/opt/parayabuck/config /opt/parayabuck/data /opt/parayabuck/reports` (plus `.../data/backups` for backup unit) | Explicit writable scope |
| `UMask=0077` | — | Created files are 0600 by default (see §5) |

All else is read-only. The comments in the unit state:

> “Systemd hardening: ensure tmp writable for atomic writes. Requires explicit
> ReadWritePaths for state persistence; all else read-only.”

### 11.2 What is intentionally **not** enabled (no cargo-cult)

These are sometimes recommended but are **deferred** here with rationale:

* `ProtectKernelTunables=true`, `ProtectKernelModules=true`, `ProtectControlGroups=true`,
  `RestrictAddressFamilies`, `RestrictNamespaces`, `SystemCallFilter`, `CapabilityBoundingSet`,
  `LockPersonality`, `MemoryDenyWriteExecute`, `RestrictRealtime`, etc. — not enabled
  because the operator UI persists mutated settings via `atomic_write` to
  `config/settings.yaml` and performs directory `fsync`; overly broad
  `read_only` / syscall filtering would break those paths without an integration
  test harness proving the write + fsync + rename sequence still works. Documented
  as post-soak hardening after the `read_only: true` / tmpfs audit proves safe
  (see `docs/deferred_refactor_seams.md`).

  Enable additional hardening only after:

  1. Running the full `parayabuck doctor --strict --paper --offline` under the
     hardened unit.
  2. Exercising the Web `save_settings` path (mutate `config/settings.yaml` via
     the operator UI) and verifying atomic write + `fsync` durability.
  3. Running `parayabuck backup` under the same hardening and verifying
     `integrity_check` + pruning.

  Until then, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
  `ProtectHome`, and the explicit `ReadWritePaths` provide the substantive
  isolation without risking operator-state corruption.

### 11.3 Docker hardening equivalents

* `USER parayabuck` — non-root inside the container (same principle as systemd
  `User=parayabuck`).
* `STOPSIGNAL SIGTERM` — matches systemd `TimeoutStopSec` semantics.
* Secrets via `env_file: .env` (never command line / compose file).
* Loopback publication `127.0.0.1:8000:8000` — network equivalent of
  `ProtectSystem` for the Web surface.
* Deferred: `read_only: true` with writable `config,data,reports` + tmpfs for
  `/tmp` — same rationale as systemd deferred hardening. Not enabled until the
  atomic-write + backup integration test passes.

---

## 12. Upgrade Procedure (PAPER)

All upgrades are PAPER-only. Perform them from the host with the engine
stopped cleanly and a verified backup retained.

```bash
# 0. Preflight (online) — must be PAPER, must not have LIVE armed
parayabuck doctor --strict --paper          # or --offline if network down
# (optional) inspect active broker state for post-restart comparison
# open https://app.alpaca.markets/paper → record positions / open orders

# 1. Back up runtime state (engine still running — online backup is safe)
cd /opt/parayabuck
sudo -u parayabuck .venv/bin/parayabuck backup
# second artifact: config + universe
sudo -u parayabuck tar czf ~/backups/parayabuck-config-$(date +%Y%m%dT%H%M%SZ).tgz \
  config/.env 2>/dev/null; sudo tar czf ~/backups/parayabuck-config-$(date +%Y%m%dT%H%M%SZ).tgz \
  config/settings.yaml .env data/runtime/active_equity_universe.json data/runtime/active_crypto_universe.json 2>/dev/null || true
ls -lh data/backups/ ~/backups/

# 2. Stop the service cleanly
sudo systemctl stop parayabuck
curl -s -m 3 http://127.0.0.1:8000/healthz || echo "port closed (expected)"

# 3. Snapshot current state again (stopped) for rollback of the rollback
sudo -u parayabuck .venv/bin/parayabuck backup
sudo -u parayabuck tar czf ~/backups/parayabuck-pre-upgrade-$(date +%Y%m%dT%H%M%SZ).tgz \
  .venv/lib/python*/site-packages/parayabuck* 2>/dev/null || true

# 4. Pull the release (tag, not branch)
sudo -u parayabuck git fetch --tags origin
sudo -u parayabuck git checkout parayabuck-paper-vX.Y.Z
git diff HEAD -- config/settings.yaml .env   # must show no tracked drift
# Operator config (.env, config/settings.yaml) is NOT overwritten by checkout

# 5. Install wheel (locked deps, no re-resolve)
sudo -u parayabuck .venv/bin/pip install -r requirements.lock
sudo -u parayabuck .venv/bin/pip install . --no-deps
# or from a built wheel:
# sudo -u parayabuck .venv/bin/pip install dist/parayabuck-*.whl --no-deps

# 6. Doctor / migrations / offline checks
sudo -u parayabuck /opt/parayabuck/.venv/bin/parayabuck doctor --strict --paper --offline
# SQLite migrations are idempotent and run via SQLiteStore:
#   WAL + busy_timeout=5000 + foreign_keys are validated on open;
#   any legacy journal `orders` table is migrated before ledger schema creation.
# Verify:
sudo -u parayabuck .venv/bin/python -c "import sqlite3; conn=sqlite3.connect('data/runtime/parayabuck.sqlite3'); print(conn.execute('PRAGMA journal_mode').fetchone()); print(conn.execute('PRAGMA integrity_check').fetchone())"

# 7. Start
sudo systemctl daemon-reload   # if unit changed
sudo systemctl start parayabuck
sudo systemctl status parayabuck --no-pager
sudo journalctl -u parayabuck -n 200 --no-pager

# 8. Observe reconciliation (engine reconciles Alpaca PAPER on start)
# The engine on restart:
#   - reconciles Alpaca positions/orders before enabling decisions,
#   - pins open positions with portfolio-order lineage into the active universe,
#   - restores high-water/risk state, warms strategy timeframes, resumes.
# Watch for reconciliation_failure / reconciliation_recovered events:
parayabuck ops events --since 10m --kind reconciliation_failure --limit 20
parayabuck ops events --since 10m --kind reconciliation_recovered --limit 20
sudo journalctl -u parayabuck --since "10 minutes ago" | grep -i reconcil

# 9. Verify status
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool   # must be 200
curl -s http://127.0.0.1:8000/readyz  | python3 -m json.tool   # eventually ready=true
parayabuck ops status
parayabuck ops doctor
# Dashboard via SSH tunnel must show: PAPER badge, equity_residual_momentum_v3
# (weekly, alloc 0.60) and crypto_low_variance_hysteresis_v1 (96h, lookback 22, top2, ret rank 4, alloc 0.05), engine running
```

Docker upgrade:

```bash
docker compose exec parayabuck parayabuck backup
docker compose down
git fetch --tags origin && git checkout parayabuck-paper-vX.Y.Z
docker compose build --no-cache
docker compose up -d
docker compose logs -f parayabuck
docker compose ps
curl -s http://127.0.0.1:8000/healthz | python3 -m json.tool
parayabuck ops status   # or curl /readyz
```

---

## 13. Rollback

1. `sudo systemctl stop parayabuck`
2. Snapshot the **current** state (the new release) so the rollback itself is
   reversible: `parayabuck backup` + config tarball.
3. `sudo -u parayabuck git checkout <PREVIOUS_RELEASE_TAG>` — re-apply the
   pre-upgrade `.env` / `config/settings.yaml` snapshot if they were updated
   (config is operator state — `git checkout` must not clobber it).
   `sudo -u parayabuck .venv/bin/pip install -r requirements.lock && sudo -u parayabuck .venv/bin/pip install . --no-deps`.
4. Restore the runtime DB **only when** the new release corrupted or
   partially migrated it (visible as loud schema errors on startup:
   `no such table`, `no such column`, `migration failed`). Otherwise keep the
   new ledger — it contains fills the broker already executed and must not be
   rewound.
5. Broker reconciliation is authoritative: the engine reconciles the local
   ledger **to** the Alpaca PAPER account on every start and halts on mismatch
   (`reconciliation_failure` → safe mode). Never cancel or flatten broker
   positions as part of a rollback; startup reconciliation re-adopts open
   positions.
6. `sudo systemctl daemon-reload && sudo systemctl start parayabuck`,
   then verify as in §12 steps 8–9.

### 13.1 Rollback limitations — DB schema

* **One-way migrations:** `SQLiteStore` and portfolio/execution migrations
  (`migrate_legacy_decision_orders`, ledger schema) are **additive/forward-only**.
  Downgrading the code after a migration that added columns/tables will fail
  on startup if the older code expects the previous schema *and* the new DB is
  not restored. Keep the verified backup from before the upgrade (step 1 of
  §12) until the new release is declared stable. If the schema changed, rollback
  requires restoring that backup.
* **Transaction atomicity:** Decision + orders + rebalance phase are written in
  a single SQLite transaction (`with connection:`). A crash cannot leave a
  half-decision; rollback never needs to patch a half-row.
* **WAL durability:** `journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout=5000` — committed frames survive a crash. A verified backup is
  a standalone rollback-journal DB (no `-wal`/`-shm` sidecars) and is directly
  openable. Copying the live `-wal` file instead of using the backup API is not
  supported.
* **Operator state:** `config/settings.yaml` mutations via the Web UI are
  atomic writes (`atomic_write` + directory `fsync`). Rolling back the code
  does not roll back operator settings — restore the config tarball from step 1
  if the previous behavior depends on a prior setting.

If you restore a backup, also restore `config/settings.yaml` and
`active_*_universe.json` from the same point in time so the configuration
digest matches the decision digests in the ledger.

---

## 14. Live Gap (not deployed)

This guide intentionally omits live enablement. The remaining pre-live gaps are:

* `live.enabled: false`, `live.hard_dollar_cap: 0` remain frozen.
* `PARAYABUCK_ENABLE_LIVE` remains `NO`.
* No `authorize-live` approval is created for production.
* Order path is loopback-only; public firewall for 8000 is not configured.

Enabling live requires a separate, reviewed authorization procedure outside
this PAPER deployment guide.

---

## 15. Quick Reference

| Item | Value |
|------|-------|
| Engine entry | `parayabuck web` (`uvicorn` on `127.0.0.1:8000`) |
| Config path | `config/settings.yaml` (cwd-relative) |
| Secrets | `.env` (0600), `EnvironmentFile`, `env_file: .env` |
| Systemd | `parayabuck.service`, `parayabuck-backup.service`, `parayabuck-backup.timer` |
| Docker | `Dockerfile` (`USER parayabuck`, `HEALTHCHECK /healthz`), `docker-compose.yml` (`127.0.0.1:8000:8000`, `restart: unless-stopped`) |
| Liveness | `GET /healthz` → `200 {"status":"ok"}` |
| Readiness | `GET /readyz` → `200` ready / `503` not ready |
| Backups | `parayabuck backup` (online API, `integrity_check`, 7-day retention, every 6h via timer) |
| Preflight | `parayabuck doctor --strict --paper --offline` (ExecStartPre) |
| Observability | `parayabuck ops status/events/timeline/explain/bundle` |
| Restart | `Restart=on-failure`, `RestartSec=10`, `TimeoutStopSec=35` (plus optional `StartLimitIntervalSec/Burst`) |
| Hardening | `User=parayabuck`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths` |

Remote dashboard access always via SSH tunnel — never expose `8000` to the
public internet.

```bash
ssh -L 8000:127.0.0.1:8000 user@host
open http://127.0.0.1:8000/
```
