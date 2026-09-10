"""Smoke verification for the completed immediate candidate package."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_completed_package_verifies() -> None:
    path = Path(__file__).resolve().parents[2] / "scripts/research/verify_immediate_candidates_economic_2026_09_09.py"
    spec = importlib.util.spec_from_file_location("immediate_verify", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify()
    assert result["status"] == "VERIFY_PASS"
