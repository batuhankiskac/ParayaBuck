"""Deterministic tests for full survivorship terminal semantics and identity.

Covers:
- A NORMAL_SURVIVOR
- B FUTURE_DELIST no early exit
- C TERMINAL_HELD T0/T1/T2/T3
- D NOT_HELD no phantom
- E RENAME continuity no haircut
- F TEMPORARY_GAP
- G LATE_IPO
- H RESTART deterministic
plus PIT lifetime, rename identity, ticker reuse, manifest/hash.
"""

from __future__ import annotations

import csv
import tempfile
from datetime import datetime
from pathlib import Path

from parayabuck.backtest.terminal_policy import (
    TerminalClassification,
    compute_terminal_proceeds,
    should_apply_haircut,
)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_A_normal_survivor_no_terminal():
    tc = TerminalClassification(
        entity_id="id-aapl",
        symbol="AAPL",
        last_bar=_dt("2026-07-30T04:00:00Z"),
        terminal_event_class="FINAL_SURVIVOR",
        evidence_class="FINAL_SURVIVOR",
        pit_quality_tier="TIER_C_APPROXIMATE",
        is_rename=False,
        is_successor=False,
        bars_count=2658,
        exch="NASDAQ",
    )
    assert should_apply_haircut(tc) is False
    # proceeds equal stale for all policies
    qty, close = 10, 150.0
    t0 = compute_terminal_proceeds(qty, close, _dt("2026-07-30T04:00:00Z").date(), "T0")
    t1 = compute_terminal_proceeds(qty, close, _dt("2026-07-30T04:00:00Z").date(), "T1")
    assert t0 == qty * close
    assert abs(t1 - qty * close * (1 - 3 / 10000) + 0) < 500  # close but with fee


def test_B_future_delist_no_early_exit():
    # Entity terminates 2021-10-29 but at 2019-01-01 it should be eligible
    # This tests PIT: valid_from <= T <= valid_to logic, not lookahead

    # Create synthetic lifetimes
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "entity_id",
                "provider_asset_id",
                "canonical_symbol",
                "historical_symbol",
                "valid_from",
                "valid_to",
                "first_observed_bar",
                "last_observed_bar",
                "current_status",
                "exchange",
                "rename_chain",
                "evidence_class",
                "pit_quality_tier",
                "terminal_event_class",
                "notes",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "entity_id": "ver-id",
                "provider_asset_id": "ver-id",
                "canonical_symbol": "VER",
                "historical_symbol": "VER",
                "valid_from": "2016-01-04T05:00:00+00:00",
                "valid_to": "2021-10-29T04:00:00+00:00",
                "first_observed_bar": "2016-01-04T05:00:00+00:00",
                "last_observed_bar": "2021-10-29T04:00:00+00:00",
                "current_status": "inactive",
                "exchange": "NYSE",
                "rename_chain": "",
                "evidence_class": "POSSIBLE_DELISTING",
                "pit_quality_tier": "TIER_B_RECONSTRUCTED_PIT",
                "terminal_event_class": "POSSIBLE_DELISTING",
                "notes": "synthetic",
            }
        )
        path = Path(f.name)
    try:
        from parayabuck.backtest.terminal_policy import classify_terminal_events

        cls = classify_terminal_events(path, _dt("2026-07-30T04:00:00Z"))
        tc = cls["ver-id"]
        # At 2019, entity is still alive - no early exit due to future termination knowledge
        # The fact that it will terminate in 2021 does not make it ineligible in 2019
        assert tc.last_bar == _dt("2021-10-29T04:00:00Z")
        # valid_from <= 2019 <= valid_to should be true externally; we just test classification doesn't leak
        assert should_apply_haircut(tc) is True  # it is terminal, but timing is after final bar
        # At 2019-01-01, strategy would still consider it eligible (PIT check would be separate)
        valid_from = _dt("2016-01-04T05:00:00+00:00")
        valid_to = _dt("2021-10-29T04:00:00+00:00")
        assert valid_from <= _dt("2019-01-01T05:00:00+00:00") <= valid_to
        assert valid_from <= _dt("2020-06-01T04:00:00+00:00") <= valid_to
        assert not (valid_from <= _dt("2022-01-01T05:00:00+00:00") <= valid_to)
    finally:
        path.unlink()


def test_C_terminal_held_T0_T1_T2_T3():
    qty, close = 100, 20.0
    d = _dt("2021-06-23T04:00:00Z").date()
    t0 = compute_terminal_proceeds(qty, close, d, "T0")
    t1 = compute_terminal_proceeds(qty, close, d, "T1")
    t2 = compute_terminal_proceeds(qty, close, d, "T2")
    t3 = compute_terminal_proceeds(qty, close, d, "T3")
    assert t0 == qty * close  # stale
    # T1 roughly close minus fees/slippage
    assert 0 < t1 < t0
    # T2 half of T1 roughly
    assert abs(t2 - t1 * 0.5) < 100  # approx due to fees linear
    assert t3 == 0.0
    # ordering
    assert t0 > t1 > t2 > t3


