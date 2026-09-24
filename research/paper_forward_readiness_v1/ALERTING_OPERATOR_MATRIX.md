# Alert operator matrix
Source: monitoring/events.py and monitoring/alerts.py. WARNING events log regardless of webhook configuration; webhook delivery is bounded, retried and throttled, and failure does not raise into trading.
| Event | Severity | Trading effect | Operator action |
| --- | --- | --- | --- |
| runtime_started | info | none | Confirm PAPER badge and readyz |
| runtime_stopped_unexpected | warning | stopped | Inspect worker/process and reconcile before restart |
| scanner_refresh_failure | warning | market entries blocked when no valid active universe | Check provider, digest and age; wait for recovery |
| scanner_recovered | info | may resume | Confirm active universe and readyz |
| order_rejected | warning | affected order not filled | Inspect reason, holdings and desired target |
| ambiguous_order | warning | new entries blocked pending reconciliation | Resolve broker order identity; never resend manually |
| reconciliation_failure | warning | startup or entries blocked | Compare broker/DB orders and fills |
| reconciliation_recovered | info | barrier cleared | Confirm no unexplained drift |
| risk_incident | warning | BUY latch may apply | Inspect daily loss/high-water state |
| drawdown_halt | warning | BUY blocked | Follow risk incident runbook |
| circuit_breaker | warning | runtime halted | Investigate underlying incident before reset |
| signal_adjustment_failure | info | equity entry basis may block | Inspect split-adjusted history |
| paper_incident | warning | incident specific | Collect ops bundle and triage |
Webhook payload secret fields are redacted by operational_event, but free-form exception strings still deserve operator review before external forwarding. No webhook request was made in this audit.
