from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest

from parayabuck.backtest.lifetimes import EntityLifetimeRecord
from parayabuck.markets.month_end import month_end_first_open

sys.path.insert(0, str(Path(__file__).resolve().parent))

from readiness_models import (  # noqa: E402
    ResearchBar,
    SignalRecord,
    average_rank,
    decimal_text,
    q90_order_statistic,
    rank_signals,
    reconstruct_signal,
    target_weight,
)
from run_readiness_v2 import IdentityResolver, ReadinessInputError, _finalize_fill  # noqa: E402


def _signal_bars() -> tuple[ResearchBar, ...]:
    """51 observations with formation return rank 25 and volume rank 50."""

    returns = [Decimal(index) / Decimal(10_000) for index in range(1, 51) if index != 25]
    returns.append(Decimal(25) / Decimal(10_000))
    closes = [Decimal(100)]
    for value in returns:
        closes.append(closes[-1] * (Decimal(1) + value))
    volumes = [Decimal(index) * Decimal(1_000_000) for index in range(1, 50)] + [Decimal(100_000_000)]
    start = date(2020, 1, 1)
    bars: list[ResearchBar] = []
    for index, close in enumerate(closes):
        session = start + timedelta(days=index)
        volume = Decimal(1_000_000) if index == 0 else volumes[index - 1]
        bars.append(
            ResearchBar(
                timestamp=datetime.combine(session, datetime.min.time(), UTC),
                session=session,
                symbol="AAA",
                entity_id="entity-a",
                open=close,
                high=close,
                low=close,
                close=close,
                volume=volume,
            )
        )
    return tuple(bars)


def _record(entity_id: str, symbol: str, *, volume_rank: str, intensity: str) -> SignalRecord:
    return SignalRecord(
        entity_id=entity_id,
        symbol=symbol,
        reference_sessions=tuple(str(index) for index in range(49)),
        reference_dollar_volumes=tuple(Decimal(index) for index in range(1, 50)),
        sorted_reference_dollar_volumes=tuple(Decimal(index) for index in range(1, 50)),
        q90=Decimal(44),
        formation_dollar_volume=Decimal(100),
        volume_rank=Decimal(volume_rank),
        intensity=Decimal(intensity),
        formation_return=Decimal("0.01"),
        return_rank=Decimal(25),
    )


def test_frozen_q90_is_exact_x44_not_library_default() -> None:
    distinct = tuple(Decimal(index) for index in range(1, 50))
    assert q90_order_statistic(distinct) == Decimal(44)
    assert q90_order_statistic((Decimal(1),) * 44 + (Decimal(100),) * 5) == Decimal(1)
    assert q90_order_statistic((Decimal(7),) * 49) == Decimal(7)
    assert q90_order_statistic((Decimal(1),) * 48 + (Decimal("1e40"),)) == Decimal(1)


@pytest.mark.parametrize("count", [0, 48, 50])
def test_frozen_q90_rejects_any_non_49_reference(count: int) -> None:
    with pytest.raises(ValueError, match="exactly 49"):
        q90_order_statistic((Decimal(1),) * count)


def test_threshold_equality_and_neighboring_intensities_are_unique() -> None:
    prior = tuple(Decimal(index) for index in range(1, 50))
    q90 = q90_order_statistic(prior)
    assert q90 == Decimal(44)
    assert decimal_text((q90 - Decimal("0.0001")) / q90) == "0.9999977272727272727272727273"
    assert decimal_text(q90 / q90) == "1"
    assert decimal_text((q90 + Decimal("0.0001")) / q90) == "1.000002272727272727272727273"


def test_average_rank_uses_one_based_average_positions_for_ties() -> None:
    assert average_rank((Decimal(1), Decimal(2), Decimal(2), Decimal(4)), 1) == Decimal("2.5")
    assert average_rank((Decimal(1), Decimal(2), Decimal(2), Decimal(4)), 2) == Decimal("2.5")


def test_exact_51_session_signal_reconstruction() -> None:
    signal, reason = reconstruct_signal(_signal_bars())
    assert reason is None
    assert signal is not None
    assert len(signal.reference_sessions) == 49
    assert signal.q90 == signal.sorted_reference_dollar_volumes[43]
    assert signal.volume_rank == Decimal(50)
    with localcontext() as context:
        context.prec = 50
        assert signal.intensity == signal.formation_dollar_volume / signal.q90
    assert signal.return_rank == Decimal(25)


def test_signal_fails_closed_on_49_sessions_zero_volume_and_missing_session() -> None:
    bars = _signal_bars()
    assert reconstruct_signal(bars[:50]) == (None, "insufficient_51_close_history")
    zero = replace(bars[10], volume=Decimal(0))
    assert reconstruct_signal((*bars[:10], zero, *bars[11:])) == (
        None,
        "nonpositive_or_nonfinite_signal_window",
    )
    duplicate_session = replace(bars[20], session=bars[19].session)
    assert reconstruct_signal((*bars[:20], duplicate_session, *bars[21:])) == (
        None,
        "non_strict_session_order",
    )


def test_forward_and_reverse_split_transform_preserves_signal() -> None:
    original, reason = reconstruct_signal(_signal_bars())
    assert reason is None and original is not None
    for factor in (Decimal(10), Decimal("0.1")):
        transformed = tuple(
            replace(
                bar,
                open=bar.open / factor,
                high=bar.high / factor,
                low=bar.low / factor,
                close=bar.close / factor,
                volume=bar.volume * factor,
            )
            for bar in _signal_bars()
        )
        replay, replay_reason = reconstruct_signal(transformed)
        assert replay_reason is None and replay is not None
        assert replay.q90 == original.q90
        assert replay.formation_dollar_volume == original.formation_dollar_volume
        assert replay.volume_rank == original.volume_rank
        assert replay.intensity == original.intensity
        assert replay.return_rank == original.return_rank


