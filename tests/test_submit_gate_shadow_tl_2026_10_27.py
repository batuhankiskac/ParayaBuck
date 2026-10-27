"""Submit-time market clock gate (item 17) and the TL shadow gain fraction (item 48).

Every fixture is synthetic and deterministic: fake brokers, ``tmp_path`` state,
and TCMB rate files written under ``tmp_path``. No network, no production path,
no credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from parayabuck.config import DownsizeGainBand
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_client import BrokerClock
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    GainBandHold,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    TargetToOrderPlanner,
    downsize_gain_band_tl_verdict,
    downsize_gain_fraction,
    downsize_gain_fraction_tl,
)
from parayabuck.monitoring import alerts
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.ownership_rebase import BUY, SELL
from parayabuck.portfolio.runtime_service import market_clock_refusal
from parayabuck.tax.rates import USDTRY_COLUMN, YIUFE_COLUMN
from tests.test_two_phase_rebalance import _bars, _runtime, _SimBroker

NEW_YORK = ZoneInfo("America/New_York")
T0 = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
OPEN_CLOCK = BrokerClock(
    timestamp=datetime(2026, 10, 27, 15, 0, tzinfo=UTC),
    is_open=True,
    next_open=datetime(2026, 10, 28, 13, 30, tzinfo=UTC),
    next_close=datetime(2026, 10, 27, 20, 0, tzinfo=UTC),
)
CLOSED_CLOCK = BrokerClock(
    timestamp=datetime(2026, 10, 27, 23, 0, tzinfo=UTC),
    is_open=False,
    next_open=datetime(2026, 10, 28, 13, 30, tzinfo=UTC),
    next_close=datetime(2026, 10, 28, 20, 0, tzinfo=UTC),
)


class _ClockBroker(_SimBroker):
    """Controlled broker that also exposes a scripted ``clock()``."""

    def __init__(self, clock_value: BrokerClock | Exception) -> None:
        super().__init__()
        self.clock_value = clock_value
        self.clock_calls = 0

    def clock(self) -> BrokerClock:
        self.clock_calls += 1
        if isinstance(self.clock_value, Exception):
            raise self.clock_value
        return self.clock_value


@pytest.fixture
def _captured_alerts(monkeypatch):
    emitted: list[dict[str, object]] = []

    def fake_emit(event: str, **fields: object) -> None:
        emitted.append({"event": event, **fields})

    monkeypatch.setattr(alerts, "emit", fake_emit)
    return emitted


def _order(decision_id: str, symbol: str, side: PortfolioOrderSide, quantity: float, *, client_order_id: str):
    return PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id=decision_id,
        symbol=symbol,
        market=Market.EQUITY,
        side=side,
        quantity=quantity,
        reference_price=100.0,
        decision_timestamp=T0,
        strategy_name="vt35",
        strategy_version="1.2.0",
    )


def _status_of(repository, decision_id: str, client_order_id: str) -> str:
    rows = repository.orders_for_decision(decision_id)
    return next(str(row["status"]) for row in rows if str(row["client_order_id"]) == client_order_id)


def _runtime_with_decision(tmp_path, broker):
    """A runtime whose first decision was recorded while the broker clock was open."""
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    broker.open_symbols.clear()
    return runtime, repository, first.decision_id


# ---------------------------------------------------------------------------
# Clock gate: pure refusal rule
# ---------------------------------------------------------------------------


def test_closed_clock_allows_a_day_market_order() -> None:
    # Decisions are made after the close; Alpaca queues the DAY order for the next open.
    assert market_clock_refusal(CLOSED_CLOCK, time_in_force="day") is None


def test_open_clock_allows_a_day_market_order() -> None:
    assert market_clock_refusal(OPEN_CLOCK, time_in_force="day") is None


def test_opg_order_after_the_0928_et_cutoff_is_refused() -> None:
    late = BrokerClock(
        timestamp=datetime(2026, 10, 28, 9, 29, tzinfo=NEW_YORK),
        is_open=False,
        next_open=datetime(2026, 10, 28, 9, 30, tzinfo=NEW_YORK),
        next_close=datetime(2026, 10, 28, 16, 0, tzinfo=NEW_YORK),
    )
    refusal = market_clock_refusal(late, time_in_force="opg")
    assert refusal is not None and refusal[0] == "opg_after_cutoff"
    assert "09:28 ET" in refusal[1]


def test_opg_order_before_the_cutoff_is_allowed() -> None:
    early = BrokerClock(
        timestamp=datetime(2026, 10, 28, 9, 27, tzinfo=NEW_YORK),
        is_open=False,
        next_open=datetime(2026, 10, 28, 9, 30, tzinfo=NEW_YORK),
        next_close=datetime(2026, 10, 28, 16, 0, tzinfo=NEW_YORK),
    )
    assert market_clock_refusal(early, time_in_force="opg") is None


def test_opg_order_while_open_targets_the_next_session() -> None:
    # Market open at 10:00 ET: the OPG targets Thursday's open, whose cutoff is still ahead.
    open_late = BrokerClock(
        timestamp=datetime(2026, 10, 28, 10, 0, tzinfo=NEW_YORK),
        is_open=True,
        next_open=datetime(2026, 10, 29, 9, 30, tzinfo=NEW_YORK),
        next_close=datetime(2026, 10, 28, 16, 0, tzinfo=NEW_YORK),
    )
    assert market_clock_refusal(open_late, time_in_force="opg") is None


# ---------------------------------------------------------------------------
# Clock gate: runtime submit path
# ---------------------------------------------------------------------------


def test_closed_clock_queues_a_day_order_for_the_next_open(tmp_path) -> None:
    broker = _ClockBroker(OPEN_CLOCK)
    runtime, repository, decision_id = _runtime_with_decision(tmp_path, broker)
    broker.clock_value = CLOSED_CLOCK
    sent_before = len(broker.submitted)
    order = _order(decision_id, "AAPL", PortfolioOrderSide.BUY, 10.0, client_order_id="closed-aapl")

    submitted = runtime._submit_orders((order,))

    assert len(submitted) == 1
    assert [sent.client_order_id for sent in broker.submitted[sent_before:]] == ["closed-aapl"]
    assert _status_of(repository, decision_id, "closed-aapl") == "submitted"


def test_day_batch_submits_without_a_clock_read(tmp_path) -> None:
    # A DAY batch is never refused by the clock, so the REST clock is not read at all.
    broker = _ClockBroker(OPEN_CLOCK)
    runtime, repository, decision_id = _runtime_with_decision(tmp_path, broker)
    sent_before = len(broker.submitted)
    order = _order(decision_id, "AAPL", PortfolioOrderSide.BUY, 10.0, client_order_id="open-aapl")

    submitted = runtime._submit_orders((order,))

    assert len(submitted) == 1
    assert broker.clock_calls == 0
    assert [sent.client_order_id for sent in broker.submitted[sent_before:]] == ["open-aapl"]
    assert _status_of(repository, decision_id, "open-aapl") == "submitted"


def test_failing_clock_does_not_affect_a_day_batch(tmp_path, monkeypatch) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(runtime_service.log, "warning", lambda message, *args, **kwargs: warnings.append(message))
    broker = _ClockBroker(RuntimeError("clock endpoint down"))
    runtime, repository, decision_id = _runtime_with_decision(tmp_path, broker)
    sent_before = len(broker.submitted)
    order = _order(decision_id, "AAPL", PortfolioOrderSide.SELL, 10.0, client_order_id="exit-aapl")

    submitted = runtime._submit_orders((order,))

    assert len(submitted) == 1
    assert broker.clock_calls == 0
    assert [sent.client_order_id for sent in broker.submitted[sent_before:]] == ["exit-aapl"]
    assert _status_of(repository, decision_id, "exit-aapl") == "submitted"
    assert not any("clock" in message.lower() for message in warnings)


def test_broker_without_clock_keeps_todays_behaviour(tmp_path) -> None:
    broker = _SimBroker()
    assert not hasattr(broker, "clock")
    runtime, repository, decision_id = _runtime_with_decision(tmp_path, broker)
    sent_before = len(broker.submitted)
    order = _order(decision_id, "AAPL", PortfolioOrderSide.BUY, 10.0, client_order_id="plain-aapl")

    submitted = runtime._submit_orders((order,))

    assert len(submitted) == 1
    assert [sent.client_order_id for sent in broker.submitted[sent_before:]] == ["plain-aapl"]


# ---------------------------------------------------------------------------
# TL shadow gain fraction: pure arithmetic
# ---------------------------------------------------------------------------

# Two lots, sale of 15 units at USD 150. USD: lot1 10 x (150-100) + lot2 5 x (150-120) = 650 over 15 x 150.
# TL: buy-date rates 30 and 32, decision rate 35. Lot1 3000 TRY/unit, lot2 3840 TRY/unit, sale 5250 TRY/unit:
# 10 x 2250 + 5 x 1410 = 29550 over 15 x 5250.
LOTS_USD = ((10.0, 100.0), (10.0, 120.0))


def test_tl_fraction_is_the_two_lot_fifo_fraction_in_lira() -> None:
    usd = downsize_gain_fraction(LOTS_USD, 15.0, 150.0)
    tl = downsize_gain_fraction_tl(
        [(10.0, 100.0, 30.0), (10.0, 120.0, 32.0)],
        15.0,
        150.0,
        35.0,
    )
    assert usd == pytest.approx(650.0 / 2250.0)
    assert tl == pytest.approx(29550.0 / 78750.0)


def test_tl_fraction_is_none_when_lots_do_not_cover_the_sale() -> None:
    assert downsize_gain_fraction_tl([(10.0, 100.0, 30.0)], 15.0, 150.0, 35.0) is None


def test_tl_verdict_uses_the_band_of_the_tl_fraction() -> None:
    schedule = DownsizeGainBand()
    # 0.375 selects the top tier (8 %): a 5 pp gap is held, a 10 pp gap sells.
    assert downsize_gain_band_tl_verdict(schedule, gap=0.05, tl_fraction=0.375) == "hold"
    assert downsize_gain_band_tl_verdict(schedule, gap=0.10, tl_fraction=0.375) == "sell"


def test_hold_metadata_carries_the_tl_shadow_only_when_present() -> None:
    plain = GainBandHold("QLD", 0.5, 0.08, 0.05).as_metadata()
    assert "gain_fraction_tl_shadow" not in plain
    assert "gain_band_tl_shadow_verdict" not in plain
    shadowed = GainBandHold("QLD", 0.5, 0.08, 0.05, 0.375, "hold").as_metadata()
    assert shadowed["gain_fraction_tl_shadow"] == pytest.approx(0.375)
    assert shadowed["gain_band_tl_shadow_verdict"] == "hold"
    assert {key: value for key, value in shadowed.items() if key in plain} == plain


# ---------------------------------------------------------------------------
# TL shadow gain fraction: runtime probe with synthetic rates and fills
# ---------------------------------------------------------------------------


def _write_rates(root: Path) -> None:
    data = root / "data" / "tax"
    data.mkdir(parents=True)
    (data / "usdtry.csv").write_text(
        f"date,{USDTRY_COLUMN}\n2026-09-30,30.0\n2026-10-09,32.0\n2026-10-19,35.0\n",
        encoding="utf-8",
    )
    (data / "yiufe.csv").write_text(f"month,{YIUFE_COLUMN}\n2026-09,1500.0\n", encoding="utf-8")


def _fills_with_a_consumed_lot() -> list[tuple[datetime, str, float, float | None, float]]:
    """A first buy fully sold, then the two lots still open; events in replay order."""
    return [
        (datetime(2026, 9, 1, 10, tzinfo=UTC), BUY, 5.0, 80.0, 0.0),
        (datetime(2026, 9, 15, 10, tzinfo=UTC), SELL, 5.0, 90.0, 0.0),
        (datetime(2026, 10, 1, 10, tzinfo=UTC), BUY, 10.0, 100.0, 0.0),
        (datetime(2026, 10, 10, 10, tzinfo=UTC), BUY, 10.0, 120.0, 0.0),
    ]


def test_runtime_probe_computes_the_tl_fraction_for_a_two_lot_book(tmp_path, monkeypatch) -> None:
    runtime, repository, _ = _runtime_with_decision(tmp_path, _SimBroker())
    _write_rates(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(repository, "_owned_events", lambda *args, **kwargs: _fills_with_a_consumed_lot())

    probe = runtime._gain_band_tl_shadow_probe()
    fraction = probe("AAPL", LOTS_USD, 15.0, 150.0, datetime(2026, 10, 20, 10, tzinfo=UTC))

    assert fraction == pytest.approx(29550.0 / 78750.0)


def test_runtime_probe_skips_silently_without_rate_files(tmp_path, monkeypatch) -> None:
    runtime, repository, _ = _runtime_with_decision(tmp_path, _SimBroker())
    empty_root = tmp_path / "no_rates"
    empty_root.mkdir()
    monkeypatch.chdir(empty_root)
    monkeypatch.setattr(repository, "_owned_events", lambda *args, **kwargs: _fills_with_a_consumed_lot())

    probe = runtime._gain_band_tl_shadow_probe()

    assert probe("AAPL", LOTS_USD, 15.0, 150.0, datetime(2026, 10, 20, 10, tzinfo=UTC)) is None


def test_runtime_probe_skips_when_the_buy_history_is_shorter_than_the_lots(tmp_path, monkeypatch) -> None:
    runtime, repository, _ = _runtime_with_decision(tmp_path, _SimBroker())
    _write_rates(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(repository, "_owned_events", lambda *args, **kwargs: _fills_with_a_consumed_lot()[-1:])

    probe = runtime._gain_band_tl_shadow_probe()

    assert probe("AAPL", LOTS_USD, 15.0, 150.0, datetime(2026, 10, 20, 10, tzinfo=UTC)) is None


# ---------------------------------------------------------------------------
# Planner: the USD decision is identical with or without the shadow
# ---------------------------------------------------------------------------

SYMBOL = "QLD"


def _planner_target(weight: float) -> TargetPortfolio:
    return TargetPortfolio("vt35", "1.2.0", T0, (TargetWeight(SYMBOL, weight),), 1.0 - weight, universe_version="t")


def _planner_account() -> AccountSnapshot:
    # 900 shares at $100 on $100k is 0.90 of equity, all managed and bought at $50.
    position = PositionSnapshot(
        SYMBOL, 900.0, 100.0, 100.0, Market.EQUITY, qty_available=900.0, ownership="managed", managed_qty=900.0
    )
    return AccountSnapshot(cash=10_000.0, equity=100_000.0, peak_equity=100_000.0, daily_pnl=0.0, positions=(position,))


def _plan_downsize(shadow=None):
    holds: list[GainBandHold] = []
    orders = TargetToOrderPlanner(PortfolioExecutionPolicy(downsize_gain_band=DownsizeGainBand())).plan(
        _planner_target(0.85),
        _planner_account(),
        market=Market.EQUITY,
        prices={SYMBOL: 100.0},
        rules={SYMBOL: AssetTradingRules(0.001, 0.001, 1.0)},
        approved_symbols=(SYMBOL,),
        decision_id="shadow-decision",
        open_lots={SYMBOL: [(900.0, 50.0)]},
        gain_band_holds=holds,
        gain_band_tl_shadow=shadow,
    )
    return orders, holds


def test_planner_records_the_tl_shadow_next_to_the_usd_hold() -> None:
    calls: list[tuple[str, object, float, float]] = []

    def shadow(symbol, lots, units, price, decision_timestamp):
        calls.append((symbol, tuple(lots), units, price))
        return 0.375

    plain_orders, plain_holds = _plan_downsize()
    orders, holds = _plan_downsize(shadow)

    assert orders == plain_orders == ()
    assert calls == [(SYMBOL, ((900.0, 50.0),), pytest.approx(50.0), 100.0)]
    (hold,) = holds
    assert hold.g_frac == pytest.approx(0.5)
    assert hold.as_metadata()["gain_fraction_tl_shadow"] == pytest.approx(0.375)
    assert hold.as_metadata()["gain_band_tl_shadow_verdict"] == "hold"
    assert set(hold.as_metadata()) - set(plain_holds[0].as_metadata()) == {
        "gain_fraction_tl_shadow",
        "gain_band_tl_shadow_verdict",
    }


def test_missing_rates_leave_the_shadow_fields_absent_and_the_usd_verdict_identical(tmp_path, monkeypatch) -> None:
    runtime, _, _ = _runtime_with_decision(tmp_path, _SimBroker())
    empty_root = tmp_path / "no_rates"
    empty_root.mkdir()
    monkeypatch.chdir(empty_root)

    plain_orders, plain_holds = _plan_downsize()
    orders, holds = _plan_downsize(runtime._gain_band_tl_shadow_probe())

    assert orders == plain_orders
    (hold,) = holds
    assert "gain_fraction_tl_shadow" not in hold.as_metadata()
    assert "gain_band_tl_shadow_verdict" not in hold.as_metadata()
    assert hold.as_metadata() == plain_holds[0].as_metadata()


def test_a_failing_shadow_never_changes_the_usd_decision() -> None:
    def broken(symbol, lots, units, price, decision_timestamp):
        raise RuntimeError("shadow bug")

    plain_orders, plain_holds = _plan_downsize()
    orders, holds = _plan_downsize(broken)

    assert orders == plain_orders
    assert holds[0].as_metadata() == plain_holds[0].as_metadata()
