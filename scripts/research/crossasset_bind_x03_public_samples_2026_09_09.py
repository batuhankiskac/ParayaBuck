"""Bind exact public SEC EWJ/EWZ N-PORT sample identities into X03 metadata.

The XML investment tags are retained as schema evidence only.  No holding
rows or valuation feature is admitted without a complete PIT series.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.research.crossasset_receipt_verifier_2026_09_08 import canonical_hash, sha256, verify_source_matrix

REPO = Path(__file__).resolve().parents[2]
ROUTE_ROOT = REPO / "data/research/external/crossasset_public_2026_09_09/public_route_exhaustion"
MATRIX_PATH = REPO / "data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"
REPORT_PATH = REPO / "data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json"
RECEIPT_DIR = REPO / "data/research/external/crossasset_public_2026_09_08/x02_x06_matrix/probes/receipts"
PREFLIGHT_MANIFEST_PATH = REPO / "data/research_manifests/crossasset_preflight_2026_09_08.json"
PREFLIGHT_RESULTS_PATH = REPO / "data/research/generated/crossasset_preflight_2026_09_08/results.json"
PREFLIGHT_VALIDATION_PATH = REPO / "data/research/generated/crossasset_preflight_2026_09_08/validation_receipt.json"

SAMPLES = {
    "EWJ": {
        "index_slug": "sec_EWJ_index_20260228",
        "xml_slug": "sec_EWJ_primary_xml_20260228",
        "accession": "0001004726-26-002861",
        "form": "NPORT-P",
        "series_id": "S000004249",
        "class_id": "C000011953",
        "report_date": "2026-02-28",
        "filed": "2026-04-23",
        "accepted": "2026-04-23T10:57:30Z",
        "primary_document": "primary_doc.xml",
    },
    "EWZ": {
        "index_slug": "sec_EWZ_index_20250228",
        "xml_slug": "sec_EWZ_primary_xml_20250228",
        "accession": "0001752724-25-090529",
        "form": "NPORT-P",
        "series_id": "S000004264",
        "class_id": "C000011968",
        "report_date": "2025-02-28",
        "filed": "2025-04-24",
        "accepted": "2025-04-24T13:34:49Z",
        "primary_document": "primary_doc.xml",
    },
}


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _body(slug: str) -> tuple[Path, dict[str, object]]:
    report = json.loads((ROUTE_ROOT / "route_exhaustion_report.json").read_text(encoding="utf-8"))
    route = next(row for row in report["routes"] if row["source_id"] == slug)
    if route["status"] != "HTTP_SUCCESS" or route["http_status"] != 200:
        raise AssertionError(f"required SEC route unavailable: {slug}")
    path = REPO / route["body_path"]
    if path.stat().st_size != route["bytes"] or sha256(path) != route["body_sha256"]:
        raise AssertionError(f"route body hash mismatch: {slug}")
    return path, route


def preflight_dependencies() -> dict[str, object]:
    """Return and validate the complete preflight dependency chain."""
    manifest = json.loads(PREFLIGHT_MANIFEST_PATH.read_text(encoding="utf-8"))
    results = json.loads(PREFLIGHT_RESULTS_PATH.read_text(encoding="utf-8"))
    validation = json.loads(PREFLIGHT_VALIDATION_PATH.read_text(encoding="utf-8"))
    manifest_sha = sha256(PREFLIGHT_MANIFEST_PATH)
    results_sha = sha256(PREFLIGHT_RESULTS_PATH)
    validation_sha = sha256(PREFLIGHT_VALIDATION_PATH)
    if results.get("manifest_sha256") != manifest_sha:
        raise AssertionError("preflight results manifest pin mismatch")
    if validation.get("manifest_sha256") != manifest_sha or validation.get("result_sha256") != results_sha:
        raise AssertionError("preflight validation receipt pin mismatch")
    generated_files = validation.get("generated_files")
    if not isinstance(generated_files, dict) or not generated_files:
        raise AssertionError("preflight generated file inventory missing")
    for relative, declared_sha in generated_files.items():
        path = REPO / str(relative)
        if not path.is_file() or sha256(path) != declared_sha:
            raise AssertionError(f"preflight generated file hash mismatch: {relative}")
    if manifest.get("output") != str(PREFLIGHT_RESULTS_PATH.relative_to(REPO)):
        raise AssertionError("preflight manifest output path mismatch")
    return {
        "manifest": {"path": str(PREFLIGHT_MANIFEST_PATH.relative_to(REPO)), "sha256": manifest_sha},
        "results": {"path": str(PREFLIGHT_RESULTS_PATH.relative_to(REPO)), "sha256": results_sha, "manifest_sha256": results.get("manifest_sha256")},
        "validation_receipt": {"path": str(PREFLIGHT_VALIDATION_PATH.relative_to(REPO)), "sha256": validation_sha, "manifest_sha256": validation.get("manifest_sha256"), "result_sha256": validation.get("result_sha256"), "generated_files": generated_files},
    }


def verify_gate_report_dependencies(path: Path = REPORT_PATH) -> dict[str, object]:
    report = json.loads(path.read_text(encoding="utf-8"))
    expected = preflight_dependencies()
    if report.get("preflight_dependencies") != expected:
        raise AssertionError("gate report preflight dependency chain mismatch")
    if report.get("preflight_manifest_path") != expected["manifest"]["path"] or report.get("preflight_manifest_sha256") != expected["manifest"]["sha256"]:
        raise AssertionError("gate report preflight manifest pin mismatch")
    if report.get("preflight_results_path") != expected["results"]["path"] or report.get("preflight_results_sha256") != expected["results"]["sha256"]:
        raise AssertionError("gate report preflight results pin mismatch")
    if report.get("preflight_validation_receipt_path") != expected["validation_receipt"]["path"] or report.get("preflight_validation_receipt_sha256") != expected["validation_receipt"]["sha256"]:
        raise AssertionError("gate report preflight validation pin mismatch")
    return expected


def _sample(ticker: str, spec: dict[str, str]) -> tuple[dict[str, object], dict[str, object]]:
    index_path, index_route = _body(spec["index_slug"])
    xml_path, xml_route = _body(spec["xml_slug"])
    xml_root = ElementTree.parse(xml_path).getroot()
    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
    invst = [node for node in xml_root.iter() if local(node.tag) == "invstOrSec"]
    def text_for(node: object, names: set[str]) -> str:
        for child in node.iter():
            if local(child.tag) in names and child.text and child.text.strip():
                return child.text.strip()
        return ""
    holding_fields = {
        "issuer": {"name", "issuerName"}, "title": {"title"}, "cusip": {"cusip"},
        "isin": {"isin"}, "lei": {"lei"}, "units": {"units"}, "balance": {"balance"},
        "currency": {"curCd", "currency"}, "value_usd": {"valUSD", "valueUSD"},
        "payoff_profile": {"payoffProfile"},
    }
    field_presence = {field: sum(bool(text_for(node, names)) for node in invst) for field, names in holding_fields.items()}
    text = index_path.read_text(encoding="utf-8", errors="replace")
    for expected in (spec["accession"], spec["filed"], spec["report_date"], spec["series_id"], spec["class_id"], ticker):
        if expected.replace("-", "") not in text.replace("-", "") and expected not in text:
            raise AssertionError(f"SEC index identity missing {expected}: {ticker}")
    source_id = f"SEC_{ticker}_NPORT-P_{spec['accession']}"
    endpoint = xml_route["endpoint"]
    receipt = {
        "schema": "crossasset-sanitized-probe-receipt-v1",
        "source_id": source_id,
        "card": "X03",
        "kind": "sec_nport_primary_xml",
        "endpoint": endpoint,
        "retrieved_at": xml_route["retrieved_at"],
        "status": "HTTP_SUCCESS",
        "http_status": 200,
        "bytes": xml_path.stat().st_size,
        "body_sha256": sha256(xml_path),
        "body_path": str(xml_path.relative_to(REPO)),
        "row_count": None,
        "count_semantics": "NPORT investment tags parsed as schema evidence; no holding rows admitted",
        "credential_fields_persisted": False,
    }
    receipt_path = RECEIPT_DIR / f"{source_id.replace('/', '_')}.receipt.json"
    _atomic_json(receipt_path, receipt)
    receipt_hash = sha256(receipt_path)
    record = {
        "family": "X03",
        "source_id": source_id,
        "url": endpoint,
        "attempted_at": xml_route["retrieved_at"],
        "http_status": 200,
        "status": "ACQUIRED_SAMPLE_METADATA_ONLY",
        "body_path": str(xml_path.relative_to(REPO)),
        "body_sha256": sha256(xml_path),
        "body_bytes": xml_path.stat().st_size,
        "receipt_path": str(receipt_path.relative_to(REPO)),
        "receipt_sha256": receipt_hash,
        "ticker": ticker,
        "cik": "0000930667",
        "accession": spec["accession"],
        "form": spec["form"],
        "filed": spec["filed"],
        "accepted": spec["accepted"],
        "report_date": spec["report_date"],
        "primary_document": spec["primary_document"],
        "series_ids": [spec["series_id"]],
        "class_ids": [spec["class_id"]],
        "structured_holdings_supported": False,
        "parsed_holding_rows": len(invst),
        "holding_tag_count": len(invst),
        "holding_field_presence": field_presence,
        "holding_count": 0,
        "admitted_holding_rows": 0,
        "holding_rows": [],
        "parsed_xml_or_html": True,
        "cutoff_status": "IN_CUTOFF_PUBLICATION",
        "binding_status": "IDENTITY_BOUND_SAMPLE_METADATA_ONLY",
        "coverage": "one official SEC N-PORT primary XML sample with exact issuer/series/class identity",
        "publication_or_pit": "filing and acceptance timestamps plus report date are bound; no continuous PIT valuation series or historical membership admitted",
        "blocker": "single bounded sample cannot establish full PIT valuation history or decision-time availability",
        "required_fields": ["cik", "accession", "form", "filingDate", "reportDate", "acceptanceDateTime", "seriesId", "classId", "holdings", "security_ids"],
        "document_identity": {
            "cik": "0000930667",
            "accession": spec["accession"],
            "filed": spec["filed"],
            "accepted": spec["accepted"],
            "report_date": spec["report_date"],
            "series_ids": [spec["series_id"]],
            "class_ids": [spec["class_id"]],
            "source_file": str(xml_path.relative_to(REPO)),
            "source_sha256": sha256(xml_path),
            "source_receipt": str(receipt_path.relative_to(REPO)),
            "source_receipt_sha256": receipt_hash,
            "identity_index_file": str(index_path.relative_to(REPO)),
            "identity_index_sha256": sha256(index_path),
        },
    }
    return record, receipt


def run() -> dict[str, object]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    records = matrix["records"]
    existing = {row["source_id"] for row in records}
    added = []
    for ticker, spec in SAMPLES.items():
        record, _ = _sample(ticker, spec)
        if record["source_id"] not in existing:
            records.append(record)
            added.append(record["source_id"])
        else:
            records[:] = [record if row["source_id"] == record["source_id"] else row for row in records]
    matrix["records"] = records
    matrix["matrix_sha256"] = canonical_hash(matrix, "matrix_sha256")
    _atomic_json(MATRIX_PATH, matrix)
    verify_source_matrix(MATRIX_PATH)

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    dependencies = preflight_dependencies()
    report["preflight_dependencies"] = dependencies
    report["preflight_manifest_path"] = dependencies["manifest"]["path"]
    report["preflight_manifest_sha256"] = dependencies["manifest"]["sha256"]
    report["preflight_results_path"] = dependencies["results"]["path"]
    report["preflight_results_sha256"] = dependencies["results"]["sha256"]
    report["preflight_validation_receipt_path"] = dependencies["validation_receipt"]["path"]
    report["preflight_validation_receipt_sha256"] = dependencies["validation_receipt"]["sha256"]
    report["matrix_sha256"] = matrix["matrix_sha256"]
    report["matrix_canonical_sha256"] = matrix["matrix_sha256"]
    report["record_count"] = len(records)
    report["receipt_count"] = len(records)
    x03 = report["X03"]
    x03["funds"] = {"MCHI": "0001100663", "EWJ": "0000930667", "EWZ": "0000930667"}
    x03["unresolved_funds"] = []
    x03_records = [row for row in records if row.get("family") == "X03"]
    counted_records = [row for row in x03_records if row.get("parsed_holding_rows")]
    bound_records = [row for row in counted_records if row.get("binding_status") == "IDENTITY_BOUND_SAMPLE_METADATA_ONLY"]
    unbound_records = [row for row in counted_records if row.get("binding_status") == "METADATA_ONLY_UNBOUND_LOCAL_XML"]
    identity_bound_rows = sum(row.get("parsed_holding_rows", 0) for row in bound_records)
    unbound_rows = sum(row.get("parsed_holding_rows", 0) for row in unbound_records)
    x03["sample_documents"] = sum(row.get("status") in {"ACQUIRED_SAMPLE", "ACQUIRED_SAMPLE_METADATA_ONLY"} for row in x03_records)
    x03["structured_evidence_rows"] = identity_bound_rows + unbound_rows
    x03["identity_bound_structured_evidence_rows"] = identity_bound_rows
    x03["unbound_structured_evidence_rows"] = unbound_rows
    x03["admitted_holding_rows"] = 0
    x03["binding_status"] = (
        "MIXED_IDENTITY_BOUND_AND_UNBOUND_METADATA_ONLY"
        if identity_bound_rows and unbound_rows
        else "IDENTITY_BOUND_SAMPLES_METADATA_ONLY"
        if identity_bound_rows
        else "METADATA_ONLY_UNBOUND_LOCAL_XML"
    )
    x03["official_sample_bindings"] = {}
    for ticker in ("EWJ", "EWZ"):
        bound = next(row for row in records if row.get("ticker") == ticker and row.get("binding_status") == "IDENTITY_BOUND_SAMPLE_METADATA_ONLY")
        x03["official_sample_bindings"][ticker] = {
            "cik": bound["cik"], "series_id": bound["series_ids"][0], "class_id": bound["class_ids"][0],
            "accession": bound["accession"], "filed": bound["filed"], "accepted": bound["accepted"],
            "report_date": bound["report_date"], "parsed_holding_rows": bound["parsed_holding_rows"],
            "source_path": bound["body_path"], "source_sha256": bound["body_sha256"],
            "receipt_path": bound["receipt_path"], "receipt_sha256": bound["receipt_sha256"],
            "identity_index_path": bound["document_identity"]["identity_index_file"],
            "identity_index_sha256": bound["document_identity"]["identity_index_sha256"],
        }
    x03["status"] = "DATA_BLOCKED_PIT_VALUATION_FIELDS_MISSING"
    # Keep the legacy SEC summary derived from this canonical X03 matrix in
    # lockstep with the detailed X03 section.  The two issuer samples are
    # identity-bound metadata-only evidence; no holding rows are admitted.
    metadata_only_statuses = {"METADATA_ONLY_UNBOUND_LOCAL_XML", "IDENTITY_BOUND_SAMPLE_METADATA_ONLY"}
    report["sec"] = {
        "admitted_holding_rows": x03["admitted_holding_rows"],
        "binding_status": x03["binding_status"],
        "identity_bound_samples": len(bound_records),
        "identity_bound_structured_evidence_rows": identity_bound_rows,
        "metadata_only_samples": sum(row.get("binding_status") in metadata_only_statuses for row in x03_records),
        "quarantined_post_cutoff_samples": sum(row.get("cutoff_status") == "QUARANTINED_POST_CUTOFF_PUBLICATION" for row in x03_records),
        "resolved_tickers": x03["funds"],
        "sample_documents": sum(row.get("status") in {"ACQUIRED_SAMPLE", "ACQUIRED_SAMPLE_METADATA_ONLY"} for row in x03_records),
        "structured_evidence_rows": identity_bound_rows + unbound_rows,
        "unbound_metadata_samples": len(unbound_records),
        "unbound_structured_evidence_rows": unbound_rows,
        "unresolved_tickers": [],
    }
    _atomic_json(REPORT_PATH, report)
    return {"added": added, "matrix_sha256": matrix["matrix_sha256"], "record_count": len(records)}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