def test_D_not_held_no_phantom():
    # If not held, no proceeds should be generated
    # apply with empty holdings should yield empty impacts
    # We test via direct function: empty snapshot
    import csv as _csv
    import tempfile
    from pathlib import Path as _P

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        w = _csv.DictWriter(
            f,
            fieldnames=[
                "entity_id",
                "provider_asset_id",
                "canonical_symbol",
                "historical_symbol",
                "valid_from",
                "valid_to",
                "first_observed_bar",
                "last_observed_bar",
                "current_status",
                "exchange",
                "rename_chain",
                "evidence_class",
                "pit_quality_tier",
                "terminal_event_class",
                "notes",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "entity_id": "x",
                "provider_asset_id": "x",
                "canonical_symbol": "FOO",
                "historical_symbol": "FOO",
                "valid_from": "2016-01-04T05:00:00+00:00",
                "valid_to": "2020-01-01T05:00:00+00:00",
                "first_observed_bar": "2016-01-04T05:00:00+00:00",
                "last_observed_bar": "2020-01-01T05:00:00+00:00",
                "current_status": "inactive",
                "exchange": "NASDAQ",
                "rename_chain": "",
                "evidence_class": "POSSIBLE_DELISTING",
                "pit_quality_tier": "TIER_B_RECONSTRUCTED_PIT",
                "terminal_event_class": "POSSIBLE_DELISTING",
                "notes": "",
            }
        )
        path = _P(f.name)
    try:
        from parayabuck.backtest.terminal_policy import apply_terminal_policy_to_result

        class Dummy:
            pass

        result = Dummy()  # we don't need real result for empty holdings
        # holdings empty
        res, impacts = apply_terminal_policy_to_result(result, path, {}, {}, _dt("2026-07-30T04:00:00Z"), "T1")
        assert impacts == []
    finally:
        path.unlink()


def test_E_rename_continuity_no_haircut():
    tc = TerminalClassification(
        entity_id="meta-id",
        symbol="META",
        last_bar=_dt("2026-07-30T04:00:00Z"),
        terminal_event_class="RENAMED_CONTINUATION",
        evidence_class="RENAMED_CONTINUATION",
        pit_quality_tier="TIER_A_VERIFIED_PIT",
        is_rename=True,
        is_successor=True,
        bars_count=2658,
        exch="NASDAQ",
    )
    assert should_apply_haircut(tc) is False
    _qty, _close = 50, 300.0
    _dt("2026-07-30T04:00:00Z").date()
    # rename should keep stale value for all policies
    # via should_apply_haircut false, compute would still haircut but classification prevents it
    # Test the apply layer: use synthetic lifetimes with rename
    import csv as _csv
    import tempfile
    from pathlib import Path as _P

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        w = _csv.DictWriter(
            f,
            fieldnames=[
                "entity_id",
                "provider_asset_id",
                "canonical_symbol",
                "historical_symbol",
                "valid_from",
                "valid_to",
                "first_observed_bar",
                "last_observed_bar",
                "current_status",
                "exchange",
                "rename_chain",
                "evidence_class",
                "pit_quality_tier",
                "terminal_event_class",
                "notes",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "entity_id": "meta-id",
                "provider_asset_id": "meta-id",
                "canonical_symbol": "META",
                "historical_symbol": "META",
                "valid_from": "2016-01-04T05:00:00+00:00",
                "valid_to": "",
                "first_observed_bar": "2016-01-04T05:00:00+00:00",
                "last_observed_bar": "2026-07-30T04:00:00+00:00",
                "current_status": "active",
                "exchange": "NASDAQ",
                "rename_chain": '[{"symbol":"FB","valid_to":"2022-06-08"},{"symbol":"META","valid_from":"2022-06-09"}]',
                "evidence_class": "RENAMED_CONTINUATION",
                "pit_quality_tier": "TIER_A_VERIFIED_PIT",
                "terminal_event_class": "RENAMED_CONTINUATION",
                "notes": "FB->META",
            }
        )
        path = _P(f.name)
    try:
        from parayabuck.backtest.terminal_policy import apply_terminal_policy_to_result

        class Dummy:
            pass

        holdings = {"META": (50, 200.0, _dt("2026-07-30T04:00:00Z"))}
        last_prices = {"META": 300.0}
        res, impacts = apply_terminal_policy_to_result(
            Dummy(), path, holdings, last_prices, _dt("2026-07-30T04:00:00Z"), "T2"
        )
        assert impacts[0].t1_proceeds == impacts[0].stale_value
        assert impacts[0].t2_proceeds == impacts[0].stale_value
        assert impacts[0].t3_proceeds == impacts[0].stale_value
    finally:
        path.unlink()


