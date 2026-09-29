from __future__ import annotations

from unittest.mock import Mock

import pytest

from parayabuck.data.alpaca_stream import AlpacaBarStream


def _adapter() -> AlpacaBarStream:
    # Only the adapter's stop handling is under test, never SDK sockets.
    return object.__new__(AlpacaBarStream)


def test_stop_stops_a_running_stream_and_propagates_its_error() -> None:
    adapter = _adapter()
    adapter.stock = Mock()
    adapter.stop()
    adapter.stock.stop.assert_called_once_with()
    adapter.stock.stop.side_effect = RuntimeError("stop failed")
    with pytest.raises(RuntimeError, match="stop failed"):
        adapter.stop()


def test_stop_skips_sdk_stream_that_never_ran() -> None:
    # alpaca-py sets ``_loop`` only inside run(); stop() before run() used to
    # raise AttributeError on the None loop.
    adapter = _adapter()
    adapter.stock = Mock(_loop=None)
    adapter.stop()
    adapter.stock.stop.assert_not_called()