def test_middle_40_condition_is_inclusive_and_own_history() -> None:
    bars = _signal_bars()
    signal, reason = reconstruct_signal(bars)
    assert reason is None and signal is not None and signal.return_rank == 25
    # Make the formation return the unique largest return without changing the
    # preceding 49 own-security comparisons.
    high_close = bars[-2].close * Decimal(2)
    high = replace(
        bars[-1],
        open=high_close,
        high=high_close,
        low=high_close,
        close=high_close,
        volume=bars[-1].dollar_volume / high_close,
    )
    assert reconstruct_signal((*bars[:-1], high)) == (None, "formation_return_outside_middle_40")


def test_total_order_top_six_reserved_boundary_and_weights() -> None:
    records = (
        _record("b", "BBB", volume_rank="50", intensity="3"),
        _record("a", "AAA", volume_rank="50", intensity="3"),
        _record("c", "CCC", volume_rank="50", intensity="2"),
        _record("d", "DDD", volume_rank="49.5", intensity="100"),
        _record("e", "EEE", volume_rank="49", intensity="9"),
        _record("f", "FFF", volume_rank="48", intensity="9"),
        _record("g", "GGG", volume_rank="47", intensity="9"),
        _record("spy", "SPY", volume_rank="50", intensity="999"),
    )
    ranked = rank_signals(records)
    assert [item.entity_id for item in ranked] == ["a", "b", "c", "d", "e", "f"]
    assert all(item.symbol != "SPY" for item in ranked)
    with localcontext() as context:
        context.prec = 50
        assert target_weight(6) == Decimal("0.55") / Decimal(6)
    assert target_weight(5) == Decimal("0.10")


def test_generic_month_end_contract_has_no_extra_session_delay() -> None:
    window = month_end_first_open(2021, 12)
    assert window.decision_session == date(2021, 12, 31)
    assert window.fill_session == date(2022, 1, 3)
    assert window.decision_timestamp < window.fill_timestamp


def _lifetime(
    entity_id: str,
    symbol: str,
    *,
    valid_to: datetime | None = None,
    last_observed: datetime | None = None,
) -> EntityLifetimeRecord:
    first = datetime(2019, 1, 2, 5, tzinfo=UTC)
    return EntityLifetimeRecord(
        entity_id=entity_id,
        provider_asset_id=entity_id,
        canonical_symbol=symbol,
        historical_symbol=symbol,
        valid_from=first,
        valid_to=valid_to,
        first_observed_bar=first,
        last_observed_bar=last_observed or datetime(2026, 7, 30, 4, tzinfo=UTC),
        current_status="inactive" if valid_to is not None else "active",
        exchange="NYSE",
        rename_chain=(),
        evidence_class="TEST",
        pit_quality_tier="TEST",
        terminal_event_class="DELISTING" if valid_to is not None else None,
        notes="synthetic readiness fixture",
    )


def test_lifecycle_ambiguity_and_unresolved_identity_fail_closed() -> None:
    first = _lifetime("00000000-0000-0000-0000-000000000001", "AAA")
    second = _lifetime("00000000-0000-0000-0000-000000000002", "AAA")
    ambiguous = IdentityResolver(
        records_by_entity={first.entity_id: first, second.entity_id: second},
        aliases={"AAA": (first, second)},
        unresolved_entity_ids=frozenset(),
    )
    with pytest.raises(ReadinessInputError, match="produced 2 matches"):
        ambiguous.resolve("AAA", date(2020, 1, 31))
    unresolved = IdentityResolver(
        records_by_entity={first.entity_id: first},
        aliases={"AAA": (first,)},
        unresolved_entity_ids=frozenset({first.entity_id}),
    )
    assert not unresolved.active_at(first, date(2020, 1, 31))


def test_fill_lifecycle_cancels_without_replacement_and_accepts_terminal_session_equality() -> None:
    fill_session = date(2020, 2, 3)
    before_fill = datetime(2020, 1, 31, 5, tzinfo=UTC)
    on_fill = datetime(2020, 2, 3, 5, tzinfo=UTC)
    cancelled = _lifetime(
        "00000000-0000-0000-0000-000000000003",
        "OLD",
        valid_to=before_fill,
        last_observed=before_fill,
    )
    admitted = _lifetime(
        "00000000-0000-0000-0000-000000000004",
        "NOW",
        valid_to=on_fill,
        last_observed=on_fill,
    )
    resolver = IdentityResolver(
        records_by_entity={cancelled.entity_id: cancelled, admitted.entity_id: admitted},
        aliases={"OLD": (cancelled,), "NOW": (admitted,)},
        unresolved_entity_ids=frozenset(),
    )
    decision: dict[str, Any] = {
        "fill_session": fill_session.isoformat(),
        "targets": [
            {"entity_id": cancelled.entity_id},
            {"entity_id": admitted.entity_id},
        ],
    }
    admitted_bar = ResearchBar(
        timestamp=on_fill,
        session=fill_session,
        symbol="NOW",
        entity_id=admitted.entity_id,
        open=Decimal(10),
        high=Decimal(10),
        low=Decimal(10),
        close=Decimal(10),
        volume=Decimal(0),
    )
    _finalize_fill(decision, {admitted.entity_id: admitted_bar}, resolver)
    assert len(decision["targets"]) == 2
    assert decision["targets"][0]["fill_available"] is False
    assert decision["targets"][0]["fill_exclusion_reason"] == "inactive_at_fill"
    assert decision["targets"][1]["fill_available"] is True
    assert decision["targets"][1]["fill_exclusion_reason"] is None
