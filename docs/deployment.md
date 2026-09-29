# Deployment — GCP VM, systemd, git bundle

The PAPER bot runs in exactly one place: a Google Cloud VM, as systemd units.
It is updated by `git bundle` because the GitHub repo is private and the VM has
no GitHub credentials. **Never start the engine anywhere else** — the laptop and
the VM share one Alpaca PAPER key, and two runners on one account corrupt the
ledger (double-run incident, 2026-09-25). Record every deploy in
`docs/OPERATIONS_LOG.md`.

## Process facts

| Item | Value |
|---|---|
| Entry point | `parayabuck web` — serves the loopback console **and** runs the engine in-process (auto-starts in `paper` mode when credentials are present) |
| Bind | `127.0.0.1:8000` (`app.host/port`); reach it through an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 <vm>` |
| Working directory | Must be the repo root: `CONFIG_PATH=./config/settings.yaml` and every `data/runtime/...` path are cwd-relative |
| Preflight | `ExecStartPre=parayabuck doctor --strict --paper --offline` — the unit refuses to start on a bad config, non-paper mode, live enabled, missing credentials, unwritable config dir, failed SQLite `integrity_check` or a tracked `.env` |
| Stop | `SIGTERM` stops the engine gracefully (ledger close, socket release); keep `TimeoutStopSec` ≥ 35 |
| Liveness / readiness | `GET /healthz` (always 200 while the process serves) / `GET /readyz` (200 ready, 503 not; see `docs/operations.md`) |
| Backups | `parayabuck-backup.timer` runs `parayabuck backup` every 6 h (10 min after boot) |

## Units (`deploy/systemd/`)

| File | Purpose |
|---|---|
| `parayabuck.service` | Engine + console. Non-root `parayabuck` user, `EnvironmentFile=/opt/parayabuck/.env`, `Restart=on-failure`, `RestartSec=10`, `TimeoutStopSec=35`, `UMask=0077`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=true`, `ReadWritePaths` = `config/`, `data/`, `reports/` |
| `parayabuck-backup.service` | Oneshot `parayabuck backup` with the same env file and hardening |
| `parayabuck-backup.timer` | `OnBootSec=10min`, `OnUnitActiveSec=6h`, `Persistent=true` |

The templates assume an install at `/opt/parayabuck`. The current VM runs from
the deploy user's home checkout (`~/ParayaBuck`, `.venv` inside it) with the
unit paths rewritten to that directory and `ProtectHome=read-only`. Always check
the installed unit rather than the template:

```bash
systemctl cat parayabuck | grep -E 'WorkingDirectory|EnvironmentFile|ExecStart'
```

### Environment file

The file named by `EnvironmentFile=` (template: `/opt/parayabuck/.env`; on the
current VM, `.env` in the checkout) is mode `0600` and holds every secret. The
config loader also reads `.env` from the working directory.

| Variable | Required | Notes |
|---|---|---|
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | yes | PAPER keys |
| `PARAYABUCK_ENABLE_LIVE` | yes | `NO` |
| `CONFIG_PATH` | no | default `./config/settings.yaml` |
| `OPEN_BROWSER` | no | `0` on the VM |
| `LOG_LEVEL` | no | default `INFO` |
| `PARAYABUCK_ALERT_WEBHOOK_URL` | no | alert webhook (below) |
| `PARAYABUCK_OPERATOR_APPROVAL_KEY` | live only | unused in PAPER |

### Enabling the alert webhook

1. Create an incoming webhook that accepts a JSON POST (Slack, Discord,
   Mattermost or any generic receiver; the payload carries `message`, `text`
   and `content`).
2. On the VM, add `PARAYABUCK_ALERT_WEBHOOK_URL=<url>` to the env file above.
   Keep `alerting.webhook_url: ""` in `config/settings.yaml` — the URL is a
   secret, and a non-empty YAML value overrides the environment variable.
3. `sudo systemctl restart parayabuck` (the alert sink is configured at engine
   start).
4. Confirm: a `runtime_started` alert arrives, and `parayabuck ops status`
   shows the `alerts` check without failures. Without a URL, alerts still go
   to the journal/log.

## First install (fresh VM)

```bash
sudo useradd --system --home /opt/parayabuck --create-home parayabuck
# full bundle from the laptop: `git bundle create pb.bundle main`, scp it, then
sudo -u parayabuck git clone -b main ~/pb.bundle /opt/parayabuck
cd /opt/parayabuck
sudo -u parayabuck python3 -m venv .venv
sudo -u parayabuck .venv/bin/pip install -r requirements.lock
sudo -u parayabuck .venv/bin/pip install -e . --no-deps
sudo install -m 0600 -o parayabuck -g parayabuck /dev/null .env   # then fill it
sudo -u parayabuck .venv/bin/parayabuck doctor --strict --paper --offline
sudo cp deploy/systemd/parayabuck*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now parayabuck.service parayabuck-backup.timer
```

## Update by git bundle

On the laptop (`<deployed-sha>` = the commit the VM runs, from
`docs/OPERATIONS_LOG.md` or `git -C <checkout> rev-parse HEAD` on the VM):

```bash
git bundle create /tmp/pb-update.bundle <deployed-sha>..main
gcloud compute scp /tmp/pb-update.bundle <vm>:~/pb-update.bundle --zone <zone>
```

On the VM, in the checkout:

```bash
.venv/bin/parayabuck backup                       # verified snapshot first
git fetch ~/pb-update.bundle main:refs/remotes/bundle/main
git log --oneline HEAD..bundle/main               # review what ships
git merge --ff-only bundle/main
.venv/bin/pip install -e . --no-deps              # when dependencies or entry points changed
.venv/bin/parayabuck doctor --strict --paper --offline
sudo systemctl restart parayabuck
```

Verify:

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz | python3 -m json.tool   # ready=true after warmup
sudo journalctl -u parayabuck -n 200 --no-pager
.venv/bin/parayabuck ops status
```

A `503` with `engine_state=warming_up` during warmup is normal. The dashboard
must show the PAPER badge, the **Leveraged Index Trend v1** card and a running
engine. A change to the configuration digest (strategy, universe, risk,
allocation) starts a new decision identity; note it in the operations log.

Then add a row to `docs/OPERATIONS_LOG.md` with the deployed sha.

## Rollback

1. `.venv/bin/parayabuck backup`, then `sudo systemctl stop parayabuck`.
2. `git reset --hard <previous-sha>` (or check out a tag), reinstall with
   `pip install -e . --no-deps`, restart. `config/settings.yaml` is tracked:
   the rolled-back commit brings its own config, which must match its code
   (e.g. a pre-2026-09-30 build needs its crypto keys; the current build
   rejects them).
3. Restore the runtime DB only when the new release corrupted or migrated it
   (loud schema errors at startup). Migrations are forward-only: an older build
   may not open a DB a newer build migrated, so keep the pre-deploy backup
   until the release is stable. Otherwise keep the current ledger — it holds
   fills the broker already executed.
4. Broker state is authoritative. The engine reconciles the ledger to the
   Alpaca account on every start and halts on mismatch. Never flatten or cancel
   broker positions as part of a rollback.

## Docker (not used in production)

`docker-compose.yml` builds the `Dockerfile` (python:3.12-slim, non-root,
`HEALTHCHECK /healthz`, loopback `127.0.0.1:8000`, `restart: unless-stopped`,
`./config`, `./data`, `./reports` mounted, `env_file: .env`). It starts the
engine, so the one-runner rule applies: never run it while the VM runs.
