#!/usr/bin/env python3
"""Compatibility executable for the production backtest command."""

from parayabuck.cli import backtest_main

if __name__ == "__main__":
    raise SystemExit(backtest_main())
