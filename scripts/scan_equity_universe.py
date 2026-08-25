#!/usr/bin/env python3
"""Force a read-only Alpaca equity universe refresh."""

from __future__ import annotations

import os

from parayabuck.config import load_config
from parayabuck.universe import refresh_equity_universe


def main() -> int:
    config = load_config()
    equity = config.universe.equity
    if not equity.enabled:
        raise SystemExit("Equity universe is disabled in config/settings.yaml")
    if not equity.dynamic_selection_enabled:
        raise SystemExit("universe.equity.dynamic_selection_enabled is false")
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")

    print("Equity scanner started")
    try:
        result = refresh_equity_universe(config, key, secret)
    except Exception as exc:
        raise SystemExit(f"Automatic-compatible equity scan failed; previous universe preserved: {exc}") from exc
    print(f"Eligible pool: {len(result.selected_symbols)} symbols at {result.selection_timestamp}")
    print(f"Streamed observation set ({len(result.observation_set)}): {', '.join(result.observation_set)}")
    print(f"Persisted: {equity.active_universe_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