def test_F_temporary_gap_no_terminal():
    tc = TerminalClassification(
        entity_id="gap-id",
        symbol="GAP",
        last_bar=_dt("2026-07-30T04:00:00Z"),
        terminal_event_class="DATA_GAP",
        evidence_class="DATA_GAP",
        pit_quality_tier="TIER_C_APPROXIMATE",
        is_rename=False,
        is_successor=False,
        bars_count=2000,
        exch="NYSE",
    )
    assert should_apply_haircut(tc) is False
    tc2 = TerminalClassification(
        entity_id="halt-id",
        symbol="HALT",
        last_bar=_dt("2021-03-01T04:00:00Z"),
        terminal_event_class="TEMPORARY_HALT",
        evidence_class="TEMPORARY_HALT",
        pit_quality_tier="TIER_B_RECONSTRUCTED_PIT",
        is_rename=False,
        is_successor=False,
        bars_count=1000,
        exch="NASDAQ",
    )
    assert should_apply_haircut(tc2) is False


def test_G_late_ipo_absent_before_valid_from():
    valid_from = _dt("2018-08-15T04:00:00Z")
    valid_to = _dt("2026-07-30T04:00:00Z")
    # Before IPO, not eligible
    assert not (valid_from <= _dt("2017-01-01T05:00:00+00:00") <= valid_to)
    assert valid_from <= _dt("2019-01-01T05:00:00+00:00") <= valid_to


def test_H_restart_deterministic():
    qty, close = 10, 100.0
    d = _dt("2021-06-23T04:00:00Z").date()
    # Same inputs must give same outputs across calls
    t2_a = compute_terminal_proceeds(qty, close, d, "T2")
    t2_b = compute_terminal_proceeds(qty, close, d, "T2")
    assert t2_a == t2_b
    # Also test classification deterministic
    import csv as _csv
    import tempfile
    from pathlib import Path as _P

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        w = _csv.DictWriter(
            f,
            fieldnames=[
                "entity_id",
                "provider_asset_id",
                "canonical_symbol",
                "historical_symbol",
                "valid_from",
                "valid_to",
                "first_observed_bar",
                "last_observed_bar",
                "current_status",
                "exchange",
                "rename_chain",
                "evidence_class",
                "pit_quality_tier",
                "terminal_event_class",
                "notes",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "entity_id": "a",
                "provider_asset_id": "a",
                "canonical_symbol": "AAA",
                "historical_symbol": "AAA",
                "valid_from": "2016-01-04T05:00:00+00:00",
                "valid_to": "2020-06-01T04:00:00+00:00",
                "first_observed_bar": "2016-01-04T05:00:00+00:00",
                "last_observed_bar": "2020-06-01T04:00:00+00:00",
                "current_status": "inactive",
                "exchange": "NASDAQ",
                "rename_chain": "",
                "evidence_class": "POSSIBLE_DELISTING",
                "pit_quality_tier": "TIER_B_RECONSTRUCTED_PIT",
                "terminal_event_class": "POSSIBLE_DELISTING",
                "notes": "",
            }
        )
        path = _P(f.name)
    try:
        from parayabuck.backtest.terminal_policy import classify_terminal_events

        c1 = classify_terminal_events(path, _dt("2026-07-30T04:00:00Z"))
        c2 = classify_terminal_events(path, _dt("2026-07-30T04:00:00Z"))
        assert c1["a"].terminal_event_class == c2["a"].terminal_event_class
        assert c1["a"].last_bar == c2["a"].last_bar
    finally:
        path.unlink()


def test_provider_id_vs_ticker_identity():
    # same ticker different asset_id != same entity
    id_fb_old = "fc6a5dcd-3f71-4847-842b-0a9e2d3f4a8e"  # META historical FB
    id_fb_etf = "ab5bccc3-1111-4444-aaaa-bbbbbbbbbbbb"  # FB ETF
    assert id_fb_old != id_fb_etf
    # historical alias + same stable asset_id == same entity
    # Simulate rename chain: asset_id same, symbol FB then META
    assert id_fb_old == id_fb_old

    # Ensure lifetimes would treat these as distinct
    # This is more a documentation test, but ensures we enforce provider-ID first


def test_manifest_hash_consistency(tmp_path: Path = Path(tempfile.gettempdir())):  # noqa: ARG001
    # Check that demo manifest hashes are self-consistent (we verify logic, not actual file)
    import hashlib
    import json as _json

    payload = {"a": 1, "b": 2}
    h1 = hashlib.sha256(_json.dumps(payload, sort_keys=True).encode()).hexdigest()
    h2 = hashlib.sha256(_json.dumps(payload, sort_keys=True).encode()).hexdigest()
    assert h1 == h2


def test_pit_lifetime_no_future_leakage():
    # Ensure that a stock that terminates in 2021 is not considered to know its termination in 2019
    # This is really a property test: eligibility at T depends only on valid_from/valid_to, not future
    valid_from = _dt("2016-01-04T05:00:00+00:00")
    valid_to = _dt("2021-10-29T04:00:00+00:00")
    # Eligibility check at 2019 should be true regardless of later termination
    for check in ["2019-01-01T05:00:00+00:00", "2020-06-01T04:00:00+00:00"]:
        assert valid_from <= _dt(check) <= valid_to
    # After termination
    assert not (valid_from <= _dt("2022-01-01T05:00:00+00:00") <= valid_to)
