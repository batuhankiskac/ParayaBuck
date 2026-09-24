"""No-return chronology audit of the archived corrected reversal replay."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
OLD = "a3a54cff64a9312300ef700f2fe9409435a064d3"
PHYSICAL = "11890e93ad1a5223cb11483488deb4d5a6aec3d3"
ARCHIVE = "research/residual_reversal_local_v1/corrected_replay_v1/"
PHYSICAL_PATH = "research/physical_availability_clock_v2/"


def tagged_json(ref: str, path: str) -> dict[str, Any]:
    raw = subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)
    result: dict[str, Any] = json.loads(raw)
    return result


def tagged_bytes(ref: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)


def _session(date_value: date) -> pd.Timestamp:
    return pd.Timestamp(date_value)


def audit() -> dict[str, Any]:
    calendar = xcals.get_calendar("XNYS", start="2016-01-01", end="2026-12-31")
    archived = tagged_json(OLD, ARCHIVE + "execution_audit.json")["reversal_1x"]
    coverage = tagged_json(OLD, ARCHIVE + "decision_coverage.json")["runs"]["reversal_1x"]
    lifecycle = tagged_json(OLD, ARCHIVE + "lifecycle_audit.json")["runs"]["reversal_1x"]
    manifest = tagged_json(OLD, ARCHIVE + "replay_manifest.json")
    v2_identity = tagged_json(PHYSICAL, PHYSICAL_PATH + "EQUITY_PHYSICAL_PARITY_V2.json")["input_identity"]
    timing_sha = hashlib.sha256(tagged_bytes(PHYSICAL, PHYSICAL_PATH + "timing.py")).hexdigest()

    decisions = archived["rebalances"]
    recorded = [row for row in coverage["attempts"] if row["decision_recorded"]]
    assert len(decisions) == len(recorded) == 495
    decision_labels = [row["decision_timestamp"] for row in decisions]
    assert len(set(decision_labels)) == len(decision_labels)
    assert decision_labels == [row["timestamp"] for row in recorded]
    target_symbols = {
        row["decision_timestamp"]: {symbol for symbol, weight in row["target_weights"] if weight > 0}
        for row in decisions
    }

    next_open: dict[str, datetime] = {}
    for label in decision_labels:
        session_date = datetime.fromisoformat(label).date()
        session = _session(session_date)
        assert calendar.is_session(session), label
        close = calendar.session_close(session).to_pydatetime().astimezone(UTC)
        opening = calendar.session_open(calendar.next_session(session)).to_pydatetime().astimezone(UTC)
        assert datetime.fromisoformat(label) < close < opening
        next_open[label] = opening

    fill_keys: set[tuple[str, str, str]] = set()
    fills_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for fill in archived["fills"]:
        label = str(fill["decision_timestamp"])
        key = (label, str(fill["symbol"]), str(fill["side"]))
        assert key not in fill_keys, key  # this archived run has no partial fill groups
        fill_keys.add(key)
        physical_open = next_open[label]
        fill_date = datetime.fromisoformat(fill["timestamp"]).date()
        assert fill_date == physical_open.date(), key
        assert physical_open > datetime.fromisoformat(label)
        if fill["side"] == "buy":
            assert fill["symbol"] in target_symbols[label], key
        fills_by_date[fill_date].append(fill)
    assert len(fill_keys) == 3348

    actions_path = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
    lifetimes_path = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
    action_sha = hashlib.sha256(actions_path.read_bytes()).hexdigest()
    lifetime_sha = hashlib.sha256(lifetimes_path.read_bytes()).hexdigest()
    assert action_sha == manifest["corporate_actions_sha256"] == v2_identity["corporate_actions_sha256"]
    assert lifetime_sha == manifest["lifetime_table_sha256"] == v2_identity["lifetimes_sha256"]

    # Both engines apply ex-date actions before the day's opening fills. Count
    # entitlements from the archived inventory, without evaluating cash/P&L.
    dividends: dict[date, set[str]] = defaultdict(set)
    with actions_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["action_type"] == "cash_dividends":
                ex_date = date.fromisoformat(row["ex_date"])
                effective = calendar.date_to_session(_session(ex_date), direction="next").date()
                dividends[effective].add(row["symbol"])
    inventory: dict[str, float] = defaultdict(float)
    entitled_symbol_events = 0
    for session in calendar.sessions_in_range(_session(date(2017, 1, 1)), _session(date(2026, 7, 31))):
        day = session.date()
        # Non-session ex-dates in the canonical queue become effective at the
        # next real session open, as in physical V2 timing.py.
        entitled_symbol_events += sum(inventory[symbol] > 0 for symbol in dividends.get(day, ()))
        for fill in fills_by_date.get(day, ()):
            symbol = str(fill["symbol"])
            qty = float(fill["qty"])
            inventory[symbol] += qty if fill["side"] == "buy" else -qty
            assert inventory[symbol] >= -1e-8, (day, symbol)

    assert lifecycle["terminal_bar_events"] == []
    return {
        "classification": "REVERSAL_PHYSICAL_CLOCK_REVALIDATION_REQUIRED",
        "archived_candidate_commit": OLD,
        "physical_clock_commit": PHYSICAL,
        "physical_timing_sha256": timing_sha,
        "decision_sessions_checked": len(decision_labels),
        "archived_fill_groups_checked": len(fill_keys),
        "archived_buy_target_symbol_mismatches": 0,
        "first_decision_session": decision_labels[0],
        "last_decision_session": decision_labels[-1],
        "non_xnys_decisions": 0,
        "fill_session_mismatches": 0,
        "corporate_action_source_sha256": action_sha,
        "lifetime_source_sha256": lifetime_sha,
        "archived_dividend_entitled_symbol_events": entitled_symbol_events,
        "archived_reversal_terminal_bar_events": 0,
        "remaining_gate": "Run isolated reversal through physical V2 and compare regenerated target identities/weights, actual orders/fills, per-event dividend entitlement, and terminal lifecycle events; archived fill remapping alone does not prove physical replay parity.",
    }


def main() -> None:
    receipt = audit()
    (OUT / "REVERSAL_PHYSICAL_CLOCK_EQUIVALENCE.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "REVERSAL_PHYSICAL_CLOCK_EQUIVALENCE.md").write_text(
        "# Residual reversal physical-clock equivalence\n\n"
        f"Classification: `{receipt['classification']}`.\n\n"
        f"The archived structural audit mapped {receipt['decision_sessions_checked']} eligible weekly decisions "
        f"and {receipt['archived_fill_groups_checked']} fill groups to official XNYS closes and immediate next-session opens. "
        "No decision or fill-session mismatch was found. The archived and physical V2 corporate-action and "
        "lifetime source hashes match. Both process ex-date actions before opening fills; the archived fill "
        f"inventory had {receipt['archived_dividend_entitled_symbol_events']} dividend-entitled symbol events. "
        "The standalone reversal archive recorded zero terminal-bar events.\n\n"
        "The archived bar labels are New York midnight timestamps; physical order creation occurs at the "
        "official session close. This date mapping alone cannot prove full candidate replay identity. "
        f"{receipt['remaining_gate']}\n\nNo economic outcomes were calculated or reported.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
