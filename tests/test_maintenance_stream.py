from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from parayabuck.data.alpaca_stream import AlpacaBarStream


class _Stream:
    def __init__(self, symbols: set[str]) -> None:
        self.bars = set(symbols)
        self.updated = set(symbols)
        self.failures: dict[str, Exception] = {}

    def _change(self, operation: str, target: set[str], symbols: tuple[str, ...], add: bool) -> None:
        if operation in self.failures:
            raise self.failures[operation]
        if add:
            target.update(symbols)
        else:
            target.difference_update(symbols)

    def subscribe_bars(self, handler: Any, *symbols: str) -> None:
        self._change("subscribe_bars", self.bars, symbols, True)

    def subscribe_updated_bars(self, handler: Any, *symbols: str) -> None:
        self._change("subscribe_updated_bars", self.updated, symbols, True)

    def unsubscribe_bars(self, *symbols: str) -> None:
        self._change("unsubscribe_bars", self.bars, symbols, False)

    def unsubscribe_updated_bars(self, *symbols: str) -> None:
        self._change("unsubscribe_updated_bars", self.updated, symbols, False)


async def _handler(raw: object) -> None:
    pass


def _adapter() -> AlpacaBarStream:
    # Only the adapter's bookkeeping is under test, never SDK sockets.
    adapter = object.__new__(AlpacaBarStream)
    adapter.stock = _Stream({"OLD"})
    adapter.crypto = _Stream(set())
    adapter._stock_symbols = {"OLD"}
    adapter._crypto_symbols = set()
    adapter._rollback_failed = False
    adapter._stock_handler = _handler
    adapter._stock_updated_handler = _handler
    adapter._crypto_handler = _handler
    adapter._crypto_updated_handler = _handler
    return adapter


@pytest.mark.parametrize(
    ("desired", "failed_operation", "failed_rollback"),
    [
        (["OLD", "NEW"], "subscribe_updated_bars", "unsubscribe_bars"),
        (["OLD", "NEW"], "subscribe_updated_bars", "unsubscribe_updated_bars"),
        ([], "unsubscribe_updated_bars", "subscribe_bars"),
        ([], "unsubscribe_updated_bars", "subscribe_updated_bars"),
    ],
)
def test_partial_rollback_failure_sets_unknown_membership_flag(
    desired: list[str], failed_operation: str, failed_rollback: str
) -> None:
    adapter = _adapter()
    original = RuntimeError("forward operation failed")
    adapter.stock.failures = {
        failed_operation: original,
        failed_rollback: RuntimeError("rollback operation failed"),
    }
    with pytest.raises(RuntimeError) as error:
        adapter.update_subscriptions(desired, [])
    assert error.value is original
    assert adapter.rollback_failed is True
    assert adapter._stock_symbols == {"OLD"}


def test_successful_rollback_does_not_claim_unknown_membership() -> None:
    adapter = _adapter()
    adapter.stock.failures = {"subscribe_updated_bars": RuntimeError("forward operation failed")}
    with pytest.raises(RuntimeError, match="forward operation failed"):
        adapter.update_subscriptions(["OLD", "NEW"], [])
    assert adapter.rollback_failed is False
    assert adapter.stock.bars == adapter.stock.updated == {"OLD"}


def test_successful_subscription_preserves_change_report_and_membership() -> None:
    adapter = _adapter()
    assert adapter.update_subscriptions(["NEW"], ["BTC/USD"]) == {
        "added_stock": ("NEW",),
        "removed_stock": ("OLD",),
        "added_crypto": ("BTC/USD",),
        "removed_crypto": (),
    }
    assert adapter.stock.bars == adapter.stock.updated == {"NEW"}
    assert adapter.crypto.bars == adapter.crypto.updated == {"BTC/USD"}
    assert adapter.rollback_failed is False


def test_rollback_failure_latch_survives_success_until_explicit_clear() -> None:
    adapter = _adapter()
    adapter._rollback_failed = True
    adapter.update_subscriptions(["OLD"], [])
    assert adapter.rollback_failed is True
    adapter.clear_rollback_failure()
    assert adapter.rollback_failed is False


@pytest.mark.parametrize("failed_stream", ["stock", "crypto", "neither"])
def test_stop_attempts_both_streams_even_when_one_raises(failed_stream: str) -> None:
    adapter = _adapter()
    adapter.stock = Mock()
    adapter.crypto = Mock()
    if failed_stream != "neither":
        getattr(adapter, failed_stream).stop.side_effect = RuntimeError("stop failed")
        with pytest.raises(RuntimeError, match="stop failed"):
            adapter.stop()
    else:
        adapter.stop()
    adapter.stock.stop.assert_called_once_with()
    adapter.crypto.stop.assert_called_once_with()


def test_crypto_failure_rolls_back_completed_stock_market() -> None:
    adapter = _adapter()
    original = RuntimeError("crypto operation failed")
    adapter.crypto.failures = {"subscribe_updated_bars": original}
    with pytest.raises(RuntimeError) as error:
        adapter.update_subscriptions(["NEW"], ["BTC/USD"])
    assert error.value is original
    assert adapter.stock.bars == adapter.stock.updated == {"OLD"}
    assert adapter.crypto.bars == adapter.crypto.updated == set()
    assert adapter._stock_symbols == {"OLD"}
    assert adapter._crypto_symbols == set()
    assert adapter.rollback_failed is False


def test_stop_preserves_both_errors_when_both_streams_fail() -> None:
    adapter = _adapter()
    stock_failure = RuntimeError("stock stop failed")
    crypto_failure = RuntimeError("crypto stop failed")
    adapter.stock = Mock()
    adapter.crypto = Mock()
    adapter.stock.stop.side_effect = stock_failure
    adapter.crypto.stop.side_effect = crypto_failure
    with pytest.raises(RuntimeError) as error:
        adapter.stop()
    assert error.value is crypto_failure
    assert error.value.__context__ is stock_failure
    adapter.stock.stop.assert_called_once_with()
    adapter.crypto.stop.assert_called_once_with()


def test_stop_skips_sdk_stream_that_never_ran() -> None:
    # alpaca-py sets ``_loop`` only inside run(); stop() before run() used to
    # raise AttributeError on the None loop.
    adapter = _adapter()
    adapter.stock = Mock(_loop=None)
    adapter.crypto = Mock()
    adapter.stop()
    adapter.stock.stop.assert_not_called()
    adapter.crypto.stop.assert_called_once_with()
