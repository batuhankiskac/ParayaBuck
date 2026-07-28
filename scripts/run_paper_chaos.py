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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    executable = str(python if python.exists() else Path(sys.executable))
    print("ParayaBuck offline paper chaos gate")
    command = [
        executable,
        "-m",
        "pytest",
        "-q",
        "-m",
        "paper_chaos",
    ]
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        print("CHAOS GATE FAILED: paper rollout must remain blocked.")
        return result.returncode
    print("CHAOS GATE PASSED: marked fault-injection scenarios executed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
