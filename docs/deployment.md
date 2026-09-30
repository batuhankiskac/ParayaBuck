# Deployment — GCP VM, systemd, git bundle

The PAPER bot runs in exactly one place: a Google Cloud VM, as systemd units.
It is updated by `git bundle` because the GitHub repo is private and the VM has
no GitHub credentials. **Never start the engine anywhere else** — the laptop and
the VM share one Alpaca PAPER key, and two runners on one account corrupt the
ledger (double-run incident, 2026-09-25). Record every deploy in
`docs/OPERATIONS_LOG.md`.

Trading additionally requires this machine to be designated as the trading
host (`PARAYABUCK_TRADING_HOST=1`, set by the systemd unit). Without it the
console, settings and backtests work and the engine refuses to start — see
"One runner per account" in `docs/operations.md`.

## Process facts

| Item | Value |
|---|---|
| Entry point | `parayabuck web` — serves the loopback console **and** runs the engine in-process (auto-starts in `paper` mode when `app.auto_start_engine` is on, credentials are present and this machine is the designated trading host) |
| Trading host | `Environment=PARAYABUCK_TRADING_HOST=1` in the unit. Without it the process serves the console and backtests but refuses to start the engine on the shared account |
| Bind | `127.0.0.1:8000` (`app.host/port`); reach it through an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 <vm>` |
| Working directory | Must be the repo root: `CONFIG_PATH=./config/settings.yaml` and every `data/runtime/...` path are cwd-relative |
| Preflight | `ExecStartPre=parayabuck doctor --strict --paper --offline` — the unit refuses to start on a bad config, non-paper mode, live enabled, missing credentials, unwritable config dir, failed SQLite `integrity_check` or a tracked `.env` |
| Stop | `SIGTERM` stops the engine gracefully (ledger close, socket release); keep `TimeoutStopSec` ≥ 35 |
| Liveness / readiness | `GET /healthz` (always 200 while the process serves) / `GET /readyz` (200 ready, 503 not; see `docs/operations.md`) |
| Backups | `parayabuck-backup.timer` runs `parayabuck backup` every 6 h (10 min after boot) |
| Dead-man probe | `parayabuck-watchdog.timer` runs `parayabuck ops watchdog` every 15 min: `/readyz` must be ready and the newest backup under 12 h old, otherwise it alerts (optional unit) |

## Units (`deploy/systemd/`)

| File | Purpose |
|---|---|
| `parayabuck.service` | Engine + console. Non-root `parayabuck` user, `EnvironmentFile=/opt/parayabuck/.env`, `Restart=on-failure`, `RestartSec=10`, `TimeoutStopSec=35`, `UMask=0077`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=true`, `ReadWritePaths` = `config/`, `data/`, `reports/` |
| `parayabuck-backup.service` | Oneshot `parayabuck backup` with the same env file and hardening |
| `parayabuck-backup.timer` | `OnBootSec=10min`, `OnUnitActiveSec=6h`, `Persistent=true` |
| `parayabuck-watchdog.service` | Oneshot `parayabuck ops watchdog`, `ExecCondition` = `systemctl is-active parayabuck.service`, same env file and hardening. Reports a failed/never-started engine and a stale backup set to the webhook. `ReadWritePaths` = `data/` |
| `parayabuck-watchdog.timer` | `OnBootSec=5min`, `OnUnitActiveSec=15min`, `Persistent=true` |

The templates assume an install at `/opt/parayabuck`. The current VM runs from
the deploy user's home checkout (`~/ParayaBuck`, `.venv` inside it) with the
unit paths rewritten to that directory and `ProtectHome=read-only`. Always check
the installed unit rather than the template:

```bash
systemctl cat parayabuck | grep -E 'WorkingDirectory|EnvironmentFile|ExecStart'
```

The installed unit must carry this exact line (unit paths are rewritten by
hand on the VM, so a bundle update never installs it):

