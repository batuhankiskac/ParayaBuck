#!/usr/bin/env python3
"""Offline paper-safety chaos gate.

This command never creates an Alpaca client and cannot place a live order. It
runs the fake-broker lifecycle, restart, universe and runtime safety tests that
cover ambiguous writes, partial fills, stop/target races and external ownership.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCENARIOS = (
    "submit timeout + client-order-id recovery",
    "cancel timeout / unconfirmed cancellation",
    "HTTP and stream failure circuit breakers",
    "partial fill + remaining-entry cancellation",
    "protective stop submission failure",
    "triggered-but-unfilled stop-limit failover",
    "process restart and ledger reconciliation",
    "journal write failure",
    "external + managed quantity isolation",
    "target / stop cancellation race",
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    executable = str(python if python.exists() else Path(sys.executable))
    print("ParayaBuck offline paper chaos gate")
    for scenario in SCENARIOS:
        print(f"  - {scenario}")
    command = [
        executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_crypto_universe.py",
        "tests/test_hardening.py",
        "tests/test_engine.py",
    ]
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        print("CHAOS GATE FAILED: paper rollout must remain blocked.")
        return result.returncode
    print(
        "CHAOS GATE PASSED: no duplicate exit, external quantity isolation, "
        "ledger ownership and ambiguity halts verified by fake brokers."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
