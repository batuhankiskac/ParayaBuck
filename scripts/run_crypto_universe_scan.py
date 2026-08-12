#!/usr/bin/env python3
"""Force a read-only Alpaca crypto universe refresh."""

from __future__ import annotations

import os

from parayabuck.config import load_config
from parayabuck.universe import elimination_reason_summary, refresh_crypto_universe


def main() -> int:
    config = load_config()
    crypto = config.universe.crypto
    if not crypto.enabled:
        raise SystemExit("Crypto universe is disabled in config/settings.yaml")
    if not crypto.dynamic_selection_enabled:
        raise SystemExit("universe.crypto.dynamic_selection_enabled is false")
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")

    print(f"Crypto scanner started; candidates={len(crypto.trade_symbols)}")
    try:
        result = refresh_crypto_universe(config, key, secret)
    except Exception as exc:
        raise SystemExit(
            f"Automatic-compatible crypto scan failed; previous universe preserved: {exc}"
        ) from exc
    print(f"Eligible pool ({len(result.selected_symbols)}): {', '.join(result.selected_symbols)}")
    print(
        f"Streamed observation set ({len(result.observation_set)}): "
        f"{', '.join(result.observation_set)}"
    )
    print(f"Eliminated: {elimination_reason_summary(result.eliminated)}")
    print(f"Persisted: {crypto.active_universe_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
