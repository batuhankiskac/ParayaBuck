"""Premarket broker probe: clock skew, session calendar and open-order parity.

The probe itself is read-only. It reads the broker clock and the broker's
working orders, compares them with the local clock, the NYSE calendar and the
local non-terminal client order ids, and reports every disagreement as a reason.

The runtime wires it in: ``EngineRuntime._maybe_run_premarket_probe`` runs it
once per NYSE session, 30 to 5 minutes before the open. A failure latches the
reconcile-pending gate, so new BUY orders wait for a clean reconciliation.
Exits are not blocked and nothing halts. The probe gates nothing on market
hours: the bot intentionally submits DAY market orders after the close and
Alpaca queues them for the next session.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from parayabuck.markets.nyse_calendar import NEW_YORK, calendar_degraded_reason
from parayabuck.utils.objects import field_value


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def premarket_probe(
    broker: Any,
    *,
    now: datetime,
    calendar_sessions: Iterable[tuple[date, datetime, datetime]],
    open_orders_local: Iterable[str],
    max_clock_skew_seconds: float = 2.0,
    calendar_degraded: bool | None = None,
) -> ProbeResult:
    """Check the broker against local time, the NYSE calendar and local orders.

    ``calendar_sessions`` are ``(session_date, open_utc, close_utc)`` tuples as
    returned by ``parayabuck.markets.nyse_calendar.sessions_around``. The
    expected ``next_open`` / ``next_close`` are the first session boundaries
    strictly after ``now``, which is what Alpaca reports for the clock.

    While the calendar is degraded (``calendar_degraded``; default: the calendar
    module's own state) the fallback counts every weekday, holidays included,
    so the next_open/next_close comparison would fail on every holiday. Those
    comparisons are skipped; clock skew and open-order parity still run.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("premarket_probe needs a timezone-aware now")
    reasons: list[str] = []

    started = time.monotonic()
    try:
        clock = broker.clock()
    except Exception as exc:
        return ProbeResult(ok=False, reasons=[f"broker clock unavailable: {type(exc).__name__}: {exc}"])
    ended_at = now + timedelta(seconds=time.monotonic() - started)

    # The broker stamped its clock somewhere inside the REST round trip, so the
    # true skew lies between these two bounds. Only a skew that is over the
    # limit on the whole interval is reported: a slow response is not skew.
    skew_low = (clock.timestamp - ended_at).total_seconds()
    skew_high = (clock.timestamp - now).total_seconds()
    if skew_low > max_clock_skew_seconds or skew_high < -max_clock_skew_seconds:
        skew = (skew_low + skew_high) / 2
        reasons.append(f"clock skew {skew:+.3f}s exceeds {max_clock_skew_seconds:.3f}s")

    if calendar_degraded is None:
        calendar_degraded = calendar_degraded_reason() is not None
    sessions = tuple(calendar_sessions)
    expected_next_open = next((opened for _, opened, _ in sorted(sessions, key=lambda s: s[1]) if opened > now), None)
    expected_next_close = next((closed for _, _, closed in sorted(sessions, key=lambda s: s[2]) if closed > now), None)
    if expected_next_open is None or expected_next_close is None:
        reasons.append(
            f"NYSE calendar has no session after {now.astimezone(NEW_YORK).date().isoformat()}; cannot verify broker clock"
        )
    elif not calendar_degraded:
        if clock.next_open != expected_next_open:
            reasons.append(
                f"broker next_open {clock.next_open.isoformat()} disagrees with NYSE calendar "
                f"{expected_next_open.isoformat()}"
            )
        if clock.next_close != expected_next_close:
            reasons.append(
                f"broker next_close {clock.next_close.isoformat()} disagrees with NYSE calendar "
                f"{expected_next_close.isoformat()}"
            )

    try:
        working = broker.working_orders()
    except Exception as exc:
        reasons.append(f"broker open orders unavailable: {type(exc).__name__}: {exc}")
    else:
        broker_ids: set[str] = set()
        for order in working or ():
            client_order_id = str(field_value(order, "client_order_id", "") or "")
            if not client_order_id:
                reasons.append("broker open order without a client_order_id")
                continue
            broker_ids.add(client_order_id)
        local_ids = {str(order_id) for order_id in open_orders_local if str(order_id)}
        for client_order_id in sorted(broker_ids - local_ids):
            reasons.append(f"unknown broker open order {client_order_id}")
        for client_order_id in sorted(local_ids - broker_ids):
            reasons.append(f"local non-terminal order {client_order_id} missing at broker")

    return ProbeResult(ok=not reasons, reasons=reasons)


__all__ = ["ProbeResult", "premarket_probe"]
