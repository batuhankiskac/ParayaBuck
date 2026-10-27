# LIVE readiness v1 — E2 (`equity_leveraged_trend_v1` 1.1.0) on Alpaca

Status 2026-10-12: **LIVE stays disabled.** This is a preparation checklist for
a small (assumed $1k–$25k) Alpaca LIVE account held by a non-US (Turkish)
individual. The audit itself changed no code; the order-path behaviour it
describes is pinned by `tests/test_live_readiness_current_behaviour.py`.

**Fix wave 2026-10-12 (LIVE readiness).** Closed: **B1** (opt-in e-mail
alerts), **B2** (standing LIVE authorization, option (b)), **B3** (separate
LIVE credentials), **R1** (gap-safe LIVE BUY cap), **R3** (rejected exit-SELL
escalation plus one retry). Still open: **R2, R4, R5, R6** and the owner/ops
items **O1–O8**. The shipped config keeps `runtime.mode: paper`, LIVE
disabled and e-mail disabled; the PAPER strategy configuration digest is
unchanged. Tests: `tests/test_email_alerts.py`,
`tests/test_live_credentials_and_standing.py`,
`tests/test_live_readiness_r1_r3.py`. Rows of the table below that changed
are marked *(was: …)*.

Confidence tags: **[H]** high, **[M]** medium, **[L]** low / verify with Alpaca.

## 1. How the LIVE path works today

