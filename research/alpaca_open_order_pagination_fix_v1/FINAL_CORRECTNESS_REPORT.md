# Correctness decision

`OPEN_ORDER_PAGINATION_REPAIR_ACCEPTED`

# Starting main SHA

Local `main` and its `origin/main` tracking ref were both
`478b15bd7c56d75735de0d94f456a2b9b9b491e8`, matching the requested starting
SHA. A live `git ls-remote` check could not resolve `github.com` in this
environment, so the tracking ref was available but the remote was not
independently queried.

# Migration-v1 closure

## Archived tag

`archive/research/account-capacity-migration-8-v1-inconclusive-2026-09`
points to `ede3b3dfadfd5f3f0bdef040a14d20365c8df469`. Its annotated message
records `ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE`, the reason, and the
re-freeze requirement.

## No migration outcomes confirmation

The frozen specification `3f7d0bc2b443f66b95d67ccb0503f1a0567799f5` is an
ancestor of the stopped commit. The stopped commit adds no six-slot or
eight-slot economics: neither arm ran, and no control/treatment outputs were
generated. The v1 spec remains provenance only, not executable authority.

## Branch deletion

The stopped branch is preserved locally until its archive tag and durable
main status have been pushed and verified. The fix branch is integrated into
local main. Branch deletion is reported in the task completion message after
remote publication is verified; a failed or unverified push must not be
treated as a completed deletion gate.

## Durable status

`docs/research/account_capacity_migration_v1_closure.md` records the
inconclusive result, archive tag, no-outcome proof, and re-freeze requirement.
`docs/research/research_register.md` links the durable register entry. Both
were merged to local main before the fix branch was created.

# Bug reproduction

Before the production change, the deterministic 51-order reproduction called
the one-page SDK path and got 50 orders; the oldest working order was absent.
The synthetic API caps responses at 500, so setting `limit=500` still returned
only 500 of 501. No account data or broker call was used.

# Exact affected production path

`GET /v2/orders` → `AlpacaClient.get_open_orders()` →
`AlpacaBroker._get_open_orders()` → broker-wide planning constraints,
same-symbol submission guard, protective-order checks, and reconciliation.
All current production order-list reads ask for `status=OPEN`; this audit
found no current closed-history or `status=all` reader. See
[the call graph](OPEN_ORDER_CALL_GRAPH.md).

# Downstream risk

The planner unions broker-visible order symbols with local nonterminal orders
and suppresses target planning for those symbols. A missing old broker order
could bypass that suppression; the symbol-filtered submission guard is a
separate defense. Protection and reconciliation also consume the open-order
list. These are code-path risks, not evidence of a live incident or realized
duplicate. The omission does not directly change `maximum_positions` or
migration economics.

# Alpaca API contract

- Endpoint: `GET /v2/orders`.
- Default `limit`: 50; documented maximum: 500.
- `direction` orders by submission time, ascending or descending.
- `before_order_id` and `after_order_id` are exclusive ID cursors and cannot
  be combined with timestamp `after` or `until` filters.
- `nested` and symbol/side/asset filters are retained where present.
- Descending traversal uses `before_order_id`; ascending traversal uses
  `after_order_id`, setting each next cursor to the last response order ID.

