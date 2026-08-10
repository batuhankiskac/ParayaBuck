#!/usr/bin/env python3
"""PAPER execution lifecycle smoke.

Exercises the real broker/repository execution lifecycle against the Alpaca
PAPER endpoint:

    submit -> fill -> portfolio ownership -> restart -> target zero -> exit
    -> ownership release -> reconciliation

SCOPE: this validates the broker + repository execution lifecycle (order
submission, idempotency, ownership bookkeeping, restart persistence, exit and
reconciliation). It does NOT route through the scanner, strategy, risk
overlay or planner, so it is NOT a full strategy end-to-end test.

CLEANUP: failures are cleanup-safe. Smoke-created order ids and the
smoke-owned filled quantity are tracked; on failure the script best-effort
cancels ONLY smoke-created open orders, resolves their terminal state,
flattens ONLY the smoke-owned quantity (never unrelated/manual inventory),
reconciles the repository and prints a loud manual-cleanup instruction when
automatic cleanup is ambiguous.

This script is structurally incapable of targeting live credentials:

* the broker is constructed with ``paper=True`` (the Alpaca paper endpoint
  rejects live API keys with 401, so live keys fail the first account read);
* the configuration mode must be ``paper``;
* ``PARAYABUCK_ENABLE_LIVE`` must not be ``YES``;
* an explicit opt-in is required: ``PARAYABUCK_ALLOW_PAPER_SMOKE=YES``.

No live order can ever be placed by this script, and no CLI flag can change
that.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.alpaca import AlpacaBroker  # noqa: E402
from parayabuck.execution.ledger import ExecutionLedger  # noqa: E402
from parayabuck.execution.portfolio import (  # noqa: E402
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight  # noqa: E402
from parayabuck.portfolio.runtime_service import PortfolioStateRepository  # noqa: E402

SMOKE_SYMBOL = "AAPL"
SMOKE_SHARES = 1.0
FILL_TIMEOUT_SECONDS = 45.0
SMOKE_ORDER_PREFIX = "pbp-smoke-"


def _fail(message: str) -> int:
    print(f"PAPER SMOKE FAILED: {message}")
    return 1


def _require_guards(config) -> bool:
    if os.getenv("PARAYABUCK_ALLOW_PAPER_SMOKE") != "YES":
        print(
            "Paper smoke refused: PARAYABUCK_ALLOW_PAPER_SMOKE=YES is required "
            "(this is the explicit opt-in for placing PAPER orders)."
        )
        return False
    if config.runtime.mode.value != "paper":
        print(f"Paper smoke refused: runtime.mode must be paper (got {config.runtime.mode.value})")
        return False
    if os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES":
        print("Paper smoke refused: PARAYABUCK_ENABLE_LIVE=YES is set")
        return False
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        print("Paper smoke refused: ALPACA_API_KEY/ALPACA_SECRET_KEY are required")
        return False
    return True


def _wait_for_fill(broker: AlpacaBroker, client_order_id: str) -> tuple[float, float, str]:
    get_by_client = broker._client.get_order_by_client_id
    deadline = time.monotonic() + FILL_TIMEOUT_SECONDS
    last_status = "new"
    while time.monotonic() < deadline:
        order = get_by_client(client_order_id)
        status = str(getattr(order, "status", "")).lower().split(".")[-1]
        last_status = status
        if status == "filled":
            filled_qty = float(
                getattr(order, "filled_qty", 0) or getattr(order, "cumulative_qty", 0) or 0
            )
            avg_price = float(getattr(order, "filled_avg_price", 0) or 0)
            broker_order_id = str(getattr(order, "id", "") or "")
            if filled_qty <= 0 or avg_price <= 0:
                raise RuntimeError(f"Filled order lacks qty/price: {client_order_id}")
            return filled_qty, avg_price, broker_order_id
        if status in {"canceled", "cancelled", "rejected", "expired"}:
            raise RuntimeError(f"Order terminated without filling: {client_order_id} ({status})")
        time.sleep(0.5)
    raise RuntimeError(f"Fill timeout for {client_order_id}; last status={last_status}")


def _poll_terminal(broker: AlpacaBroker, client_order_id: str, timeout: float = 20.0) -> str:
    """Poll one order to a terminal state; returns the last observed status
    (which may be non-terminal when the timeout expires = ambiguous)."""
    get_by_client = broker._client.get_order_by_client_id
    deadline = time.monotonic() + timeout
    last_status = "new"
    while time.monotonic() < deadline:
        try:
            order = get_by_client(client_order_id)
        except Exception as exc:
            last_status = f"lookup_error:{exc}"
            time.sleep(0.5)
            continue
        status = str(getattr(order, "status", "")).lower().split(".")[-1]
        last_status = status
        if status in {"filled", "canceled", "cancelled", "rejected", "expired"}:
            return status
        time.sleep(0.5)
    return last_status


def _cancel_by_client_id(broker: AlpacaBroker, client_order_id: str) -> None:
    """Cancel one broker order by its client id (raises on failure)."""
    get_by_client = broker._client.get_order_by_client_id
    order = get_by_client(client_order_id)
    order_id = str(getattr(order, "id", "") or "")
    if not order_id:
        raise RuntimeError(f"order {client_order_id} has no broker id")
    broker._client.cancel_order_by_id(order_id)


def _book_fill(
    repo: PortfolioStateRepository,
    *,
    client_order_id: str,
    broker_order_id: str,
    filled_qty: float,
    avg_price: float,
    activity_id: str,
) -> None:
    booked = repo.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        status="fill",
        cumulative_filled_quantity=filled_qty,
        average_fill_price=avg_price,
        occurred_at=datetime.now(UTC),
        activity_id=activity_id,
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    if not booked:
        raise RuntimeError(f"Fill not booked (order missing from repository): {client_order_id}")


def _submit_and_wait(
    repo: PortfolioStateRepository,
    broker: AlpacaBroker,
    *,
    account_id: str,
    session_id: str,
    digest: str,
    side: PortfolioOrderSide,
    quantity: float,
    decision_timestamp: datetime,
    tag: str,
) -> tuple[float, float]:
    weights = (TargetWeight(SMOKE_SYMBOL, 0.01),) if side == PortfolioOrderSide.BUY else ()
    decision = TargetPortfolio(
        strategy_name="paper_smoke",
        strategy_version="1.0.0",
        decision_timestamp=decision_timestamp,
        weights=weights,
        cash_weight=1.0 - sum(weight.weight for weight in weights),
        universe_version="smoke",
    )
    decision_id = repo.record_decision(
        session_id,
        account_id,
        digest,
        decision,
        RiskOverlayDecision(decision, False, (), 0.0, 0.0),
        Market.EQUITY,
    )
    order = PortfolioOrderRequest(
        client_order_id=f"{SMOKE_ORDER_PREFIX}{tag}-{decision_id[:8]}",
        decision_id=decision_id,
        symbol=SMOKE_SYMBOL,
        market=Market.EQUITY,
        side=side,
        quantity=quantity,
        reference_price=1.0,
        decision_timestamp=decision_timestamp,
        strategy_name="paper_smoke",
        strategy_version="1.0.0",
        risk_reducing=side == PortfolioOrderSide.SELL,
    )
    repo.record_order(order, "submitting")
    broker_order_id = broker.submit_portfolio_order(order)
    repo.update_order(order.client_order_id, "submitted", broker_order_id)
    filled_qty, avg_price, _broker_order_id = _wait_for_fill(broker, order.client_order_id)
    _book_fill(
        repo,
        client_order_id=order.client_order_id,
        broker_order_id=broker_order_id,
        filled_qty=filled_qty,
        avg_price=avg_price,
        activity_id=f"smoke-{tag}-{datetime.now(UTC).timestamp()}",
    )
    return filled_qty, avg_price


def _manual_cleanup_instructions(account_id: str) -> None:
    print("=" * 72)
    print("PAPER SMOKE MANUAL CLEANUP REQUIRED")
    print("Automatic cleanup is ambiguous; inspect the PAPER account manually:")
    print(f"  account: ...{account_id[-4:] if account_id else 'unknown'}")
    print(f"  symbol : {SMOKE_SYMBOL} (PAPER environment only)")
    print(
        "  1. Cancel any remaining open PAPER orders for the smoke client ids "
        f"(prefix {SMOKE_ORDER_PREFIX})."
    )
    print(f"  2. If the PAPER position in {SMOKE_SYMBOL} is still open, sell it back to zero.")
    print("  3. Do NOT touch unrelated manual inventory or other symbols.")
    print("=" * 72)


def _cleanup(
    repo: PortfolioStateRepository,
    broker: AlpacaBroker,
    *,
    account_id: str,
    session_id: str,
    digest: str,
) -> None:
    """Best-effort cleanup of smoke-created orders/positions after a failure.

    1. cancel ONLY smoke-created open orders;
    2. resolve their terminal state (bounded poll);
    3. flatten ONLY the smoke-owned quantity (a SELL capped at the smoke net,
       never unrelated/manual inventory);
    4. reconcile the repository;
    5. print loud manual instructions when automatic cleanup is ambiguous.
    """
    print("PAPER SMOKE CLEANUP (best-effort):")
    ambiguous: list[str] = []

    def is_smoke(client_order_id: str) -> bool:
        return client_order_id.startswith(SMOKE_ORDER_PREFIX)

    # 1+2. cancel and resolve only smoke-created open orders.
    try:
        for order in repo.open_orders(account_id, Market.EQUITY):
            if not is_smoke(order.client_order_id):
                continue
            try:
                _cancel_by_client_id(broker, order.client_order_id)
                print(f"  canceled open smoke order {order.client_order_id}")
            except Exception as exc:
                print(f"  cancel failed for {order.client_order_id}: {exc}")
    except Exception as exc:
        print(f"  could not enumerate open orders for cleanup: {exc}")
        ambiguous.append("open_order_enumeration")
    try:
        for order in repo.open_orders(account_id, Market.EQUITY):
            if not is_smoke(order.client_order_id):
                continue
            terminal = _poll_terminal(broker, order.client_order_id)
            print(f"  smoke order {order.client_order_id} terminal={terminal}")
            if terminal not in {"filled", "canceled", "cancelled", "rejected", "expired"}:
                ambiguous.append(f"unresolved_order:{order.client_order_id}:{terminal}")
    except Exception as exc:
        print(f"  terminal-state resolution failed: {exc}")
        ambiguous.append("terminal_resolution")

    # 3. flatten ONLY the smoke-owned quantity. Smoke is the only managed
    # holder of the symbol (preflight guarantees a flat start), so the repo's
    # net owned quantity IS the smoke-owned quantity; the SELL is still capped
    # by the broker position so unrelated/manual inventory is never touched.
    try:
        smoke_owned = repo.owned_quantities(account_id).get(SMOKE_SYMBOL, 0.0)
        broker_position = broker._position_qty(SMOKE_SYMBOL)
        if smoke_owned > 1e-6:
            print(
                f"  smoke-owned quantity {smoke_owned:.4f}; broker position {broker_position:.4f}"
            )
            if broker_position < smoke_owned - 1e-6:
                ambiguous.append(f"broker_position_below_smoke_owned:{broker_position}")
                print(
                    "  WARNING: broker position is below the smoke-owned quantity; "
                    "automatic flatten would oversell, skipping."
                )
            else:
                flatten_qty = min(smoke_owned, broker_position)
                if flatten_qty > 0:
                    try:
                        _submit_and_wait(
                            repo,
                            broker,
                            account_id=account_id,
                            session_id=session_id,
                            digest=digest,
                            side=PortfolioOrderSide.SELL,
                            quantity=flatten_qty,
                            decision_timestamp=datetime.now(UTC),
                            tag="cleanup",
                        )
                        print(f"  flattened {flatten_qty:.4f} {SMOKE_SYMBOL}")
                    except Exception as exc:
                        print(f"  flatten failed: {exc}")
                        ambiguous.append("flatten_failed")
        else:
            print("  no smoke-owned quantity to flatten")
    except Exception as exc:
        print(f"  flatten assessment failed: {exc}")
        ambiguous.append("flatten_assessment")

    # 4. reconcile the repository (PAPER).
    try:
        broker.reconcile()
        print("  repository reconciled")
    except Exception as exc:
        print(f"  reconciliation failed: {exc}")
        ambiguous.append("reconcile_failed")

    if ambiguous:
        print("  ambiguous outcomes: " + "; ".join(ambiguous))
        _manual_cleanup_instructions(account_id)
    else:
        print("  automatic cleanup complete (no ambiguous outcomes)")


def main() -> int:
    config = load_config()
    if not _require_guards(config):
        return 1

    workdir = ROOT / "data" / "smoke"
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "paper_smoke.sqlite3"

    repo = PortfolioStateRepository(db_path)
    ledger = ExecutionLedger(str(workdir / "paper_smoke_ledger.sqlite3"))
    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    broker = AlpacaBroker(
        key,
        secret,
        paper=True,
        ledger=ledger,
        portfolio_owned_quantities=lambda: repo.owned_quantities(broker.account_id),
        portfolio_open_orders=lambda: repo.open_orders(broker.account_id),
        portfolio_fill_recovered=lambda *args: None,
        portfolio_flat_reconciled=lambda symbol, market: repo.clear_stale_ownership(
            broker.account_id, symbol, market
        ),
    )
    account_id = ""
    session_id = ""
    digest = "paper-smoke-v1"
    try:
        account = broker.account()
        account_id = broker.account_id or "unknown"
        print(
            f"paper account ...{broker.account_suffix}: equity={account.equity:.2f} "
            f"cash={account.cash:.2f}"
        )
        # The paper endpoint rejects live keys with 401 before we get here.
        assert not os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES"

        rules = broker.asset_trading_rules(SMOKE_SYMBOL, Market.EQUITY)
        print(f"trading rules {SMOKE_SYMBOL}: min_order_size={rules.minimum_order_size}")

        # The smoke's ownership math assumes a flat start for the symbol.
        preflight_held = [
            p for p in account.positions if not p.is_flat and p.symbol == SMOKE_SYMBOL
        ]
        if preflight_held:
            return _fail(
                f"smoke symbol {SMOKE_SYMBOL} is already held "
                f"({preflight_held[0].qty}); refusing to double the position"
            )

        session_id = repo.begin_session(account_id, digest, "paper")

        # 1. submit + fill (BUY)
        print(f"submitting BUY {SMOKE_SHARES} {SMOKE_SYMBOL} ...")
        buy_qty, buy_price = _submit_and_wait(
            repo,
            broker,
            account_id=account_id,
            session_id=session_id,
            digest=digest,
            side=PortfolioOrderSide.BUY,
            quantity=SMOKE_SHARES,
            decision_timestamp=datetime.now(UTC),
            tag="buy",
        )
        owned = repo.owned_quantities(account_id)
        print(f"BUY filled: qty={buy_qty} @ {buy_price:.2f}; owned={owned}")
        if abs(owned.get(SMOKE_SYMBOL, 0.0) - buy_qty) > 1e-6:
            return _fail(f"ownership after BUY mismatch: {owned}")

        # 2. restart: reopen the same SQLite and verify ownership persists
        repo.close()
        ledger.close()
        repo = PortfolioStateRepository(db_path)
        ledger = ExecutionLedger(str(workdir / "paper_smoke_ledger.sqlite3"))
        broker.ledger = ledger
        broker.portfolio_owned_quantities = lambda: repo.owned_quantities(broker.account_id)
        broker.portfolio_open_orders = lambda: repo.open_orders(broker.account_id)
        broker.portfolio_flat_reconciled = lambda symbol, market: repo.clear_stale_ownership(
            broker.account_id, symbol, market
        )
        restarted_owned = repo.owned_quantities(account_id)
        print(f"restart: owned={restarted_owned}")
        if abs(restarted_owned.get(SMOKE_SYMBOL, 0.0) - buy_qty) > 1e-6:
            return _fail(f"ownership lost across restart: {restarted_owned}")

        # 3. target zero / exit (SELL the full quantity)
        # Alpaca can briefly report a just-filled order as open; the runtime's
        # submit guard rejects a SELL while that transitional record exists,
        # so wait for the order book to settle before exiting.
        settle_deadline = time.monotonic() + 30.0
        while time.monotonic() < settle_deadline and broker._has_open_order(SMOKE_SYMBOL):
            time.sleep(0.5)
        if broker._has_open_order(SMOKE_SYMBOL):
            return _fail(
                f"open order record for {SMOKE_SYMBOL} did not settle; refusing to stack a SELL"
            )
        print(f"submitting SELL {buy_qty} {SMOKE_SYMBOL} ...")
        sell_qty, sell_price = _submit_and_wait(
            repo,
            broker,
            account_id=account_id,
            session_id=session_id,
            digest=digest,
            side=PortfolioOrderSide.SELL,
            quantity=buy_qty,
            decision_timestamp=datetime.now(UTC),
            tag="sell",
        )
        print(f"SELL filled: qty={sell_qty} @ {sell_price:.2f}")
        after_exit = repo.owned_quantities(account_id)
        print(f"after exit: owned={after_exit}")
        if SMOKE_SYMBOL in after_exit:
            return _fail(f"ownership not released after exit: {after_exit}")

        # 4. reconciliation: no drift, no anomalies, broker flat
        broker.reconcile()
        anomalies = repo.ownership_adjustments(account_id)

        account = broker.account()
        flat = not any(
            not p.is_flat and p.market == Market.EQUITY and p.symbol == SMOKE_SYMBOL
            for p in account.positions
        )
        print(f"reconcile: anomalies={len(anomalies)} flat={flat}")
        if anomalies:
            return _fail(f"reconciliation anomalies after clean exit: {anomalies}")
        if not flat:
            return _fail("broker still holds the smoke position after exit")

        repo.end_session(session_id, "completed")
        print(
            "PAPER SMOKE PASSED: submit -> fill -> ownership -> restart -> "
            "exit -> ownership release -> reconciliation"
        )
        return 0
    except Exception as exc:
        try:
            _cleanup(
                repo,
                broker,
                account_id=account_id,
                session_id=session_id,
                digest=digest,
            )
        except Exception as cleanup_exc:
            print(f"  cleanup itself failed: {cleanup_exc}")
            _manual_cleanup_instructions(account_id)
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            broker.close()
        except Exception:
            pass
        try:
            repo.close()
        except Exception:
            pass
        try:
            ledger.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
