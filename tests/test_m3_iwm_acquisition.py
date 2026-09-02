from __future__ import annotations

import importlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import acquire_m3_iwm_raw_bundle as acquisition  # noqa: E402
import m3_data_gate_helpers as helpers  # noqa: E402


def _timestamp(session: date) -> str:
    local = datetime.combine(session, datetime.min.time(), tzinfo=ZoneInfo(helpers.EXCHANGE_TIMEZONE))
    return local.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _bar(session: date, close: str = "100.00", *, symbol: str = "IWM") -> dict[str, str]:
    return {
        "S": symbol,
        "t": _timestamp(session),
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": "1000",
        "timeframe": "1Day",
    }


def _page(entries: list[dict[str, str]], token: str | None, *, compact: bool = False) -> bytes:
    payload = {"bars": {"IWM": entries}, "next_page_token": token}
    if compact:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return (json.dumps(payload, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, params: object, headers: object, timeout: float) -> acquisition.HttpResponse:
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, acquisition.HttpResponse)
        return response


def _credentials(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    key = "synthetic-api-key-never-printed"
    secret = "synthetic-secret-never-printed"
    monkeypatch.setenv("ALPACA_API_KEY", key)
    monkeypatch.setenv("ALPACA_SECRET_KEY", secret)
    return key, secret


def test_import_has_no_network_or_output_side_effect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("import attempted network access")

    monkeypatch.setattr(requests.Session, "get", fail)
    module = importlib.reload(acquisition)
    assert module.BASE_URL == "https://data.alpaca.markets/v2/stocks/bars"
    assert not list(tmp_path.glob("m3_iwm*"))


def test_multipage_transport_preserves_exact_raw_bytes_and_frozen_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key, secret = _credentials(monkeypatch)
    first = _page([_bar(date(2024, 7, 1), "100.00")], "page-2")
    second = _page([_bar(date(2024, 7, 2), "101.125")], None, compact=True)
    transport = FakeTransport(
        [
            acquisition.HttpResponse(200, first, {"x-request-id": "one"}),
            acquisition.HttpResponse(200, second, {"x-request-id": "two"}),
        ]
    )
    output = tmp_path / "m3_iwm_raw"
    result = acquisition.acquire_bundle(
        output, transport=transport, timestamp_utc=lambda: datetime(2026, 9, 1, tzinfo=UTC)
    )

    assert (output / "pages/000001.json").read_bytes() == first
    assert (output / "pages/000002.json").read_bytes() == second
    assert result["page_count"] == 2
    assert result["row_count"] == 2
    assert result["raw_tokens_in_result"] is False
    assert result["host"] == acquisition.HOST
    assert transport.calls[0]["url"] == acquisition.BASE_URL
    assert transport.calls[0]["params"] == acquisition.REQUEST_PARAMS
    assert transport.calls[0]["headers"]["APCA-API-KEY-ID"] == key  # type: ignore[index]
    assert transport.calls[0]["headers"]["APCA-API-SECRET-KEY"] == secret  # type: ignore[index]
    second_params = transport.calls[1]["params"]
    assert second_params["page_token"] == "page-2"  # type: ignore[index]
    assert second_params["symbols"] == "IWM"  # type: ignore[index]
    assert second_params["feed"] == "sip"  # type: ignore[index]
    assert second_params["timeframe"] == "1Day"  # type: ignore[index]
    assert "page-2" in (output / "source_receipt.json").read_text(encoding="utf-8")


def test_provider_bar_symbol_is_taken_from_bars_map_key() -> None:
    provider_bar = _bar(date(2024, 7, 1))
    provider_bar.pop("S")
    _payload, rows, token = acquisition._parse_page(_page([provider_bar], None), input_page_token=None)
    assert token is None
    assert len(rows) == 1
    assert rows[0].symbol == "IWM"


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_or_sip_entitlement_failure_has_no_fallback(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    _credentials(monkeypatch)
    transport = FakeTransport([acquisition.HttpResponse(status, b'{"error":"denied"}', {})])
    with pytest.raises(acquisition.AcquisitionError, match="authentication_or_sip_entitlement_failure"):
        acquisition._request_page(
            transport,
            input_page_token=None,
            api_key="key",
            secret_key="secret",
            sleeper=lambda _seconds: pytest.fail("401/403 must not retry"),
            now=lambda: 0.0,
        )
    assert len(transport.calls) == 1


def test_transient_retry_policy_is_bounded() -> None:
    transport = FakeTransport([acquisition.HttpResponse(503, b"", {}) for _ in range(acquisition.MAX_RETRIES + 1)])
    delays: list[float] = []
    with pytest.raises(acquisition.AcquisitionError, match="transient_retry_exhaustion"):
        acquisition._request_page(
            transport,
            input_page_token=None,
            api_key="key",
            secret_key="secret",
            sleeper=delays.append,
            now=lambda: 0.0,
        )
    assert len(transport.calls) == acquisition.MAX_RETRIES + 1
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.parametrize(
    ("body", "category"),
    [
        (b"not-json", "malformed_json"),
        (b'{"bars":{"IWM":[]}}', "missing_next_page_token"),
        (b'{"status":"error","next_page_token":null,"bars":{}}', "provider_error_payload"),
        (b'{"next_page_token":null,"bars":{"SPY":[]}}', "response_schema_mismatch"),
        (b'{"next_page_token":null,"bars":{"IWM":[{"S":"SPY"}]}}', "response_schema_mismatch"),
    ],
)
def test_malformed_provider_payloads_block(body: bytes, category: str) -> None:
    with pytest.raises(acquisition.AcquisitionError, match=category):
        acquisition._parse_page(body, input_page_token=None)


def test_non_sip_or_endpoint_identity_cannot_enter_the_transport() -> None:
    class Session:
        def get(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("unexpected request")

    client = acquisition.RequestsTransport()
    client._session = Session()  # type: ignore[assignment]
    with pytest.raises(acquisition.AcquisitionError, match="endpoint_identity_mismatch"):
        client.get("https://example.invalid/v2/stocks/bars", params={}, headers={}, timeout=1)
    assert acquisition.REQUEST_PARAMS["symbols"] == "IWM"
    assert acquisition.REQUEST_PARAMS["feed"] == "sip"


def test_credentials_are_absent_from_result_receipt_and_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key, secret = _credentials(monkeypatch)
    transport = FakeTransport([acquisition.HttpResponse(403, b"forbidden", {})])
    with pytest.raises(acquisition.AcquisitionError) as caught:
        acquisition.acquire_bundle(tmp_path / "blocked", transport=transport)
    blocked = (tmp_path / "blocked.blocked.json").read_text(encoding="utf-8")
    assert key not in str(caught.value) and secret not in str(caught.value)
    assert key not in blocked and secret not in blocked
    assert "credentials_disclosed" in blocked


def test_incomplete_bundle_is_removed_and_not_promoted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _credentials(monkeypatch)
    first = _page([_bar(date(2024, 7, 1))], "page-2")
    transport = FakeTransport(
        [
            acquisition.HttpResponse(200, first, {}),
            acquisition.HttpResponse(500, b"", {}),
            acquisition.HttpResponse(500, b"", {}),
            acquisition.HttpResponse(500, b"", {}),
            acquisition.HttpResponse(500, b"", {}),
        ]
    )
    with pytest.raises(acquisition.AcquisitionError, match="transient_retry_exhaustion"):
        acquisition.acquire_bundle(tmp_path / "incomplete", transport=transport, sleeper=lambda _seconds: None)
    assert not (tmp_path / "incomplete").exists()
    assert (tmp_path / "incomplete.blocked.json").is_file()
    assert not list(tmp_path.glob(".incomplete.tmp-*"))


def test_missing_credentials_blocks_without_constructing_a_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    transport = FakeTransport([])
    with pytest.raises(acquisition.AcquisitionError, match="missing_credentials"):
        acquisition.acquire_bundle(tmp_path / "missing", transport=transport)
    assert transport.calls == []


def test_normalized_only_iwm_is_rejected(tmp_path: Path) -> None:
    sidecar = tmp_path / "iwm.csv"
    sidecar.write_text("timestamp,symbol,timeframe,open,high,low,close,volume,market\n", encoding="utf-8")
    with pytest.raises(helpers.GateError) as caught:
        helpers.validate_iwm_bundle(sidecar, tmp_path / "stage")
    assert caught.value.category == "normalized_only_iwm"


def test_page_payload_token_and_page_set_mismatches_block(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    pages = root / "pages"
    pages.mkdir(parents=True)
    raw = _page([_bar(date(2024, 7, 1))], None)
    (pages / "000001.json").write_bytes(raw)
    receipt = {
        "schema_version": "1.0.0",
        "provider": helpers.SOURCE_PROVIDER,
        "api_family_version": helpers.SOURCE_API_FAMILY_VERSION,
        "provider_api_product_version": "fixture",
        "asset_class": "us_equity",
        "symbol": "IWM",
        "endpoint_identity": helpers.SOURCE_ENDPOINT,
        "acquisition_timestamp_utc": "2026-09-01T00:00:00Z",
        "request": {
            "start": "2016-01-01",
            "end": "2026-07-31",
            "timeframe": "1Day",
            "feed": "sip",
            "adjustment": "split",
            "sort": "asc",
        },
        "entitlement_feed_assertion": "fixture",
        "no_fallback_assertion": True,
        "operator_attestation": acquisition.OPERATOR_ATTESTATION,
        "normalizer_version": helpers.NORMALIZER_VERSION,
        "page_count": 1,
        "pages": [
            {
                "relative_path": "pages/000001.json",
                "raw_sha256": helpers._sha256_bytes(raw),  # type: ignore[attr-defined]
                "raw_byte_size": len(raw),
                "row_count": 1,
                "input_page_token": None,
                "output_next_page_token": "not-null-in-payload",
            }
        ],
    }
    (root / "source_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(helpers.GateError, match="continuation token"):
        helpers.validate_iwm_bundle(root, tmp_path / "stage")
