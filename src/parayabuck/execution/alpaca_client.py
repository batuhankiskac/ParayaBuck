from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from alpaca.trading.client import TradingClient

from parayabuck.core.models import Market
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import normalize_symbol
from parayabuck.utils.http import patch_session_timeout
from parayabuck.utils.objects import field_value

log = get_logger("parayabuck.execution.alpaca_client")

_DEFAULT_TIMEOUT = 15.0


class AlpacaClient:
    """Thin Alpaca SDK adapter isolating REST lifecycle and DTO normalization.

    Owns :class:`alpaca.trading.client.TradingClient`, bounds every REST
    call with a 15s socket timeout via :func:`patch_session_timeout`, and
    keeps raw Alpaca DTO adaptation (``field_value`` / ``_field`` /
    ``_normalized_field``) at the SDK boundary.

    No brokerage abstraction is introduced — this is Alpaca-specific and
    intentionally small (<400 lines). ``close()`` is idempotent and safe
    for fakes that have no ``_session``.
    """

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        paper: bool = True,
        *,
        client: Any | None = None,
    ) -> None:
        if client is not None:
            self._client: Any = client
        else:
            self._client = TradingClient(api_key, secret_key, paper=paper)
        self._asset_cache: dict[str, Any] = {}
        self._closed: bool = False
        session = getattr(self._client, "_session", None)
        if session is not None:
            try:
                patch_session_timeout(session, timeout=_DEFAULT_TIMEOUT)
            except Exception:
                log.warning("Failed to patch Alpaca session timeout", exc_info=True)

    @property
    def client(self) -> Any:
        return self._client

    @client.setter
    def client(self, value: Any) -> None:
        self._client = value

    # -- lifecycle -----------------------------------------------------

    def close(self) -> None:
        """Close the Alpaca REST session pool; idempotent and safe for fakes."""
        session = getattr(self._client, "_session", None)
        if session is None:
            self._closed = True
            return
        try:
            session.close()
        except Exception as exc:
            log.warning("Alpaca session close warning: %s", exc)
        finally:
            self._closed = True

    # -- thin TradingClient wrappers -----------------------------------

    def get_account(self) -> Any:
        return self._client.get_account()

    def get_all_positions(self) -> Any:
        return self._client.get_all_positions()

    def get_open_position(self, symbol: str) -> Any:
        # Alpaca expects the asset-lookup symbol (no slash for crypto).
        lookup = normalize_symbol(symbol).asset_lookup_symbol
        return self._client.get_open_position(lookup)

    def get_asset(self, symbol: str) -> Any:
        return self._asset(symbol)

    def get_orders(self, request: Any) -> Any:
        return self._client.get_orders(request)

    def get_order_by_id(self, order_id: str) -> Any:
        return self._client.get_order_by_id(order_id)

    def get_order_by_client_id(self, client_order_id: str) -> Any:
        getter = getattr(self._client, "get_order_by_client_id", None)
        if getter is None:
            return None
        return getter(client_order_id)

    def submit_order(self, request: Any) -> Any:
        return self._client.submit_order(request)

    def cancel_order_by_id(self, order_id: str) -> Any:
        canceller = getattr(self._client, "cancel_order_by_id", None)
        if canceller is not None:
            return canceller(order_id)
        # Fallback alias used by some fakes
        alt = getattr(self._client, "cancel_order", None)
        if alt is not None:
            return alt(order_id)
        raise AttributeError("underlying client has no cancel_order_by_id")

    def cancel_order(self, order_id: str) -> Any:
        return self.cancel_order_by_id(order_id)

    def request(self, method: str, path: str, data: dict[str, object] | None = None) -> Any:
        """Raw ``_request`` wrapper for Fill activity pagination (FILL).

        Mirrors ``TradingClient._request(method, path, data=...)``; returns
        whatever the underlying client returns (typically a list payload).
        """
        req = getattr(self._client, "_request", None)
        if req is None:
            raise AttributeError("underlying client has no _request")
        if data is not None:
            return req(method, path, data=data)
        return req(method, path)

    # Back-compat alias used by existing callers that accessed _request
    def _request(self, method: str, path: str, data: dict[str, object] | None = None) -> Any:
        return self.request(method, path, data=data)

    # -- normalization helpers (kept at SDK boundary) -------------------

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        return field_value(value, name, default)

    def _normalized_field(self, value: Any, name: str) -> str:
        return str(self._field(value, name, "")).lower().split(".")[-1]

    # Re-export for callers that imported field_value via adapter
    @staticmethod
    def field_value(value: Any, name: str, default: Any = None) -> Any:
        return field_value(value, name, default)

    def _asset(self, symbol: str) -> Any:
        identity = normalize_symbol(symbol)
        cache_key = identity.canonical
        asset = (
            self._asset_cache.get(cache_key)
            or self._asset_cache.get(symbol)
            or self._asset_cache.get(identity.asset_lookup_symbol)
        )
        if asset is None:
            asset = self._client.get_asset(identity.asset_lookup_symbol)
            self._asset_cache[cache_key] = asset
        return asset

    def _price_step(self, symbol: str, market: Market, reference_price: float) -> Decimal:
        increment = getattr(self._asset(symbol), "price_increment", None)
        if increment is not None:
            return Decimal(str(increment))
        if market == Market.EQUITY:
            return Decimal("0.01") if reference_price >= 1 else Decimal("0.0001")
        return Decimal("0.01") if reference_price >= 1 else Decimal("0.000001")

    @staticmethod
    def _round_to_step(value: float, step: Decimal) -> float:
        rounded = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_HALF_UP) * step
        return float(rounded)


__all__ = ["AlpacaClient"]
