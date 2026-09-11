from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.research.acquire_r46_tlt_market_data_2026_09_10 import (
    AcquisitionContractError,
    acquire_tlt_market_data,
    build_alpaca_bars_request,
    build_alpaca_dividends_request,
    load_calendar_rows,
    normalize_alpaca_bars_pair,
    normalize_alpaca_dividends,
    normalize_alpaca_identity,
    normalize_alpaca_identity_records,
)

IDENTITY = {
    "canonical_symbol": "TLT",
    "historical_symbol": "TLT",
    "permanent_security_identifier": "alpaca:asset-tlt",
    "provider_asset_id": "asset-tlt",
    "security_identifier": "912810TM2",
    "venue": "NASDAQ",
    "listing_and_tradability_interval": "2002-07-22/",
    "valid_from": "2002-07-22",
    "valid_to": None,
    "first_observed_bar": "2016-01-04",
    "last_observed_bar": "2026-07-31",
    "identity_basis": "historical_provider_asset_map",
    "source_url": "https://paper-api.alpaca.markets/v2/assets/TLT",
    "available_at": "2026-07-31T23:00:00Z",
}

SOURCE_VINTAGE = "alpaca-sip-2026-07-31-close-v1"
ACQUISITION_TIME = "2026-09-10T08:00:00Z"


def _page(*, open_price: float = 92.0, day: str = "2024-02-09", token: str | None = None) -> dict[str, object]:
    return {
        "bars": {
            "TLT": [
                {
                    "t": f"{day}T14:30:00Z",
                    "o": open_price,
                    "h": open_price + 1,
                    "l": open_price - 1,
                    "c": open_price + 0.5,
                    "v": 1_000_000,
                }
            ],
        },
        "next_page_token": token,
    }


def _midnight_page() -> dict[str, object]:
    page = _page()
    page["bars"]["TLT"][0]["t"] = "2024-02-09T05:00:00Z"  # type: ignore[index]
    return page


def _dividend(*, day: str = "2024-02-01", token: str | None = None) -> dict[str, object]:
    return {
        "corporate_actions": [
            {
                "id": f"ca-{day}",
                "symbol": "TLT",
                "action_type": "cash_dividend",
                "ex_date": day,
                # Provider process-date evidence is date-only at UTC midnight;
                # normalization must replace it with the effective ex-date
                # regular NYSE open from the supplied calendar.
                "event_timestamp": f"{day}T00:00:00Z",
                "amount": 0.28,
                "currency": "USD",
                "duplicate_event_key": f"ca-{day}",
            }
        ],
        "next_page_token": token,
    }


def _provider_dividend() -> dict[str, object]:
    return {
        "corporate_actions": [
            {
                "id": "ca-provider-2024-02-01",
                "ca_type": "dividend",
                "ca_sub_type": "cash",
                "initiating_symbol": "TLT",
                "ex_date": "2024-02-01",
                "created_at": "2024-01-20T15:00:00Z",
                "cash": "0.28",
                "currency": "USD",
            }
        ]
    }


def _current_alpaca_cash_dividend_page() -> dict[str, object]:
    return {
        "corporate_actions": {
            "cash_dividends": [
                {
                    "id": "ca-current-2024-02-01",
                    "symbol": "TLT",
                    "cusip": "464287432",
                    "ex_date": "2024-02-01",
                    # Process-date evidence may be later than the ex-date;
                    # it is not the economic event timestamp for R46.
                    "process_date": "2024-02-07",
                    "rate": 0.28,
                    "special": False,
                    "foreign": False,
                }
            ]
        },
        "next_page_token": None,
    }


def _current_provider_identity() -> dict[str, object]:
    return {
        "asset": {
            "id": "asset-tlt",
            "symbol": "TLT",
            "exchange": "NASDAQ",
            "status": "active",
            "tradable": True,
        }
    }


def _historical_identity_payload() -> dict[str, object]:
    common = {
        "canonical_symbol": "TLT",
        "historical_symbol": "TLT",
        "permanent_security_identifier": "sec:0001100663:S000004360:C000012090",
        "provider_asset_id": "asset-tlt",
        "security_identifier": "464287432",
        "valid_from": "2002-07-22",
        "first_observed_bar": "2016-01-04",
        "identity_basis": "issuer_historical_filing",
        "available_at": "2026-07-31T23:00:00Z",
    }
    payload: dict[str, object] = {
        "schema": "r46-tlt-historical-identity-evidence-v1",
        "source_vintage": "sec-nasdaq-tlt-2026-07-31-v1",
        "records": [
            {
                **common,
                "venue": "NYSE Arca",
                "listing_and_tradability_interval": "2002-07-22/2016-02-02",
                "valid_to": "2016-02-02",
                "last_observed_bar": "2016-02-01",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1100663/000119312516564893/d177059d485apos.htm",
            },
            {
                **common,
                "venue": "NASDAQ",
                "listing_and_tradability_interval": "2016-02-02/",
                "valid_from": "2016-02-02",
                "valid_to": None,
                "last_observed_bar": "2026-07-31",
                "identity_basis": "exchange_archive",
                "source_url": "https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2016-20",
            },
        ],
        "integrity": {
            "canonicalization": "UTF-8 JSON sorted object keys compact separators ensure_ascii=false; omit integrity.artifact_sha256",
            "artifact_sha256": "",
        },
    }
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value["integrity"].pop("artifact_sha256")  # type: ignore[index]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload["integrity"]["artifact_sha256"] = hashlib.sha256(encoded).hexdigest()  # type: ignore[index]
    return payload


