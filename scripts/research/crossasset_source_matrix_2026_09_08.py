"""Acquire bounded official/public source evidence for crossasset X02-X06.

The output is a provenance matrix and gate report only.  It contains no
credentials, no economic calculations, and no strategy/runtime integration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/crossasset_public_2026_09_08/x02_x06_matrix"
MATRIX = REPO / "data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"
REPORT = REPO / "data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json"
TARGET_CUTOFF = "2026-07-31T23:59:59Z"
FROZEN_AT = "2026-09-09T00:00:00+00:00"
OUT_REL = OUT.relative_to(REPO)
SEC_FORMS = {"NPORT-P", "NPORT-P/A", "N-CSR", "N-CSR/A", "N-CSRS", "N-CSRS/A"}
MCHI_RAW = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/MCHI_NPORT_2025-05-31_raw.xml"
MCHI_SUBMISSION = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/submissions_CIK0001100663.json"
X02_RETRY_MANIFEST = REPO / "data/research/external/crossasset_public_2026_09_10/x02_cme_retry/retry_manifest.json"
X02_TERMINAL_SOURCES = {"cme_settlements", "cme_treasury_futures_settlements"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: dict[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _x02_retry_evidence(source_id: str) -> dict[str, Any]:
    retry = json.loads(X02_RETRY_MANIFEST.read_text(encoding="utf-8"))
    attempts = [item for item in retry.get("records", []) if item.get("source_id") == source_id or item.get("endpoint") in {
        "https://www.cmegroup.com/market-data/settlements.html" if source_id == "cme_settlements" else "https://www.cmegroup.com/markets/interest-rates/us-treasury/30-year-us-treasury-bond.settlements.html"
    }]
    return {
        "retry_manifest_path": str(X02_RETRY_MANIFEST.relative_to(REPO)),
        "retry_manifest_sha256": _sha(X02_RETRY_MANIFEST),
        "retry_manifest_canonical_sha256": retry.get("manifest_sha256"),
        "attempt_count": len(attempts),
        "attempt_receipts": [
            {"slug": item.get("slug"), "path": item.get("receipt_path"), "sha256": item.get("receipt_sha256"), "status": item.get("status"), "http_status": item.get("http_status"), "error": item.get("error")}
            for item in attempts
        ],
        "outcome": retry.get("conclusion", "NO_ADMISSIBLE_CME_SETTLEMENT_ROWS"),
        "error_class": "READ_TIMEOUT",
        "source_absence_claim": False,
        "further_public_retry_required": False,
        "external_data_blocker": True,
        "admitted_rows": 0,
        "economic_rows": 0,
    }


def _apply_x02_terminality(records: list[dict[str, Any]]) -> None:
    """Promote the two original CME probes to the verified run-terminal state."""
    for record in records:
        if record.get("source_id") not in X02_TERMINAL_SOURCES:
            continue
        record.update({"status": "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN", "error_type": "ReadTimeout", "blocker": "official public CME routes exhausted for this bounded run; no source absence claim", **_x02_retry_evidence(record["source_id"])})


def _name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:120]


def _rel(path: Path, out_root: Path) -> str:
    if out_root in path.parents or path == out_root:
        return str(OUT_REL / path.relative_to(out_root))
    return str(path.relative_to(REPO))


def _write_receipt(record: dict[str, Any], *, out_root: Path, retrieved_at: str) -> None:
    source_id = str(record["source_id"])
    receipt_path = out_root / "probes" / "receipts" / f"{_name(source_id)}.receipt.json"
    body_path = record.get("body_path") or record.get("path")
    body = None
    if body_path:
        rel_path = Path(body_path)
        candidate = out_root / rel_path.relative_to(OUT_REL) if rel_path == OUT_REL or OUT_REL in rel_path.parents else REPO / rel_path
        body = candidate if candidate.is_file() else None
    body_bytes = body.stat().st_size if body and body.is_file() else 0
    row_count = None
    count_semantics = "not_tabular_or_not_declared"
    if body and body.is_file() and record.get("content_type", "").startswith("application/json"):
        try:
            payload = json.loads(body.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                row_count, count_semantics = len(payload), "json_array_rows"
            elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
                row_count, count_semantics = len(payload["data"]), "json_data_rows"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            count_semantics = "json_unparsed"
    status = "ON_DISK" if record.get("status", "").startswith("ACQUIRED_EXISTING") else ("REQUEST_ERROR" if record.get("http_status") is None and record.get("status") in {"PROBE_REQUEST_ERROR", "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN"} else ("HTTP_SUCCESS" if isinstance(record.get("http_status"), int) and 200 <= record["http_status"] < 300 else "HTTP_ERROR"))
    receipt = {
        "schema": "crossasset-sanitized-probe-receipt-v1",
        "source_id": source_id,
        "card": record.get("family", ""),
        "kind": "existing_file" if status == "ON_DISK" else "http_probe",
        "endpoint": record.get("url") or record.get("url_or_path"),
        "retrieved_at": retrieved_at,
        "status": status,
        "http_status": record.get("http_status") if isinstance(record.get("http_status"), int) else None,
        "error": record.get("error_type") if status == "REQUEST_ERROR" else None,
        "body_path": _rel(body, out_root) if body and body.is_file() and body.is_relative_to(out_root) else (body_path if body and body.is_file() else None),
        "body_sha256": _sha(body) if body and body.is_file() else None,
        "bytes": body_bytes,
        "row_count": row_count,
        "count_semantics": count_semantics,
        "credential_fields_persisted": False,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record["receipt_path"] = _rel(receipt_path, out_root)
    record["receipt_sha256"] = _sha(receipt_path)
    if isinstance(record.get("document_identity"), dict):
        record["document_identity"]["source_receipt"] = record["receipt_path"]
        record["document_identity"]["source_receipt_sha256"] = record["receipt_sha256"]
        for holding in record.get("holding_rows", []):
            if isinstance(holding.get("document_identity"), dict):
                holding["document_identity"] = dict(record["document_identity"])
    if body and body.is_file():
        if "body_path" in record:
            record["body_path"] = _rel(body, out_root)
            record["body_sha256"] = _sha(body)
        else:
            record["path"] = _rel(body, out_root)
            record["body_or_file_sha256"] = _sha(body)
        record["body_bytes"] = body_bytes


def _probe(source_id: str, family: str, url: str, *, params: dict[str, Any] | None = None, out_root: Path = OUT, retrieved: str = FROZEN_AT) -> dict[str, Any]:
    try:
        response = requests.get(url, params=params, timeout=30)
        body = response.content
        body_path = out_root / "probes" / f"{_name(source_id)}.body"
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        status = "PROBE_HTTP_200" if response.status_code == 200 else f"PROBE_HTTP_{response.status_code}"
        return {"family": family, "source_id": source_id, "url": response.url, "attempted_at": retrieved, "http_status": response.status_code, "status": status, "body_path": _rel(body_path, out_root), "body_sha256": _sha(body_path), "content_type": response.headers.get("content-type", "").split(";", 1)[0], "required_fields": [], "coverage": "bounded source probe only", "publication_or_pit": "not supplied by probe", "blocker": "probe does not establish field coverage or PIT admissibility"}
    except requests.RequestException as exc:
        return {"family": family, "source_id": source_id, "url": url, "attempted_at": retrieved, "http_status": None, "status": "PROBE_REQUEST_ERROR", "error_type": type(exc).__name__, "required_fields": [], "coverage": "none", "publication_or_pit": "not supplied", "blocker": "network/DNS request failed"}


def _sec_headers() -> dict[str, str]:
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        raise RuntimeError("SEC_USER_AGENT_REQUIRED")
    # Constructed only in process memory; never written to a receipt or stdout.
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def _sec_get(url: str) -> requests.Response:
    return requests.get(url, headers=_sec_headers(), timeout=30)


def _save_response(path: Path, response: requests.Response) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return _sha(path)


def _filing_samples(cik: str, submission: dict[str, Any]) -> list[dict[str, Any]]:
    recent = submission.get("filings", {}).get("recent", {})
    rows: list[dict[str, Any]] = []
    for index, form in enumerate(recent.get("form", [])):
        if form in SEC_FORMS:
            rows.append({key: recent.get(key, [""] * len(recent.get("form", [])))[index] for key in ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument")})
    # Select earliest and latest for each form, retaining bounded historical span.
    selected: list[dict[str, Any]] = []
    for form in sorted(SEC_FORMS):
        matches = sorted((row for row in rows if row["form"] == form), key=lambda row: (row.get("filingDate") or "", row["accessionNumber"]))
        if matches:
            selected.extend(matches[:1])
            if matches[-1] != matches[0]:
                selected.append(matches[-1])
    return selected


def _parse_sec_document(path: Path, form: str, *, cik: str = "", accession: str = "", filed: str = "", report_date: str = "", accepted: str = "", source_path: str = "", source_receipt: str = "", submission_index_path: str = "", submission_index_sha256: str = "") -> dict[str, Any]:
    resolved_path = path if path.is_absolute() else REPO / path
    text = resolved_path.read_text(encoding="utf-8", errors="replace")
    result: dict[str, Any] = {
        "form": form,
        "document_bytes": resolved_path.stat().st_size,
        "parsed_xml_or_html": bool(re.search(r"<html|<\?xml", text, flags=re.IGNORECASE)),
        "structured_holdings_supported": False,
        "holding_rows": [],
        "holding_count": 0,
        "holding_field_presence": {},
        "series_ids": [],
        "class_ids": [],
        "document_identity": {
            "cik": cik,
            "accession": accession,
            "report_date": report_date,
            "filed": filed,
            "accepted": accepted,
            "series_ids": [],
            "class_ids": [],
            "source_file": source_path or str(resolved_path.relative_to(REPO)),
            "source_sha256": _sha(resolved_path),
            "source_receipt": source_receipt,
            "source_receipt_sha256": "",
            "submission_index_path": submission_index_path,
            "submission_index_sha256": submission_index_sha256,
        },
    }
    if not form.startswith("NPORT"):
        return result
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        # SEC XSL-rendered HTML is retained as evidence, but it is not an
        # authoritative structured holdings payload.
        return result

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def first_text(node: Any, names: set[str]) -> str:
        for child in node.iter():
            if local(child.tag) in names and child.text and child.text.strip():
                return child.text.strip()
        return ""

    field_names = {
        "issuer": {"name", "issuerName"},
        "title": {"title"},
        "cusip": {"cusip"},
        "isin": {"isin"},
        "lei": {"lei"},
        "units": {"units"},
        "balance": {"balance"},
        "currency": {"curCd", "currency"},
        "value_usd": {"valUSD", "valueUSD"},
        "payoff_profile": {"payoffProfile"},
        "asset_category": {"assetCat"},
        "series_id": {"seriesId", "seriesIdentifier"},
        "class_id": {"classId", "classIdentifier"},
    }
    document_cik = first_text(root, {"cik", "regCik"}) or cik
    document_report = first_text(root, {"repPdDate", "reportDate"}) or report_date
    document_series = sorted({first_text(root, {"seriesId", "seriesIdentifier"})} - {""})
    document_classes = sorted({first_text(root, {"classId", "classIdentifier"})} - {""})
    identity = result["document_identity"]
    identity.update({"cik": document_cik, "report_date": document_report, "series_ids": document_series, "class_ids": document_classes})
    holdings: list[dict[str, Any]] = []
    for node in root.iter():
        if local(node.tag) != "invstOrSec":
            continue
        holding = {field: first_text(node, names) for field, names in field_names.items()}
        holding["series_id"] = holding["series_id"] or (document_series[0] if document_series else "")
        holding["class_id"] = holding["class_id"] or (document_classes[0] if document_classes else "")
        holding["document_identity"] = dict(identity)
        holdings.append(holding)
    result["structured_holdings_supported"] = True
    result["holding_rows"] = holdings
    result["holding_count"] = len(holdings)
    result["holding_tag_count"] = len(holdings)
    result["security_id_tag_count"] = sum(bool(row["cusip"] or row["isin"] or row["lei"]) for row in holdings)
    result["holding_field_presence"] = {field: sum(bool(row[field]) for row in holdings) for field in field_names}
    result["series_ids"] = sorted({row["series_id"] for row in holdings if row["series_id"]})
    result["class_ids"] = sorted({row["class_id"] for row in holdings if row["class_id"]})
    result["document_identity"] = identity
    return result


def _existing_mchi_structured_evidence() -> dict[str, Any] | None:
    """Retain the local parse count without asserting a filing binding.

    The raw XML is useful schema evidence, but its exact accession/receipt
    pairing is absent from the frozen inputs.  Therefore its parsed holdings
    are deliberately not emitted as holding rows or admitted inputs.
    """
    if not MCHI_RAW.is_file() or not MCHI_SUBMISSION.is_file():
        return None
    parsed = _parse_sec_document(
        MCHI_RAW,
        "NPORT-P",
        cik="0001100663",
        report_date="2025-05-31",
        source_path=str(MCHI_RAW.relative_to(REPO)),
        submission_index_path=str(MCHI_SUBMISSION.relative_to(REPO)),
        submission_index_sha256=_sha(MCHI_SUBMISSION),
    )
    parsed_rows = int(parsed["holding_count"])
    parsed["holding_rows"] = []
    parsed["holding_count"] = 0
    parsed["parsed_holding_rows"] = parsed_rows
    parsed["admitted_holding_rows"] = 0
    parsed["structured_holdings_supported"] = False
    parsed["binding_status"] = "METADATA_ONLY_UNBOUND_LOCAL_XML"
    parsed["blocker"] = "raw XML parse is retained as schema evidence; exact accession and retrieval receipt are not independently bound"
    parsed["status"] = "ACQUIRED_EXISTING_METADATA_ONLY"
    parsed["coverage"] = "one locally retained MCHI NPORT structured parse; no holding rows admitted"
    parsed["publication_or_pit"] = "report date and issuer identity are present; filing accession/acceptance binding and valuation PIT are absent"
    parsed.update({
        "family": "X03",
        "source_id": "SEC_existing_MCHI_NPORT_structured_parse_2025-05-31",
        "url": "https://www.sec.gov/Archives/edgar/data/1100663/",
        "attempted_at": FROZEN_AT,
        "http_status": None,
        "body_path": str(MCHI_RAW.relative_to(REPO)),
        "body_sha256": _sha(MCHI_RAW),
        "body_bytes": MCHI_RAW.stat().st_size,
        "ticker": "MCHI",
        "cik": "0001100663",
        "report_date": "2025-05-31",
        "required_fields": ["cik", "accession", "form", "filingDate", "reportDate", "acceptanceDateTime", "seriesId", "classId", "holdings", "security_ids"],
    })
    return parsed


def _acquire_sec(*, out_root: Path = OUT, retrieved: str = FROZEN_AT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = "https://www.sec.gov/files/company_tickers.json"
    local_structured_evidence = _existing_mchi_structured_evidence()
    try:
        response = _sec_get(url)
    except (requests.RequestException, RuntimeError) as exc:
        existing = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/submissions_CIK0001100663.json"
        records = [{"family": "X03", "source_id": "SEC_company_tickers", "url": url, "attempted_at": retrieved, "http_status": None, "status": "PROBE_REQUEST_ERROR", "error_type": type(exc).__name__, "required_fields": ["ticker", "cik_str"], "coverage": "none", "publication_or_pit": "not supplied", "blocker": "SEC_USER_AGENT_REQUIRED" if isinstance(exc, RuntimeError) else "network/DNS request failed"}]
        if existing.is_file():
            records.append({"family": "X03", "source_id": "SEC_existing_CIK0001100663", "url": "https://data.sec.gov/submissions/CIK0001100663.json", "attempted_at": retrieved, "http_status": None, "status": "ACQUIRED_EXISTING_METADATA", "body_path": str(existing.relative_to(REPO)), "body_sha256": _sha(existing), "cik": "0001100663", "required_fields": ["cik", "accession", "form", "filingDate", "reportDate", "acceptanceDateTime", "holdings", "security_ids"], "coverage": "issuer metadata and historical filing index already present", "publication_or_pit": "SEC filing dates are publication metadata; no valuation PIT vintages inferred", "blocker": "network DNS prevented refresh; EWJ/EWZ fund-to-CIK mapping remains unresolved without a fresh authoritative lookup"})
        if local_structured_evidence is not None:
            records.append(local_structured_evidence)
        return records, {"resolved_tickers": {"MCHI": "0001100663"}, "unresolved_tickers": ["EWJ", "EWZ"], "sample_documents": 1 if existing.is_file() else 0, "structured_evidence_rows": local_structured_evidence.get("parsed_holding_rows", 0) if local_structured_evidence else 0, "admitted_holding_rows": 0, "blocker": type(exc).__name__}
    path = out_root / "sec" / "company_tickers.json"
    digest = _save_response(path, response)
    mapping: dict[str, str] = {}
    if response.status_code == 200:
        payload = response.json()
        for row in payload.values() if isinstance(payload, dict) else []:
            if isinstance(row, dict) and row.get("ticker") in {"MCHI", "EWJ", "EWZ"}:
                mapping[row["ticker"]] = str(row["cik_str"]).zfill(10)
    records: list[dict[str, Any]] = [{"family": "X03", "source_id": "SEC_company_tickers", "url": url, "attempted_at": retrieved, "http_status": response.status_code, "status": "ACQUIRED_METADATA" if mapping else "BLOCKED_CIK_RESOLUTION", "body_path": _rel(path, out_root), "body_sha256": digest, "required_fields": ["ticker", "cik_str"], "coverage": "SEC company ticker index; ETF rows may be absent", "publication_or_pit": "metadata retrieval only", "blocker": "no exact ETF mapping in response" if not mapping else "", "resolved_tickers": mapping}]
    exchange_url = "https://www.sec.gov/files/company_tickers_exchange.json"
    try:
        exchange_response = _sec_get(exchange_url)
    except (requests.RequestException, RuntimeError) as exc:
        records.append({"family": "X03", "source_id": "SEC_company_tickers_exchange", "url": exchange_url, "attempted_at": retrieved, "http_status": None, "status": "PROBE_REQUEST_ERROR", "error_type": type(exc).__name__, "required_fields": ["ticker", "cik", "exchange"], "coverage": "official exchange ticker identity refresh unavailable", "publication_or_pit": "not supplied", "blocker": "official exchange ticker lookup failed"})
    else:
        exchange_path = out_root / "sec" / "company_tickers_exchange.json"
        exchange_digest = _save_response(exchange_path, exchange_response)
        records.append({"family": "X03", "source_id": "SEC_company_tickers_exchange", "url": exchange_url, "attempted_at": retrieved, "http_status": exchange_response.status_code, "status": "ACQUIRED_METADATA" if exchange_response.status_code == 200 else "BLOCKED_HTTP", "body_path": _rel(exchange_path, out_root), "body_sha256": exchange_digest, "required_fields": ["ticker", "cik", "exchange"], "coverage": "official SEC exchange ticker identity file", "publication_or_pit": "metadata retrieval only", "blocker": "EWJ/EWZ exact fund series still requires series/class mapping"})
    if not mapping:
        existing = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/submissions_CIK0001100663.json"
        mapping = {"MCHI": "0001100663"} if existing.is_file() else {}
        if existing.is_file():
            records.append({"family": "X03", "source_id": "SEC_existing_CIK0001100663", "url": "https://data.sec.gov/submissions/CIK0001100663.json", "attempted_at": retrieved, "http_status": "on_disk", "status": "ACQUIRED_EXISTING_METADATA", "body_path": str(existing.relative_to(REPO)), "body_sha256": _sha(existing), "cik": "0001100663", "required_fields": ["cik", "accession", "form", "filingDate", "reportDate", "acceptanceDateTime", "holdings", "security_ids"], "coverage": "issuer metadata and historical filing index already present", "publication_or_pit": "SEC filing dates are publication metadata; no valuation PIT vintages inferred", "blocker": "SEC company_tickers has no ETF rows; EWJ/EWZ fund-to-CIK mapping remains unresolved without fund-series lookup"})
        unresolved = [ticker for ticker in ("EWJ", "EWZ") if ticker not in mapping]
    else:
        unresolved = [ticker for ticker in ("MCHI", "EWJ", "EWZ") if ticker not in mapping]
    documents: list[dict[str, Any]] = []
    for ticker, cik in sorted(mapping.items()):
        sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            sub_response = _sec_get(sub_url)
        except (requests.RequestException, RuntimeError) as exc:
            records.append({"family": "X03", "source_id": f"SEC_submissions_{ticker}", "url": sub_url, "attempted_at": retrieved, "http_status": None, "status": "PROBE_REQUEST_ERROR", "error_type": type(exc).__name__, "cik": cik, "required_fields": ["accession", "form", "filingDate", "reportDate", "acceptanceDateTime"], "coverage": "refresh unavailable", "publication_or_pit": "not supplied", "blocker": "SEC_USER_AGENT_REQUIRED" if isinstance(exc, RuntimeError) else "SEC data endpoint DNS unavailable during authorized probe"})
            continue
        sub_path = out_root / "sec" / f"submissions_CIK{cik}.json"
        sub_digest = _save_response(sub_path, sub_response)
        records.append({"family": "X03", "source_id": f"SEC_submissions_{ticker}", "url": sub_url, "attempted_at": retrieved, "http_status": sub_response.status_code, "status": "ACQUIRED_METADATA" if sub_response.status_code == 200 else "BLOCKED_HTTP", "body_path": _rel(sub_path, out_root), "body_sha256": sub_digest, "cik": cik, "required_fields": ["accession", "form", "filingDate", "reportDate", "acceptanceDateTime", "primaryDocument"], "coverage": "issuer submission index", "publication_or_pit": "SEC filed and acceptance timestamps; no valuation PIT inferred", "blocker": "fund series membership requires document-level identity"})
        if sub_response.status_code != 200:
            continue
        for filing in _filing_samples(cik, sub_response.json()):
            accession = filing["accessionNumber"]
            accession_path = accession.replace("-", "")
            document = filing.get("primaryDocument") or ""
            if not document:
                continue
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{document}"
            try:
                doc_response = _sec_get(doc_url)
            except (requests.RequestException, RuntimeError) as exc:
                records.append({"family": "X03", "source_id": f"SEC_{ticker}_{filing['form']}_{accession}", "url": doc_url, "attempted_at": retrieved, "http_status": None, "status": "PROBE_REQUEST_ERROR", "error_type": type(exc).__name__, "ticker": ticker, "cik": cik, "accession": accession, "form": filing["form"], "required_fields": ["accession", "form", "filingDate", "reportDate", "acceptanceDateTime", "holdings", "security_ids"], "coverage": "refresh unavailable", "publication_or_pit": "not supplied", "blocker": "SEC_USER_AGENT_REQUIRED" if isinstance(exc, RuntimeError) else "SEC document request failed"})
                continue
            doc_path = out_root / "sec" / ticker / f"{accession_path}_{_name(document)}"
            doc_digest = _save_response(doc_path, doc_response)
            parsed = _parse_sec_document(doc_path, filing["form"], cik=cik, accession=accession, filed=filing.get("filingDate", ""), report_date=filing.get("reportDate", ""), accepted=filing.get("acceptanceDateTime", ""), source_path=_rel(doc_path, out_root), submission_index_sha256=sub_digest) if doc_response.status_code == 200 else {}
            record = {"family": "X03", "source_id": f"SEC_{ticker}_{filing['form']}_{accession}", "url": doc_url, "attempted_at": retrieved, "http_status": doc_response.status_code, "status": "ACQUIRED_SAMPLE" if doc_response.status_code == 200 else "BLOCKED_HTTP", "body_path": _rel(doc_path, out_root), "body_sha256": doc_digest, "ticker": ticker, "cik": cik, "accession": accession, "form": filing["form"], "filed": filing.get("filingDate"), "report_date": filing.get("reportDate"), "accepted": filing.get("acceptanceDateTime"), "primary_document": document, "required_fields": ["accession", "form", "filingDate", "reportDate", "acceptanceDateTime", "holdings", "security_ids"], "coverage": "bounded issuer-level SEC document sample", "publication_or_pit": "filed/accepted/report dates captured; valuation PIT not established", "blocker": "fund-level identity and complete historical membership require series-aware parsing"}
            record["cutoff_status"] = "QUARANTINED_POST_CUTOFF_PUBLICATION" if (record["filed"] or "") > "2026-07-31" or (record["accepted"] or "")[:10] > "2026-07-31" else "IN_CUTOFF_PUBLICATION"
            record.update(parsed)
            documents.append(record)
    if local_structured_evidence is not None:
        documents.append(local_structured_evidence)
    return records + documents, {"resolved_tickers": mapping, "unresolved_tickers": unresolved, "sample_documents": len(documents), "structured_evidence_rows": local_structured_evidence.get("parsed_holding_rows", 0) if local_structured_evidence else 0, "admitted_holding_rows": 0}


def _existing(family: str, source_id: str, path: Path, url: str, required_fields: list[str], *, status: str, blocker: str, coverage: str, pit: str, attempted_at: str = FROZEN_AT) -> dict[str, Any]:
    return {"family": family, "source_id": source_id, "url_or_path": url, "attempted_at": attempted_at, "http_status": None, "status": status, "body_or_file_sha256": _sha(path) if path.is_file() else None, "path": str(path.relative_to(REPO)) if path.is_file() else None, "required_fields": required_fields, "coverage": coverage, "publication_or_pit": pit, "blocker": blocker}


def build_matrix(*, out_root: Path = OUT, retrieved: str = FROZEN_AT) -> tuple[dict[str, Any], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend([_probe("cme_settlements", "X02", "https://www.cmegroup.com/market-data/settlements.html", out_root=out_root, retrieved=retrieved), _probe("cme_treasury_futures_settlements", "X02", "https://www.cmegroup.com/markets/interest-rates/us-treasury/30-year-us-treasury-bond.settlements.html", out_root=out_root, retrieved=retrieved), _probe("cftc_cot", "X02", "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm", out_root=out_root, retrieved=retrieved), _probe("cftc_cot_bulk_2026", "X02", "https://www.cftc.gov/files/dea/history/deacot2026.zip", out_root=out_root, retrieved=retrieved), _probe("treasury_curve", "X02", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve&field_tdr_date_value=2026", out_root=out_root, retrieved=retrieved)])
    sec_records, sec_summary = _acquire_sec(out_root=out_root, retrieved=retrieved)
    records.extend(sec_records)
    records.extend([_probe("finra_short_sale_volume", "X04", "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files", out_root=out_root, retrieved=retrieved), _probe("nyse_short_interest", "X04", "https://www.nyse.com/market-data/short-interest", out_root=out_root, retrieved=retrieved), _probe("ibkr_slb_public", "X04", "https://www.interactivebrokers.com/en/trading/short-securities-availability.php", out_root=out_root, retrieved=retrieved)])
    # These are explicit official REST alternatives; a 401/404 is evidence of
    # the route response, never a claim that the archive has causal availability.
    records.extend([
        _probe("binance_um_open_interest_rest", "X05", "https://fapi.binance.com/futures/data/openInterestHist", params={"symbol": "BTCUSDT", "period": "1d", "limit": 5}, out_root=out_root, retrieved=retrieved),
        _probe("binance_cm_open_interest_rest", "X05", "https://dapi.binance.com/futures/data/openInterestHist", params={"pair": "BTCUSD", "period": "1d", "limit": 5}, out_root=out_root, retrieved=retrieved),
        _probe("binance_um_force_orders_rest", "X06", "https://fapi.binance.com/fapi/v1/forceOrders", params={"symbol": "BTCUSDT", "limit": 5}, out_root=out_root, retrieved=retrieved),
        _probe("binance_cm_force_orders_rest", "X06", "https://dapi.binance.com/dapi/v1/forceOrders", params={"symbol": "BTCUSD_PERP", "limit": 5}, out_root=out_root, retrieved=retrieved),
    ])
    required = {
        "cme_settlements": ["venue", "contract_code", "contract_month", "settlement_time", "settlement_price", "currency", "publication_time"],
        "cme_treasury_futures_settlements": ["venue", "contract_code", "contract_month", "settlement_time", "settlement_price", "currency", "publication_time"],
        "cftc_cot": ["report_date", "market", "contract_code", "open_interest", "participant_positions", "publication_time"],
        "cftc_cot_bulk_2026": ["report_date", "market", "contract_code", "open_interest", "participant_positions", "publication_time"],
        "treasury_curve": ["observation_date", "maturity_bucket", "yield", "publication_time"],
        "finra_short_sale_volume": ["trade_date", "symbol", "short_volume", "total_volume", "publication_time"],
        "nyse_short_interest": ["settlement_date", "symbol", "short_interest", "publication_time"],
        "ibkr_slb_public": ["instrument", "available_quantity", "borrow_rate", "recall_flag", "observation_time", "publication_time"],
    }
    for record in records:
        if record["source_id"] in required:
            record["required_fields"] = required[record["source_id"]]
    sec_summary["quarantined_post_cutoff_samples"] = sum(record.get("cutoff_status") == "QUARANTINED_POST_CUTOFF_PUBLICATION" for record in records)
    oi = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K09_binance_oi_metrics_archive_pilot.csv"
    liq = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K10_binance_liquidation_snapshot_pilot.csv"
    records.extend([_existing("X05", "binance_vision_metrics", oi, "https://data.binance.vision/data/futures/um/daily/metrics/", ["venue", "contract_type", "instrument", "event_time_utc", "metric", "value", "unit", "sum_open_interest_value_usd", "available_at"], status="ACQUIRED_ARCHIVE_SAMPLE", blocker="continuous historical and PIT availability absent", coverage="1,152 rows; BTCUSDT/ETHUSDT; 2024-01-02 and 2026-07-30 archive samples", pit="archive Last-Modified is publication bound; row available_at blank"), _existing("X06", "binance_vision_liquidation_snapshot", liq, "https://data.binance.vision/data/futures/cm/daily/liquidationSnapshot/", ["venue", "instrument", "event_time_utc", "side", "order_type", "time_in_force", "original_quantity", "price", "average_price", "order_status", "last_fill_quantity", "accumulated_fill_quantity", "available_at"], status="ACQUIRED_ARCHIVE_SAMPLE", blocker="259 exact duplicate rows and blank availability; public REST alternative returned 404/401", coverage="518 rows / 259 unique events; BTCUSD_PERP and ETHUSD_PERP; 2024-01-02", pit="archive Last-Modified is publication bound; row available_at blank")])
    _apply_x02_terminality(records)
    for record in records:
        _write_receipt(record, out_root=out_root, retrieved_at=retrieved)
    matrix = {"schema": "crossasset-source-matrix-v1", "generated_at": retrieved, "target_cutoff": TARGET_CUTOFF, "records": records}
    report = {"schema": "crossasset-x02-x06-gate-report-v1", "matrix_sha256": "", "sec": sec_summary, "X02": {"status": "DATA_BLOCKED_CONTRACT_CURVE_FIELDS_NOT_ADMITTED", "required_fields_missing": ["dated_contract_identity", "settlement", "collateral", "margin", "liquidation", "expiry", "roll", "fees"], "proxy_policy": "OHLCV and undated/current snapshots cannot substitute"}, "X03": {"status": "DATA_BLOCKED_PIT_VALUATION_FIELDS_MISSING", "funds": sec_summary.get("resolved_tickers", {}), "unresolved_funds": sec_summary.get("unresolved_tickers", []), "sample_documents": sec_summary.get("sample_documents", 0), "structured_evidence_rows": sec_summary.get("structured_evidence_rows", 0), "admitted_holding_rows": 0, "binding_status": "METADATA_ONLY_UNBOUND_LOCAL_XML", "missing": ["independently bound raw XML accession/retrieval receipt", "historical point-in-time price/book", "historical point-in-time price/earnings", "complete membership snapshots across all periods"]}, "X04": {"status": "DATA_BLOCKED_BORROW_HISTORY_UNAVAILABLE", "ledger": "research-only two-leg schema emitted; production long-only capability unchanged"}, "X05": {"status": "DATA_BLOCKED_CONTINUOUS_PIT_OI_UNAVAILABLE", "raw_rows": 1152, "admitted_rows": 0}, "X06": {"status": "DATA_BLOCKED_DUPLICATE_REVIEW_AND_PIT_UNAVAILABLE", "raw_rows": 518, "unique_rows": 259, "exact_duplicate_rows": 259, "admitted_rows": 0}}
    x02_records = [record for record in records if record.get("source_id") in X02_TERMINAL_SOURCES]
    if len(x02_records) == len(X02_TERMINAL_SOURCES):
        report["X02"].update({
            "status": "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN",
            "outcome": "NO_ADMISSIBLE_CME_SETTLEMENT_ROWS",
            "error_class": "READ_TIMEOUT",
            "attempt_count": sum(int(record.get("attempt_count", 0)) for record in x02_records),
            "attempt_receipts": [receipt for record in x02_records for receipt in record.get("attempt_receipts", [])],
            "retry_manifest_path": x02_records[0].get("retry_manifest_path"),
            "retry_manifest_sha256": x02_records[0].get("retry_manifest_sha256"),
            "retry_manifest_canonical_sha256": x02_records[0].get("retry_manifest_canonical_sha256"),
            "source_absence_claim": False,
            "further_public_retry_required": False,
            "external_data_blocker": True,
            "admitted_rows": 0,
            "economic_rows": 0,
        })
    return matrix, report


def reclassify_existing() -> dict[str, Any]:
    """Reclassify only the frozen matrix's two CME records, without network."""
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    records = matrix.get("records", [])
    _apply_x02_terminality(records)
    matrix["records"] = records
    matrix["matrix_sha256"] = _canonical_hash(matrix, "matrix_sha256")
    matrix_tmp = MATRIX.with_suffix(".terminal.tmp")
    matrix_tmp.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    x02_records = [record for record in records if record.get("source_id") in X02_TERMINAL_SOURCES]
    if len(x02_records) != len(X02_TERMINAL_SOURCES):
        raise RuntimeError("X02 terminal records missing")
    report["matrix_sha256"] = matrix["matrix_sha256"]
    report["matrix_file_sha256"] = _sha(matrix_tmp)
    report["matrix_canonical_sha256"] = matrix["matrix_sha256"]
    report["X02"].update({
        "status": "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN",
        "outcome": "NO_ADMISSIBLE_CME_SETTLEMENT_ROWS",
        "error_class": "READ_TIMEOUT",
        "attempt_count": sum(int(record.get("attempt_count", 0)) for record in x02_records),
        "attempt_receipts": [receipt for record in x02_records for receipt in record.get("attempt_receipts", [])],
        "retry_manifest_path": x02_records[0].get("retry_manifest_path"),
        "retry_manifest_sha256": x02_records[0].get("retry_manifest_sha256"),
        "retry_manifest_canonical_sha256": x02_records[0].get("retry_manifest_canonical_sha256"),
        "source_absence_claim": False,
        "further_public_retry_required": False,
        "external_data_blocker": True,
        "admitted_rows": 0,
        "economic_rows": 0,
    })
    report_tmp = REPORT.with_suffix(".terminal.tmp")
    report_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        from scripts.research.crossasset_receipt_verifier_2026_09_08 import verify_source_matrix
    except ModuleNotFoundError:
        from crossasset_receipt_verifier_2026_09_08 import verify_source_matrix
    verify_source_matrix(matrix_tmp)
    os.replace(matrix_tmp, MATRIX)
    os.replace(report_tmp, REPORT)
    return report


