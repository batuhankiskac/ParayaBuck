"""Focused causal-contract tests for the SEC research normalizer."""

import json
from pathlib import Path

from scripts.research.normalize_equity_sec_fundamentals_pilot_2026_09_07 import (
    _with_flow_conversion,
    _with_lineage,
    asof,
    split_consistent_market_cap,
)


FIXTURE = Path(__file__).parent / "fixtures/research_sec_normalization_fixture.json"


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text())["rows"]


def test_asof_selects_latest_revision_without_future_filing() -> None:
    rows = _with_lineage(_rows())
    early = asof(rows, "2024-12-31T00:00:00.000Z")
    assets = [r for r in early if r["tag"] == "Assets"]
    assert len(assets) == 1
    assert assets[0]["value"] == 100
    assert all(r["available_at"] <= "2024-12-31T00:00:00.000Z" for r in early)

    late = asof(rows, "2025-12-31T00:00:00.000Z")
    assert next(r for r in late if r["tag"] == "Assets")["value"] == 120


def test_annual_and_ytd_flows_are_explicitly_distinguished() -> None:
    rows = _with_flow_conversion(_with_lineage(_rows()))
    annual = next(r for r in rows if r["form"] == "10-K" and r["tag"] == "NetIncomeLoss")
    q1 = next(r for r in rows if r.get("fp") == "Q1")
    q2 = next(r for r in rows if r.get("fp") == "Q2")
    assert annual["normalized_flow_value"] == 10
    assert annual["flow_basis"] == "annual_10k_reported_period"
    assert q1["normalized_flow_value"] == 2
    assert q1["flow_basis"] == "q1_ytd_as_first_period"
    assert q2["normalized_flow_value"] == 3
    assert q2["flow_basis"] == "10q_ytd_delta_from_prior_ytd"


def test_split_adjusted_join_requires_asof_split_factor() -> None:
    assert split_consistent_market_cap(25, 100, price_basis="raw", shares_basis="reported") == 2500
    assert split_consistent_market_cap(
        25, 100, price_basis="split_adjusted", shares_basis="reported", cumulative_split_factor=4
    ) == 10000
    try:
        split_consistent_market_cap(25, 100, price_basis="split_adjusted", shares_basis="reported")
    except ValueError as exc:
        assert "explicit positive as-of split factor" in str(exc)
    else:
        raise AssertionError("missing split factor must fail closed")
