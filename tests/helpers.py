from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger


def make_broker(
    tmp_path: Path,
    *,
    client: Any = None,
    ledger: ExecutionLedger | None = None,
    peak_state_path: Path | str | None = None,
    **kwargs: Any,
) -> AlpacaBroker:
    """Build a real ``AlpacaBroker`` for a test.

    Tests must never bypass ``AlpacaBroker.__init__``: an uninitialised broker
    owns no service objects, so it can only reach fallback code inside the
    facade instead of the production service implementations. The peak
    sidecar is always confined to ``tmp_path`` so no test touches a
    production state path.
    """
    return AlpacaBroker(
        "test-api-key",
        "test-secret-key",
        paper=True,
        client=client if client is not None else SimpleNamespace(),
        ledger=ledger,
        peak_state_path=(peak_state_path if peak_state_path is not None else tmp_path / "paper_equity_peak.json"),
        **kwargs,
    )


__all__ = ["make_broker"]