```bash
systemctl cat parayabuck | grep PARAYABUCK_TRADING_HOST
# Environment=PARAYABUCK_TRADING_HOST=1
```

Missing it, `systemctl restart parayabuck` serves the console with no engine:
auto-start logs the refusal and the dashboard stays `stopped`.

### Environment file

The file named by `EnvironmentFile=` (template: `/opt/parayabuck/.env`; on the
current VM, `.env` in the checkout) is mode `0600` and holds every secret. The
config loader also reads `.env` from the working directory.

| Variable | Required | Notes |
|---|---|---|
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | yes | PAPER keys |
| `PARAYABUCK_ENABLE_LIVE` | yes | `NO` |
| `PARAYABUCK_TRADING_HOST` | no | `1` designates the single runner. Set it in the unit's `[Service]` block, not here: a laptop `.env` must never carry it |
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
   shows the `alerts` check `OK`. `ops status`/`ops doctor` read that check from
   the copy the **engine** persists in `health_snapshot.json`, not from their
   own process — so `DEGRADED` ("no alert webhook configured", failures, drops
   or a last success older than 24 h) means the alert path really is broken,
   and `UNKNOWN` means the engine has not published a copy yet. Without a URL,
   alerts still go to the journal/log, but every surface says `DEGRADED`.

### Changing API keys on the VM

The unit runs with `ProtectSystem=strict` and `ReadWritePaths=` limited to
`config/`, `data/` and `reports/`, so `.env` in the repo root is read-only.
The settings drawer therefore refuses a credential
change up front (HTTP 400, engine left running) instead of stopping the engine
and failing the write with EROFS. Edit `/opt/parayabuck/.env` on the host
(`sudo install -m 0600 …`) and `sudo systemctl restart parayabuck`; never work
around it by relaxing `ProtectSystem`.

