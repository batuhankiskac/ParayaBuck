from __future__ import annotations

from parayabuck.features.indicators import rsi


def test_rsi_is_neutral_for_flat_series() -> None:
    assert rsi([100.0] * 15) == 50.0


def test_rsi_is_maximum_for_gain_only_series() -> None:
    assert rsi([float(value) for value in range(15)]) == 100.0