Verified against the [official Alpaca order API reference](https://docs.alpaca.markets/us/reference/getallorders-1)
on 2026-09-22.

# Installed alpaca-py capability

Installed and locked `alpaca-py` is 0.43.5. Its `GetOrdersRequest` has no
`before_order_id` or `after_order_id`; `TradingClient.get_orders()` performs
one request and validates one page. The authenticated lower-level
`TradingClient.get()` supports the API cursor parameters, so no SDK upgrade or
site-package patch was needed. See [the SDK audit](SDK_CAPABILITY_AUDIT.md).

# Chosen implementation

The named exhaustive adapter method requests 500 rows, preserves supported
filters and ordering, concatenates in API order, deduplicates only by broker
order ID, and stops only on a short page. Missing IDs, stalled/repeated
cursors, timestamp-filtered full pages, malformed responses, and API errors
raise; callers never receive an earlier partial prefix as a complete list.
All production `OPEN` readers now call through this adapter method.

# Why `limit=500` alone is insufficient

500 is the API's maximum page size, not an unbounded query. With 501 open
orders, a one-page request still omits one order. Tests prove retrieval at
501 and 1,001 as well as at both page boundaries.

# Pagination invariants

- Every synthetic open order is returned exactly once.
- The broker order ID is the pagination and deduplication identity.
- Cursor direction and all caller filters are stable across pages.
- A short terminal page is required to prove exhaustion.
- A later-page error or no-progress cursor fails closed.
- Open status and nested-order semantics are preserved.
- Existing client-order-ID, strategy ownership, reservation, and order
  submission semantics are unchanged.

# Regression tests

Boundary counts: **0, 1, 49, 50, 51, 499, 500, 501, and 1,001**. Additional
cases cover a 500-row one-page limit, ascending and descending cursor choice,
filter preservation, overlap deduplication, repeated pages/cursors, missing
IDs, timestamp-filtered full pages, and second-page failure. The pre-fix
51-order omission was reproduced before implementation. See
[the regression matrix](REGRESSION_TEST_MATRIX.md).

# Concurrent broker-state behavior

The API does not provide a transactional snapshot across pages. An order may
fill or be canceled during traversal; a new order can appear before the
descending cursor after page one. The method returns the collection exposed
by that cursor walk or raises. Existing startup reconciliation and nominal
60-second decision-loop reconciliation scheduling observe later state. No
second verification read or repeated polling was added.

# Same-bug-class audit

Orders are `PAGINATION_SAFE` after this repair. FILL activity pagination is a
separate `POTENTIALLY_INCOMPLETE` finding: a missing/repeated token can stop
after partial activity history, which may still be processed and advance its
watermark. It was not changed. Positions and assets are `BOUNDED_BY_CONTRACT`
for their current endpoints. Historical bars are `PAGINATION_SAFE` for
repository request windows because the SDK follows continuation tokens and
the repository splits requests above 10,000 expected rows. Details and source
references are in [the audit](SAME_BUG_CLASS_AUDIT.md).

# Production files changed

- `src/parayabuck/execution/alpaca_client.py`
- `src/parayabuck/execution/alpaca.py`
- `src/parayabuck/execution/alpaca_protection.py`
- `src/parayabuck/execution/alpaca_reconciliation.py`
- `src/parayabuck/execution/fake_client.py` (test fake's authenticated GET
  surface)
- Test doubles in `tests/test_hardening.py`,
  `tests/test_portfolio_runtime.py`, and
  `tests/test_release_v2_integration.py` now model that same GET surface.
- `tests/execution/test_alpaca_order_pagination.py`
- Evidence files in this directory.

# Dependency changes

None. No dependency files changed.

# Validation

- Focused pagination suite: **18 passed** on each of **3 consecutive runs**.
- Affected hardening, portfolio runtime, and release integration checks:
  **76 passed**.
- Full repository suite: **1,156 passed, 1 skipped**, two existing
  deprecation warnings.
- Existing full-suite tests bind ephemeral `127.0.0.1` sockets. The runner
  needed permission for those loopback binds; no external broker API was
  contacted.
- `ruff check .`: passed.
- `ruff format --check .`: passed; 251 files already formatted.
- `mypy src`: passed; 133 source files.
- Decision JSON validation: passed.
- `git diff --check`: passed.
- Credential-literal scan: no matches in changed lines or new tests/evidence
  artifacts. A broader scan of an existing test file matched an untouched
  fixture line; that line is outside the patch.

# Production-state confirmation

V3, crypto strategy, `maximum_positions`, equity/crypto sleeves, gross limits,
PAPER/LIVE settings, and order submission semantics are unchanged. There are
no migration-specific code changes and no broker API or order-placement calls.

# Final main SHA

The post-integration local main SHA is reported in the task completion
message. It includes the correctness report itself, so that final commit ID
cannot be embedded self-referentially in this committed report.

# Exact next action

`RE_FREEZE_ACCOUNT_CAPACITY_MIGRATION_8_V2_FROM_CORRECTED_MAIN`

Do not start v2 as part of this repair.

# Files

- [Bug reproduction](BUG_REPRODUCTION.md)
- [Alpaca API contract](ALPACA_API_CONTRACT.md)
- [SDK capability audit](SDK_CAPABILITY_AUDIT.md)
- [Open-order call graph](OPEN_ORDER_CALL_GRAPH.md)
- [Pagination design](PAGINATION_DESIGN.md)
- [Regression test matrix](REGRESSION_TEST_MATRIX.md)
- [Same-bug-class audit](SAME_BUG_CLASS_AUDIT.md)
- [Decision JSON](CORRECTNESS_DECISION.json)
- This final report