def refresh_gate_report() -> dict[str, Any]:
    """Repin the crossasset gate to the current matrix and preflight outputs."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    preflight_manifest = REPO / "data/research_manifests/crossasset_preflight_2026_09_08.json"
    results = REPO / "data/research/generated/crossasset_preflight_2026_09_08/results.json"
    validation = REPO / "data/research/generated/crossasset_preflight_2026_09_08/validation_receipt.json"
    validation_payload = json.loads(validation.read_text(encoding="utf-8"))
    matrix_file_sha = _sha(MATRIX)
    matrix_canonical_sha = matrix.get("matrix_sha256")
    manifest_sha = _sha(preflight_manifest)
    results_sha = _sha(results)
    validation_sha = _sha(validation)
    report.update({
        "matrix_sha256": matrix_canonical_sha,
        "matrix_canonical_sha256": matrix_canonical_sha,
        "matrix_file_sha256": matrix_file_sha,
        "preflight_manifest_sha256": manifest_sha,
        "preflight_results_sha256": results_sha,
        "preflight_validation_receipt_sha256": validation_sha,
    })
    report["preflight_dependencies"] = {
        "manifest": {"path": str(preflight_manifest.relative_to(REPO)), "sha256": manifest_sha},
        "results": {"path": str(results.relative_to(REPO)), "sha256": results_sha, "manifest_sha256": manifest_sha},
        "validation_receipt": {
            "path": str(validation.relative_to(REPO)),
            "sha256": validation_sha,
            "manifest_sha256": validation_payload.get("manifest_sha256"),
            "result_sha256": validation_payload.get("result_sha256"),
            "generated_files": validation_payload.get("generated_files", {}),
        },
    }
    tmp = REPORT.with_suffix(".dependencies.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, REPORT)
    return report


def verify_current() -> dict[str, Any]:
    """Verify current matrix/report pins without network or writes."""
    before = {path: (_sha(path), path.stat().st_mtime_ns) for path in [MATRIX, REPORT] if path.is_file()}
    try:
        from scripts.research.crossasset_receipt_verifier_2026_09_08 import verify_source_matrix
    except ModuleNotFoundError:
        from crossasset_receipt_verifier_2026_09_08 import verify_source_matrix
    matrix_result = verify_source_matrix(MATRIX)
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("matrix_sha256") != matrix.get("matrix_sha256") or report.get("matrix_canonical_sha256") != matrix.get("matrix_sha256"):
        raise RuntimeError("gate report matrix canonical pin mismatch")
    if report.get("matrix_file_sha256") != _sha(MATRIX):
        raise RuntimeError("gate report matrix file pin mismatch")
    for record in matrix.get("records", []):
        if record.get("source_id") in X02_TERMINAL_SOURCES and record.get("status") != "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN":
            raise RuntimeError(f"X02 terminal classification missing: {record.get('source_id')}")
    after = {path: (_sha(path), path.stat().st_mtime_ns) for path in [MATRIX, REPORT] if path.is_file()}
    if before != after:
        raise RuntimeError("source matrix verify mutated bytes or mtime")
    return {"status": "X02_TERMINAL_ROUTE_CLASSIFICATION_VERIFIED", "read_only": True, **matrix_result}


def main() -> dict[str, Any]:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".x02_x06_matrix.", dir=str(OUT.parent)))
    staged_matrix = stage.parent / (stage.name + ".matrix.json")
    staged_report = stage.parent / (stage.name + ".report.json")
    try:
        matrix, report = build_matrix(out_root=stage, retrieved=FROZEN_AT)
        try:
            from scripts.research.crossasset_receipt_verifier_2026_09_08 import canonical_hash, verify_source_matrix
        except ModuleNotFoundError:
            from crossasset_receipt_verifier_2026_09_08 import canonical_hash, verify_source_matrix
        matrix["matrix_sha256"] = canonical_hash(matrix, "matrix_sha256")
        staged_matrix.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["matrix_sha256"] = matrix["matrix_sha256"]
        report["matrix_canonical_sha256"] = matrix["matrix_sha256"]
        report["matrix_file_sha256"] = _sha(staged_matrix)
        report["record_count"] = len(matrix["records"])
        report["receipt_count"] = sum(bool(record.get("receipt_path")) for record in matrix["records"])
        for card in ("X05", "X06"):
            report[card]["rest_probes"] = [
                {"source_id": record["source_id"], "status": record["status"], "http_status": record.get("http_status"), "receipt_path": record["receipt_path"], "body_sha256": record.get("body_sha256")}
                for record in matrix["records"] if record.get("family") == card and record["source_id"].endswith("_rest")
            ]
        staged_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        verify_source_matrix(staged_matrix, output_root=stage)
        previous = OUT.parent / (OUT.name + ".previous")
        if OUT.exists():
            if previous.exists():
                shutil.rmtree(previous)
            os.replace(OUT, previous)
        os.replace(stage, OUT)
        os.replace(staged_matrix, MATRIX)
        os.replace(staged_report, REPORT)
        return report
    except Exception:
        if not OUT.exists() and (OUT.parent / (OUT.name + ".previous")).exists():
            os.replace(OUT.parent / (OUT.name + ".previous"), OUT)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        for leftover in (staged_matrix, staged_report):
            if leftover.exists():
                leftover.unlink()


if __name__ == "__main__":
    if "--verify" in os.sys.argv[1:]:
        print(json.dumps(verify_current(), indent=2, sort_keys=True))
    elif "--reclassify-existing" in os.sys.argv[1:]:
        print(json.dumps(reclassify_existing(), indent=2, sort_keys=True))
    elif "--refresh-gate-report" in os.sys.argv[1:]:
        print(json.dumps(refresh_gate_report(), indent=2, sort_keys=True))
    else:
        print(json.dumps(main(), indent=2, sort_keys=True))