| Area | Today | Where |
|---|---|---|
| Enable | Needs all of: `runtime.mode: live`, `live.enabled: true`, `live.hard_dollar_cap > 0`, `live.operator_approval_path`, env `PARAYABUCK_ENABLE_LIVE=YES`, `PARAYABUCK_OPERATOR_APPROVAL_KEY` (at least 32 chars), and `PARAYABUCK_TRADING_HOST=1` | `config.py` (`LiveConfig`, mode/enabled cross-check), `runtime.py` `_start_impl` |
| Operator approval | `parayabuck authorize-live --operator X --expires-minutes N` (N is 1–1440). It runs a read-only `account()` call against the live endpoint and writes an HMAC-signed file. The file binds account id, the configuration digests (strategy, allocation, universe, risk limits, rebalance band and minimum notional) and the dollar cap | `cli.py` `_authorize_live`, `live_safety.py` `LiveOperatorApproval` |
| Dashboard arm | `POST /api/mode` toggles the mode while the engine is stopped. To arm: request (live account probe, 2-minute token), then type `LIVE <suffix>` (5-minute token). The start converts this into a `LiveStartupProof`, which the runtime re-verifies (credential fingerprint, digests, account id) before reconciliation | `web/routers/safety.py`, `web/managers/engine.py` `request_live_arming` / `confirm_live_arming`, `runtime.py` `_verify_live_startup_proof` |
| Exposure cap | `min(live.hard_dollar_cap, approval.maximum_dollar_exposure)` caps the target allocation (`cap/equity`) and BUY capacity (`cap − managed − committed`) | `runtime.py` around line 1174, `portfolio/runtime_service.py` lines 1331, 1962, 2229 |
| Auto-start | PAPER as before. LIVE auto-starts only with a valid standing LIVE authorization (B2); otherwise it waits for a manual re-arm and e-mails `live_rearm_required`. The unit runs `doctor --strict --configured-mode` (PAPER: the identical `--paper` checks) *(was: paper only; `doctor --strict --paper` refused a live config)* | `web/managers/engine.py` `should_auto_start`, `live_authorization.py`, `deploy/systemd/parayabuck.service` |
| Keys / endpoints | PAPER reads `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, LIVE only `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`, never falling back. A LIVE start is refused unless the account endpoint reports a live (non-`PA`) account; PAPER refuses an `AK` live key *(was: one key pair for both modes)*. `paper=False` selects `api.alpaca.markets` for REST and the trade-update stream. Market data uses the mode's keys | `live_safety.py`, `web/managers/engine.py`, `cli.py`, `runtime.py` |
| Orders | One market order per leg, **`time_in_force=day`, sized by share `qty`** (fractional when the asset is fractionable, floored to `min_trade_increment`). No `notional`, no `opg`, no extended hours. Deterministic `client_order_id` `parayabuck-{decision}-{salt}-{symbol}-{side}` | `execution/alpaca_orders.py` |
| Order existence | Skip when the drift is below `rebalance_band_pct` (0.005), unless a downsize between two positive weights is held by the gain band (A1u) or postponed by the December deferral (H2); a held downsize is recorded as a hold, not an order. Also skipped: a BUY into a manually opened position, a symbol with an open order (a SELL may still go out while only a local BUY is pending), an order below `max($10, broker min notional)`, and a SELL beyond the managed quantity. Risk-overlay reductions are never held. SELLs go before BUYs | `execution/portfolio.py` `TargetToOrderPlanner.plan` |
| BUY sizing | `target_weight × equity / close`, then gated by `cash − 2% × equity − committed` (Alpaca's `cash` field). LIVE adds the R1 cap: notional × (1 + `live.buy_gap_margin_pct`, 0.03) ≤ min(`cash`, `non_marginable_buying_power`) − committed. PAPER sizing unchanged | `portfolio/runtime_service.py` `_size_buys_to_safe_capacity` |
| Rejections | A 4xx whose client id the broker never accepted becomes `DefiniteOrderRejection`. The row is booked `rejected`, alert `order_rejected`, and the batch continues; the broker layer never retries the same client id. A rejected **SELL** also raises `exit_sell_rejected` (e-mailed) and is retried once at the next safe account poll with client id `<original>-retry1`; a rejected retry is escalated again and never retried *(was: no retry until the next session)* | `alpaca.py` `_submit_with_recovery`, `runtime_service.py` `_submit_orders`, `retry_rejected_exit_sells` |
| Timing | Decision after the close (SIP cross-check, about 16:15–16:40 ET). The DAY market order is queued and fills at or just after the next open, not in the opening auction. A restart that missed a close decides immediately ("startup catch-up"); during market hours that order fills intraday | `portfolio/runtime_service.py` `startup_catch_up` |
| Reconciliation | Fail-closed on unknown open orders or on QLD inventory the ledger does not own. Foreign symbols are tolerated | `docs/deployment.md` "Pre-deploy checks" |
| Risk latches | `daily_pnl = equity − last_equity`; drawdown is against a peak file (`live_equity_peak.json`). Neither knows about deposits or withdrawals | `execution/alpaca_account.py`, `execution/risk_peak.py` |
| Alerts | Journal plus `ops events` always; opt-in e-mail (`alerting.email`, SMTP over TLS, async, throttled) for the owner-relevant kinds *(was: local only)* | `monitoring/email_alerts.py`, `docs/deployment.md` "E-mail alerts" |

## 2. Alpaca LIVE constraints that matter

1. **All Alpaca accounts are margin accounts; there are no cash accounts.**
   [H] ([support: cash accounts](https://alpaca.markets/support/alpaca-cash-accounts),
   [turn off margin](https://alpaca.markets/support/turn-off-margin),
   [account plans](https://docs.alpaca.markets/docs/account-plans))
   - Below $2,000 of equity the `multiplier` is 1: buying power equals cash.
   - At $2,000 or more it becomes 2 (Reg T).
   - Whether an account can be pinned to 1x is unclear [L].
   - Consequence: there are no good-faith or free-riding violations to manage. Unsettled sale proceeds can be reused for buys; Alpaca covers the float. They cannot be withdrawn until settled (T+1) [H] ([unsettled funds](https://alpaca.markets/learn/understanding-unsettled-funds)).
2. **The PDT rule is retired.**
   - FINRA Regulatory Notice 26-10 set the effective date as 2026-06-04 [H] ([FINRA 26-10](https://www.finra.org/rules-guidance/notices/26-10)).
   - Alpaca removed day-trade counting and the $25k minimum. Pre-trade checks now reject orders that would create a margin deficit instead (Alpaca's blog says this applies "in line with" the effective date) [H] ([Alpaca blog](https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/), checked 2026-10-12).
   - A long-only position at most 0.98× equity should never be near an intraday margin deficit [M].
   - Even under the old rule, this strategy could not day-trade by itself: every decision fills at the next open, so a buy and a sell of QLD never land on the same day. The only exceptions are a manual operator trade, or a startup catch-up during market hours following a same-day buy fill. In both cases the opposing fill would have to come from a different source.
   - The legacy code was HTTP 403, code 40310100. The bot already turns it into a definite rejection with no retry (pinned by a test).
3. **Wash-trade protection** rejects a market order while an opposing order in the same symbol is open [H] ([user protection](https://docs.alpaca.markets/docs/user-protection)). The bot is safe here: it never submits while any order is open in the symbol (planner `open_symbols` and the `OrderService` open-order guard).
4. **Fractional orders**
   - Fractional orders allow only `time_in_force=day` with market, limit, stop or stop-limit [H] ([fractional trading](https://docs.alpaca.markets/docs/fractional-trading)).
   - **OPG and CLS are not allowed for fractional or notional orders** [H].
   - Minimum is $1 notional [H].
   - QLD meets the eligibility rules (over $1, ADV over 10k). The authoritative check is `GET /v2/assets/QLD` → `fractionable` [M].
5. **OPG window**
   - An `opg` order is rejected if submitted between 09:28 and 19:00 ET. Submitted after 19:00 ET, it is queued for the next opening auction [H] ([orders at Alpaca](https://docs.alpaca.markets/docs/orders-at-alpaca)).
   - The current decision time (about 16:30 ET) falls inside the reject window.
   - Whether OPG needs account enablement is unclear [L].
6. **Fees**
   - No commission for self-directed API equity trades [H].
   - On sells: SEC $20.60 per $1M (from 2026-04-04) and FINRA TAF $0.000195 per share (cap $9.79) [H/M].
   - CAT fee per share on both sides [M].
   - The daily total is rounded up to $0.01 [H] ([regulatory fees](https://alpaca.markets/support/regulatory-fees)).
   - Not modelled: the backtest, telemetry and shadow comparator use the unrounded per-trade fee, so a modelled fee is slightly below the broker's daily total. Live fee activity is not imported either, so no observed fee reaches the books yet (see the tax ledger's fee guard).
   - At $1k–$25k this is cents per year. The real cost of small resizes is the spread and open-price slippage, not fees.
   - The official schedule is in [BrokFeeSched.pdf](https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf); it was not machine-readable here, so wire fees need reading by hand.
7. **Funding from Turkey**
   - No minimum deposit [H].
   - Fund by international USD wire. Incoming wire appears free and outgoing wire is about $50 [L]; intermediary bank fees may apply.
   - CurrencyCloud local-currency transfer does not list Turkey [M].
   - Wiring USD from a Turkish USD account avoids FX at Alpaca. TRY→USD conversion costs are the Turkish bank's.
   - Turkey's availability as a residence country is not on a public list; the account is already open, so this is presumably fine [L] ([international funding](https://alpaca.markets/support/how-can-an-international-user-non-us-tax-resident-fund-their-account)).
8. **High-Yield Cash sweep**
   - Up to 3.56% APY (variable, 2026-09-22), FDIC pass-through [H] ([program](https://alpaca.markets/blog/alpaca-launches-high-yield-cash-program-for-trading-api-users-enabling-growth-on-deposits/)).
   - An older FAQ says US residents only; whether non-US accounts qualify is unclear [L].
   - It matters for E2's cash periods (exits about 6 times a year).
9. **W-8BEN**
   - Signed at onboarding. Renew every 3 years, or US withholding goes to 30% [H] ([international accounts](https://docs.alpaca.markets/docs/international-accounts)).
   - Under the US–Turkey treaty, an individual's dividends (including RIC/ETF dividends such as QLD's) are withheld at 20% [M-H].
   - US capital gains on QLD sales are not withheld for non-residents [M]. Turkish tax on gains and dividends is the owner's matter and is out of scope here.
10. **What PAPER hides** [H] ([paper trading](https://docs.alpaca.markets/docs/paper-trading)): regulatory fees, dividends, market impact, and the multiplier change at $2k. Paper's simulated open fills are not real auction prints.

## 3. Bot behaviour versus each constraint

| Constraint | Bot today | Verdict |
|---|---|---|
| Min order size, small account | At $1k the 0.5 pp band is $5, below the $10 floor, so the effective band is 1 pp (a $12 resize trades). At $25k the band decides (a 0.6 pp move is a $150 order). Pinned by tests | OK. Consider a larger floor (see R2) |
| Fractional vs whole | Fractional `qty` when `fractionable`; whole-share floor otherwise; local rejection if below 1 share | OK. Verify `fractionable` on the live asset |
| Settlement | Not settlement-aware, but not needed on a margin account. Buys gate on `cash`, which includes unsettled proceeds | OK at 1x. At 2x a gap-up open can push a market buy past cash (see R1) |
| Margin borrowing | LIVE BUYs are capped so a gap-up open of up to 3% fits in min(cash, non-marginable buying power) | Done (R1) |
| PDT / day trades | Rule retired; strategy cannot self-day-trade; a 403 is booked rejected | OK |
| Order type at the open | DAY market queued after the close: fills at or just after 09:30, not the auction. OPG is impossible with fractional qty and would need submission after 19:00 ET | Acceptable. Measure first (R4) |
| Rejection behaviour | Booked `rejected`, alert (e-mailed when enabled), no halt. A rejected SELL is escalated and retried once, never looped | Done (B1, R3) |
| Deposits / withdrawals | Counted as P&L. A withdrawal of more than 20% in one day trips the daily-loss latch; withdrawals also deepen the drawdown against the peak | Ops rule O6, improvement R5 |
| Restarts | Default: manual re-arm after every restart, with a `live_rearm_required` e-mail. Opt-in: a standing LIVE authorization (≤ 30 days) lets the trading host restart LIVE unattended | Done (B2) |

## 4. Checklist

### Blockers (fix before the first LIVE dollar)

- **B1 — Off-host alerting for LIVE** (code, about 1 day). **Status: closed 2026-10-12.**
  - Implemented as opt-in e-mail (owner's choice): `alerting.email` (disabled by default), SMTP over TLS with the stdlib only (STARTTLS 587 or `ssl` 465), login only from `PARAYABUCK_SMTP_USERNAME` / `PARAYABUCK_SMTP_PASSWORD`, never logged. Asynchronous bounded queue, per-incident window (`min_interval_minutes`, keyed like the alert throttle on kind + correlation + reason + state) and an hourly ceiling; failures are journaled as `alert_delivery_failed`. Kinds: engine stopped/halted/start failed, safety incidents and halts, `order_rejected`, `exit_sell_rejected`, `intent_alarm`, `reconciliation_failure`, `live_rearm_required`, `live_authorization_expiring`/`_expired`, optional `daily_summary`. `doctor` validates it offline (required in LIVE); `parayabuck ops test-email --confirm` sends one test mail. Setup: `docs/deployment.md` "E-mail alerts".
  - Today every alert stays on the VM. With real money, these must reach the owner's phone: `engine stopped`/`halted`, `order_rejected` (especially a SELL to cash), `intent_alarm`, `reconciliation_failure`, and a missed daily summary.
  - A minimal outbound channel is enough (for example a Telegram bot or e-mail), LIVE-only, with secrets in env.
- **B2 — A LIVE run mode that survives operations** (deploy/ops plus small code, about 1 day). **Status: closed 2026-10-12 with option (b) plus an explicit owner opt-in.**
  - Without anything else LIVE behaves as (a): manual re-arm after every restart, now with a `live_rearm_required` e-mail.
  - `parayabuck authorize-live --operator X --standing-days N` (N ≤ 30) writes an HMAC-signed standing LIVE authorization bound to the LIVE account id, the strategy configuration digests, a digest of the whole loaded settings, the hard dollar cap and the trading host (hostname + machine id). While valid, the web auto-start starts LIVE unattended; the runtime re-verifies the signature, expiry, bindings, the account and that the key is a live account before reconciliation. Any settings/cap/account/host change voids it. Expiry within 3 days and expiry itself are e-mailed; `--revoke-standing` deletes it. All existing gates stay (`live.enabled`, cap, `PARAYABUCK_ENABLE_LIVE`, trading host, HMAC key). A manual dashboard start still needs the interactive arm.
  - The unit's preflight is mode-aware (`doctor --strict --configured-mode --offline`): PAPER gets exactly the `--paper` checks; LIVE checks the LIVE gates, distinct LIVE/PAPER keys, a separate ledger and valid e-mail alerts. Runbook: `docs/deployment.md` "LIVE runbook".
  - Original options:
  - (a) Accept manual-only restarts. Document a LIVE runbook: stop the paper unit, run `authorize-live`, arm in the dashboard, restart after every deploy or reboot. Disable GCE automatic restarts and maintenance live-migration surprises.
  - (b) Add a LIVE systemd variant whose preflight is `doctor --strict` (a non-paper `--live` invariant set). A missed restart plus no alert means an exit may be skipped.

  Whichever is chosen, B1 is required.
- **B3 — Separate credential names** (code, about 0.5 day). **Status: closed 2026-10-12.** LIVE reads only `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY` (web manager, `authorize-live`, doctor), PAPER only the old names; neither falls back. `authorize-live`, the dashboard arm and the runtime refuse a LIVE key whose account endpoint reports a paper (`PA…`) account, and PAPER refuses an `AK` live key. A LIVE configuration that keeps the PAPER default ledger path uses `data/runtime/live/parayabuck.sqlite3` (and its own risk-peak sidecar), so PAPER and LIVE state never mix (O1's separate `decision_db_path` is now the default). The settings drawer edits only the PAPER pair.
  - Both modes read `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, so going LIVE means overwriting the PAPER keys on the VM. PAPER then stops, and the laptop `.env` convention ("shared PAPER key") silently stops being true for the VM.
  - Add `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`, selected only when `mode == live`. Also refuse a live start if the key resolves to a paper account.

