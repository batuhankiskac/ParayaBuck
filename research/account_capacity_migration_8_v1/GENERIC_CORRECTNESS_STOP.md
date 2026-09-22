# Migration stop: broker-wide open-order pagination

**Status:** The migration study stopped before any historical replay or economic output. This is the brief's explicit generic-production-correctness stop condition, not a cap-six/cap-eight result. No production file or setting was changed.

## Independently isolated defect

`AlpacaBroker.all_open_order_symbols()` promises broker-wide working-order claims, including manual orders, but issues exactly one `GetOrdersRequest(status=OPEN, nested=True)` with no `limit` or pagination (`src/parayabuck/execution/alpaca.py:546-557`). The SDK request built by the installed version has `limit=None`; Alpaca's [current Trading API order-list reference](https://docs.alpaca.markets/us/reference/getallorders-1) documents a default response limit of **50**, maximum **500**, newest-first default order, and pagination filters. The code neither requests the remaining pages nor detects that a page was truncated. The reconciliation path independently makes an unpaginated account-wide OPEN query (`src/parayabuck/execution/alpaca_reconciliation.py:524-527,612`).

In a deterministic 51-order fixture honoring the documented default page size, the current method requested `limit=None`, returned 50 symbols, and omitted the oldest working symbol. The fixture was run against the method with a fake `get_orders`; it made no broker request and does **not** assert that this account has ever had 51 open orders. The issue predates the migration branch: its source is unchanged from consolidated main `478b15bd7c56d75735de0d94f456a2b9b9b491e8`.

`PortfolioRuntime._effective_open_order_constraints()` adds the broker-wide symbols to its local nonterminal order claims (`src/parayabuck/portfolio/runtime_service.py:1496-1517`), then the overlay counts those claims against the shared account cap (`src/parayabuck/execution/portfolio.py:287-308`). A manual order on a distinct symbol outside the first page can be absent from this account-wide reservation, allowing the target decision to treat its slot as free. The submission gateway also performs a **symbol-filtered** open-order check; that narrows duplicate-order risk for the exact same symbol, but it does not restore the missing account-wide slot claim. This is a conditional safety defect, not evidence of a realized limit breach.

## Why the study stops here

The migration brief says to **stop the migration study and isolate a generic production correctness bug first**. The defect is independent of whether the configured cap is six or eight. Continuing to implement or run an eight-slot historical replay against an incomplete production open-order contract would make G7 parity and G6 operational assertions unsafe. The unfinished research adapter was discarded; no 6/8 historical run, count-denial result, NAV, or economic comparison was produced.

Separately, G6 remains unresolved: Alpaca publishes a [Trading API usage limit](https://alpaca.markets/support/usage-limit-api-calls) of 200 requests/minute/account, but an eight-position full replacement requires more requests than its 16 order submissions, and account-specific headroom plus concurrent reconciliation load were not verified. Public documentation reveals no eight-position count prohibition. This is an operational-evidence gap, not a reason to infer eight is safe or unsafe.

## Isolated next work

Address pagination as a separate generic broker-integration defect on a branch from current main. A targeted regression should exercise more than 50 and more than 500 open orders, pagination boundaries, duplicate timestamps/order IDs, nested orders, manual symbols, failures between pages, and conservative fail-closed behavior. Confirm the SDK's supported cursor semantics and rate impact against the current Alpaca API. After a reviewed fix is merged, rebase or recreate the migration study from the **new** production main and make a fresh pre-outcome freeze reflecting that code version. Then finish research-only shared-account parity and operational capability work before running the two frozen arms. No alternate cap or previously rejected strategy should be introduced.
