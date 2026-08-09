#!/usr/bin/env python3
"""Download Alpaca paper state read-only and reconcile it with local SQLite."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from parayabuck.monitoring.paper_incident import (
    BrokerFillRecord,
    BrokerOrderRecord,
    BrokerPositionRecord,
    PaperIncidentReconciler,
    PaperIncidentSnapshot,
    write_anonymized_fixture,
)


def _instant(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _get(
    path: str,
    *,
    key: str,
    secret: str,
    params: dict[str, object] | None = None,
) -> object:
    response = requests.get(
        "https://paper-api.alpaca.markets/v2" + path,
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _all_orders(key: str, secret: str, after: str | None) -> list[dict[str, object]]:
    """Read the complete order history without relying on Alpaca's first page."""
    until: str | None = None
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    while True:
        params: dict[str, object] = {
            "status": "all",
            "direction": "desc",
            "limit": 500,
            "nested": "true",
        }
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        page = _get("/orders", key=key, secret=secret, params=params)
        if not isinstance(page, list):
            raise RuntimeError("Alpaca orders response is not a list")
        records = [item for item in page if isinstance(item, dict)]
        for item in records:
            identifier = str(item.get("id", ""))
            if identifier and identifier not in seen:
                seen.add(identifier)
                result.append(item)
        if len(records) < 500:
            break
        timestamps = [str(item.get("created_at", "")) for item in records if item.get("created_at")]
        next_until = min(timestamps, default="")
        if not next_until or next_until == until:
            raise RuntimeError("Alpaca order pagination did not advance")
        until = next_until
    return sorted(result, key=lambda item: _instant(item.get("created_at")))


def _all_fills(key: str, secret: str) -> list[dict[str, object]]:
    """Read all fill activities using Alpaca's activity-id page token."""
    page_token: str | None = None
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    while True:
        params: dict[str, object] = {"direction": "desc", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        page = _get("/account/activities/FILL", key=key, secret=secret, params=params)
        if not isinstance(page, list):
            raise RuntimeError("Alpaca fill activities response is not a list")
        records = [item for item in page if isinstance(item, dict)]
        for item in records:
            identifier = str(item.get("id", ""))
            if identifier and identifier not in seen:
                seen.add(identifier)
                result.append(item)
        if len(records) < 100:
            break
        next_token = str(records[-1].get("id", "")) if records else ""
        if not next_token or next_token == page_token:
            raise RuntimeError("Alpaca fill pagination did not advance")
        page_token = next_token
    return sorted(
        result,
        key=lambda item: _instant(item.get("transaction_time") or item.get("date")),
    )


def _snapshot(key: str, secret: str, after: str | None) -> PaperIncidentSnapshot:
    raw_orders = _all_orders(key, secret, after)
    raw_fills = _all_fills(key, secret)
    raw_positions = _get("/positions", key=key, secret=secret)
    if not isinstance(raw_positions, list):
        raise RuntimeError("Alpaca positions response is not a list")
    orders = tuple(
        BrokerOrderRecord(
            order_id=str(item.get("id", "")),
            client_order_id=str(item.get("client_order_id", "")),
            symbol=str(item.get("symbol", "")),
            side=str(item.get("side", "")),
            quantity=float(str(item.get("qty", 0) or 0)),
            filled_quantity=float(str(item.get("filled_qty", 0) or 0)),
            status=str(item.get("status", "")),
            created_at=_instant(item.get("created_at")),
        )
        for item in raw_orders
        if isinstance(item, dict)
    )
    fills = tuple(
        BrokerFillRecord(
            activity_id=str(item.get("id", "")),
            order_id=str(item.get("order_id", "")),
            symbol=str(item.get("symbol", "")),
            side=str(item.get("side", "")),
            quantity=abs(float(str(item.get("qty", 0) or 0))),
            price=float(str(item.get("price", 0) or 0)),
            fee=abs(float(str(item.get("commission", item.get("fee", 0)) or 0))),
            occurred_at=_instant(item.get("transaction_time") or item.get("date")),
        )
        for item in raw_fills
        if isinstance(item, dict)
    )
    positions = tuple(
        BrokerPositionRecord(
            symbol=str(item.get("symbol", "")),
            quantity=float(str(item.get("qty", 0) or 0)),
            average_entry_price=float(str(item.get("avg_entry_price", 0) or 0)),
            current_price=float(str(item.get("current_price", 0) or 0)),
        )
        for item in raw_positions
        if isinstance(item, dict)
    )
    return PaperIncidentSnapshot(orders, fills, positions)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--after", help="ISO-8601 start time for order history")
    parser.add_argument("--report", default="reports/paper_incident/report.json")
    parser.add_argument("--fixture", default="tests/fixtures/paper_incident_replay.json")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print broker-linked reconciliation identifiers to stdout",
    )
    args = parser.parse_args(argv)
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
    snapshot = _snapshot(key, secret, args.after)
    reconciler = PaperIncidentReconciler()
    report = reconciler.reconcile(snapshot, args.database)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    write_anonymized_fixture(args.fixture, reconciler.anonymized_replay_fixture(snapshot, report))
    if not args.quiet:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
