# Final correctness report: Alpaca open-order pagination repair

**Decision:** `OPEN_ORDER_PAGINATION_REPAIR_ACCEPTED`
**Date:** 2026-09-22
**Branch:** `fix/alpaca-open-order-pagination-v1`

## Defect and repair

The installed `alpaca-py 0.43.5` `TradingClient.get_orders()` performs one
`GET /orders` request. An unbounded `OPEN` request omits `limit`, so Alpaca's
default page size of 50 silently omitted later working orders. Setting the
maximum page size of 500 alone would still omit order 501.

`AlpacaClient.get_open_orders()` now exhausts pages of 500 through the SDK's
authenticated `get()` method. It preserves supported request filters and
direction, uses exclusive `before_order_id` cursors for descending requests
and `after_order_id` for ascending requests, deduplicates by broker order ID,
and returns only after a short terminal page. Invalid IDs, stalled cursors,
DTO/API errors, and full timestamp-filtered pages raise instead of returning a
partial collection. Every production `OPEN` order reader now routes through
this named operation; bounded and non-OPEN SDK calls retain their prior path.

The 51-order regression reproduced the old behavior before the fix: the
default one-page request returned 50 and omitted the oldest open symbol. The
regression suite also proves completeness through 1,001 orders and the
fail-closed behaviors above. No broker API call or order placement was used.

## Proven production scope

Complete order enumeration feeds the account-wide symbol suppression in the
portfolio planner, the symbol-filtered same-symbol submission guard, protective
order checks, and broker/local reconciliation. These paths can observe orders
and client IDs omitted after the first page. This establishes a code-path risk;
it does not establish a live incident or realized duplicate. The repair does
not directly change `maximum_positions`, strategy configuration, reservation
rules, or account-capacity migration economics.

Page traversal is not a transactional broker snapshot. Orders may change while
pages are read. Existing startup reconciliation and the decision loop's
nominal 60-second reconciliation scheduling remain the observation paths for
subsequent state; this change adds no second read or repeated polling.

## Same-bug-class audit

The audit found one separate `POTENTIALLY_INCOMPLETE` path: FILL activity
pagination can stop on a missing or repeated page token after collecting a
partial history, then process it and possibly advance its watermark. This
remains a separate follow-up and was not changed here. Positions and assets
are `BOUNDED_BY_CONTRACT` for their current Alpaca endpoints; historical bars
are `PAGINATION_SAFE` for repository request windows because the SDK follows
continuation tokens and the repository splits requests above its 10,000-row
bound. See [the full same-bug-class audit](SAME_BUG_CLASS_AUDIT.md).

## Validation

- Focused pagination suite: **18 passed** on each of **3 consecutive runs**.
- Affected hardening, portfolio runtime, and release integration checks: **76
  passed**.
- Full repository suite: **1,156 passed, 1 skipped**, with two existing
  deprecation warnings.
- The full suite needed runner permission for existing tests that bind
  ephemeral `127.0.0.1` sockets. They use loopback only.
- `ruff check .`: passed.
- `ruff format --check .`: passed (251 files already formatted).
- `mypy src`: passed (133 source files).
- `git diff --check`: passed.
- No dependency lock or production strategy configuration changed.

## Account-capacity migration disposition

Migration v1 is archived as inconclusive. Its frozen specification predates
this defect, but neither the six-slot nor eight-slot arm ran and no economic
outcomes were generated. `maximum_positions` remains unchanged. After this
repair lands on main, the authorized next action is to re-freeze a new v2
specification from corrected main; this repair does not start that study.