def _calendar_rows() -> list[dict[str, object]]:
    return [
        {
            "regular_session_date": day,
            "regular_open_timestamp": f"{day}T14:30:00Z",
            "regular_close_timestamp": f"{day}T21:00:00Z",
            "holiday_or_closure_reason": None,
        }
        for day in ("2024-02-01", "2024-02-09")
    ]


def _receipt_hash(payload: dict[str, object]) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value["integrity"].pop("receipt_sha256", None)  # type: ignore[index]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_alpaca_request_makes_raw_and_split_contract_explicit() -> None:
    raw = build_alpaca_bars_request("raw")
    split = build_alpaca_bars_request("split")

    assert raw["url"] == "https://data.alpaca.markets/v2/stocks/bars"
    assert raw["params"]["symbols"] == "TLT"
    assert raw["params"]["feed"] == "sip"
    assert raw["params"]["adjustment"] == "raw"
    assert split["params"]["adjustment"] == "split"
    assert raw["params"]["start"] == "2016-01-01"
    assert raw["params"]["end"] == "2026-08-01"


def test_alpaca_corporate_action_request_uses_current_cash_dividend_route() -> None:
    request = build_alpaca_dividends_request()
    assert request["url"] == "https://data.alpaca.markets/v1/corporate-actions"
    assert request["params"]["types"] == "cash_dividend"
    assert request["params"]["limit"] == 1000


def test_normalize_bars_derives_session_close_availability_and_requires_calendar_for_provider_midnight() -> None:
    normalized = normalize_alpaca_bars_pair(
        [_page()],
        [_page(open_price=91.0)],
        identity=IDENTITY,
        source_vintage=SOURCE_VINTAGE,
        acquisition_time=ACQUISITION_TIME,
        calendar_rows=_calendar_rows(),
    )

    assert normalized["status"] == "COMPLETE"
    assert normalized["raw_split_key_parity"] is True
    assert normalized["split_rows"][0]["adjustment"] == "split"
    assert normalized["split_rows"][0]["permanent_security_identifier"] == "alpaca:asset-tlt"
    assert normalized["split_rows"][0]["regular_open_timestamp"] == "2024-02-09T14:30:00Z"
    assert normalized["split_rows"][0]["available_at"] == "2024-02-09T21:00:00Z"
    assert normalized["availability_basis"] == "session_close_from_supplied_nyse_calendar"
    assert normalized["acquisition_time"] == ACQUISITION_TIME

    with pytest.raises(AcquisitionContractError, match="calendar"):
        normalize_alpaca_bars_pair(
            [_midnight_page()],
            [_midnight_page()],
            identity=IDENTITY,
            source_vintage=SOURCE_VINTAGE,
            acquisition_time=ACQUISITION_TIME,
        )


