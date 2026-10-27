"""Orchestrator follow-ups that join two workers' fixes (audit 2026-10-27)."""

from __future__ import annotations

from parayabuck.monitoring.daily_summary import format_tax_ytd


def test_tax_line_names_missing_tables() -> None:
    assert format_tax_ytd({"status": "tables_missing"}) == "tax: ledger tables missing"


def test_tax_line_flags_missing_income_rows() -> None:
    line = format_tax_ytd(
        {
            "status": "ok",
            "year": 2026,
            "net_gain_tl": 100.0,
            "estimated_tax_tl": 15.0,
            "income_declaration_status": "veri eksik",
        }
    )
    assert line.endswith(", income data missing")


def test_tax_line_without_income_flag_is_unchanged() -> None:
    line = format_tax_ytd({"status": "ok", "year": 2026, "net_gain_tl": 100.0, "estimated_tax_tl": 15.0})
    assert line == "tax YTD 2026: net gain +100.00 TL, est. tax 15.00 TL"


def _gate_runtime(*, running: bool = True, breaker: str | None = None, state=None):
    from parayabuck.runtime import EngineRuntime, TradingState

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = running
    runtime._circuit_breaker_reason = breaker
    runtime.trading_state = lambda market: state or TradingState.ACTIVE
    return runtime


def test_advance_submit_gate_holds_a_buy_when_the_market_stops_being_active() -> None:
    from types import SimpleNamespace

    from parayabuck.core.models import Market
    from parayabuck.execution.portfolio import PortfolioOrderSide
    from parayabuck.runtime import TradingState

    buy = SimpleNamespace(side=PortfolioOrderSide.BUY)
    sell = SimpleNamespace(side=PortfolioOrderSide.SELL)
    halted = _gate_runtime(state=TradingState.HALTED)
    assert halted._advance_submit_block_reason(Market.EQUITY, buy) == "buy_not_permitted"
    # SELLs keep flowing under a latch or halt, as on the decision path.
    assert halted._advance_submit_block_reason(Market.EQUITY, sell) is None
    assert _gate_runtime()._advance_submit_block_reason(Market.EQUITY, buy) is None


def test_advance_submit_gate_holds_every_order_when_stopping_or_tripped() -> None:
    from types import SimpleNamespace

    from parayabuck.core.models import Market
    from parayabuck.execution.portfolio import PortfolioOrderSide

    sell = SimpleNamespace(side=PortfolioOrderSide.SELL)
    assert _gate_runtime(running=False)._advance_submit_block_reason(Market.EQUITY, sell) == "engine_not_running"
    assert _gate_runtime(breaker="x")._advance_submit_block_reason(Market.EQUITY, sell) == "circuit_breaker"


def test_locked_pin_check_reads_a_hashed_export(tmp_path) -> None:
    import importlib.metadata

    from parayabuck.cli import _locked_pin_mismatches

    installed = importlib.metadata.version("pyyaml")
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        f"pyyaml=={installed} \\\n"
        "    --hash=sha256:00\n"
        f"pyyaml=={installed} ; python_version >= '3.11' \\\n"
        "    --hash=sha256:11\n",
        encoding="utf-8",
    )
    assert _locked_pin_mismatches(lock) == (2, [])
