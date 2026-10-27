"""Account snapshot and ownership classification.

Extracted from :mod:`parayabuck.execution.alpaca` (Mission 8 — Finish the Boundary).

Responsibility:
- Fetch ``get_account`` + ``get_all_positions`` → :class:`AccountSnapshot`.
- Fail-closed blocked checks: ``account_blocked`` / ``trading_blocked`` /
  ``trade_suspended_by_user`` → ``RuntimeError`` (never tradable).
- Market classification via ``asset_class`` / ``is_crypto``: a foreign
  crypto holding in the shared account is classified ``Market.CRYPTO`` so it
  is recognized and left alone (the bot trades equities only).
- Ownership: ``managed`` / ``mixed`` / ``external`` with
  ``managed_qty = min(|raw|, owned)``, ``external_qty``, ``qty_available``,
  shortfall with SELL exemption. A short on a symbol the bot trades (or
  owns) is never inventory: it is ownership drift. A share count that moved
  without a fill of ours (stock split, foreign trade on the shared key) is
  first offered to ``broker.portfolio_ownership_resolved``, which records an
  audited adjustment; whatever it cannot prove keeps the fail-closed drift
  error.
- Portfolio-owned quantity comes from ``PortfolioStateRepository`` through
  ``broker.portfolio_owned_quantities``.
- Foreign positions the bot cannot claim tolerate a missing quote or entry
  price; a position the bot trades never does.
- Snapshot cache: one immutable ``(snapshot, monotonic_ts)`` tuple.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alerting import emit_execution_alert
from parayabuck.execution.alpaca_models import EmptyPositionsReadError, OwnershipDriftError
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.ownership_rebase import QUANTITY_TOLERANCE
from parayabuck.utils import is_crypto
from parayabuck.utils.objects import field_value

log = get_logger("parayabuck.execution.alpaca_account")

__all__ = ["AccountService", "flat_broker_reconcile_scope", "quantities_agree"]

# Set only by the reconcile path (see ``flat_broker_reconcile_scope``): the
# reconciler must read a flat broker to clear stale ownership. Every other
# snapshot treats an empty position read while the bot owns inventory as a
# broker fault, not as a flat account.
_FLAT_BROKER_ALLOWED: ContextVar[bool] = ContextVar("parayabuck_flat_broker_allowed", default=False)

# Alpaca reports quantities to 9 decimals: this absolute floor absorbs that
# noise for small holdings. Drift detection and the reconcile MATCH bucket
# share ``quantities_agree`` so a share count is classified the same way
# everywhere.
QUANTITY_MATCH_FLOOR = 1e-8

_NEW_YORK = ZoneInfo("America/New_York")
# Same-day external cash movements: deposits, withdrawals and journals.
EXTERNAL_CASH_FLOW_ACTIVITY_TYPES: tuple[str, ...] = ("CSD", "CSW", "JNLC")
_ACTIVITIES_PATH = "/account/activities"
_CASH_FLOW_PAGE_SIZE = 100
# Activities are re-read at most this often; a failed read is retried next poll.
_CASH_FLOW_CACHE_SECONDS = 60.0


def quantities_agree(owned: float, broker: float) -> bool:
    """Whether a portfolio-owned and a broker share count are the same position.

    One band for ownership drift (``AccountService.account``) and for the
    reconcile MATCH bucket, equal to the ownership resolver's relative band,
    so a difference is either drift or a MATCH everywhere, never both.
    """
    return abs(owned - broker) <= max(QUANTITY_MATCH_FLOOR, owned * QUANTITY_TOLERANCE)


def bot_tradable_symbols(broker: Any) -> frozenset[str] | None:
    """Canonical symbols the bot trades, or None when no provider says.

    The brokerage account is shared, so the bot's own tradable universe is
    the boundary between its positions and someone else's. None (no
    provider, or an unreadable one) is fail-closed: every unowned equity
    position then stays drift, exactly as before.
    """
    provider = getattr(broker, "bot_tradable_symbols", None)
    if provider is None:
        return None
    try:
        return frozenset(canonical_symbol(str(symbol)) for symbol in provider() or ())
    except Exception:
        log.warning("Bot tradable symbols unavailable; equity positions stay fail-closed", exc_info=True)
        return None


def _trading_day(now: datetime) -> date:
    """The New York calendar date a poll belongs to."""
    return now.astimezone(_NEW_YORK).date()


def _account_status(account: Any) -> str | None:
    """Upper-cased account status, or None when the broker reports none."""
    raw = field_value(account, "status", None)
    if raw is None:
        return None
    return str(getattr(raw, "value", raw)).strip().upper()


def _read_same_day_external_flows(client: Any, trading_day: date) -> float:
    """Net external cash moved into the account on ``trading_day``.

    Deposits count positive, withdrawals negative, journals as reported.
    Raises on any malformed row or an unfinished page so the caller keeps the
    unadjusted behaviour instead of trusting a partial sum.
    """
    total = 0.0
    seen_tokens: set[str] = set()
    page_token: str | None = None
    while True:
        query: dict[str, Any] = {
            "activity_types": ",".join(EXTERNAL_CASH_FLOW_ACTIVITY_TYPES),
            # ``after`` is exclusive on the date: yesterday includes today.
            "after": (trading_day - timedelta(days=1)).isoformat(),
            "direction": "asc",
            "page_size": _CASH_FLOW_PAGE_SIZE,
        }
        if page_token is not None:
            query["page_token"] = page_token
        page = client.get(_ACTIVITIES_PATH, query)
        if not isinstance(page, list):
            raise ValueError("Alpaca activities response is not a list")
        for row in page:
            if str(field_value(row, "date", "") or "")[:10] != trading_day.isoformat():
                continue
            activity_type = str(field_value(row, "activity_type", "") or "").upper()
            raw_amount = field_value(row, "net_amount", None)
            amount = float(raw_amount) if raw_amount is not None else math.nan
            if not math.isfinite(amount):
                raise ValueError(f"Alpaca {activity_type or 'cash'} activity has a non-finite amount")
            if activity_type == "CSD":
                total += abs(amount)
            elif activity_type == "CSW":
                total -= abs(amount)
            elif activity_type == "JNLC":
                total += amount
        if len(page) < _CASH_FLOW_PAGE_SIZE:
            return total
        token = str(field_value(page[-1], "id", "") or "") if page else ""
        if not token or token in seen_tokens:
            raise ValueError("Alpaca activity pagination did not advance")
        seen_tokens.add(token)
        page_token = token


@contextmanager
def flat_broker_reconcile_scope() -> Iterator[None]:
    """Allow ``AccountService.account`` to accept a flat broker while the
    bot still owns inventory, for the duration of the reconcile call."""
    token = _FLAT_BROKER_ALLOWED.set(True)
    try:
        yield
    finally:
        _FLAT_BROKER_ALLOWED.reset(token)


class AccountService:
    """Account/ownership service — production implementation.

    The service holds a reference to the owning :class:`AlpacaBroker`
    facade (typed as ``Any`` to avoid a circular import). All broker
    state (``_client``, ``ledger``, ``portfolio_owned_quantities``,
    ``_account_id``, ``_risk_peak``) is accessed through that reference
    so ``broker._client = fake`` in tests is observed immediately.
    """

    def __init__(self, broker: Any) -> None:
        self._broker = broker
        # Peak sidecar write failed: retry on the next poll even if the peak
        # itself did not move again.
        self._peak_save_pending = False
        # Last previous-close equity seen with its New York trading day; a
        # missing ``last_equity`` on the same day reuses it instead of flat.
        self._last_equity_seen: tuple[date, float] | None = None
        # (day, net external flow, monotonic read time) of the last good read.
        self._cash_flow_cache: tuple[date, float, float] | None = None

    def _same_day_external_flows(self, client: Any, trading_day: date) -> float:
        """Net same-day external cash flow; 0.0 (current behaviour) on failure."""
        cached = self._cash_flow_cache
        now = time.monotonic()
        if cached is not None and cached[0] == trading_day and now - cached[2] <= _CASH_FLOW_CACHE_SECONDS:
            return cached[1]
        try:
            flow = _read_same_day_external_flows(client, trading_day)
        except Exception as exc:
            log.warning(
                "Cash-flow activities unavailable; day P&L and peak are not adjusted this poll: %s",
                exc,
            )
            return 0.0
        self._cash_flow_cache = (trading_day, flow, now)
        return flow

    # -- blocked helpers -------------------------------------------------

    @staticmethod
    def is_account_blocked(account: Any) -> bool:
        from parayabuck.utils.objects import field_value

        return bool(
            field_value(account, "account_blocked", False)
            or field_value(account, "trading_blocked", False)
            or field_value(account, "trade_suspended_by_user", False)
        )

    @staticmethod
    def classify_market(asset: Any) -> Market:
        """CRYPTO when the asset class says so *or* the symbol says so.

        Alpaca populates ``asset_class`` inconsistently, so a crypto pair
        arriving as ``''`` or ``'us_equity'`` must still be recognized; a
        misread market makes ``canonical_symbol`` either raise or return the
        wrong ownership key.
        """
        raw_class = getattr(asset, "asset_class", None)
        if raw_class is not None:
            try:
                if "crypto" in str(raw_class).lower():
                    return Market.CRYPTO
            except Exception:
                pass
        try:
            return Market.CRYPTO if is_crypto(str(getattr(asset, "symbol", ""))) else Market.EQUITY
        except Exception:
            return Market.EQUITY

    # -- drift helpers (used by account) --------------------------------

    def _portfolio_open_order_map(self) -> dict[str, list[object]]:
        provider = getattr(self._broker, "portfolio_open_orders", None)
        if provider is None:
            return {}
        grouped: dict[str, list[object]] = {}
        for order in provider() or ():
            symbol = str(getattr(order, "symbol", ""))
            if not symbol or str(getattr(order, "side", "")).lower() != "sell":
                continue
            market = getattr(order, "market", None)
            canonical = canonical_symbol(symbol, market)
            grouped.setdefault(canonical, []).append(order)
        return grouped

    @staticmethod
    def _open_order_explains_drift(
        canonical: str,
        owned_qty: float,
        raw_qty: float,
        market: Market,
        orders_by_symbol: dict[str, list[object]],
    ) -> bool:
        shortfall = max(0.0, owned_qty - abs(raw_qty))
        if shortfall <= 1e-12:
            return False
        for order in orders_by_symbol.get(canonical, ()):
            order_market = getattr(order, "market", None)
            if order_market is not None and order_market != market:
                continue
            quantity = float(getattr(order, "quantity", 0.0) or 0.0)
            filled = float(getattr(order, "cumulative_filled_quantity", 0.0) or 0.0)
            remaining = max(0.0, quantity - filled)
            if remaining >= shortfall - max(1e-9, shortfall * 1e-8):
                return True
        return False

    def _resolve_broker_quantity(
        self,
        canonical: str,
        market: Market,
        broker_qty: float,
        raw: Any,
        owned_before: float,
    ) -> float | None:
        """Rebase portfolio ownership onto a differing broker share count.

        A stock split, or another consumer of the shared Alpaca key, moves the
        broker's shares without a fill of ours. While ownership disagrees,
        every decision (exits included) is dropped and no safety reset can
        succeed, because a clean reconcile is impossible. The repository
        records the audited adjustment and refuses anything it cannot prove;
        a refusal leaves the fail-closed drift path in place. Returns the
        owned quantity after the rebase, or ``None`` when it was declined.
        """
        resolver = getattr(self._broker, "portfolio_ownership_resolved", None)
        if resolver is None:
            return None
        average_entry = getattr(raw, "avg_entry_price", None)
        resolved = resolver(
            canonical,
            market,
            broker_qty,
            float(average_entry) if average_entry is not None else None,
        )
        if resolved is None:
            return None
        self._alert_ownership_rebase(canonical, market, owned_before, broker_qty)
        return float(resolved)

    @staticmethod
    def _alert_ownership_rebase(canonical: str, market: Market, owned_before: float, broker_qty: float) -> None:
        """One alert per rebase episode; the audit row is the durable record."""
        emit_execution_alert(
            "paper_incident",
            message=(
                f"Ownership rebased onto the broker share count for {canonical}: "
                f"owned {owned_before:g} -> broker {broker_qty:g}. Verify the corporate "
                "action or the foreign trade on the broker; the adjustment is listed "
                "as a reconciliation anomaly."
            ),
            category="ownership_rebased",
            incident_id=f"ownership_rebased:{market.value}:{canonical}:{datetime.now(UTC):%Y-%m-%d}",
            symbol=canonical,
            market=market.value,
            previous_owned_quantity=owned_before,
            broker_quantity=broker_qty,
        )

    # -- snapshot cache --------------------------------------------------

    def snapshot_if_fresh(self, max_age_seconds: float) -> AccountSnapshot | None:
        cached = getattr(self._broker, "_snapshot_cache", None)
        if cached is None:
            return None
        snapshot, cached_at = cached
        if time.monotonic() - cached_at <= max_age_seconds:
            return snapshot  # type: ignore[no-any-return]
        return None

    @staticmethod
    def _price(value: Any, *, symbol: str, tolerate_missing: bool) -> float:
        """A finite broker mark; a foreign position reads a bad one as ``0.0``.

        A bot-traded symbol fails closed on a missing, unparseable or
        non-finite mark (same contract as non-finite equity/cash) instead of
        letting NaN reach the planner and halt the decision there.
        """
        if value is None:
            if tolerate_missing:
                return 0.0
            raise ValueError(f"Broker position {symbol} has no price")
        try:
            price = float(value)
        except (TypeError, ValueError):
            price = math.nan
        if math.isfinite(price):
            return price
        if tolerate_missing:
            return 0.0
        raise ValueError(f"Broker position {symbol} has a non-finite price; snapshot unusable, entries refused")

    # -- main entry ------------------------------------------------------

    def account(self) -> AccountSnapshot:
        broker = self._broker
        # Access via property so fake injection is observed
        client = broker._client
        account: Any = client.get_account()
        # Bind account id. The id alone keys ownership and the peak sidecar:
        # the account number is never a substitute (fail closed).
        account_id = str(getattr(account, "id", "") or "").strip()
        if not account_id:
            raise ValueError("Broker account has no id; snapshot refused (no account_number fallback for the peak key)")
        broker._account_id = account_id
        # The human account number ("PA..." on paper) classifies the account
        # environment for the PAPER/LIVE start check.
        broker._account_number = str(getattr(account, "account_number", "") or "")
        if self.is_account_blocked(account):
            raise RuntimeError("Alpaca account is blocked from trading")
        # Only an ACTIVE account may trade. A status the broker does not report
        # is not refused here (the field is optional in the test doubles).
        status = _account_status(account)
        if status is not None and status != "ACTIVE":
            raise RuntimeError(f"Alpaca account status is {status}; snapshot refused until the account is ACTIVE")
        trading_day = _trading_day(datetime.now(UTC))
        try:
            equity = float(account.equity)
            raw_previous = getattr(account, "last_equity", None)
            # Alpaca types ``last_equity`` Optional. Anything present must
            # parse; a missing one is handled below, never read as flat.
            previous_equity = None if raw_previous is None else float(raw_previous)
            raw_cash = getattr(account, "cash", None)
            if raw_cash is None:
                raise ValueError("Broker account has no cash value")
            cash = float(raw_cash)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Broker account values are not numeric: {exc}") from exc
        if (
            not math.isfinite(equity)
            or (previous_equity is not None and not math.isfinite(previous_equity))
            or not math.isfinite(cash)
        ):
            raise ValueError("Broker account values are non-finite; snapshot unusable, entries refused")
        if previous_equity is None:
            # A missing previous close reads as flat only if no baseline is
            # known: reuse this trading day's value seen earlier, else refuse.
            seen = self._last_equity_seen
            if seen is None or seen[0] != trading_day:
                emit_execution_alert(
                    "paper_incident",
                    message="Broker last_equity is missing and no baseline for today is known; day P&L cannot be measured, entries refused",
                    category="last_equity_missing",
                    incident_id=f"last_equity_missing:{trading_day:%Y-%m-%d}",
                )
                raise ValueError("Broker account last_equity is missing; day P&L unknown, snapshot refused")
            previous_equity = seen[1]
        else:
            self._last_equity_seen = (trading_day, previous_equity)
        # Same-day external cash moves are not trading P&L. Off by default:
        # the flag lives in ``risk.adjust_for_cash_flows`` and the runtime
        # copies it onto the broker.
        cash_flows = 0.0
        if getattr(broker, "adjust_for_cash_flows", False) is True:
            cash_flows = self._same_day_external_flows(client, trading_day)
        # Optional: only the LIVE gap-safe BUY cap reads it; an absent or
        # unparsable value leaves that cap on cash alone.
        non_marginable_buying_power: float | None = None
        try:
            raw_nmbp = getattr(account, "non_marginable_buying_power", None)
            if raw_nmbp is not None:
                parsed_nmbp = float(raw_nmbp)
                if math.isfinite(parsed_nmbp):
                    non_marginable_buying_power = parsed_nmbp
        except (TypeError, ValueError):
            non_marginable_buying_power = None
        positions: list[PositionSnapshot] = []
        ledger = getattr(broker, "ledger", None)
        owned_quantities: dict[str, float] = {}
        portfolio_owned_provider = getattr(broker, "portfolio_owned_quantities", None)
        if portfolio_owned_provider is not None:
            owned_quantities = portfolio_owned_provider() or {}
        tradable_symbols = bot_tradable_symbols(self._broker)
        unrealized = 0.0
        intraday_unrealized = 0.0
        raw_positions: Any = client.get_all_positions()
        if not raw_positions:
            held_by_bot = sorted(symbol for symbol, qty in owned_quantities.items() if float(qty) > 1e-12)
            if held_by_bot:
                # An empty read while the bot's own ledger holds inventory is
                # never a flat account on its own: a second read is required,
                # inside the reconcile scope too. Only two empty reads may let
                # reconcile see a flat broker and clear stale ownership.
                raw_positions = client.get_all_positions()
                if not raw_positions and not _FLAT_BROKER_ALLOWED.get():
                    raise EmptyPositionsReadError(
                        f"Broker reported no positions while the portfolio still owns {held_by_bot}; "
                        "snapshot refused until a reconcile confirms the account is flat"
                    )
        raw_positions = raw_positions or []
        drift_candidates: list[tuple[str, float, float, Market]] = []
        seen_position_keys: set[tuple[Market, str]] = set()
        open_sell_orders: dict[str, list[object]] | None = None

        def sell_orders() -> dict[str, list[object]]:
            """Our working SELL orders, read at most once per snapshot."""
            nonlocal open_sell_orders
            if open_sell_orders is None:
                open_sell_orders = self._portfolio_open_order_map()
            return open_sell_orders

        for raw in raw_positions:
            market = self.classify_market(raw)
            canonical = canonical_symbol(str(raw.symbol), market)
            total_owned = float(owned_quantities.get(canonical, 0.0))
            bot_symbol = total_owned > 1e-12 or (
                market == Market.EQUITY and tradable_symbols is not None and canonical in tradable_symbols
            )
            try:
                position_unrealized = float(raw.unrealized_pl or 0)
                position_intraday = float(raw.unrealized_intraday_pl or 0)
            except (TypeError, ValueError) as exc:
                if bot_symbol:
                    raise ValueError(f"Broker position {canonical} PnL is not numeric: {exc}") from exc
                # A foreign position's PnL is informational: one bad field
                # must not refuse the snapshot (and so block the bot's exits).
                log.warning("Foreign position %s has an unparseable PnL; not counted: %s", canonical, exc)
                position_unrealized = position_intraday = math.nan
            if not math.isfinite(position_unrealized) or not math.isfinite(position_intraday):
                if bot_symbol:
                    # A NaN PnL would be persisted into risk_state and served to
                    # strict-JSON readers: fail closed like non-finite equity/cash.
                    raise ValueError(
                        f"Broker position {canonical} PnL is non-finite; snapshot unusable, entries refused"
                    )
                position_unrealized = position_intraday = 0.0
            unrealized += position_unrealized
            intraday_unrealized += position_intraday
            position_key = (market, canonical)
            if position_key in seen_position_keys:
                raise OwnershipDriftError(
                    f"Duplicate broker position for {canonical} ({market.value}); "
                    "reconciliation is required before trading resumes",
                    symbols=(canonical,),
                )
            seen_position_keys.add(position_key)
            raw_qty = float(raw.qty)
            broker_qty = abs(raw_qty)
            if raw_qty < -1e-12 and bot_symbol:
                # The bot is long-only: a short on a symbol it trades is
                # foreign activity on the shared key. Reading it as owned
                # inventory would make a "cash" target BUY to cover it.
                raise OwnershipDriftError(
                    f"Ownership drift for {canonical}: the broker reports a short position "
                    f"qty={raw_qty} on a bot-traded symbol (portfolio owned qty={total_owned}). "
                    "Close the foreign short at the broker; reconciliation is required before trading resumes",
                    symbols=(canonical,),
                )
            current_price = self._price(raw.current_price, symbol=canonical, tolerate_missing=not bot_symbol)
            average_price = self._price(raw.avg_entry_price, symbol=canonical, tolerate_missing=not bot_symbol)
            if not bot_symbol and current_price <= 0.0:
                # A foreign position with no usable mark is valued at its cost
                # basis: zero would understate the account's gross exposure.
                current_price = average_price
            if total_owned > 1e-12 and broker_qty > 1e-12 and not quantities_agree(total_owned, broker_qty):
                # The share count moved without a fill of ours: a corporate
                # action, or another consumer of the shared Alpaca key. A
                # SELL of ours still working at the broker explains a
                # shortfall and keeps ownership until it completes.
                explained = broker_qty < total_owned and self._open_order_explains_drift(
                    canonical, total_owned, broker_qty, market, sell_orders()
                )
                if not explained:
                    resolved = self._resolve_broker_quantity(canonical, market, broker_qty, raw, total_owned)
                    if resolved is not None:
                        total_owned = resolved
                    elif broker_qty < total_owned:
                        drift_candidates.append((canonical, total_owned, raw_qty, market))
            managed_qty = min(broker_qty, total_owned)
            external_qty = broker_qty - managed_qty
            ownership = (
                "managed"
                if managed_qty > 1e-12 and external_qty <= 1e-12
                else "mixed"
                if managed_qty > 1e-12
                else "external"
            )
            raw_available = getattr(raw, "qty_available", None)
            if raw_available is None:
                qty_available: float | None = None
            else:
                try:
                    parsed_available = float(raw_available)
                except (TypeError, ValueError):
                    parsed_available = math.nan
                # Non-finite or negative availability cannot size an exit: fall
                # back to the full position quantity so the SELL is never
                # silently suppressed (the planner clamps negatives to zero).
                qty_available = parsed_available if math.isfinite(parsed_available) and parsed_available >= 0 else None
            positions.append(
                PositionSnapshot(
                    symbol=canonical,
                    qty=raw_qty,
                    avg_price=average_price,
                    current_price=current_price,
                    market=market,
                    qty_available=qty_available,
                    ownership=ownership,
                    managed_qty=managed_qty,
                )
            )
        if raw_positions and not _FLAT_BROKER_ALLOWED.get():
            # An owned symbol the broker no longer lists, while it lists other
            # positions, is a full-position shortfall (broker quantity 0). It is
            # judged like a partial one, before any decision: left out, the
            # snapshot would read "no holding" and the planner would buy the
            # whole target again. Reconcile keeps its own classification of it.
            for owned_symbol, owned_amount in owned_quantities.items():
                owned_value = float(owned_amount)
                if owned_value <= 1e-12:
                    continue
                owned_market = Market.CRYPTO if is_crypto(owned_symbol) else Market.EQUITY
                owned_canonical = canonical_symbol(owned_symbol, owned_market)
                if (owned_market, owned_canonical) not in seen_position_keys:
                    drift_candidates.append((owned_canonical, owned_value, 0.0, owned_market))
        # Per-symbol drift lock (runtime.per_symbol_drift_lock, default off): an
        # unexplained quantity shortfall is collected instead of raised, so the
        # snapshot still builds and the other symbols keep trading. The runtime
        # locks exactly these symbols and unlocks them once the broker again
        # covers the owned quantity (this same comparison, next pass).
        per_symbol_lock = getattr(broker, "per_symbol_drift_lock", False) is True
        locked_shortfalls: list[str] = []
        if drift_candidates:
            repo_orders = sell_orders()
            for canonical, owned_qty, raw_qty, market in drift_candidates:
                if self._open_order_explains_drift(canonical, owned_qty, raw_qty, market, repo_orders):
                    continue
                if per_symbol_lock:
                    locked_shortfalls.append(canonical)
                    continue
                raise OwnershipDriftError(
                    f"Ownership drift for {canonical}: repository managed "
                    f"qty={owned_qty} exceeds broker position qty={raw_qty}. "
                    "Confirm the broker position, then adopt it explicitly with "
                    "`parayabuck ops rebase-ownership " + canonical + " --broker-qty <qty> --confirm`",
                    symbols=(canonical,),
                )
        if per_symbol_lock:
            broker.drift_locked_symbols = frozenset(locked_shortfalls)
        # Account-scoped peak reconciliation (serialized: the peak is shared
        # mutable state across the account, trade-update and decision threads).
        # The lock is created in ``AlpacaBroker.__init__``; the fallback below
        # only serves test doubles that bypass it.
        peak_lock = getattr(broker, "_peak_lock", None)
        if peak_lock is None:
            peak_lock = threading.Lock()
            broker._peak_lock = peak_lock
        with peak_lock:
            broker._ensure_peak_reconciled(broker._account_id or "unknown")
            old_peak = broker._peak_equity
            # The high-water mark tracks equity net of today's external cash,
            # so a same-day deposit or withdrawal never reads as a gain or loss.
            broker._peak_equity = max(broker._peak_equity, equity - cash_flows)
            peak_equity = broker._peak_equity
            if peak_equity != old_peak or self._peak_save_pending:
                try:
                    broker._save_peak_equity()
                    self._peak_save_pending = False
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    # The account was already fetched: keep the in-memory peak
                    # and the snapshot, and retry the sidecar on the next poll
                    # even if the peak does not move again.
                    self._peak_save_pending = True
                    log.warning("Peak sidecar write failed; keeping the fetched snapshot: %s", exc)
                    emit_execution_alert(
                        "paper_incident",
                        message="Peak sidecar write failed; snapshot kept, retry next poll",
                        category="peak_sidecar_write_failed",
                        incident_id="peak_sidecar_write_failed",
                    )
        daily_pnl = equity - previous_equity - cash_flows
        snapshot = AccountSnapshot(
            cash=cash,
            equity=equity,
            # Reported against raw equity: adding today's net cash back keeps
            # drawdown measured on trading performance, not on the transfer.
            peak_equity=peak_equity + cash_flows,
            daily_pnl=daily_pnl,
            positions=tuple(positions),
            realized_pnl=daily_pnl - intraday_unrealized,
            unrealized_pnl=unrealized,
            fees=(ledger.total_fees_for_account(broker._account_id or "unknown") if ledger is not None else 0.0),
            non_marginable_buying_power=non_marginable_buying_power,
        )
        # One assignment publishes snapshot and timestamp together, so a
        # concurrent snapshot_if_fresh never pairs a new snapshot with an old
        # timestamp (or the reverse).
        broker._snapshot_cache = (snapshot, time.monotonic())
        return snapshot
