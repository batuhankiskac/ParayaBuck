#!/usr/bin/env python3
"""Acquire a small, systematic SEC fundamentals/Form 4 pilot.

This is a research-only downloader.  It refuses to invent SEC contact
identity: the operator must provide an approved SEC_USER_AGENT value that
identifies the requester and a contact address.  Raw responses and a
sanitized receipt are written under a new immutable research directory; no
runtime cache or production input is touched.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

import requests


REPO = Path(__file__).resolve().parents[2]
LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
ROOT = Path(
    os.environ.get(
        "PARAYABUCK_SEC_PILOT_ROOT",
        str(REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07"),
    )
).resolve()
MANIFEST = REPO / "data/research_manifests/equity_fundamentals_pilot_2026_09_07.json"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
DATA_URL = "https://data.sec.gov"
PILOT_COUNT = 20
MIN_START = "2016-01-04T05:00:00+00:00"
MAX_END = "2026-07-30T04:00:00+00:00"
CONCEPTS = (
    "Assets",
    "StockholdersEquity",
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "GrossProfit",
    "NetIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsOfDividends",
    "PaymentsForRepurchaseOfCommonStock",
    "PaymentsForRepurchaseOfEquity",
    "ProceedsFromStockOptionsExercised",
    "ProceedsFromIssuanceOfCommonStock",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved_user_agent() -> str:
    value = os.environ.get("SEC_USER_AGENT", "").strip()
    if not value:
        raise RuntimeError(
            "BLOCKED_MISSING_APPROVED_SEC_USER_AGENT: set SEC_USER_AGENT to "
            "the operator-approved requester identity and contact address"
        )
    if "@" not in value:
        raise RuntimeError("BLOCKED_SEC_USER_AGENT_MISSING_CONTACT_ADDRESS")
    return value


def pilot_symbols() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with LIFETIMES.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (
                row["current_status"] == "active"
                and row["exchange"] in {"NYSE", "NASDAQ"}
                and (row["valid_from"] or "") <= MIN_START
                and (row["last_observed_bar"] or "") >= MAX_END
            ):
                rows.append(
                    {
                        "symbol": row["canonical_symbol"],
                        "provider_asset_id": row["provider_asset_id"],
                        "exchange": row["exchange"],
                    }
                )
    return sorted(rows, key=lambda row: row["symbol"])


def fetch_json(session: requests.Session, url: str, *, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
    try:
        response = session.get(url, headers=headers, timeout=35, allow_redirects=False)
    except requests.RequestException as exc:
        # Keep the receipt useful without persisting URL/query-bearing transport text.
        return 599, b"", {"transport_error": type(exc).__name__}
    return int(response.status_code), bytes(response.content), {
        str(key): str(value) for key, value in response.headers.items()
    }


def write_receipt(*, status: str, retrieval_at: str, requests_seen: list[dict[str, Any]], error: str | None = None) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, Any] = {
        "status": status,
        "retrieved_at": retrieval_at,
        "provider": "U.S. Securities and Exchange Commission EDGAR",
        "requests": requests_seen,
        "credential_fields_persisted": False,
        "response_bodies_persisted": status == "COMPLETE",
    }
    if error:
        receipt["error"] = error
    (ROOT / "acquisition_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    retrieval_at = datetime.now(UTC).isoformat()
    requests_seen: list[dict[str, Any]] = []
    try:
        user_agent = approved_user_agent()
    except RuntimeError as exc:
        write_receipt(
            status="BLOCKED_MISSING_APPROVED_SEC_USER_AGENT",
            retrieval_at=retrieval_at,
            requests_seen=[],
            error=str(exc),
        )
        print(str(exc))
        return 2

    ROOT.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    session = requests.Session()
    status, content, response_headers = fetch_json(session, TICKER_URL, headers=headers)
    requests_seen.append(
        {
            "url": TICKER_URL,
            "status_code": status,
            "bytes": len(content),
            "content_type": response_headers.get("content-type", ""),
            "transport_error": response_headers.get("transport_error", ""),
        }
    )
    if status != 200:
        write_receipt(
            status="BLOCKED_PROVIDER_RESPONSE",
            retrieval_at=retrieval_at,
            requests_seen=requests_seen,
            error=f"company_tickers_status_{status}",
        )
        print(f"BLOCKED_PROVIDER_RESPONSE: company_tickers status={status}")
        return 3
    ticker_path = ROOT / "company_tickers.json"
    ticker_path.write_bytes(content)
    ticker_data = json.loads(content)
    by_symbol = {
        str(value["ticker"]).upper(): {
            "cik": f"{int(value['cik_str']):010d}",
            "title": str(value["title"]),
        }
        for value in ticker_data.values()
    }

    selected: list[dict[str, str]] = []
    for row in pilot_symbols():
        sec = by_symbol.get(row["symbol"].upper())
        if sec:
            selected.append({**row, **sec})
        if len(selected) == PILOT_COUNT:
            break
    (ROOT / "pilot_selection.json").write_text(
        json.dumps(
            {
                "selection_rule": "first 20 lexicographic active NYSE/NASDAQ v5 entities with complete bars from 2016-01-04 through 2026-07-30 and a current SEC ticker mapping",
                "rows": selected,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    for row in selected:
        cik = row["cik"]
        for label, url in (
            ("submissions", f"{DATA_URL}/submissions/CIK{cik}.json"),
            ("companyfacts", f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik}.json"),
        ):
            time.sleep(0.2)
            status, content, response_headers = fetch_json(session, url, headers=headers)
            requests_seen.append(
                {
                    "url": url,
                    "status_code": status,
                    "bytes": len(content),
                    "content_type": response_headers.get("content-type", ""),
                    "transport_error": response_headers.get("transport_error", ""),
                    "symbol": row["symbol"],
                    "cik": cik,
                }
            )
            if status != 200:
                write_receipt(
                    status="BLOCKED_PROVIDER_RESPONSE",
                    retrieval_at=retrieval_at,
                    requests_seen=requests_seen,
                    error=f"{label}_status_{status}_{row['symbol']}",
                )
                print(f"BLOCKED_PROVIDER_RESPONSE: {label} symbol={row['symbol']} status={status}")
                return 3
            (ROOT / f"{label}_{cik}.json").write_bytes(content)

    receipt = {
        "status": "COMPLETE",
        "retrieved_at": retrieval_at,
        "provider": "U.S. Securities and Exchange Commission EDGAR",
        "requested": {
            "company_tickers": TICKER_URL,
            "submissions_and_companyfacts": f"{DATA_URL}/.../CIK{{cik}}.json",
            "historical_window": ["2016-01-01", "2026-07-30"],
        },
        "pilot_count": len(selected),
        "requests": requests_seen,
        "credential_fields_persisted": False,
        "raw_sha256": {
            str(path.relative_to(REPO)): sha256(path)
            for path in ROOT.glob("*.json")
            if path.name != "acquisition_receipt.json"
        },
        "pit_status": "RECONSTRUCTED_FROM_CURRENT_EDGAR_ENDPOINT; filed/accepted fields retained; no historical response snapshot claim",
    }
    (ROOT / "acquisition_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"SEC_PILOT_COMPLETE rows={len(selected)} output={ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