### Config / ops steps (owner or operator)

- **O1** — Settings for LIVE:
  - `runtime.mode: live`
  - `live.enabled: true`
  - `live.hard_dollar_cap` set to the funded amount (for example the deposit × 0.98)
  - `live.operator_approval_path: data/runtime/live_approval.json`
  - A separate ledger is now automatic (B3): keeping the PAPER default `runtime.decision_db_path` resolves to `data/runtime/live/parayabuck.sqlite3` in LIVE.
  - Enable `alerting.email` (B1; LIVE preflight requires it).
- **O2** — Env on the VM only: `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY`, `PARAYABUCK_ENABLE_LIVE=YES`, `PARAYABUCK_OPERATOR_APPROVAL_KEY` (a random value of at least 32 characters) and `PARAYABUCK_SMTP_USERNAME` / `PARAYABUCK_SMTP_PASSWORD`, file `chmod 600`. Never on the laptop. Steps: `docs/deployment.md` "LIVE runbook".
- **O3** — Start with an empty LIVE account: no QLD bought by hand and no open orders, otherwise reconciliation halts. Never trade QLD manually in that account.
- **O4** — Arm after the close (after about 16:45 ET) or before the open. The first start's catch-up trades immediately; during market hours that is a mid-session market fill.
- **O5** — Check on the live account (read-only, by the owner in the dashboard or with one `GET`):
  - `GET /v2/assets/QLD` → `fractionable: true`
  - account `multiplier` (1 below $2k)
  - W-8BEN status and expiry year
  - High-Yield Cash eligibility
