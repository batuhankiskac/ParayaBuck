"""Canonical, sanitized integrity checks for crossasset research evidence."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
HEX64 = set("0123456789abcdef")
RECEIPT_SCHEMA = "crossasset-sanitized-probe-receipt-v1"
STATUS_ENUM = {"HTTP_SUCCESS", "HTTP_ERROR", "REQUEST_ERROR", "ON_DISK"}
X02_RETRY_STATUS_ENUM = {"HTTP_SUCCESS", "HTTP_ERROR", "REQUEST_ERROR"}
X02_RETRY_SCHEMA = "crossasset-x02-cme-retry-manifest-v1"
X02_RETRY_RECEIPT_SCHEMA = "crossasset-x02-cme-retry-receipt-v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: dict[str, Any], field: str) -> str:
    payload = copy.deepcopy(value)
    payload.pop(field, None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _path(value: str, *, repo_root: Path = REPO, output_root: Path | None = None) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AssertionError(f"unsafe evidence path: {value}")
    output_rel = Path("data/research/external/crossasset_public_2026_09_08/x02_x06_matrix")
    path = output_root / candidate.relative_to(output_rel) if output_root is not None and (candidate == output_rel or output_rel in candidate.parents) else repo_root / candidate
    if not path.is_file():
        raise AssertionError(f"missing evidence path: {value}")
    return path


def _aware_utc(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AssertionError("receipt retrieval timestamp missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssertionError("receipt retrieval timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AssertionError("receipt retrieval timestamp must be timezone-aware")


def _endpoint(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AssertionError("receipt endpoint must be a nonempty canonical HTTPS URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or any(ch.isspace() for ch in value):
        raise AssertionError("receipt endpoint must be a nonempty canonical HTTPS URL")
    return value


def verify_probe_receipt(receipt_path: Path, *, repo_root: Path = REPO, output_root: Path | None = None) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for key in ("schema", "source_id", "card", "kind", "endpoint", "retrieved_at", "status", "http_status", "bytes", "body_sha256", "row_count", "count_semantics", "credential_fields_persisted"):
        if key not in receipt:
            raise AssertionError(f"receipt missing {key}")
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise AssertionError("receipt schema mismatch")
    if not isinstance(receipt["source_id"], str) or not receipt["source_id"].strip() or not isinstance(receipt["card"], str) or not receipt["card"].strip() or not isinstance(receipt["kind"], str) or not receipt["kind"].strip():
        raise AssertionError("receipt source/card/kind binding missing")
    _endpoint(receipt["endpoint"])
    _aware_utc(receipt["retrieved_at"])
    if receipt["status"] not in STATUS_ENUM:
        raise AssertionError("receipt status enum invalid")
    http_status = receipt["http_status"]
    if http_status is not None and (isinstance(http_status, bool) or not isinstance(http_status, int) or not 100 <= http_status <= 599):
        raise AssertionError("receipt http_status invalid")
    expected_status = "ON_DISK" if receipt["status"] == "ON_DISK" else ("REQUEST_ERROR" if receipt["status"] == "REQUEST_ERROR" else ("HTTP_SUCCESS" if http_status is not None and 200 <= http_status < 300 else "HTTP_ERROR"))
    if receipt["status"] != expected_status:
        raise AssertionError("receipt status/http_status inconsistent")
    if receipt["status"] in {"ON_DISK", "REQUEST_ERROR"} and http_status is not None:
        raise AssertionError("receipt status/http_status inconsistent")
    if receipt["status"] == "REQUEST_ERROR" and not isinstance(receipt.get("error"), str):
        raise AssertionError("request error receipt missing error")
    if receipt["credential_fields_persisted"] is not False:
        raise AssertionError("receipt credential flag must be false")
    if not isinstance(receipt["bytes"], int) or receipt["bytes"] < 0:
        raise AssertionError("receipt bytes invalid")
    if receipt["row_count"] is not None and (isinstance(receipt["row_count"], bool) or not isinstance(receipt["row_count"], int) or receipt["row_count"] < 0):
        raise AssertionError("receipt row count invalid")
    if not isinstance(receipt["count_semantics"], str) or not receipt["count_semantics"].strip():
        raise AssertionError("receipt count semantics missing")
    body_path = receipt.get("body_path")
    if body_path:
        body = _path(body_path, repo_root=repo_root, output_root=output_root)
        if body.stat().st_size != receipt["bytes"] or sha256(body) != receipt["body_sha256"]:
            raise AssertionError(f"receipt body mismatch: {receipt_path}")
    elif receipt["bytes"] != 0 or receipt["body_sha256"] is not None:
        raise AssertionError(f"error receipt has body metadata: {receipt_path}")
    if receipt["body_sha256"] is not None and (len(receipt["body_sha256"]) != 64 or set(receipt["body_sha256"]) - HEX64):
        raise AssertionError(f"invalid body hash: {receipt_path}")
    if receipt.get("row_count") is not None and body_path:
        body = _path(body_path, repo_root=repo_root, output_root=output_root)
        if body.suffix.lower() == ".csv":
            with body.open(newline="", encoding="utf-8", errors="replace") as handle:
                count = sum(1 for _ in csv.DictReader(handle))
        else:
            payload = json.loads(body.read_text(encoding="utf-8"))
            count = len(payload) if isinstance(payload, list) else None
        if count is None or count != receipt["row_count"]:
            raise AssertionError(f"receipt row count mismatch: {receipt_path}")
    archive = receipt.get("archive")
    if archive:
        archive_path = _path(archive["path"], repo_root=repo_root, output_root=output_root)
        with zipfile.ZipFile(archive_path) as handle:
            if handle.testzip() is not None:
                raise AssertionError(f"archive CRC failure: {receipt_path}")
            infos = {info.filename: info for info in handle.infolist()}
            if sorted(infos) != sorted(archive["members"]):
                raise AssertionError(f"archive members mismatch: {receipt_path}")
            for member, expected in archive.get("member_metadata", {}).items():
                info = infos[member]
                if info.file_size != expected["size"] or info.CRC != expected["crc"]:
                    raise AssertionError(f"archive member metadata mismatch: {receipt_path}")
    return receipt


def verify_source_matrix(path: Path, *, repo_root: Path = REPO, output_root: Path | None = None) -> dict[str, Any]:
    matrix = json.loads(path.read_text(encoding="utf-8"))
    if matrix.get("matrix_sha256") != canonical_hash(matrix, "matrix_sha256"):
        raise AssertionError("source matrix canonical hash mismatch")
    retry_pin = matrix.get("x02_cme_retry")
    if retry_pin is not None:
        retry_rel = retry_pin.get("manifest_path") if isinstance(retry_pin, dict) else None
        if not isinstance(retry_rel, str) or not retry_rel.startswith("data/research/external/crossasset_public_2026_09_10/x02_cme_retry/"):
            raise AssertionError("X02 retry manifest path invalid")
        retry_path = repo_root / retry_rel
        retry = verify_x02_retry_manifest(retry_path, repo_root=repo_root)
        if sha256(retry_path) != retry_pin.get("manifest_sha256") or retry["manifest_sha256"] != retry_pin.get("manifest_canonical_sha256"):
            raise AssertionError("X02 retry manifest pin mismatch")
        if retry["probe_count"] != retry_pin.get("probe_count") or retry["admitted_rows"] != retry_pin.get("admitted_rows"):
            raise AssertionError("X02 retry manifest count/admission mismatch")
    for record in matrix.get("records", []):
        receipt_rel = record.get("receipt_path")
        if not receipt_rel:
            raise AssertionError(f"record missing receipt: {record.get('source_id')}")
        receipt = verify_probe_receipt(_path(receipt_rel, repo_root=repo_root, output_root=output_root), repo_root=repo_root, output_root=output_root)
        if record.get("receipt_sha256") is not None and sha256(_path(receipt_rel, repo_root=repo_root, output_root=output_root)) != record["receipt_sha256"]:
            raise AssertionError(f"record receipt hash mismatch: {record.get('source_id')}")
        endpoint = record.get("url") or record.get("url_or_path")
        if receipt["source_id"] != record.get("source_id") or receipt["card"] != record.get("family") or receipt["endpoint"] != endpoint:
            raise AssertionError(f"receipt identity mismatch: {record.get('source_id')}")
        if receipt["http_status"] != (record.get("http_status") if isinstance(record.get("http_status"), int) else None):
            raise AssertionError(f"receipt HTTP binding mismatch: {record.get('source_id')} ({receipt['http_status']!r} != {record.get('http_status')!r})")
        record_path = record.get("body_path") or record.get("path")
        if record_path and record.get("body_sha256"):
            if sha256(_path(record_path, repo_root=repo_root, output_root=output_root)) != record["body_sha256"]:
                raise AssertionError(f"record hash mismatch: {record.get('source_id')}")
        if record.get("body_or_file_sha256") and record.get("path"):
            if sha256(_path(record["path"], repo_root=repo_root, output_root=output_root)) != record["body_or_file_sha256"]:
                raise AssertionError(f"record file hash mismatch: {record.get('source_id')}")
        identity = record.get("document_identity")
        if isinstance(identity, dict) and identity.get("submission_index_path"):
            submission = _path(identity["submission_index_path"], repo_root=repo_root, output_root=output_root)
            if sha256(submission) != identity.get("submission_index_sha256"):
                raise AssertionError(f"submission index hash mismatch: {record.get('source_id')}")
        if isinstance(identity, dict) and identity.get("source_receipt") and identity.get("source_receipt_sha256"):
            receipt_file = _path(identity["source_receipt"], repo_root=repo_root, output_root=output_root)
            if sha256(receipt_file) != identity.get("source_receipt_sha256"):
                raise AssertionError(f"source receipt hash mismatch: {record.get('source_id')}")
        structured = record.get("structured_holdings_supported") is True
        rows = record.get("holding_rows")
        if structured and (not isinstance(rows, list) or record.get("holding_count") != len(rows)):
            raise AssertionError(f"structured holding count/binding mismatch: {record.get('source_id')}")
        if record.get("holding_count", 0) and not structured:
            raise AssertionError(f"unsupported holding claim: {record.get('source_id')}")
        if record.get("binding_status") == "METADATA_ONLY_UNBOUND_LOCAL_XML" and record.get("admitted_holding_rows", 0) != 0:
            raise AssertionError(f"metadata-only holding admission: {record.get('source_id')}")
    return {"records": len(matrix.get("records", [])), "matrix_sha256": matrix["matrix_sha256"]}


def verify_x02_retry_manifest(path: Path, *, repo_root: Path = REPO) -> dict[str, Any]:
    """Verify the isolated CME retry bundle, including every receipt and raw byte pin."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != X02_RETRY_SCHEMA or manifest.get("card") != "X02":
        raise AssertionError("X02 retry manifest schema/card mismatch")
    if manifest.get("manifest_sha256") != canonical_hash(manifest, "manifest_sha256"):
        raise AssertionError("X02 retry manifest canonical hash mismatch")
    records = manifest.get("records")
    if not isinstance(records, list) or manifest.get("probe_count") != len(records):
        raise AssertionError("X02 retry probe count mismatch")
    for record in records:
        slug = record.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            raise AssertionError("X02 retry slug missing")
        endpoint = record.get("endpoint")
        parsed = urlparse(endpoint) if isinstance(endpoint, str) else None
        if parsed is None or parsed.scheme != "https" or not parsed.netloc or endpoint != endpoint.strip() or any(ch.isspace() for ch in endpoint):
            raise AssertionError(f"X02 retry endpoint invalid: {slug}")
        if record.get("card") != "X02" or record.get("status") not in X02_RETRY_STATUS_ENUM:
            raise AssertionError(f"X02 retry identity/status invalid: {slug}")
        http_status = record.get("http_status")
        if http_status is not None and (isinstance(http_status, bool) or not isinstance(http_status, int) or not 100 <= http_status <= 599):
            raise AssertionError(f"X02 retry HTTP status invalid: {slug}")
        if record["status"] == "REQUEST_ERROR":
            if http_status is not None or not isinstance(record.get("error"), str) or not record["error"].strip():
                raise AssertionError(f"X02 retry request error binding invalid: {slug}")
        elif record["status"] == "HTTP_SUCCESS" and (http_status is None or not 200 <= http_status < 300):
            raise AssertionError(f"X02 retry success status binding invalid: {slug}")
        elif record["status"] == "HTTP_ERROR" and (http_status is None or 200 <= http_status < 300):
            raise AssertionError(f"X02 retry HTTP error binding invalid: {slug}")
        _aware_utc(record.get("retrieved_at"))
        if record.get("credential_fields_persisted") is not False:
            raise AssertionError(f"X02 retry credential flag invalid: {slug}")
        receipt_rel = record.get("receipt_path")
        if not isinstance(receipt_rel, str) or not receipt_rel.startswith("data/research/external/crossasset_public_2026_09_10/x02_cme_retry/"):
            raise AssertionError(f"X02 retry receipt path invalid: {slug}")
        receipt_path = repo_root / receipt_rel
        if not receipt_path.is_file() or sha256(receipt_path) != record.get("receipt_sha256"):
            raise AssertionError(f"X02 retry receipt hash mismatch: {slug}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("schema") != X02_RETRY_RECEIPT_SCHEMA:
            raise AssertionError(f"X02 retry receipt schema mismatch: {slug}")
        for field in ("source_id", "card", "kind", "method", "endpoint", "retrieved_at", "timeout_seconds", "target_cutoff", "status", "http_status", "error", "body_path", "bytes", "body_sha256", "response_headers_path", "response_headers_sha256", "parse", "credential_fields_persisted"):
            if field not in receipt or receipt[field] != record.get(field):
                raise AssertionError(f"X02 retry receipt binding mismatch: {slug}:{field}")
        header_rel = receipt.get("response_headers_path")
        if header_rel is None:
            if receipt["status"] != "REQUEST_ERROR" or receipt.get("response_headers_sha256") is not None:
                raise AssertionError(f"X02 retry missing header binding: {slug}")
        else:
            if not isinstance(header_rel, str) or not header_rel.startswith("data/research/external/crossasset_public_2026_09_10/x02_cme_retry/headers/"):
                raise AssertionError(f"X02 retry header path invalid: {slug}")
            header_path = repo_root / header_rel
            if not header_path.is_file() or sha256(header_path) != receipt.get("response_headers_sha256"):
                raise AssertionError(f"X02 retry header hash mismatch: {slug}")
            if not isinstance(json.loads(header_path.read_text(encoding="utf-8")), dict):
                raise AssertionError(f"X02 retry headers are not an object: {slug}")
        body_rel = receipt.get("body_path")
        if body_rel:
            if not body_rel.startswith("data/research/external/crossasset_public_2026_09_10/x02_cme_retry/bodies/"):
                raise AssertionError(f"X02 retry body path invalid: {slug}")
            body_path = repo_root / body_rel
            if not body_path.is_file() or body_path.stat().st_size != receipt.get("bytes") or sha256(body_path) != receipt.get("body_sha256"):
                raise AssertionError(f"X02 retry body hash/size mismatch: {slug}")
        elif receipt.get("bytes") != 0 or receipt.get("body_sha256") is not None:
            raise AssertionError(f"X02 retry empty body binding invalid: {slug}")
        parse = record.get("parse")
        if not isinstance(parse, dict) or any(not isinstance(parse.get(key), int) or parse[key] < 0 for key in ("contract_rows", "settlement_rows", "publication_time_rows", "available_at_rows")):
            raise AssertionError(f"X02 retry parse summary invalid: {slug}")
    return {"probe_count": len(records), "manifest_sha256": manifest["manifest_sha256"], "admitted_rows": manifest.get("admitted_rows", 0)}


def verify_x01_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != canonical_hash(manifest, "manifest_sha256"):
        raise AssertionError("X01 canonical hash mismatch")
    receipt = manifest["receipt"]
    receipt_path = _path(receipt["path"])
    if sha256(receipt_path) != receipt["sha256"]:
        raise AssertionError("X01 receipt hash mismatch")
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    for record in receipt_payload["records"].values():
        for key in ("request", "http_status", "retrieved_at", "raw_path", "raw_sha256", "bars_path", "bars_sha256", "dividends_path", "dividends_sha256"):
            if key not in record:
                raise AssertionError(f"X01 probe receipt missing {key}")
        if not isinstance(record["http_status"], int) or not record["request"].get("url"):
            raise AssertionError("X01 probe receipt status/endpoint invalid")
        for path_key, hash_key in (("raw_path", "raw_sha256"), ("bars_path", "bars_sha256"), ("dividends_path", "dividends_sha256")):
            if sha256(_path(record[path_key])) != record[hash_key]:
                raise AssertionError(f"X01 {path_key} hash mismatch")
        for path_key, count_key in (("bars_path", "rows"), ("dividends_path", "dividend_rows")):
            with _path(record[path_key]).open(newline="", encoding="utf-8") as handle:
                actual = sum(1 for _ in csv.DictReader(handle))
            if actual != record[count_key]:
                raise AssertionError(f"X01 {path_key} row count mismatch")
    runner = _path(manifest["runner"]["path"])
    if sha256(runner) != manifest["runner"]["sha256"]:
        raise AssertionError("X01 runner hash mismatch")
    return {"symbols": len(manifest.get("symbols", [])), "manifest_sha256": manifest["manifest_sha256"]}


def verify_archive(path: Path, expected_member: str | None = None) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise AssertionError(f"archive CRC failure: {path}")
        members = archive.infolist()
        if expected_member and [member.filename for member in members] != [expected_member]:
            raise AssertionError("archive member mismatch")
        return {"members": len(members), "sizes": {member.filename: member.file_size for member in members}, "crcs": {member.filename: member.CRC for member in members}}


if __name__ == "__main__":
    matrix = REPO / "data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"
    x01 = REPO / "data/research_manifests/crossasset_x01_yahoo_fallback_2026_09_08.json"
    print(json.dumps({"matrix": verify_source_matrix(matrix), "x01": verify_x01_manifest(x01)}, sort_keys=True))
