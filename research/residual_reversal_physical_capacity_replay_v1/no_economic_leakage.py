"""Fail closed if a structural result artifact contains an economic outcome key."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FORBIDDEN = re.compile(
    r"(^|_)(return|net_return|cagr|sharpe|sortino|calmar|drawdown|pnl|profit|ending_nav|performance)($|_)",
    re.IGNORECASE,
)


def assert_no_economics(payload: Any, location: str = "$") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if FORBIDDEN.search(str(key)):
                raise AssertionError(f"Prohibited economic field at {location}.{key}")
            assert_no_economics(value, f"{location}.{key}")
    elif isinstance(payload, list | tuple):
        for index, value in enumerate(payload):
            assert_no_economics(value, f"{location}[{index}]")


def audit_json(path: Path) -> None:
    assert_no_economics(json.loads(path.read_text(encoding="utf-8")))