- **O6** — Deposits and withdrawals only while the engine is stopped. After one, start a new risk epoch (`POST /api/safety/risk-epoch`) so the peak and daily-loss baselines reflect the new capital.
- **O7** — Fund by USD wire from a Turkish USD account; one deposit rather than many small ones (per-wire bank fees).
- **O8** — Run PAPER and LIVE in parallel only if B3 is done. Two engines must never share one key.

### Recommended improvements (ordered)

- **R1 — Gap-safe BUY cap** (about 0.5 day). **Status: closed 2026-10-12 (LIVE only).** `live.buy_gap_margin_pct` (default 0.03) caps a LIVE BUY batch at (min(`cash`, `non_marginable_buying_power`) − committed) / 1.03. PAPER sizing, the strategy digest and the backtest engine are unchanged (VT35/E2 golden tests unchanged), so the PAPER soak keeps its identity.
  - Cap buy notional at `min(cash, non_marginable_buying_power) − gap buffer`, with the buffer about 3% of order notional for QLD. This means a gap-up open never borrows.
  - The alternative is to size buys as `notional` market orders, which are still DAY-only.
- **R2 — Minimum trade notional for small accounts** (config, about 0.5 day including a backtest). **Status: open.**
  - Raise `execution.minimum_order_notional` to about $25–50, or the band to about 1 pp. Re-run the Stage B replay at $1k / $5k / $25k starting equity to confirm the CAGR cost is negligible.
  - Both values are in the live-approval digest, so re-authorize after any change.
