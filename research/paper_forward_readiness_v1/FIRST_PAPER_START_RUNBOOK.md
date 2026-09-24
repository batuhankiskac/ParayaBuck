# First sustained PAPER start — operator only
Current audit decision is BLOCKED. Do not run the launch section until the receipt, current-production soak-auditor and crypto effective-slot visibility blockers are repaired and all offline gates pass on the final publication SHA.

## Before launch
1. In the deployed checkout, git fetch origin --prune --tags, record git rev-parse HEAD and origin/main, require equality to the approved SHA, and require git status --porcelain empty. Keep the matching durable runtime DB when managed inventory exists.
2. Use Python 3.12 and the pinned requirements.lock as docs/production_deployment.md specifies. Run .venv/bin/parayabuck doctor --strict --paper --offline. It may exit 0 with REFRESH_NEEDED; record those statuses.
3. Load PAPER credentials through a 0600 .env/EnvironmentFile. Check presence only with the SET/NOT SET command in SECRET_BOUNDARY_AUDIT.md. Require PARAYABUCK_ENABLE_LIVE=NO, runtime.mode=paper, live.enabled=false and hard cap 0. Do not use a shared account if avoidable; inspect positions and OPEN orders in the operator broker UI before start.
4. Verify config, data/runtime, reports and data/backups permissions. If a runtime DB already exists, take and verify an online backup, preserving WAL sidecars. Missing active universes require the normal online scanner at actual launch; no static fallback exists.

## Launch
Repository-supported VM command: sudo systemctl enable --now parayabuck.service (after unit installation per docs/production_deployment.md). Direct repository CLI is .venv/bin/parayabuck web. Both auto-start PAPER when credentials are present. This document is a procedure, not authorization to execute it during this audit.

## Immediately after launch
Check systemctl status parayabuck --no-pager; journalctl -u parayabuck -n 200 --no-pager; curl -i http://127.0.0.1:8000/healthz; curl -i http://127.0.0.1:8000/readyz; .venv/bin/parayabuck ops status --json; .venv/bin/parayabuck ops doctor --json. Require PAPER/LIVE disabled, expected account suffix, reconciliation complete, both active universes valid, workers healthy, 8 global and 2 crypto caps, cash/gross headroom and no ambiguous orders. /healthz 200 alone is insufficient.

## Natural decision and 24 hours
Observe the next natural scheduled or startup catch-up decision; do not create a manual order. Verify a decision-unique receipt, DB decision/order/fill lifecycle and both-market soak summary after the blocker repair. During the first day check rejects, drift, scanner state, effective slots, crypto claims, gross, cash and alerts. Archive read-only audit output.

## Restart and stop
After stable observation, take a verified backup, use sudo systemctl restart parayabuck, and require reconciliation before new orders; compare client order IDs and persistent rebalance state. Graceful stop: sudo systemctl stop parayabuck, then confirm /healthz no longer answers and retain the DB, WAL and backup. Do not use kill -9 as the first drill.
