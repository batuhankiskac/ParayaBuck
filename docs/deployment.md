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
| Entry point | `parayabuck web` — serves the loopback console **and** runs the engine in-process (auto-starts in `paper` mode when `app.auto_start_engine` is on, credentials are present and this machine is the designated trading host; in `live` mode only with a valid standing LIVE authorization, see "LIVE runbook") |
| Trading host | `Environment=PARAYABUCK_TRADING_HOST=1` in the unit. Without it the process serves the console and backtests but refuses to start the engine on the shared account |
| Bind | `127.0.0.1:8000` (`app.host/port`); reach it through an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 <vm>` |
| Working directory | Must be the repo root: `CONFIG_PATH=./config/settings.yaml` and every `data/runtime/...` path are cwd-relative |
| Preflight | `ExecStartPre=parayabuck doctor --strict --configured-mode --offline` — validates the mode `runtime.mode` names. PAPER gets exactly the old `--paper` set: the unit refuses to start on a bad config, live enabled, a dollar cap, `PARAYABUCK_ENABLE_LIVE`, missing PAPER credentials, unwritable config dir, failed SQLite `integrity_check` or a tracked `.env`. LIVE gets the LIVE set (see "LIVE runbook") |
| Stop | `SIGTERM` stops the engine gracefully (worker joins, ledger close, socket release); `TimeoutStopSec=120` covers the worst-case join sequence |
| Liveness / readiness | `GET /healthz` (always 200 while the process serves) / `GET /readyz` (200 ready, 503 not; see `docs/operations.md`) |
| Backups | `parayabuck-backup.timer` runs `parayabuck backup` every 6 h (10 min after boot) |
| Alerts | Always local: the log (`journalctl -u parayabuck`) and the event journal (`parayabuck ops events`). Off-host delivery is opt-in e-mail only (`alerting.email`, disabled in the shipped config; see "E-mail alerts") |

## Units (`deploy/systemd/`)

| File | Purpose |
|---|---|
| `parayabuck.service` | Engine + console. Non-root `parayabuck` user, `EnvironmentFile=/opt/parayabuck/.env`, `Restart=on-failure`, `RestartSec=10`, `TimeoutStopSec=120`, `UMask=0077`, `ReadWritePaths` = `config/`, `data/`, `reports/`, plus the sandbox below |
| `parayabuck-backup.service` | Oneshot `parayabuck backup`, same env file and sandbox; `ReadWritePaths` = `data/` only (it reads `config/` and writes `data/backups` and the event journal) |
| `parayabuck-backup.timer` | `OnBootSec=10min`, `OnUnitActiveSec=6h`, `Persistent=true` |
| `parayabuck-deadman.service` | Oneshot `python -m parayabuck.monitoring.deadman`: pings the external heartbeat (see the dead-man section); `LoadCredential=` for the URL, no writes |
| `parayabuck-deadman.timer` | `OnBootSec=3min`, `OnUnitActiveSec=5min`, `Persistent=true` |

Both services share one sandbox: `NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=strict`, `ProtectHome=true`, an empty `CapabilityBoundingSet=`,
`PrivateDevices`, `ProtectKernelTunables/Modules/Logs`, `ProtectControlGroups`,
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK` (netlink only for
glibc's interface probe in `getaddrinfo`), `RestrictNamespaces`,
`LockPersonality`, `RestrictSUIDSGID`, `SystemCallArchitectures=native` and
`SystemCallFilter=@system-service` (denied calls fail with `EPERM`, never kill
the process). `MemoryDenyWriteExecute` stays off for CPython/numpy. Check the
result with `systemd-analyze security parayabuck` after installing.

There is no watchdog unit any more: `parayabuck ops watchdog` and
`parayabuck-watchdog.{service,timer}` existed only to deliver a dead-man alert
off the VM and were removed with outbound alerting on 2026-10-06.

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
config loader also reads `.env`, but only from the path in `ENV_PATH` or from
the working directory — never from a parent directory. If that credential file
is accessible by group or other users, `doctor` prints a WARN line with the
`chmod 600` fix (the start is not refused).

| Variable | Required | Notes |
|---|---|---|
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | yes | PAPER keys. PAPER never reads the LIVE names |
| `ALPACA_LIVE_API_KEY`, `ALPACA_LIVE_SECRET_KEY` | live only | LIVE keys. LIVE never reads the PAPER names, and a LIVE start is refused when the key resolves to a paper account |
| `PARAYABUCK_ENABLE_LIVE` | yes | `NO` |
| `PARAYABUCK_SMTP_USERNAME`, `PARAYABUCK_SMTP_PASSWORD` | e-mail only | SMTP login for `alerting.email` (Gmail: the address and a 16-character App Password). Never put them in `config/settings.yaml`; they are never logged |
| `PARAYABUCK_TRADING_HOST` | no | `1` designates the single runner. Set it in the unit's `[Service]` block, not here: a laptop `.env` must never carry it |
| `CONFIG_PATH` | no | default `./config/settings.yaml` |
| `OPEN_BROWSER` | no | `0` on the VM |
| `LOG_LEVEL` | no | default `INFO` |
| `PARAYABUCK_OPERATOR_APPROVAL_KEY` | live only | unused in PAPER |

### Where alerts go

Every alert kind is written to the service log and to the operational event
journal (`data/runtime/observability.sqlite3`), throttled per incident. With
the shipped config nothing leaves the VM; the owner can opt in to e-mail (next
section). Read the local copy with:

```bash
.venv/bin/parayabuck ops status                 # health checks + alert counters the engine published
.venv/bin/parayabuck ops events --since 24h     # the journal (filter with --kind/--severity)
sudo journalctl -u parayabuck -g 'ALERT ' --since today
```

or on the dashboard through the SSH tunnel. No surface turns `DEGRADED`
because of alert delivery. `alerting.enabled`, `alerting.webhook_url` and
`alerting.events` (the webhook removed on 2026-10-06) are still rejected on
load; the alerting keys are `alerting.daily_summary` and the `alerting.email`
block.

### E-mail alerts (opt-in)

Disabled by default. When enabled, the engine e-mails the alerts that need the
owner: engine stopped/halted/start failed, safety incidents and halts
(`circuit_breaker`, `drawdown_halt`, `risk_incident`, `ambiguous_order`,
`paper_incident`), `order_rejected` and `exit_sell_rejected`, `intent_alarm`,
`reconciliation_failure` (which also carries the ownership-drift and
protection-failure halts in its `reason`), `market_data_degraded`,
`data_provider_unavailable`, `backup_failed`, `live_rearm_required`,
`live_authorization_expiring`/`live_authorization_expired`, and the daily
summary (`alerting.email.daily_summary`). SMTP runs over TLS only (STARTTLS on
587 or implicit TLS on 465), in a background thread with a 10 s timeout: a slow
or broken mail server never blocks or stops trading. A transient send failure
is retried up to three attempts (1 s, then 4 s apart); a rejected login is not
retried. The same incident is mailed at most once per `min_interval_minutes` (30) and at most
`max_per_hour` (20) mails leave per hour. A failed or dropped mail is recorded
locally as `alert_delivery_failed` (`parayabuck ops events --kind
alert_delivery_failed`).

Setup with Gmail (the owner does this; nothing here needs a developer):

1. Turn on 2-Step Verification for the Google account that will send the
   mails, then open <https://myaccount.google.com/apppasswords>, create an app
   password named `ParayaBuck` and copy the 16 characters.
2. On the VM, add two lines to the env file (`sudo nano <env file>`, keep it
   `chmod 600`):

   ```bash
   PARAYABUCK_SMTP_USERNAME=sender@gmail.com
   PARAYABUCK_SMTP_PASSWORD=your_app_password_here   # the 16 letters, no spaces
   ```

3. In `config/settings.yaml`:

   ```yaml
   alerting:
     daily_summary: true
     email:
       enabled: true
       smtp_host: smtp.gmail.com
       smtp_port: 587
       security: starttls
       from_address: sender@gmail.com
       to_addresses: [owner@example.com]
       daily_summary: true
   ```

4. Check without sending, then send one test mail, then restart:

   ```bash
   .venv/bin/parayabuck doctor --strict --configured-mode --offline   # "e-mail alerts valid"
   .venv/bin/parayabuck ops test-email                                # validates, sends nothing
   .venv/bin/parayabuck ops test-email --confirm                      # sends ONE test e-mail
   sudo systemctl restart parayabuck
   ```

Other providers work the same way (`security: ssl` with port 465 for implicit
TLS). In PAPER a broken e-mail setup is a `WARN` row and never stops the
engine; in LIVE `doctor --configured-mode` requires valid e-mail alerts.

### Dead-man switch (optional, healthchecks.io)

The e-mail channel cannot report that the VM itself is down. The unit
`parayabuck-deadman.timer` runs every 5 minutes: it GETs the loopback
readiness endpoint (`PARAYABUCK_READYZ_URL`, default
`http://127.0.0.1:8000/readyz`) and pings the heartbeat URL on HTTP 200, or
`<url>/fail` on any other status or error. The external service e-mails the
owner when pings stop. With no URL configured the unit does nothing. Its only
outbound traffic is one HTTPS request per run.

1. In healthchecks.io create a check with period 5 minutes and grace 10
   minutes, attach the owner's e-mail, and copy the ping URL
   (`https://hc-ping.com/<uuid>`).
2. On the VM, as root, store the URL where systemd reads it (the unit exposes it
   only through `LoadCredential=`):

   ```bash
   sudo mkdir -p /etc/parayabuck
   sudo sh -c 'umask 077; printf "%s\n" "https://hc-ping.com/<uuid>" > /etc/parayabuck/healthcheck-url'
   ```

3. Install and start the timer (the unit paths follow the same rewrite rules as
   `parayabuck.service`):

   ```bash
   sudo cp deploy/systemd/parayabuck-deadman.service deploy/systemd/parayabuck-deadman.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now parayabuck-deadman.timer
   sudo systemctl start parayabuck-deadman.service
   journalctl -u parayabuck-deadman -n 3    # "deadman: pinged success; ready"
   ```

Without the credential file the service fails to start, by design. A failed run
in journald with `ping failed` means the heartbeat host itself was unreachable;
the external check then alarms on the missing ping.

### Off-host backup copy and monthly restore drill (optional)

Local snapshots stay on the VM disk. Set `PARAYABUCK_BACKUP_OFFHOST_CMD` in the
env file to copy each verified snapshot off the host, for example:

```bash
PARAYABUCK_BACKUP_OFFHOST_CMD=gsutil cp {path} gs://<bucket>/parayabuck/
```

`{path}` is shell-quoted and the command runs without a shell, with a
900 s timeout. Unset (the default) means no copy. A failed copy keeps the
verified local snapshot and emits `backup_failed` (reason
`offhost_copy_failed`), which the e-mail channel forwards. The backup unit runs
with `ProtectHome=true`, so the copy tool cannot read credentials from the
deploy user's home: point it at a service-account key the `parayabuck` user can
read (for example `GOOGLE_APPLICATION_CREDENTIALS` in the env file, with the key
file mode `0400` and owned by `parayabuck`). Check the tool once by hand as the
`parayabuck` user before relying on the timer.

Monthly restore drill (about 20 minutes, record the result in the operations log):

1. List the newest off-host object: `gsutil ls -l gs://<bucket>/parayabuck/ | tail -3`.
2. Download it to a scratch directory outside `data/`, for example
   `/var/tmp/restore-drill/`. Do not restore into the live `data/` path.
3. Verify it: `.venv/bin/parayabuck backup --verify-only /var/tmp/restore-drill/<file>`
   must print `Backup OK`.
4. Compare the row counts of the decision tables with the newest local
   snapshot in `data/backups/`; they should differ only by activity since that
   snapshot.
5. Run `.venv/bin/parayabuck doctor` and confirm the backup freshness row is OK.
6. Delete the scratch copy and record the date, the object name and the result.

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

### Encrypting the API keys with systemd-creds (optional)

The engine reads every credential from `$CREDENTIALS_DIRECTORY/<NAME>` first and
from the environment second (`config.read_credential`), so the Alpaca keys can
leave the plain `.env`. Do this on the VM yourself; the keys never pass through
any tool or log:

```bash
sudo install -d -m 0700 /etc/parayabuck/creds
read -rs KEY && printf %s "$KEY" | sudo systemd-creds encrypt --name=ALPACA_API_KEY - /etc/parayabuck/creds/ALPACA_API_KEY.cred; unset KEY
read -rs SECRET && printf %s "$SECRET" | sudo systemd-creds encrypt --name=ALPACA_SECRET_KEY - /etc/parayabuck/creds/ALPACA_SECRET_KEY.cred; unset SECRET
sudo systemctl edit parayabuck
```

In the drop-in add:

```ini
[Service]
LoadCredentialEncrypted=ALPACA_API_KEY:/etc/parayabuck/creds/ALPACA_API_KEY.cred
LoadCredentialEncrypted=ALPACA_SECRET_KEY:/etc/parayabuck/creds/ALPACA_SECRET_KEY.cred
```

Then remove the two lines from `.env`, keep it `chmod 600` (`doctor --strict`
refuses a group- or world-readable credential file), and run
`sudo systemctl restart parayabuck` and `curl 127.0.0.1:8000/readyz`. SMTP
login (`PARAYABUCK_SMTP_USERNAME` / `PARAYABUCK_SMTP_PASSWORD`) and the LIVE keys
(`ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`) work the same way. Rollback:
delete the drop-in, put the lines back in `.env`, restart.

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
sudo -u parayabuck .venv/bin/parayabuck doctor --strict --configured-mode --offline
sudo cp deploy/systemd/parayabuck*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now parayabuck.service parayabuck-backup.timer
systemctl list-timers 'parayabuck-*'                     # confirm the backup timer is scheduled
```

Nothing outside the web process watches the engine: a start that failed
shows up in `/readyz` (503 with `engine_error`), `parayabuck ops status`, the
dashboard and `runtime_start_failed` in the journal; a failing backup timer
shows up as a failed `parayabuck-backup.service` and as the `backup stale` row
of `parayabuck doctor`.

### Removing the old watchdog and webhook (hosts installed before 2026-10-06)

```bash
sudo systemctl disable --now parayabuck-watchdog.timer
sudo rm -f /etc/systemd/system/parayabuck-watchdog.service /etc/systemd/system/parayabuck-watchdog.timer
sudo cp deploy/systemd/parayabuck.service deploy/systemd/parayabuck-backup.service /etc/systemd/system/
#   (re-apply the hand edits: checkout paths, ProtectHome=read-only, the trading-host drop-in stays)
sudo systemctl daemon-reload
sudo sed -i '/^PARAYABUCK_ALERT_WEBHOOK_URL=/d' <env file>   # ignored now; drop the secret
grep -n -A6 '^alerting:' config/settings.yaml                # only daily_summary may remain
```

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
5. **Prefer no restart from 15:55 ET to the session close + 15 min** (16:30 ET
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
.venv/bin/parayabuck doctor --strict --configured-mode --offline
sudo systemctl restart parayabuck
```

`requirements.lock` is a complete, closed runtime closure (every transitive
dependency pinned, nothing resolved at install time) and it matches the
environment the test suite runs in. When a deploy changes it — the 2026-10-01
closure update did — reinstall it on the VM with the command above; the
`pip install -e . --no-deps` line alone leaves the old pins in place.

From 2026-10-01 the trading host must be designated explicitly. The shipped
config keeps `app.auto_start_engine: true`, which is safe because only a host
with `PARAYABUCK_TRADING_HOST=1` can start the engine. Before restarting:

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
Step 2 needs no local edit: the shipped value is already `true`. On the VM the
unit line lives in the drop-in `/etc/systemd/system/parayabuck.service.d/trading-host.conf`.

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

### Mode-aware preflight (hosts installed before 2026-10-12)

The installed unit is hand-edited, so a bundle never changes its
`ExecStartPre`. The old `doctor --strict --paper --offline` line keeps working
for PAPER (it applies the identical PAPER checks); switch to the mode-aware
line once, before LIVE is ever configured:

```bash
sudo systemctl edit --full parayabuck
#   ExecStartPre=<checkout>/.venv/bin/parayabuck doctor --strict --configured-mode --offline
sudo systemctl daemon-reload
systemctl cat parayabuck | grep ExecStartPre
```

## LIVE runbook

LIVE stays disabled in the shipped config. These steps are for the day the
owner funds the Alpaca LIVE account; run them on the trading host only.

1. **Keys.** Add `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY` (from the
   LIVE dashboard, keys starting `AK`) to the env file. Keep the PAPER pair;
   the two pairs are never mixed. Also set `PARAYABUCK_ENABLE_LIVE=YES` and a
   random `PARAYABUCK_OPERATOR_APPROVAL_KEY` (at least 32 characters).
2. **E-mail.** Enable e-mail alerts (section above). LIVE preflight refuses
   to start without them.
3. **Settings.** `runtime.mode: live`, `live.enabled: true`,
   `live.hard_dollar_cap: <deposit x 0.98>`,
   `live.operator_approval_path: data/runtime/live_approval.json`. LIVE keeps
   its own ledger: a LIVE config that names the PAPER default
   `data/runtime/parayabuck.sqlite3` uses `data/runtime/live/parayabuck.sqlite3`
   instead (its own risk-peak sidecar sits next to it). `live.buy_gap_margin_pct`
   (0.03) keeps each BUY small enough that a gap-up open of 3 % never borrows.
4. **Preflight.** `.venv/bin/parayabuck doctor --strict --configured-mode --offline`
   must say READY.
5. **Authorize.** Two ways, chosen by the owner:
   - *Manual re-arm (default).* `parayabuck authorize-live --operator <name>
     --expires-minutes 30`, then arm in the dashboard (mode chip, type
     `LIVE <suffix>`) and press Start. After **every** reboot, deploy or crash
     LIVE stays stopped and the owner gets a `live_rearm_required` e-mail;
     positions stay open until the re-arm.
   - *Standing authorization (opt-in, at most 30 days).*
     `parayabuck authorize-live --operator <name> --standing-days 14` writes
     `live_standing_authorization.json` next to the approval file (HMAC-signed
     with the approval key). While it is valid, the trading host starts LIVE
     by itself after a reboot or deploy. It is bound to the LIVE account id,
     the strategy digests, the whole settings file, the dollar cap and this
     machine: any settings edit (even the log level), a cap change, another
     account or another host voids it and LIVE falls back to manual re-arm
     (with the e-mail). Three days before it ends the owner gets
     `live_authorization_expiring` (one per day), and `live_authorization_expired`
     when it ends; a running engine keeps running, only the next restart needs
     a fresh authorization. Revoke it at any time with
     `parayabuck authorize-live --revoke-standing`.
6. **Every LIVE start** still checks, in the engine: `live.enabled`, the cap,
   `PARAYABUCK_ENABLE_LIVE=YES`, the trading-host flag, the approval or
   standing HMAC, the account id, and that the LIVE key is a live account
   (`PA...` paper accounts are refused).
7. Go back to PAPER by setting `runtime.mode: paper` (the dashboard mode chip
   clears the cap and approval path) and restarting.

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
Inside the container the console binds `0.0.0.0` (`PARAYABUCK_CONTAINER=YES`);
the control token and every mutating request are then served only to loopback
or private (Docker bridge) peers, and a trading host refuses that bind
outright.