- **R3 — Exit-SELL rejection escalation** (about 0.5 day). If a SELL that targets cash is rejected, send an immediate off-host alert (B1). Optionally retry once with a salted client id during the same session after a fresh reconcile. **Status: closed 2026-10-12.** Every rejected SELL (synchronous 4xx or a `rejected` trade update) raises `exit_sell_rejected`. The account loop retries it once at the next safe poll (engine running and committed, not warming up, no breaker, no failed reconciliation, no market entry halt, no open order in the symbol, newest decision only) with client id `<original>-retry1`, sized to min(remaining quantity, managed quantity still held and available). A rejected retry is escalated (`state=retry_rejected`) and never retried.
- **R4 — Open-fill quality check before considering OPG** (about 0.5 day analysis). **Status: open.**
  - Compare LIVE or PAPER fill prices with the official open over 20+ sessions.
  - Only if slippage is material: split orders into an OPG whole-share leg submitted after 19:00 ET (decision timing permitting) plus a DAY fractional remainder.
- **R5 — Cash-flow-aware risk baselines** (about 1 day). **Status: open** (O6 still applies). Read Alpaca account activities (`CSD`/`CSW`) to exclude deposits and withdrawals from `daily_pnl` and the peak, instead of relying on O6.
- **R6 — Dividend and fee booking** (about 0.5 day). **Status: open.** QLD distributions (net of 20% withholding) and the regulatory-fee debits arrive as account activities. They already flow through `equity`; add them to reporting only.

## 5. Open questions for the owner

1. **Funding size.** Below $2,000 the account stays at 1x (no margin borrowing possible), which is the simplest regime. Is $1k–$2k for the first months acceptable?
2. **Restart policy (B2).** Answered by the fix wave: manual re-arm stays the default; the owner may opt in to a standing authorization of up to 30 days.
3. **Alert channel (B1).** Answered: e-mail (opt-in, disabled until the owner sets it up).
4. **Run PAPER alongside LIVE?** B3 is done (separate keys and ledgers), but one host runs one engine; a parallel PAPER engine needs its own unit, config and checkout.
5. **Ask Alpaca support:**
   - Can the account be fixed at multiplier 1?
   - Does OPG need enablement?
   - Can a Turkish resident join High-Yield Cash?
   - What are the exact incoming and outgoing wire fees?
6. **Tax.** Turkish declaration of US-source gains and dividends (20% US withholding creditable?) is for the owner's accountant.