def test_dividend_normalization_derives_ex_date_regular_open() -> None:
    rows = normalize_alpaca_dividends(
        _dividend(),
        identity=IDENTITY,
        source_vintage=SOURCE_VINTAGE,
        acquisition_time=ACQUISITION_TIME,
        calendar_rows=_calendar_rows(),
    )
    assert rows[0]["action_type"] == "cash_dividend"
    raw_action = _dividend()["corporate_actions"][0]  # type: ignore[index]
    expected_hash = hashlib.sha256(
        json.dumps(raw_action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert rows[0]["source_document_hash"] == expected_hash
    assert rows[0]["duplicate_event_key"] == "ca-2024-02-01"
    assert rows[0]["event_timestamp"] == "2024-02-01T14:30:00Z"
    assert rows[0]["available_at"] == "2024-02-01T14:30:00Z"

    generated = normalize_alpaca_dividends(
        _dividend(),
        identity=IDENTITY,
        source_vintage=SOURCE_VINTAGE,
        acquisition_time=ACQUISITION_TIME,
        calendar_rows=_calendar_rows(),
    )
    assert len(generated[0]["source_document_hash"]) == 64


def test_provider_corporate_action_schema_is_normalized_without_fixture_only_fields() -> None:
    rows = normalize_alpaca_dividends(
        _provider_dividend(),
        identity=IDENTITY,
        source_vintage=SOURCE_VINTAGE,
        acquisition_time=ACQUISITION_TIME,
        calendar_rows=_calendar_rows(),
    )
    assert rows[0]["cash_or_ratio"] == 0.28
    assert rows[0]["duplicate_event_key"] == "ca-provider-2024-02-01:2024-02-01"
    assert len(rows[0]["source_document_hash"]) == 64


def test_current_alpaca_cash_dividend_mapping_is_normalized_from_raw_provider_fields() -> None:
    raw = _current_alpaca_cash_dividend_page()
    rows = normalize_alpaca_dividends(
        raw,
        identity=IDENTITY,
        source_vintage=SOURCE_VINTAGE,
        acquisition_time=ACQUISITION_TIME,
        calendar_rows=_calendar_rows(),
    )
    expected_raw = raw["corporate_actions"]["cash_dividends"][0]  # type: ignore[index]
    expected_hash = hashlib.sha256(
        json.dumps(expected_raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert rows[0]["cash_or_ratio"] == 0.28
    assert rows[0]["currency"] == "USD"
    assert rows[0]["event_timestamp"] == "2024-02-01T14:30:00Z"
    assert rows[0]["available_at"] == "2024-02-01T14:30:00Z"
    assert rows[0]["duplicate_event_key"] == "ca-current-2024-02-01:2024-02-01"
    assert rows[0]["source_document_hash"] == expected_hash


def test_identity_normalization_is_cutoff_bounded_and_rejects_current_snapshot_without_historical_interval() -> None:
    payload = {"asset": dict(IDENTITY)}
    payload["asset"].pop("listing_and_tradability_interval")
    with pytest.raises(AcquisitionContractError, match="listing_and_tradability_interval"):
        normalize_alpaca_identity(payload, source_vintage=SOURCE_VINTAGE, acquisition_time=ACQUISITION_TIME)

    late = {"asset": dict(IDENTITY, available_at="2026-08-01T00:00:00Z")}
    with pytest.raises(AcquisitionContractError, match="post_cutoff"):
        normalize_alpaca_identity(late, source_vintage=SOURCE_VINTAGE, acquisition_time=ACQUISITION_TIME)


def test_historical_identity_evidence_preserves_exchange_transition_and_is_hash_pinned() -> None:
    rows = normalize_alpaca_identity_records(
        _historical_identity_payload(),
        source_vintage="sec-nasdaq-tlt-2026-07-31-v1",
        acquisition_time=ACQUISITION_TIME,
    )
    assert [row["venue"] for row in rows] == ["NYSE Arca", "NASDAQ"]
    assert rows[0]["valid_to"] == "2016-02-02"
    assert rows[1]["valid_from"] == "2016-02-02"


def test_calendar_loader_accepts_pinned_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "nyse_calendar.jsonl"
    rows = _calendar_rows()
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert load_calendar_rows(path) == rows


def test_authenticated_current_identity_is_attested_against_supplied_history(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        calls.append(url)
        if url.endswith("/v2/assets/TLT"):
            return 200, _current_provider_identity()
        if url.endswith("/v1/corporate-actions"):
            return 200, _dividend()
        adjustment = str(params["adjustment"])
        return 200, _page(open_price=92.0 if adjustment == "raw" else 91.0)

    receipt = acquire_tlt_market_data(
        tmp_path,
        identity_payload=_historical_identity_payload(),
        source_vintage=SOURCE_VINTAGE,
        acquisition_time=ACQUISITION_TIME,
        calendar_rows=_calendar_rows(),
        credentials={"key_id": "key", "secret_key": "secret"},
        transport=transport,
    )
    assert receipt["status"] == "COMPLETE"
    assert receipt["identity_attestation"]["provider_asset_id"] == "asset-tlt"  # type: ignore[index]
    eligibility_rows = [
        json.loads(line)
        for line in (tmp_path / "eligibility.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(eligibility_rows) == 2
    assert len(calls) == 4


def test_mocked_acquisition_fetches_all_alpaca_routes_and_writes_credential_safe_receipt(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        calls.append({"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout})
        if url.endswith("/v2/assets/TLT"):
            return 200, _current_provider_identity()
        if url.endswith("/v1/corporate-actions"):
            if params.get("page_token") == "div-next":
                return 200, _dividend(day="2024-02-02")
            return 200, _dividend(token="div-next")
        adjustment = str(params["adjustment"])
        if params.get("page_token") == f"{adjustment}-next":
            return 200, _page(open_price=92.0 if adjustment == "raw" else 91.0, day="2024-02-12")
        return 200, _page(open_price=92.0 if adjustment == "raw" else 91.0, token=f"{adjustment}-next")

    receipt = acquire_tlt_market_data(
        tmp_path,
        identity_payload=_historical_identity_payload(),
        source_vintage=SOURCE_VINTAGE,
        acquisition_time=ACQUISITION_TIME,
        calendar_rows=_calendar_rows() + [
            {
                "regular_session_date": "2024-02-12",
                "regular_open_timestamp": "2024-02-12T14:30:00Z",
                "regular_close_timestamp": "2024-02-12T21:00:00Z",
                "holiday_or_closure_reason": None,
            },
            {
                "regular_session_date": "2024-02-02",
                "regular_open_timestamp": "2024-02-02T14:30:00Z",
                "regular_close_timestamp": "2024-02-02T21:00:00Z",
                "holiday_or_closure_reason": None,
            },
        ],
        credentials={"key_id": "secret-key", "secret_key": "secret-value"},
        transport=transport,
    )

    assert receipt["status"] == "COMPLETE"
    assert receipt["credentials_used"] is True
    assert receipt["network_used"] is True
    assert {call["url"] for call in calls} == {
        "https://data.alpaca.markets/v2/stocks/bars",
        "https://data.alpaca.markets/v1/corporate-actions",
        "https://paper-api.alpaca.markets/v2/assets/TLT",
    }
    assert len(calls) == 7
    assert receipt["integrity"]["receipt_sha256"] == _receipt_hash(receipt)  # type: ignore[index]
    assert receipt["acquisition_time"] == ACQUISITION_TIME
    assert receipt["availability_basis"]["bars"] == "session_close_from_supplied_nyse_calendar"  # type: ignore[index]
    assert receipt["availability_basis"]["dividends"] == "ex_date_regular_open_from_supplied_nyse_calendar"  # type: ignore[index]
    assert receipt["corporate_action_normalization_basis"] == {  # type: ignore[index]
        "provider_route": "https://data.alpaca.markets/v1/corporate-actions",
        "container": "corporate_actions.cash_dividends",
        "rate_field": "rate_to_cash_or_ratio",
        "event_timestamp": "effective_date_regular_open_from_supplied_nyse_calendar",
        "currency": "USD_contract_derived",
        "source_document_hash": "sha256_canonical_raw_provider_row",
        "duplicate_event_key": "provider_id_plus_ex_date",
    }
    receipt_text = (tmp_path / "receipt.json").read_text(encoding="utf-8")
    assert "secret-key" not in receipt_text
    assert "secret-value" not in receipt_text
    assert "APCA-API-KEY-ID" not in receipt_text
    assert "APCA-API-SECRET-KEY" not in receipt_text
    for persisted in tmp_path.glob("*.json"):
        assert "secret-key" not in persisted.read_text(encoding="utf-8")
        assert "secret-value" not in persisted.read_text(encoding="utf-8")
    assert (tmp_path / "split_bars.csv").is_file()
    assert (tmp_path / "dividends.csv").is_file()
    source = json.loads((tmp_path / "dividends_source.json").read_text(encoding="utf-8"))
    source_actions = source["corporate_actions"]
    normalized_rows = list(csv.DictReader((tmp_path / "dividends.csv").open(newline="", encoding="utf-8")))
    assert len(source_actions) == len(normalized_rows) == 2
    for source_action, normalized_row in zip(source_actions, normalized_rows):
        raw_hash = hashlib.sha256(
            json.dumps(source_action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert normalized_row["source_document_hash"] == raw_hash
    with (tmp_path / "split_bars.csv").open(newline="", encoding="utf-8") as handle:
        assert "permanent_security_identifier" in (csv.DictReader(handle).fieldnames or ())


def test_env_loading_is_opt_in_and_second_publication_cannot_overwrite(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APCA_API_KEY_ID=dotenv-key\nAPCA_API_SECRET_KEY=dotenv-secret\n", encoding="utf-8")

    def transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        assert headers["APCA-API-KEY-ID"] == "dotenv-key"
        assert headers["APCA-API-SECRET-KEY"] == "dotenv-secret"
        if url.endswith("/v2/assets/TLT"):
            return 200, {"asset": IDENTITY}
        if url.endswith("/v1/corporate-actions"):
            return 200, {"corporate_actions": []}
        adjustment = str(params["adjustment"])
        return 200, _page(open_price=92.0 if adjustment == "raw" else 91.0)

    kwargs = {
        "source_vintage": SOURCE_VINTAGE,
        "acquisition_time": ACQUISITION_TIME,
        "calendar_rows": _calendar_rows(),
        "dotenv_path": env_path,
        "transport": transport,
    }
    acquire_tlt_market_data(tmp_path / "one", **kwargs)
    with pytest.raises((FileExistsError, AcquisitionContractError)):
        acquire_tlt_market_data(tmp_path / "one", **kwargs)
    assert "dotenv-secret" not in (tmp_path / "one" / "receipt.json").read_text(encoding="utf-8")
