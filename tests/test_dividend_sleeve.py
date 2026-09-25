"""Contract tests for the V3 + dividend-growth sleeve equity identity.

Each test pins one invariant of the composition: V3 invariance when the sleeve
symbols are observed, the stateless monthly sleeve rule, the overlay split
(core at ``core_allocation``, sleeve appended inside the remaining room),
reduce-only latches, and sleeve slot exemption.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.config import load_config
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    reserve_opening_order_slots,
)
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.dividend_sleeve import (
    SLEEVE_MODE_DECIDE,
    SLEEVE_MODE_HOLD,
    EquityResidualMomentumV3DividendSleeveV1,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

ROOT = Path(__file__).resolve().parents[1]
SLEEVE = ("SCHD", "VIG", "DGRO")
SHIPPED_SLEEVE = ("SCHD", "VIG", "DGRO", "GLD", "OUNZ")
V3_PARAMS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}
START = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)
LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.005,
    drawdown_halt=0.05,
    maximum_gross=0.60,
    maximum_equity=0.60,
    maximum_crypto=0.10,
    maximum_equity_asset=0.10,
    maximum_crypto_asset=0.05,
    minimum_cash=0.40,
    maximum_positions=8,
    maximum_crypto_positions=2,
    slot_exempt_equity_symbols=SLEEVE,
)


def _strategy() -> EquityResidualMomentumV3DividendSleeveV1:
    strategy = portfolio_strategy_from(
        "equity_residual_momentum_v3_dividend_sleeve_v1",
        {**V3_PARAMS, "core_allocation": 0.40, "sleeve_allocation": 0.15, "sleeve_symbols": list(SLEEVE)},
    )
    assert isinstance(strategy, EquityResidualMomentumV3DividendSleeveV1)
    return strategy


def _closes(daily: float, phase: float, n: int = 300) -> list[float]:
    return [100.0 * (1.0 + daily) ** index * (1.0 + 0.004 * math.sin(index * 1.7 + phase)) for index in range(n)]


def _bars(symbol: str, closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(START + timedelta(days=index), symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)
        for index, close in enumerate(closes)
    )


class _Universe:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


def _context(
    paths: dict[str, list[float]],
    *,
    holdings: dict[str, float] | None = None,
    last: int | None = None,
) -> PortfolioContext:
    end = (max(len(closes) for closes in paths.values()) if last is None else last + 1) - 1
    history = {symbol: _bars(symbol, closes[: end + 1]) for symbol, closes in paths.items()}
    return PortfolioContext(
        decision_timestamp=START + timedelta(days=end),
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=_Universe(),
        universe_version="test",
        equity=100_000.0,
        holdings=dict(holdings or {}),
        prices={},
        benchmark_symbol="SPY",
    )


def _paths() -> dict[str, list[float]]:
    return {
        "SPY": [100.0] * 300,
        "AAA": _closes(0.004, 0.0),
        "BBB": _closes(0.003, 1.0),
        "CCC": _closes(0.002, 2.0),
        "DDD": _closes(-0.003, 3.0),
        # Sleeve ETFs with the strongest momentum: V3 would select them if it saw them.
        "SCHD": _closes(0.006, 4.0),
        "VIG": _closes(0.006, 5.0),
        "DGRO": _closes(0.006, 6.0),
    }


def _position(symbol: str, weight: float, market: Market = Market.EQUITY, *, external: float = 0.0):
    qty = weight * 100_000.0 / 100.0
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_price=100.0,
        current_price=100.0,
        market=market,
        qty_available=qty,
        ownership="managed",
        managed_qty=qty * (1.0 - external),
    )


def _account(*positions: PositionSnapshot, daily_pnl: float = 0.0) -> AccountSnapshot:
    invested = sum(position.market_value for position in positions)
    return AccountSnapshot(
        cash=100_000.0 - invested,
        equity=100_000.0,
        peak_equity=100_000.0,
        daily_pnl=daily_pnl,
        positions=tuple(positions),
    )


def _target(core: dict[str, float], *, mode: str = SLEEVE_MODE_DECIDE, **metadata) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="equity_residual_momentum_v3_dividend_sleeve_v1",
        strategy_version="1.0.0",
        decision_timestamp=START,
        weights=tuple(
            TargetWeight(symbol, weight, "test", rank=index + 1) for index, (symbol, weight) in enumerate(core.items())
        ),
        cash_weight=max(0.0, 1.0 - sum(core.values())),
        universe_version="test",
        metadata={
            "sleeve": {
                "mode": mode,
                "symbols": list(SLEEVE),
                "weights": {symbol: 0.05 for symbol in SLEEVE} if mode == SLEEVE_MODE_DECIDE else {},
                "core_allocation": 0.40,
            },
            **metadata,
        },
    )


EQUITY_SYMBOLS = tuple(f"E{index}" for index in range(8)) + SLEEVE


def _apply(target: TargetPortfolio, account: AccountSnapshot, **kwargs):
    options = {
        "market": Market.EQUITY,
        "approved_symbols": EQUITY_SYMBOLS,
        "maximum_allocation": 0.55,
        "mode": "paper",
        **kwargs,
    }
    return PortfolioRiskOverlay(LIMITS).apply(target, account, **options)


def _weights(decision) -> dict[str, float]:
    return {weight.symbol: weight.weight for weight in decision.target.weights}


# --------------------------------------------------------------------------- #
# Strategy
# --------------------------------------------------------------------------- #


def test_core_matches_v3_when_sleeve_symbols_are_observed() -> None:
    paths = _paths()
    context = _context(paths)
    v3 = portfolio_strategy_from("equity_residual_momentum_v3", dict(V3_PARAMS))
    assert set(SLEEVE) & set(v3.rank_candidates(context)), "fixture must make the sleeve attractive to V3"
    hidden = _context({symbol: closes for symbol, closes in paths.items() if symbol not in SLEEVE})
    expected = v3.decide(hidden)
    target = _strategy().decide(context)
    assert [(w.symbol, w.weight, w.rank) for w in target.weights] == [
        (w.symbol, w.weight, w.rank) for w in expected.weights
    ]
    assert _strategy().rank_candidates(context) == v3.rank_candidates(hidden)
    assert target.strategy_name == "equity_residual_momentum_v3_dividend_sleeve_v1"


def test_decide_carries_equal_sleeve_weights_for_symbols_with_history() -> None:
    paths = {symbol: closes for symbol, closes in _paths().items() if symbol != "DGRO"}
    sleeve = _strategy().decide(_context(paths)).metadata["sleeve"]
    assert sleeve["mode"] == SLEEVE_MODE_DECIDE
    assert sleeve["weights"] == {"SCHD": pytest.approx(0.05), "VIG": pytest.approx(0.05)}
    assert sleeve["core_allocation"] == 0.40


@pytest.mark.parametrize(
    ("decision_day", "expected"),
    [
        (datetime(2024, 10, 1, 4, 0, tzinfo=UTC), SLEEVE_MODE_DECIDE),  # prior week began in September
        (datetime(2024, 10, 7, 4, 0, tzinfo=UTC), SLEEVE_MODE_DECIDE),  # prior week's decision was Sep 30
        (datetime(2024, 10, 14, 4, 0, tzinfo=UTC), SLEEVE_MODE_HOLD),  # prior week's decision was Oct 7
        (datetime(2024, 10, 28, 4, 0, tzinfo=UTC), SLEEVE_MODE_HOLD),
    ],
)
def test_sleeve_is_decided_on_first_weekly_decision_of_each_month(decision_day: datetime, expected: str) -> None:
    context = _context(_paths(), holdings={"SCHD": 10.0}, last=(decision_day - START).days)
    assert context.decision_timestamp == decision_day
    assert _strategy().sleeve_mode(context) == expected


def test_empty_sleeve_is_decided_mid_month() -> None:
    last = (datetime(2024, 10, 14, 4, 0, tzinfo=UTC) - START).days
    assert _strategy().sleeve_mode(_context(_paths(), last=last)) == SLEEVE_MODE_DECIDE


def test_sleeve_symbols_must_not_overlap_v3_reference() -> None:
    with pytest.raises(ValueError, match="sleeve_symbols"):
        portfolio_strategy_from(
            "equity_residual_momentum_v3_dividend_sleeve_v1", {**V3_PARAMS, "sleeve_symbols": ["SPY", "VIG"]}
        )


# --------------------------------------------------------------------------- #
# Overlay composition
# --------------------------------------------------------------------------- #


def test_overlay_caps_core_at_core_allocation_and_appends_sleeve() -> None:
    core = {f"E{index}": 0.10 for index in range(8)}
    decision = _apply(_target(core), _account())
    weights = _weights(decision)
    assert sum(weights[symbol] for symbol in core) == pytest.approx(0.40)
    assert {symbol: weights[symbol] for symbol in SLEEVE} == {symbol: pytest.approx(0.05) for symbol in SLEEVE}
    assert decision.gross_after == pytest.approx(0.55)


def test_hold_mode_keeps_current_sleeve_weights() -> None:
    account = _account(_position("SCHD", 0.06), _position("VIG", 0.045), _position("DGRO", 0.05))
    weights = _weights(_apply(_target({"E0": 0.10}, mode=SLEEVE_MODE_HOLD), account))
    assert {symbol: weights[symbol] for symbol in SLEEVE} == {
        "SCHD": pytest.approx(0.06),
        "VIG": pytest.approx(0.045),
        "DGRO": pytest.approx(0.05),
    }


@pytest.mark.parametrize(
    "latch",
    [
        {"daily_pnl": -600.0},
        {"metadata": {"entry_risk_latch": {"reason": "test"}}},
        {"metadata": {"intermediate_risk_reduction": True}},
    ],
)
def test_risk_latches_never_grow_the_sleeve(latch: dict) -> None:
    account = _account(_position("SCHD", 0.03), daily_pnl=latch.get("daily_pnl", 0.0))
    decision = _apply(_target({"E0": 0.10}, **latch.get("metadata", {})), account)
    weights = _weights(decision)
    assert weights.get("SCHD") == pytest.approx(0.03)
    assert "VIG" not in weights and "DGRO" not in weights
    assert decision.target.metadata["sleeve_reduce_only"] is True


def test_sleeve_is_scaled_into_room_left_by_other_market() -> None:
    core = {f"E{index}": 0.10 for index in range(8)}
    account = _account(_position("BTC/USD", 0.05, Market.CRYPTO), _position("ETH/USD", 0.05, Market.CRYPTO))
    weights = _weights(_apply(_target(core), account))
    # Two crypto slots leave six core names; the sleeve takes no slot.
    assert sum(weight for symbol, weight in weights.items() if symbol in core) == pytest.approx(0.40)
    assert sum(weights[symbol] for symbol in SLEEVE) == pytest.approx(0.10)
    assert weights["SCHD"] == pytest.approx(0.10 / 3)


def test_live_dollar_cap_bounds_core_and_sleeve() -> None:
    core = {f"E{index}": 0.10 for index in range(8)}
    weights = _weights(_apply(_target(core), _account(), maximum_allocation=0.20))
    assert sum(weights.values()) == pytest.approx(0.20)
    assert not set(SLEEVE) & set(weights)


def test_plain_targets_are_unchanged_by_the_sleeve_path() -> None:
    target = replace(_target({"E0": 0.10, "E1": 0.10}), metadata={})
    overlay = PortfolioRiskOverlay(LIMITS)
    decision = overlay.apply(
        target, _account(), market=Market.EQUITY, approved_symbols=EQUITY_SYMBOLS, maximum_allocation=0.55, mode="paper"
    )
    assert _weights(decision) == {"E0": pytest.approx(0.10), "E1": pytest.approx(0.10)}
    assert "sleeve_weights" not in decision.target.metadata


# --------------------------------------------------------------------------- #
# Slot exemption
# --------------------------------------------------------------------------- #


def _crypto_decision(limits: PortfolioRiskLimits):
    account = _account(*(_position(f"E{index}", 0.04) for index in range(5)), *(_position(s, 0.05) for s in SLEEVE))
    target = TargetPortfolio(
        strategy_name="crypto",
        strategy_version="1",
        decision_timestamp=START,
        weights=(TargetWeight("BTC/USD", 0.5, "test", rank=1), TargetWeight("ETH/USD", 0.5, "test", rank=2)),
        cash_weight=0.0,
    )
    return PortfolioRiskOverlay(limits).apply(
        target,
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD"),
        maximum_allocation=0.05,
        mode="paper",
    )


def test_sleeve_holdings_do_not_consume_account_slots() -> None:
    assert set(_weights(_crypto_decision(LIMITS))) == {"BTC/USD", "ETH/USD"}
    without = replace(LIMITS, slot_exempt_equity_symbols=())
    assert _weights(_crypto_decision(without)) == {}


def test_sleeve_buys_are_admitted_without_slots() -> None:
    account = _account(*(_position(f"E{index}", 0.05) for index in range(8)))
    orders = tuple(
        PortfolioOrderRequest(
            f"id-{symbol}",
            "decision",
            symbol,
            Market.EQUITY,
            PortfolioOrderSide.BUY,
            1.0,
            100.0,
            START,
            "s",
            "1",
            False,
        )
        for symbol in ("SCHD", "NEW")
    )
    admitted = reserve_opening_order_slots(orders, account, maximum_positions=8, slot_exempt_equity_symbols=SLEEVE)
    assert [order.symbol for order in admitted] == ["SCHD"]
    assert reserve_opening_order_slots(orders, account, maximum_positions=8) == ()


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_shipped_config_declares_one_sleeve_symbol_set() -> None:
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    assert config.strategy.equity.parameters["sleeve_symbols"] == list(SHIPPED_SLEEVE)
    assert config.universe.equity.sleeve_trade_symbols == SHIPPED_SLEEVE
    assert config.risk.slot_exempt_equity_symbols == SHIPPED_SLEEVE
    assert set(SHIPPED_SLEEVE) <= set(config.universe.equity.ordinary_alpha_excluded_symbols)
    assert len(config.universe.equity.configured_stream_symbols) <= 30


def test_mismatched_sleeve_declarations_fail_closed(tmp_path: Path) -> None:
    text = (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    broken = text.replace(
        "  slot_exempt_equity_symbols: [SCHD, VIG, DGRO, GLD, OUNZ]\n", "  slot_exempt_equity_symbols: [SCHD]\n"
    )
    assert broken != text
    path = tmp_path / "settings.yaml"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="same symbols"):
        load_config(str(path))
