# Secret boundary
PAPER requires ALPACA_API_KEY and ALPACA_SECRET_KEY. EngineManager.get_credentials loads .env (src/parayabuck/web/managers/engine.py:166-172); the paper auto-start predicate needs both. PARAYABUCK_OPERATOR_APPROVAL_KEY is only part of LIVE authorization, not PAPER execution. PARAYABUCK_ENABLE_LIVE must remain NO/unset. Optional PARAYABUCK_ALERT_WEBHOOK_URL is an alert sink secret.
.gitignore ignores .env; git ls-files returned no tracked .env. Tracked literal scan matched placeholder examples/tests, not a verified production key. No real credential file or value was inspected. diagnostic._redact and operational_event._redact_fields redact named secret fields; tests/test_observability_mission5.py exercises this. Avoid raw secrets on the shell command line; use a mode-0600 .env, systemd EnvironmentFile, or Docker env_file. deploy/systemd/parayabuck.service uses UMask=0077.
Presence-only shell check after loading the operator environment:

    for n in ALPACA_API_KEY ALPACA_SECRET_KEY; do
      if [ -n "$(printenv "$n")" ]; then echo "$n SET"; else echo "$n NOT SET"; fi
    done

Never print the values.