Config-only edits (risk limits, host/port, log level) are unaffected: they do
not touch `.env`.

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
sudo systemctl enable --now parayabuck-watchdog.timer   # optional dead-man probe
systemctl list-timers 'parayabuck-*'                     # confirm all three are scheduled
```

The watchdog timer is optional but recommended: without it, nothing outside
the web process notices that the engine never started (`/healthz` stays 200
and systemd restarts nothing) or that the backup timer has been failing for
weeks. It reads `PARAYABUCK_ALERT_WEBHOOK_URL` from the same env file — no
credential is stored in the repo or in the unit. Run it by hand with
`.venv/bin/parayabuck ops watchdog`; the unit's `ExecCondition` skips the
probe while `parayabuck.service` is inactive, so a planned
`systemctl stop parayabuck` (deploy, rollback) does not fire a false alert.

## Pre-deploy checks (shared PAPER account)

The Alpaca PAPER account is shared with the operator and with older builds, so
what the account holds decides whether the next start trades. Read it in the
Alpaca dashboard (or `GET /positions`, `GET /orders?status=open`) before
`systemctl restart`:

1. **No open orders the engine does not own.** Any open broker order whose
   client id is not one of ours is `UNKNOWN_ORDER` drift and halts the start —
   cancel them by hand first. A *filled* foreign order is harmless.
2. **Foreign positions are fine.** Crypto, and equity outside the strategy
   universe that no ledger or portfolio row claims, are classified as foreign
   inventory: they raise one `risk_incident` at startup, are excluded from
   ownership/drift and are never traded, stopped or pinned, and they still
   count toward account gross and slot limits. Liquidate them by hand if you
   do not want them. A position in a symbol the strategy *does* trade (QLD)
   is still fail-closed drift.
3. **No leftover halt rows.** List `safety_incidents` in the runtime DB and
   resolve every row through its own verified recovery before deploying. A row
   whose condition was in-process only (decision worker crash, trade-update
   callback failure, event-queue overflow) is migrated automatically at the
   next start with a `circuit_breaker` audit record, so it will not halt the
   deploy — but every risk-, ownership-, protection- or reconciliation-owned
   row is restored and still halts.
4. **No crypto rebalances mid-phase and no open crypto orders** (only relevant
   when coming from a build that had the crypto sleeve). Rebalance rows of
   markets the equity runtime no longer executes are ignored by `ops status`;
   orders are not, see 1.
5. **Never run `scripts/paper_smoke.py` while the engine runs or between
   15:45 and 16:30 ET.** The script refuses both cases itself.
6. **Prefer no restart from 15:55 ET to the session close + 15 min** (16:30 ET
   on a full session; **13:30 ET on an early-close day**, which ends at 13:00
   ET). That window spans the session-close decision, the second-source (SIP
   cross-check) deferral — which is held in memory — and the SIP embargo, so a
   restart inside it decides the *previous* session instead of the one that
   just closed. A restart after the embargo is now recoverable: the new process
   decides the newest completed session when it is a scheduled boundary with no
   persisted decision (`startup_catch_up_status: recovered_session:<status>`) —
   but that decision is made without the cross-check, so a restart inside the
   window costs the second-source comparison. A restart *before* the close is
   always safe.

## Update by git bundle

On the laptop (`<deployed-sha>` = the commit the VM runs, from
`docs/OPERATIONS_LOG.md` or `git -C <checkout> rev-parse HEAD` on the VM):

```bash
git bundle create /tmp/pb-update.bundle <deployed-sha>..main
gcloud compute scp /tmp/pb-update.bundle <vm>:~/pb-update.bundle --zone <zone>
```

On the VM, in the checkout:

```bash
.venv/bin/parayabuck backup --label predeploy     # verified snapshot first; never auto-pruned
git fetch ~/pb-update.bundle main:refs/remotes/bundle/main
git log --oneline HEAD..bundle/main               # review what ships
git merge --ff-only bundle/main
.venv/bin/pip install -e . --no-deps              # when dependencies or entry points changed
# when requirements.lock changed: the lock is a closed closure, so every
# transitive dependency is pinned there — reinstall the whole file
.venv/bin/pip install -r requirements.lock   # skip when the lock is unchanged
.venv/bin/parayabuck doctor --strict --paper --offline
sudo systemctl restart parayabuck
```

`requirements.lock` is a complete, closed runtime closure (every transitive
dependency pinned, nothing resolved at install time) and it matches the
environment the test suite runs in. When a deploy changes it — the 2026-10-01
closure update did — reinstall it on the VM with the command above; the
`pip install -e . --no-deps` line alone leaves the old pins in place.

From 2026-10-01 the trading host must be designated explicitly, and the
shipped config no longer auto-starts. Before restarting:

```bash
# 1. the unit line (add it once; it is not managed by the bundle)
sudo systemctl edit parayabuck     # [Service] Environment=PARAYABUCK_TRADING_HOST=1
systemctl cat parayabuck | grep PARAYABUCK_TRADING_HOST

# 2. this deployment wants the engine running unattended
grep -n auto_start_engine config/settings.yaml   # app.auto_start_engine: true
```

Without step 1 the engine refuses to start on the shared account; without
step 2 it stays `stopped` until an operator presses Start (readiness reports
`trading_expected=false`, so check it explicitly rather than trusting a 200).
`config/settings.yaml` is tracked: step 2 is a local edit, and a later bundle
that changes the `app:` block makes `git merge --ff-only` abort rather than
overwrite it.

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

1. `.venv/bin/parayabuck backup --label rollback-<sha>`, then
   `sudo systemctl stop parayabuck`. Labelled snapshots are never auto-pruned,
   so the pre-rollback ledger is still there if the rollback has to be undone.
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
`./config`, `./data`, `./reports` mounted, `env_file: .env`). It is a
console/backtest path only: the compose file sets no `PARAYABUCK_TRADING_HOST`,
so the engine refuses to start inside the container on the shared account —
the one-runner guard, not documentation, is what keeps a second runner out.
Never add the variable there, and never run it while the VM engine trades.
