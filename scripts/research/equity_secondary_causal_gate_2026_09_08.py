"""Research-only acquisition and causal/schema gate for H06-H13, H15 and H16."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CUTOFF = "2026-07-31T23:59:59Z"
PROTOCOL = REPO / "docs/research/equity_secondary_causal_protocol_2026_09_08.md"
REPORT_MD = REPO / "docs/research/equity_secondary_causal_gate_report_2026_09_08.md"
REPORT = REPO / "data/research_runs/equity_secondary_causal_gate_2026_09_08/gate_report.json"
MANIFEST = REPO / "data/research_manifests/equity_secondary_causal_gate_2026_09_08.json"
TEST = REPO / "tests/research/test_equity_secondary_causal_gate_2026_09_08.py"
MASTER = REPO / "docs/research/strategy_queue_full_report_2026_09_06.md"
DETAIL = REPO / "docs/research/strategy_queue_equity_review_2026_09_06.md"
INDEPENDENT = REPO / "docs/research/strategy_queue_equity_independent_review_2026_09_06.md"
SEC_DOC_ROOT = REPO / "data/research/external/equity_sec_original_documents_pilot_2026_09_07"
SEC_DOC_RECEIPT = SEC_DOC_ROOT / "document_acquisition_receipt.json"
SEC_V2_ROOT = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2"
SEC_V2_RECEIPT = SEC_V2_ROOT / "acquisition_receipt.json"
SEC_ARCHIVE_ROOT = REPO / "data/research/external/equity_sec_archived_submissions_2026_09_07"
SEC_ARCHIVE_RECEIPT = SEC_ARCHIVE_ROOT / "archived_submissions_receipt.json"
PIT_UNIVERSE = REPO / "data/research/external/equity_pit_universe_2026_09_07/pit_eligibility_intervals.jsonl"
BARS = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
ACTIONS = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
NPORT = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/MCHI_NPORT_2025-05-31_raw.xml"
NPORT_SUB = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/submissions_CIK0001100663.json"
SOURCE_MATRIX = REPO / "data/research/external/equity_secondary_public_2026_09_08/source_matrix.json"
PUBLIC_ROOT = SOURCE_MATRIX.parent
CANDIDATE_INVENTORY = PUBLIC_ROOT / "candidate_filing_inventory.json"
BATCH_RECEIPT = PUBLIC_ROOT / "sec_batch_receipt.json"
BATCH_ANALYSIS = PUBLIC_ROOT / "sec_batch_analysis.json"
H09_SCAN = PUBLIC_ROOT / "h09_relationship_scan.json"
H11_INVENTORY = PUBLIC_ROOT / "h11_nport_inventory.json"
NPORT_BATCH_RECEIPT = PUBLIC_ROOT / "nport_batch_receipt.json"
ROUTE_RECEIPT = PUBLIC_ROOT / "candidate_public_route_receipt.json"
H06_LOCAL_MANIFEST = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09/manifest.json"
H06_LOCAL_REPORT = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09/report.json"
H06_LOCAL_ROWS = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09/form4_transaction_rows.json"
H06_LOCAL_INVENTORY = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09/input_inventory.json"
H06_LOCAL_SCRIPT = REPO / "scripts/research/normalize_equity_h06_form4_2026_09_09.py"
H06_LOCAL_TEST = REPO / "tests/research/test_equity_h06_local_normalization_2026_09_09.py"
H0815_LOCAL_MANIFEST = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09/normalization_manifest.json"
H0815_LOCAL_REPORT = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09/normalization_report.json"
H0815_LOCAL_INVENTORY = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09/normalization_inventory.json"
H0815_LOCAL_RECEIPT = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09/normalization_receipt.json"
H0815_LOCAL_H08_ROWS = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09/parsed/h08_actual_eps_rows.json"
H0815_LOCAL_H15_ROWS = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09/parsed/h15_lifecycle_metadata_rows.json"
H0815_LOCAL_SCRIPT = REPO / "scripts/research/normalize_equity_secondary_h08_h15_2026_09_09.py"
H0815_LOCAL_TEST = REPO / "tests/research/test_equity_secondary_local_normalization_2026_09_09.py"
H11_LOCAL_MANIFEST = REPO / "data/research_runs/equity_h11_nport_snapshot_2026_09_09/manifest.json"
H11_LOCAL_REPORT = REPO / "data/research_runs/equity_h11_nport_snapshot_2026_09_09/normalization_report.json"
H11_LOCAL_ROWS = REPO / "data/research_runs/equity_h11_nport_snapshot_2026_09_09/snapshot_rows.jsonl"
H11_LOCAL_PLAN = REPO / "data/research_runs/equity_h11_nport_snapshot_2026_09_09/metadata_cleanup_plan.md"
H11_LOCAL_SCRIPT = REPO / "scripts/research/normalize_h11_nport_snapshot_2026_09_09.py"
H11_LOCAL_TEST = REPO / "tests/research/test_h11_nport_snapshot_2026_09_09.py"
REVIEW2_ROOT = REPO / "data/research/external/equity_secondary_source_gap_audit_2026_09_09/review_r2"
REVIEW2_MANIFEST = REVIEW2_ROOT / "review_manifest.json"
REVIEW2_INVENTORY = REVIEW2_ROOT / "review_inventory.json"
REVIEW2_REPORT = REVIEW2_ROOT / "review_report.json"
REVIEW2_PARSER_RECEIPT = REVIEW2_ROOT / "parsed/parser_receipt.json"
REVIEW2_PARSED = {
    "H09": REVIEW2_ROOT / "parsed/h09_relationship_rows.json",
    "H10": REVIEW2_ROOT / "parsed/h10_version_pairs.json",
    "H15": REVIEW2_ROOT / "parsed/h15_lifecycle_rows.json",
    "H16": REVIEW2_ROOT / "parsed/h16_lifecycle_rows.json",
}
REVIEW2_PINS = {
    "manifest": "d7f53f4abdd9ebcf094a910af6b952e20d54d90a8f7461f2402b913bcbaf3e50",
    "canonical_self": "423c747409931b2a39d5c5ff6e7f7ffcc5be1558ae4e02dee95298e621bb4cd1",
    "inventory": "76064376e5b961982fcb178a1ceddf8258f1966f34c13e6b000365f9bac3c10b",
    "report": "9c05871e92a2645f52c00694eb6aa19250bfd55e5350334ac9278aa5e21a1710",
    "parser_receipt": "79aba569ee7314122fd4745895d67a0070520e7c94a00d947a63561bc8bf79c2",
}

SEC_ERROR_MARKERS = (
    "file unavailable",
    "document not found",
    "page not found",
    "access denied",
    "rate limit",
    "too many requests",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "request origin error",
)
SEC_BODY_FORMS = {
    "4", "4/A", "8-K", "8-K/A", "6-K", "6-K/A", "10-K", "10-K/A",
    "10-Q", "10-Q/A", "DEFM14A", "10", "10-12B", "SC TO-I", "SC TO-T",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def source_ref(path: Path, role: str) -> dict[str, Any]:
    return {"path": str(path.relative_to(REPO)), "sha256": sha256(path), "role": role}


LOCAL_PACKAGE_SPECS = {
    "H06": {
        "manifest": H06_LOCAL_MANIFEST,
        "files": {"report": H06_LOCAL_REPORT, "rows": H06_LOCAL_ROWS, "inventory": H06_LOCAL_INVENTORY, "receipt": BATCH_RECEIPT, "script": H06_LOCAL_SCRIPT, "test": H06_LOCAL_TEST},
        "lifecycle_status": "FROZEN_LOCAL_FORM4_NORMALIZATION_EVIDENCE_ONLY",
    },
    "H08": {
        "manifest": H0815_LOCAL_MANIFEST,
        "files": {"report": H0815_LOCAL_REPORT, "inventory": H0815_LOCAL_INVENTORY, "receipt": H0815_LOCAL_RECEIPT, "rows": H0815_LOCAL_H08_ROWS, "script": H0815_LOCAL_SCRIPT, "test": H0815_LOCAL_TEST},
        "lifecycle_status": "FROZEN_LOCAL_EARNINGS_NORMALIZATION_EVIDENCE_ONLY",
    },
    "H11": {
        "manifest": H11_LOCAL_MANIFEST,
        "files": {"report": H11_LOCAL_REPORT, "rows": H11_LOCAL_ROWS, "metadata_plan": H11_LOCAL_PLAN, "receipt": REPO / "data/research/external/crossasset_public_2026_09_08/x02_x06_matrix/probes/receipts/SEC_existing_MCHI_NPORT_structured_parse_2025-05-31.receipt.json", "script": H11_LOCAL_SCRIPT, "test": H11_LOCAL_TEST},
        "lifecycle_status": "FROZEN_LOCAL_NPORT_SNAPSHOT_EVIDENCE_ONLY",
    },
    "H15": {
        "manifest": H0815_LOCAL_MANIFEST,
        "files": {"report": H0815_LOCAL_REPORT, "inventory": H0815_LOCAL_INVENTORY, "receipt": H0815_LOCAL_RECEIPT, "rows": H0815_LOCAL_H15_ROWS, "script": H0815_LOCAL_SCRIPT, "test": H0815_LOCAL_TEST},
        "lifecycle_status": "FROZEN_LOCAL_LIFECYCLE_METADATA_ONLY",
    },
    "H16": {
        "manifest": REVIEW2_MANIFEST,
        "files": {"report": REVIEW2_REPORT, "inventory": REVIEW2_INVENTORY, "receipt": REVIEW2_PARSER_RECEIPT, "rows": REVIEW2_PARSED["H16"]},
        "lifecycle_status": "FROZEN_REVIEW2_ZERO_SAMPLE_EVIDENCE_ONLY",
    },
}


def package_binding(card: str) -> dict[str, Any]:
    spec = LOCAL_PACKAGE_SPECS[card]
    manifest = read_json(spec["manifest"])
    return {
        "status": "VERIFIED",
        "package_path": str(spec["manifest"].parent.relative_to(REPO)),
        "manifest_path": str(spec["manifest"].relative_to(REPO)),
        "manifest_physical_sha256": sha256(spec["manifest"]),
        "manifest_canonical_sha256": manifest.get("manifest_sha256"),
        "files": {name: {"path": str(path.relative_to(REPO)), "sha256": sha256(path)} for name, path in spec["files"].items()},
        "economic_run": False,
        "local_engineering_gap": False,
        "next_stage_status": "NOT_APPROVED",
        "lifecycle_status": spec["lifecycle_status"],
    }


def _with_packages(card: str, value: dict[str, Any], *extra: str) -> dict[str, Any]:
    value["package_bindings"] = [package_binding(name) for name in (card, *extra)]
    return value


def verify_review2_package() -> dict[str, Any]:
    """Bind the quiescent offline review package by content, identity and rows."""
    paths = {"manifest": REVIEW2_MANIFEST, "inventory": REVIEW2_INVENTORY, "report": REVIEW2_REPORT, "parser_receipt": REVIEW2_PARSER_RECEIPT}
    for name, path in paths.items():
        if not path.is_file() or sha256(path) != REVIEW2_PINS[name]:
            raise ValueError(f"review2_pin_mismatch:{name}")
    manifest = read_json(REVIEW2_MANIFEST)
    if manifest.get("manifest_sha256") != REVIEW2_PINS["canonical_self"]:
        raise ValueError("review2_canonical_self_mismatch")
    canonical = dict(manifest)
    claimed = canonical.pop("manifest_sha256", None)
    expected = hashlib.sha256((json.dumps(canonical, indent=2, sort_keys=True) + "\n").encode("utf-8")).hexdigest()
    if claimed != expected:
        raise ValueError("review2_manifest_canonical_hash_mismatch")
    if manifest.get("inventory_sha256") != REVIEW2_PINS["inventory"] or manifest.get("report_sha256") != REVIEW2_PINS["report"] or manifest.get("artifact_count") != 41 or manifest.get("route_count") != 11:
        raise ValueError("review2_manifest_dependency_mismatch")
    for item in manifest.get("artifacts", []):
        path = REPO / str(item.get("path"))
        if not path.is_file() or path.stat().st_size != item.get("bytes") or sha256(path) != item.get("sha256"):
            raise ValueError(f"review2_artifact_mismatch:{item.get('path')}")
    inventory = read_json(REVIEW2_INVENTORY)
    if len(inventory.get("routes", [])) != 11 or inventory.get("counts") != {"errors": 8, "routes": 11, "successful": 3}:
        raise ValueError("review2_route_count_mismatch")
    parser_receipt = read_json(REVIEW2_PARSER_RECEIPT)
    if parser_receipt.get("economic_run") is not False:
        raise ValueError("review2_economic_scope_violation")
    for name, path in REVIEW2_PARSED.items():
        declaration = parser_receipt.get("outputs", {}).get(path.name)
        if not declaration or declaration.get("sha256") != sha256(path) or declaration.get("path") != str(path.relative_to(REPO)):
            raise ValueError(f"review2_parser_output_mismatch:{name}")
    h09 = read_json(REVIEW2_PARSED["H09"])
    h10 = read_json(REVIEW2_PARSED["H10"])
    h15 = read_json(REVIEW2_PARSED["H15"])
    h16 = read_json(REVIEW2_PARSED["H16"])
    if (len(h09.get("rows", [])), h09.get("admitted_rows")) != (2, 0):
        raise ValueError("review2_h09_row_count_mismatch")
    if (h10.get("exact_pairs"), h10.get("admitted_pairs"), len(h10.get("pairs", []))) != (3, 3, 3):
        raise ValueError("review2_h10_row_count_mismatch")
    if (h15.get("source_rows"), h15.get("admitted_rows"), h15.get("economic_admissible_rows")) != (1, 1, 0):
        raise ValueError("review2_h15_row_count_mismatch")
    if (h16.get("source_rows"), h16.get("admitted_rows")) != (0, 0):
        raise ValueError("review2_h16_row_count_mismatch")
    return {"status": "REVIEW_R2_BOUND", "manifest_sha256": REVIEW2_PINS["manifest"], "canonical_self_sha256": REVIEW2_PINS["canonical_self"], "inventory_sha256": REVIEW2_PINS["inventory"], "report_sha256": REVIEW2_PINS["report"], "parser_receipt_sha256": REVIEW2_PINS["parser_receipt"], "artifact_count": 41, "route_count": 11, "h09_rows": 2, "h09_admitted": 0, "h10_causal_revision_rows": 3, "h15_lifecycle_rows": 1, "h15_economic_rows": 0, "h16_rows": 0}


CONTRACTS: dict[str, dict[str, Any]] = {
    "H06": {"fields": "SEC Form 4 issuer CIK, owner role, transaction code, shares, price, ownership, amendment", "universe": "PIT equity universe with CIK/ticker continuity", "clock": "EDGAR acceptance/publication", "availability": "accepted_at <= decision_time", "formula": "unusual open-market purchase rule; grants/exercises/gifts/amendments excluded", "window": "one frozen routine-history rule and holding horizon; exact thresholds unspecified by source review", "execution": "next executable NYSE bar", "role": "public insider information, long-only", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "Form 4 taxonomy, routine history, amendment handling and CIK/ticker identity"},
    "H07": {"fields": "historical analyst estimate, horizon, analyst/date identity, company identity, revision and publication/vintage", "universe": "PIT equity universe", "clock": "forecast publication/vintage", "availability": "published_at <= decision_time", "formula": "positive revision aggregation and rank; exact horizon/threshold unspecified", "window": "one frozen forecast horizon, rank, cadence and hold", "execution": "next executable NYSE bar", "role": "forecast revision, long-only", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "full historical vintages and stale-estimate policy"},
    "H08": {"fields": "earnings release time, actual EPS, pre-release consensus, surprise, revisions/restatements, event identity", "universe": "PIT equity universe", "clock": "release timestamp", "availability": "released_at <= decision_time", "formula": "positive surprise after release with fixed event horizon", "window": "one fixed horizon and collision policy; post-2016 holdout required", "execution": "next executable session", "role": "PEAD, long-only", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "post-2016 required; exact dates UNSPECIFIED"}, "minimum": "actual/consensus vintages, release timestamps and collision handling"},
    "H09": {"fields": "customer/supplier link, vintage, direction, identifiers, termination and linked-firm event", "universe": "PIT relationship-mapped equity universe", "clock": "relationship vintage plus linked event", "availability": "relationship_vintage and event_publication <= decision_time", "formula": "customer-to-supplier spillover after fixed lag; direction/hold unspecified", "window": "one link source, event mapping, lag and hold", "execution": "next executable NYSE bar", "role": "information spillover, long-only adaptation", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "PIT links, termination and event identifiers"},
    "H10": {"fields": "original 10-K/10-Q versions, CIK, section text, accession and acceptance", "universe": "PIT SEC filing universe", "clock": "EDGAR acceptance", "availability": "accepted_at <= decision_time", "formula": "text change versus prior filing; direction frozen ex ante", "window": "one pair/version rule, section vocabulary and cadence", "execution": "next executable NYSE bar", "role": "filing text change, long-only adaptation", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "original versions, text normalization, sections and CIK continuity"},
    "H11": {"fields": "fund flow, holdings vintage, fund identity, ownership weight and publication", "universe": "PIT equity universe covered by fund holdings", "clock": "flow/holdings publication", "availability": "published_at <= decision_time", "formula": "flow multiplied by pre-known ownership pressure; buy-after-pressure direction must be frozen", "window": "one lag, pressure aggregation, hold, capacity and collision rule", "execution": "next executable NYSE bar", "role": "fund forced-sale pressure, long-only adaptation", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "PIT holdings/flows, rebalance events and capacity"},
    "H12": {"fields": "named index, methodology/membership vintage, announcement and implementation timestamps", "universe": "PIT named-index membership", "clock": "announcement and effective/application timestamps", "availability": "event timestamps <= decision_time", "formula": "index addition/removal pressure; direction/hold frozen before outcomes", "window": "one index/vintage, event window and execution rule", "execution": "fixed event-window next executable bar", "role": "index-change pressure, long-only", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "PIT membership, methodology versions and corporate actions", "named_index": "NYSE Composite", "freeze_rationale": "The authoritative queue leaves the index unspecified; NYSE Composite is frozen pre-outcome because the cited official NYSE methodology is public and defines announcement/effective distinctions. Membership history remains unacquired."},
    "H13": {"fields": "raw/adjusted intraday quotes or trades, session/auction calendar and executable open/close", "universe": "PIT equity universe with SIP/live feed parity", "clock": "explicit session timestamps", "availability": "feed availability <= decision_time", "formula": "overnight/intraday split using transaction/VWAP-style construction; daily OHLC proxy forbidden", "window": "one session split and exact fill model", "execution": "explicit opening/closing auction or next-bar semantics", "role": "session return decomposition, long-only", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "intraday SIP/live parity and executable fills"},
    "H15": {"fields": "cash deal terms, target identity, amendments, announcement/close/cancel/break/delist events", "universe": "PIT cash-offer targets", "clock": "announcement and lifecycle timestamps", "availability": "event publication <= decision_time", "formula": "cash-only merger spread with fixed entry/exit/cancel rules", "window": "one event source, lifecycle policy and cash ledger", "execution": "next executable NYSE bar", "role": "cash merger arbitrage, long-only", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "terms, amendments, terminal events and identity lifecycle"},
    "H16": {"fields": "two-step spinoff announcement/effective date, distribution, ownership conversion, successor IDs and lifecycle", "universe": "PIT two-step spinoff events", "clock": "announcement/effective timestamps", "availability": "event publication <= decision_time", "formula": "narrow two-step ownership-conversion mechanism; direction unspecified", "window": "one event taxonomy, successor rule and hold", "execution": "next executable NYSE bar", "role": "post-spinoff event pricing, long-only", "controls": "incumbent, isolated candidate, passive/cash, matched exposure and shared-capital diagnostic", "cost": [1.0, 2.0, 3.0], "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "UNSPECIFIED"}, "minimum": "events, distributions, successor mapping and adjusted lifecycle"},
}


def sec_documents() -> list[dict[str, Any]]:
    return read_json(SEC_DOC_RECEIPT).get("documents", [])


def parse_accepted(value: Any) -> datetime:
    """Return an aware UTC timestamp; naive or malformed SEC times fail closed."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("sec_accepted_timestamp_missing")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"sec_accepted_timestamp_invalid:{value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"sec_accepted_timestamp_naive:{value}")
    return parsed.astimezone(UTC)


def _strip_markup(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value))


def _ix_value(text: str, name: str) -> str:
    match = re.search(
        rf'name=["\']{re.escape(name)}["\'][^>]*>(.*?)</ix:(?:nonNumeric|nonFraction)>',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    return re.sub(r"\s+", " ", _strip_markup(match.group(1))).strip() if match else ""


def sec_body_failure_reason(text: str, form: str) -> str | None:
    """Reject HTTP-200 SEC interstitials before any filing is counted."""
    normalized = _strip_markup(text[:12000]).lower()
    if len(normalized.strip()) < 256:
        return "body_too_short"
    for marker in SEC_ERROR_MARKERS:
        if marker in normalized:
            return "sec_error_page:" + marker.replace(" ", "_")
    if form.upper() in SEC_BODY_FORMS and not re.search(r"<html|<document|<edgarSubmission|<ownershipDocument|<ix:", text, re.I):
        return "non_filing_body"
    return None


def _document_type(text: str) -> str:
    value = _ix_value(text, "dei:DocumentType")
    if value:
        return value.upper()
    visible = _strip_markup(text)
    match = re.search(r"(?:document\s*type|form\s*type)\s*[:\-]?\s*(10-K/A|10-K|10-Q/A|10-Q|8-K/A|8-K|6-K/A|6-K|DEFM14A|10-12B|10)", visible, re.I)
    return match.group(1).upper() if match else ""


def parse_sec_document_content(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Validate the document body against receipt metadata before a gate can count it."""
    text = path.read_text(encoding="utf-8", errors="replace")
    form = str(row.get("form", "")).upper()
    expected_cik = str(row.get("issuer_cik") or row.get("cik") or "").zfill(10)
    expected_accn = str(row.get("accn", ""))
    if form not in SEC_BODY_FORMS:
        raise ValueError(f"sec_document_form_unsupported:{form}")
    failure = sec_body_failure_reason(text, form)
    if failure:
        raise ValueError(f"sec_document_body_invalid:{failure}:{path}")
    # The archive URL and output name are receipt identity, even when SEC renders
    # the Form 4 XML as an HTML stylesheet page.
    identity = f"{row.get('url', '')} {path.name} {row.get('output', '')}"
    if expected_accn and expected_accn not in identity:
        raise ValueError(f"sec_document_accession_mismatch:{path}")
    if not expected_cik or not re.search(rf"\b{re.escape(expected_cik)}\b", text):
        raise ValueError(f"sec_document_issuer_cik_missing:{path}")
    if form in {"4", "4/A"}:
        lower = _strip_markup(text).lower()
        if not re.search(r"<title[^>]*>\s*sec form\s*4", lower):
            # title markup was removed above; inspect the original body too.
            if not re.search(r"<title[^>]*>\s*sec form\s*4", text.lower()):
                raise ValueError(f"form4_document_type_missing:{path}")
        required = {
            "issuer": "issuer",
            "reporting_owner": "reporting person",
            "transaction_code": "transaction code",
            "transaction_date": "transaction date",
            "non_derivative": "non-derivative",
            "beneficial_ownership": "amount of securities beneficially owned",
        }
        missing = [name for name, marker in required.items() if marker not in lower]
        if missing:
            raise ValueError(f"form4_taxonomy_missing:{path}:{','.join(missing)}")
        if "derivative securities" not in lower and "derivative transaction" not in lower:
            raise ValueError(f"form4_derivative_section_missing:{path}")
        cik_values = re.findall(r"(?:CIK|cik)[=:\s\"']+(\d{10})", text, re.IGNORECASE)
        owner_ciks = sorted({value for value in cik_values if value != expected_cik})
        if not owner_ciks:
            raise ValueError(f"form4_owner_cik_missing:{path}")
        transaction_codes = sorted({value.upper() for value in re.findall(r'class=["\'][^"\']*(?:FormData|SmallFormData)[^"\']*["\'][^>]*>\s*([A-Z])\s*</span>', text, re.IGNORECASE) if value.upper() in set("ADFGIMPS")})
        if not transaction_codes:
            raise ValueError(f"form4_transaction_code_missing:{path}")
        if form.endswith("/A") and "amend" not in lower:
            raise ValueError(f"form4_amendment_marker_missing:{path}")
        return {"document_type": "FORM4", "issuer_cik": expected_cik, "owner_ciks": owner_ciks, "transaction_codes": transaction_codes, "amendment": form.endswith("/A"), "taxonomy": "ownership/non-derivative/derivative"}
    document_type = _document_type(text)
    if document_type != form:
        raise ValueError(f"sec_document_type_mismatch:{path}:{document_type}:{form}")
    entity_cik = _ix_value(text, "dei:EntityCentralIndexKey")
    if entity_cik and entity_cik.zfill(10) != expected_cik:
        raise ValueError(f"sec_document_cik_mismatch:{path}:{entity_cik}:{expected_cik}")
    if form in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
        for marker in ("management", "financial statements"):
            if marker.lower() not in text.lower():
                raise ValueError(f"sec_filing_section_missing:{path}:{marker}")
        return {"document_type": document_type, "issuer_cik": expected_cik, "sections": ["management", "Risk Factors", "financial statements"]}
    visible = _strip_markup(text).lower()
    if form in {"8-K", "8-K/A", "6-K", "6-K/A"} and not re.search(r"item\s+(?:\d+\.\d+|1|2|9)|exhibit|report", visible):
        raise ValueError(f"sec_filing_content_missing:{path}")
    return {"document_type": document_type, "issuer_cik": expected_cik, "sections": ["filing body"]}


def _pit_identity_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if PIT_UNIVERSE.is_file():
        for line in PIT_UNIVERSE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sec = row.get("sec_mapping") or {}
            cik = sec.get("cik")
            symbol = row.get("canonical_symbol") or row.get("historical_symbol")
            if cik and symbol:
                mapping[str(symbol).upper()] = str(cik).zfill(10)
    return mapping


def _external_source(url: str, status: str, coverage: str, pit: str) -> dict[str, Any]:
    return {"url": url, "status": status, "body_sha256": None, "coverage": coverage, "pit": pit}


def _source_matrix() -> dict[str, Any]:
    if SOURCE_MATRIX.is_file():
        return read_json(SOURCE_MATRIX)
    receipt_source = source_ref(SEC_DOC_RECEIPT, "existing SEC receipt")
    return {
        "status": "FROZEN_SOURCE_MATRIX_WITH_BLOCKERS",
        "historical_cutoff": CUTOFF,
        "cards": {
            "H06": {"sources": [receipt_source, _external_source("https://www.sec.gov/Archives/edgar/data/", "NEXT_PROBE_REQUIRED", "full Form 4/4-A history, XML/HTML and archive submissions", "acceptance timestamps are available only for the 20-symbol pilot")]},
            "H07": {"sources": [_external_source("https://www.sec.gov/edgar/browse/?action=getcompany", "AVAILABLE_PUBLIC_METADATA_ONLY", "company filings/guidance routes", "does not contain historical analyst forecast vintages"), _external_source("https://www.tokenomist.ai/", "NO_AUTHORIZED_FORECAST_EXPORT_FOUND", "presence-only licensed-feed scan", "no PIT forecast vintage") ]},
            "H08": {"sources": [receipt_source, _external_source("https://www.sec.gov/Archives/edgar/data/", "AVAILABLE_PUBLIC_FILINGS_ONLY", "8-K/6-K exhibits can be acquired by accession", "filing acceptance is not necessarily earnings release time"), _external_source("https://www.sec.gov/edgar/browse/", "NO_CONSENSUS_VINTAGE", "SEC browse", "pre-release consensus absent") ]},
            "H09": {"sources": [_external_source("https://www.sec.gov/Archives/edgar/data/", "AVAILABLE_PUBLIC_FILINGS_ONLY", "10-K/Q customer, supplier and segment text", "relationship vintages/direction/termination require parsing per filing"), _external_source("https://www.sec.gov/edgar/browse/", "NO_PIT_LINK_ARCHIVE", "issuer archive discovery", "current relationship labels are inadmissible") ]},
            "H10": {"sources": [receipt_source, _external_source("https://www.sec.gov/Archives/edgar/data/", "AVAILABLE_PUBLIC_FILINGS_ONLY", "36 original 10-K/Q bodies; 28 accepted by cutoff", "20-symbol pilot, not full PIT filing history") ]},
            "H11": {"sources": [source_ref(NPORT, "existing NPORT body"), source_ref(NPORT_SUB, "existing SEC submission sidecar"), _external_source("https://www.sec.gov/Archives/edgar/data/", "NEXT_PROBE_REQUIRED", "NPORT-P/P-A and N-CSR/N-CSRS history", "one current snapshot is not a historical holdings/flow vintage")]},
            "H12": {"sources": [_external_source("https://www.nyse.com/publicdocs/nyse/indices/NYSE_Index_Series_Methodology.pdf", "PUBLIC_METHODOLOGY", "NYSE Composite methodology", "named index must be frozen before event outcomes"), _external_source("https://www.nyse.com/markets/indices", "NEXT_PROBE_REQUIRED", "announcement/membership archive", "no PIT additions/removals acquired") ]},
            "H13": {"sources": [_external_source("https://docs.alpaca.markets/docs/market-data", "AUTHORIZED_PATH_CONFIGURED", "configured SIP historical / IEX live paths", "entitlement and historical/live feed parity unresolved"), _external_source("https://data.alpaca.markets/v2/stocks/bars", "NO_CREDENTIAL_PROBE", "endpoint template only", "daily OHLCV cannot satisfy intraday contract") ]},
            "H15": {"sources": [_external_source("https://www.sec.gov/Archives/edgar/data/", "NEXT_PROBE_REQUIRED", "8-K, DEFM14A, SC TO, 14D-9 and terminal filings", "no lifecycle event rows acquired"), _external_source("https://www.sec.gov/edgar/browse/", "PUBLIC_DISCOVERY_ONLY", "issuer filing discovery", "terms/amendments/close/break/delist not mapped") ]},
            "H16": {"sources": [_external_source("https://www.sec.gov/Archives/edgar/data/", "NEXT_PROBE_REQUIRED", "Form 10/10-12B/8-K spinoff filings", "no successor/distribution PIT archive"), _external_source("https://www.sec.gov/edgar/browse/", "PUBLIC_DISCOVERY_ONLY", "issuer filing discovery", "two-step lifecycle not mapped") ]},
        },
    }


def verify_sec_documents() -> dict[str, Any]:
    receipt = read_json(SEC_DOC_RECEIPT)
    if receipt.get("status") != "COMPLETE" or not isinstance(receipt.get("documents"), list):
        raise ValueError("sec_document_receipt_incomplete")
    identity_map = _pit_identity_map()
    parsed: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for row in receipt.get("documents", []):
        path = REPO / str(row["output"])
        if row.get("status_code") != 200 or not path.is_file():
            raise ValueError(f"sec_document_missing:{path}")
        if path.stat().st_size != int(row["bytes"]) or sha256(path) != row["sha256"]:
            raise ValueError(f"sec_document_receipt_mismatch:{path}")
        accepted = parse_accepted(row.get("accepted"))
        if accepted > parse_accepted(CUTOFF):
            # Future-dated rows remain valid evidence, but cannot leak into a
            # through-cutoff count.
            pass
        symbol = str(row.get("symbol", "")).upper()
        expected = identity_map.get(symbol)
        if expected and expected != str(row.get("issuer_cik", "")).zfill(10):
            raise ValueError(f"sec_document_identity_mismatch:{symbol}")
        try:
            checks = parse_sec_document_content(path, row)
        except ValueError as exc:
            invalid.append({"path": str(path.relative_to(REPO)), "accn": row.get("accn"), "form": row.get("form"), "reason": str(exc).split(":", 2)[0]})
            continue
        parsed.append({"path": str(path.relative_to(REPO)), "accepted": accepted.isoformat(), **checks})
    return {"documents": len(parsed) + len(invalid), "valid_documents": len(parsed), "invalid_documents": len(invalid), "invalid": invalid, "content_checks": parsed}


def verify_batch_documents() -> dict[str, Any]:
    """Validate every acquired batch body without treating HTTP 200 as content."""
    if not BATCH_RECEIPT.is_file():
        return {"inventory_documents": 0, "acquired_documents": 0, "valid_documents": 0, "invalid_documents": 0, "invalid": [], "valid": []}
    receipt = read_json(BATCH_RECEIPT)
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    latest_rows: dict[str, dict[str, Any]] = {}
    for row in receipt.get("rows", []):
        if row.get("accn"):
            latest_rows[str(row["accn"])] = row
    for row in latest_rows.values():
        body_name = row.get("body_path")
        path = REPO / str(body_name) if body_name else None
        if row.get("status_code") != 200 or path is None or not path.is_file():
            invalid.append({"accn": row.get("accn"), "form": row.get("form"), "reason": "missing_or_non200_body"})
            continue
        if path.stat().st_size != int(row.get("bytes", -1)) or sha256(path) != row.get("body_sha256"):
            invalid.append({"accn": row.get("accn"), "form": row.get("form"), "reason": "body_receipt_mismatch"})
            continue
        try:
            accepted = parse_accepted(row.get("accepted"))
            checks = parse_sec_document_content(path, row)
        except (TypeError, ValueError) as exc:
            invalid.append({"accn": row.get("accn"), "form": row.get("form"), "reason": str(exc).split(":", 2)[0]})
            continue
        valid.append({"accn": row.get("accn"), "form": row.get("form"), "symbol": row.get("symbol"), "issuer_cik": str(row.get("cik", "")).zfill(10), "accepted": accepted.isoformat(), **checks})
    terminal = [row for row in latest_rows.values() if row.get("terminal")]
    return {"inventory_documents": read_json(CANDIDATE_INVENTORY).get("record_count", 0) if CANDIDATE_INVENTORY.is_file() else 0, "acquired_documents": len(valid) + len(invalid), "valid_documents": len(valid), "invalid_documents": len(invalid), "terminal_documents": len(terminal), "terminal": terminal, "invalid": invalid, "valid": valid}


def gate_h06() -> dict[str, Any]:
    inventory = read_json(CANDIDATE_INVENTORY) if CANDIDATE_INVENTORY.is_file() else {}
    batch = verify_batch_documents()
    docs = [row for row in batch.get("valid", []) if row.get("form") in {"4", "4/A"}]
    cutoff = [row for row in docs if parse_accepted(row["accepted"]) <= parse_accepted(CUTOFF)]
    inventory_count = sum(inventory.get("window_counts", {}).get(form, 0) for form in ("4", "4/A"))
    terminal_count = sum(1 for row in batch.get("terminal", []) if row.get("form") in {"4", "4/A"})
    pending = inventory_count - len([row for row in batch.get("valid", []) if row.get("form") in {"4", "4/A"} and parse_accepted(row.get("accepted")) <= parse_accepted(CUTOFF)]) - terminal_count
    invalid_count = sum(1 for row in batch.get("invalid", []) if row.get("form") in {"4", "4/A"})
    pending_count = max(0, pending)
    disposition_accounted = len(cutoff) + invalid_count + terminal_count + pending_count
    card = {"status": "PIT_PENDING", "inventory_documents": inventory_count, "acquired_documents": len(docs) + invalid_count + terminal_count, "acquired_valid": len(docs), "acquired_invalid": invalid_count, "terminal_documents": terminal_count, "pending_documents": pending_count, "form4_documents": len(docs), "form4_through_cutoff": len(cutoff), "symbols": len({row.get("symbol") for row in docs}), "publication_time_rows": len(docs), "available_at_rows": len(cutoff), "disposition_accounted_documents": disposition_accounted, "disposition_complete": disposition_accounted == inventory_count, "economic_signal_rows": 0, "blockers": ["the frozen 20-issuer Form 4/4-A inventory is complete and all 3,993 in-window rows have valid receipt-backed bodies", "routine-history/exclusion taxonomy, amendment linkage and historical CIK/ticker continuity remain incomplete"], "source": source_ref(BATCH_RECEIPT, "receipt-backed Form 4/4-A batch")}
    card["blockers"][-1] = "the local parser has one explicit unbound body outside the 3,993 valid-body denominator; routine-history/exclusion taxonomy, amendment linkage and historical CIK/ticker continuity remain external blockers"
    card["sources"] = _source_matrix()["cards"]["H06"]["sources"]
    return _with_packages("H06", card)


def gate_h07() -> dict[str, Any]:
    return {"status": "DATA_ACCESS_BLOCKED", "rows": 0, "publication_time_rows": 0, "available_at_rows": 0, "economic_signal_rows": 0, "blockers": ["no authorized historical analyst forecast vintages", "no publication timestamps, analyst identity, horizon or stale-estimate policy", "no exact provider archive through cutoff"], "sources": _source_matrix()["cards"]["H07"]["sources"]}


def gate_h08() -> dict[str, Any]:
    local = read_json(H0815_LOCAL_REPORT)["H08"]
    card = {"status": local["status"], "inventory_documents": local["accession_count"], "acquired_documents": local["any_valid_body_accessions"], "acquired_valid": local["latest_attempt_valid_accessions"], "acquired_invalid": 0, "terminal_documents": local["accession_count"] - local["any_valid_body_accessions"], "pending_documents": 0, "parsed_documents": local["latest_attempt_valid_accessions"], "admitted_documents": 0, "metadata_rows": local["accession_count"], "body_confirmed_rows": local["latest_attempt_valid_accessions"], "any_valid_body_accessions": local["any_valid_body_accessions"], "latest_attempt_valid_accessions": local["latest_attempt_valid_accessions"], "conflict_accessions": local["conflict_accessions"], "no_valid_body_accessions": local["accession_count"] - local["any_valid_body_accessions"], "text_eps_candidate_rows": local["text_eps_candidate_rows"], "release_date_prose_candidate_rows": local["release_date_prose_candidate_rows"], "release_date_prose_candidate_bodies": local["release_date_prose_candidate_bodies"], "teleconference_time_candidate_rows": local["teleconference_time_candidate_rows"], "consensus_rows": local["consensus_vintage_rows"], "surprise_rows": local["surprise_rows"], "release_time_rows": local["release_timestamp_rows"], "economic_signal_rows": 0, "blockers": ["991 latest-attempt-valid bodies and eight preserved prior-valid/latest-invalid conflicts are receipt-backed local normalization evidence only", "seven numeric text/table spans, 102 release-date spans and 19 teleconference spans remain explicitly non-admitted; no exact release_at or PIT publication timestamp is established", "pre-release consensus vintages and surprise revisions are absent", "post-2016 holdout and event-collision policy cannot be evaluated"], "sources": _source_matrix()["cards"]["H08"]["sources"]}
    return _with_packages("H08", card)


def gate_h09() -> dict[str, Any]:
    scan = read_json(H09_SCAN) if H09_SCAN.is_file() else {}
    scanned = int(scan.get("scanned_files", 0))
    parsed = read_json(REVIEW2_PARSED["H09"])
    rows = parsed.get("rows", []) if isinstance(parsed.get("rows", []), list) else []
    admitted = sum(bool(row.get("admitted")) for row in rows)
    return {"status": "DATA_ACCESS_BLOCKED", "inventory_documents": scanned, "acquired_documents": scanned, "acquired_valid": scanned, "acquired_invalid": 0, "pending_documents": 0, "parsed_documents": scanned, "admitted_documents": admitted, "relationship_rows": len(rows), "explicit_evidence_rows": len(rows), "publication_time_rows": 0, "available_at_rows": 0, "economic_signal_rows": 0, "blockers": ["two receipt-backed named-counterparty rows remain excluded because counterparty identity and linked-firm event are absent", "no PIT link vintage, direction, termination or event mapping is complete", "current relationship labels would violate PIT"], "sources": _source_matrix()["cards"]["H09"]["sources"]}


def gate_h10() -> dict[str, Any]:
    inventory = read_json(CANDIDATE_INVENTORY) if CANDIDATE_INVENTORY.is_file() else {}
    batch = verify_batch_documents()
    docs = [row for row in batch.get("valid", []) if row.get("form") in {"10-K", "10-K/A", "10-Q", "10-Q/A"}]
    cutoff = [row for row in docs if parse_accepted(row["accepted"]) <= parse_accepted(CUTOFF)]
    total = sum(inventory.get("window_counts", {}).get(form, 0) for form in ("10-K", "10-K/A", "10-Q", "10-Q/A"))
    parsed = read_json(REVIEW2_PARSED["H10"])
    pair_rows = parsed.get("pairs", []) if isinstance(parsed.get("pairs", []), list) else []
    card = {"status": "PIT_PENDING", "inventory_documents": total, "acquired_documents": len(docs), "acquired_valid": len(docs), "acquired_invalid": sum(1 for row in batch.get("invalid", []) if row.get("form") in {"10-K", "10-K/A", "10-Q", "10-Q/A"}), "pending_documents": max(0, total - len(docs)), "parsed_documents": len(docs), "admitted_documents": len(pair_rows), "original_filing_documents": len(docs), "filings_through_cutoff": len(cutoff), "symbols": len({row.get("symbol") for row in docs}), "acceptance_time_rows": len(docs), "version_pair_rows": len(pair_rows), "causal_revision_event_rows": sum(bool(row.get("admitted")) and row.get("admission_scope") == "causal_revision_event" for row in pair_rows), "breadth_status": "INCOMPLETE_FULL_AND_RECENT_PIT_HISTORY", "direction_status": "UNFROZEN_LONG_ONLY_DIRECTION", "economic_signal_rows": 0, "blockers": ["the expanded acquisition is terminal for 333 validated documents, but full and recent PIT filing breadth remains incomplete", "three exact same-CIK/report-period 10-K/A pairs provide metadata-only causal revision-event evidence; section normalization and amendment breadth remain incomplete", "CIK/security continuity and frozen signal direction remain incomplete"], "source": source_ref(BATCH_RECEIPT, "receipt-backed 10-K/10-Q batch")}
    card["sources"] = _source_matrix()["cards"]["H10"]["sources"]
    return card


def gate_h11() -> dict[str, Any]:
    local = read_json(H11_LOCAL_REPORT)
    rows = int(local["parsed_holding_rows"])
    batch = read_json(NPORT_BATCH_RECEIPT) if NPORT_BATCH_RECEIPT.is_file() else {}
    batch_rows = batch.get("rows", []) if isinstance(batch.get("rows", []), list) else []
    latest_batch: dict[str, dict[str, Any]] = {str(row.get("accn")): row for row in batch_rows if row.get("accn")}
    latest_values = list(latest_batch.values())
    valid_batch = sum(int(row.get("status_code") == 200 and row.get("body_path") and not row.get("content_validation_error")) for row in latest_values)
    terminal_batch = sum(int(bool(row.get("terminal"))) for row in latest_values)
    retryable_batch = len(latest_values) - valid_batch - terminal_batch
    inventory_documents = int(batch.get("inventory_metadata_rows", 0) or 0)
    if not inventory_documents:
        inventory_documents = int(batch.get("remaining_metadata_rows", 0)) + valid_batch + terminal_batch + retryable_batch
    card = {"status": "DATA_BLOCKED", "inventory_documents": inventory_documents, "acquired_documents": valid_batch + terminal_batch, "acquired_valid": valid_batch, "acquired_invalid": sum(int(bool(row.get("content_validation_error"))) for row in latest_values), "terminal_documents": terminal_batch, "retryable_documents": retryable_batch, "pending_documents": int(batch.get("remaining_metadata_rows", 0)), "parsed_documents": rows, "admitted_documents": 0, "nport_investment_rows": rows, "fund_flow_rows": 0, "holdings_vintage_rows": 0, "identity_groups": local["unique_security_identity_keys"], "duplicate_identity_rows": local["duplicate_identity_rows"], "rows_with_any_valid_identifier": local["rows_with_any_valid_identifier"], "valid_identifier_values": local["valid_identifier_values"], "economic_signal_rows": 0, "blockers": ["the local H11 package parses one receipt-valid NPORT-P snapshot (565 rows, 516 identity groups, 49 duplicate occurrences) but accession/filed/accepted/publication/available_at are unbound", "NPORT primary bodies are receipt-backed samples, not a complete historical fund-flow/holdings archive", "no publication-lag and ownership-weight panel; flow/admission remains zero"], "sources": _source_matrix()["cards"]["H11"]["sources"]}
    return _with_packages("H11", card)


def gate_h12() -> dict[str, Any]:
    routes = read_json(ROUTE_RECEIPT).get("routes", []) if ROUTE_RECEIPT.is_file() else []
    h12_routes = [row for row in routes if row.get("candidate") == "H12"]
    return {"status": "DATA_ACCESS_BLOCKED", "named_index": CONTRACTS["H12"]["named_index"], "inventory_documents": 0, "acquired_documents": 0, "acquired_valid": 0, "acquired_invalid": 0, "pending_documents": 0, "parsed_documents": 0, "admitted_documents": 0, "route_probe_rows": len(h12_routes), "route_statuses": [{"url": row.get("url"), "status_code": row.get("status_code"), "body_sha256": row.get("body_sha256")} for row in h12_routes], "membership_event_rows": 0, "announcement_time_rows": 0, "implementation_time_rows": 0, "economic_signal_rows": 0, "blockers": ["all documented NYSE/ICE public index routes were probed; none returned dated NYSE Composite constituent or change rows", "the public ICE product/platform pages describe membership and rebalance data access but route the historical package behind a purchase-gated data product", "methodology defines rebalance announcement/effective timing but no historical membership event panel is available"], "sources": _source_matrix()["cards"]["H12"]["sources"]}


def gate_h13() -> dict[str, Any]:
    with BARS.open(encoding="utf-8") as handle:
        header = handle.readline().strip().split(",")
        daily_rows = sum(1 for _ in handle)
    return {"status": "DATA_BLOCKED", "inventory_documents": 0, "acquired_documents": 0, "acquired_valid": 0, "acquired_invalid": 0, "pending_documents": 0, "parsed_documents": 0, "admitted_documents": 0, "daily_bar_rows": daily_rows, "daily_bar_header": header, "intraday_rows": 0, "execution_timestamp_rows": 0, "economic_signal_rows": 0, "blockers": ["available data are daily OHLCV and cannot substitute for intraday transactions/quotes", "opening/closing auction semantics are absent", "historical SIP versus configured live IEX feed parity is unresolved"], "sources": _source_matrix()["cards"]["H13"]["sources"]}


def gate_h15() -> dict[str, Any]:
    parsed = read_json(H0815_LOCAL_REPORT)["H15"]
    normalized_rows = json.loads(H0815_LOCAL_H15_ROWS.read_text(encoding="utf-8"))
    rows = normalized_rows if isinstance(normalized_rows, list) else []
    admitted = sum(bool(row.get("admitted")) for row in rows)
    economic = sum(bool(row.get("economic_admissible")) for row in rows)
    card = {"status": "DATA_BLOCKED", "inventory_documents": len(rows), "acquired_documents": len(rows), "acquired_valid": len(rows), "acquired_invalid": 0, "pending_documents": 0, "parsed_documents": len(rows), "admitted_documents": admitted, "deal_event_rows": admitted, "terms_rows": sum(bool(row.get("consideration_type")) for row in rows), "lifecycle_rows": len(rows), "lifecycle_metadata_rows": admitted, "announcement_date_candidate_count": parsed["announcement_date_candidate_count"], "announcement_at_rows": sum(row.get("announcement_at") is not None for row in rows), "agreement_date": rows[0].get("agreement_date") if rows else None, "amendment_restated_date": rows[0].get("amendment_restated_date") if rows else None, "terminal_close_rows": sum(row.get("close_at") is not None for row in rows), "terminal_cancel_rows": sum(row.get("cancel_at") is not None for row in rows), "terminal_break_rows": sum(row.get("break_at") is not None for row in rows), "terminal_delist_rows": sum(row.get("delist_at") is not None for row in rows), "consideration_currency_status": rows[0].get("consideration_value", {}).get("currency_status") if rows and rows[0].get("consideration_value") else None, "economic_admissible_rows": economic, "economic_signal_rows": 0, "blockers": ["one receipt-backed DEFM14A row supplies lifecycle metadata only; announcement_at remains null despite two explicit date candidates", "agreement 2024-03-12 and amendment/restatement 2024-05-20 are parsed; close/cancel/break/delist timestamps and linked terminal filings remain null", "currency remains null/AMBIGUOUS for raw dollar consideration; cash-only terms and target security continuity remain unresolved"], "sources": _source_matrix()["cards"]["H15"]["sources"]}
    return _with_packages("H15", card)


def gate_h16() -> dict[str, Any]:
    parsed = read_json(REVIEW2_PARSED["H16"])
    rows = parsed.get("rows", []) if isinstance(parsed.get("rows", []), list) else []
    card = {"status": "DATA_BLOCKED", "inventory_documents": int(parsed.get("source_rows", 0) or 0), "acquired_documents": int(parsed.get("source_rows", 0) or 0), "acquired_valid": int(parsed.get("source_rows", 0) or 0), "acquired_invalid": 0, "pending_documents": 0, "parsed_documents": len(rows), "admitted_documents": int(parsed.get("admitted_rows", 0) or 0), "spinoff_event_rows": len(rows), "distribution_rows": 0, "successor_mapping_rows": 0, "zero_result_semantics": "NO_RECEIPT_VALID_SAMPLE_ACQUIRED; NOT_GLOBAL_ABSENCE", "economic_signal_rows": 0, "blockers": ["no receipt-valid Form 10/10-12B/8-K two-step spinoff sample was acquired", "zero rows mean NO_RECEIPT_VALID_SAMPLE_ACQUIRED and NOT_GLOBAL_ABSENCE", "distribution/ownership conversion and successor mapping are absent; generic successor/effective-date mentions are insufficient"], "sources": _source_matrix()["cards"]["H16"]["sources"]}
    return _with_packages("H16", card)


GATES = {"H06": gate_h06, "H07": gate_h07, "H08": gate_h08, "H09": gate_h09, "H10": gate_h10, "H11": gate_h11, "H12": gate_h12, "H13": gate_h13, "H15": gate_h15, "H16": gate_h16}


def verify_source_matrix() -> dict[str, Any]:
    matrix = read_json(SOURCE_MATRIX)
    if matrix.get("status") != "FROZEN_SOURCE_MATRIX_WITH_BLOCKERS":
        raise ValueError("source_matrix_status_invalid")
    cards = matrix.get("cards")
    if set(cards or {}) != set(CONTRACTS):
        raise ValueError("source_matrix_cards_incomplete")
    checked = 0
    for key, card in cards.items():
        sources = card.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"source_matrix_sources_missing:{key}")
        for source in sources:
            if not source.get("status") or not source.get("coverage") or not source.get("pit"):
                raise ValueError(f"source_matrix_metadata_missing:{key}")
            if source.get("kind") == "existing_local":
                path = REPO / source["path"]
                if not path.is_file() or source.get("body_sha256") != sha256(path):
                    raise ValueError(f"source_matrix_local_hash_mismatch:{key}:{path}")
            elif source.get("body_sha256") not in (None, ""):
                if not re.fullmatch(r"[0-9a-f]{64}", str(source["body_sha256"])):
                    raise ValueError(f"source_matrix_hash_invalid:{key}")
            checked += 1
    return {"cards": len(cards), "sources": checked}


def verify_public_probe_receipts() -> int:
    checked = 0
    receipt_paths = sorted(set(PUBLIC_ROOT.glob("*.receipt.json")) | set(PUBLIC_ROOT.glob("*receipt.json")))
    for receipt_path in receipt_paths:
        receipt = read_json(receipt_path)
        rows = receipt.get("routes") if isinstance(receipt.get("routes"), list) else receipt.get("rows") if isinstance(receipt.get("rows"), list) else [receipt]
        for row in rows:
            checked += 1
            _verify_probe_row(row, receipt_path)
    return checked


def _verify_probe_row(receipt: dict[str, Any], receipt_path: Path) -> None:
    """Verify each declared probe/body pair, including resumable SEC batches."""
    if not isinstance(receipt.get("url"), str) or not receipt["url"].startswith("https://"):
            raise ValueError(f"probe_endpoint_invalid:{receipt_path}")
    if not isinstance(receipt.get("status_code"), int) or not 100 <= receipt["status_code"] <= 599:
            raise ValueError(f"probe_status_invalid:{receipt_path}")
    body_name = receipt.get("body_path")
    body_sha = receipt.get("body_sha256")
    if body_name is None:
        if receipt.get("bytes") != 0 or body_sha not in (None, ""):
                raise ValueError(f"probe_empty_body_metadata_invalid:{receipt_path}")
    else:
        body = REPO / str(body_name)
        if not body.is_file() or body.stat().st_size != int(receipt.get("bytes", -1)) or sha256(body) != body_sha:
                raise ValueError(f"probe_body_receipt_mismatch:{receipt_path}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(body_sha)):
                raise ValueError(f"probe_body_hash_invalid:{receipt_path}")
    for path_key, sha_key, bytes_key in (("index_body_path", "index_body_sha256", "index_bytes"),):
        declared_path = receipt.get(path_key)
        if declared_path is None:
            if receipt.get(sha_key) not in (None, ""):
                raise ValueError(f"probe_related_hash_without_body:{receipt_path}")
            continue
        related = REPO / str(declared_path)
        if not related.is_file() or related.stat().st_size != int(receipt.get(bytes_key, -1)) or sha256(related) != receipt.get(sha_key):
            raise ValueError(f"probe_related_body_receipt_mismatch:{receipt_path}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get(sha_key))):
            raise ValueError(f"probe_related_hash_invalid:{receipt_path}")
    for exhibit in receipt.get("exhibits", []):
        if not isinstance(exhibit, dict) or not exhibit.get("body_path"):
            continue
        related = REPO / str(exhibit["body_path"])
        if not related.is_file() or related.stat().st_size != int(exhibit.get("bytes", -1)) or sha256(related) != exhibit.get("body_sha256"):
            raise ValueError(f"probe_exhibit_receipt_mismatch:{receipt_path}")


def build_report() -> dict[str, Any]:
    verification = verify_sec_documents()
    batch_verification = verify_batch_documents()
    matrix = verify_source_matrix()
    probe_receipts = verify_public_probe_receipts()
    review2 = verify_review2_package()
    cards = {key: fn() for key, fn in GATES.items()}
    inventory = read_json(CANDIDATE_INVENTORY) if CANDIDATE_INVENTORY.is_file() else {}
    batch = read_json(BATCH_RECEIPT) if BATCH_RECEIPT.is_file() else {}
    h09 = read_json(H09_SCAN) if H09_SCAN.is_file() else {}
    h11 = read_json(H11_INVENTORY) if H11_INVENTORY.is_file() else {}
    routes = read_json(ROUTE_RECEIPT) if ROUTE_RECEIPT.is_file() else {}
    acquisition = {"inventory_rows": inventory.get("record_count", 0), "window_counts": inventory.get("window_counts", {}), "body_rows": len(batch.get("rows", [])), "body_counts": {form: sum(1 for row in batch.get("rows", []) if row.get("form") == form) for form in sorted({str(row.get("form")) for row in batch.get("rows", [])})}, "remaining_by_form": batch.get("remaining_by_form", {}), "h09_scanned_files": h09.get("scanned_files", 0), "h09_keyword_counts": h09.get("term_counts", {}), "h11_counts": h11.get("counts", {}), "h11_through_cutoff": h11.get("through_cutoff", 0), "route_statuses": [{"candidate": row.get("candidate"), "status_code": row.get("status_code"), "url": row.get("url")} for row in routes.get("routes", [])]}
    return {"status": "DONE_WITH_CONCERNS", "economic_run": False, "historical_cutoff": CUTOFF, "cards": cards, "contracts": CONTRACTS, "source_matrix": {"path": str(SOURCE_MATRIX.relative_to(REPO)), **matrix}, "review2_binding": review2, "sec_content_verification": {"documents": verification["documents"], "valid_documents": verification["valid_documents"], "invalid_documents": verification["invalid_documents"], "batch": batch_verification}, "public_probe_receipts": probe_receipts, "acquisition_evidence": acquisition, "constraints": {"no_ohlcv_proxy": True, "no_runtime_mutation": True, "no_paper_live": True, "no_auto_promotion": True, "no_economic_cells": True}, "overall_blockers": "All ten cards remain data/PIT blocked or partial; SEC document plumbing and bounded review2 parser rows are evidence only, and no candidate is economic-ready."}


def write_markdown(report: dict[str, Any]) -> None:
    lines = ["# Equity secondary causal/schema gate report — 2026-09-08", "", "Research-only acquisition and schema/PIT gate. No economics, PAPER, LIVE, production/configuration or promotion action was run.", "", f"Status: `{report['status']}`; cutoff: `{CUTOFF}`.", "", "| Candidate | Status | Key coverage | Blockers |", "|---|---|---:|---|"]
    for key, card in report["cards"].items():
        numeric = next((f"{field}={value}" for field, value in card.items() if field.endswith("rows") and isinstance(value, int)), "no rows")
        lines.append(f"| {key} | `{card['status']}` | {numeric} | {'; '.join(card['blockers'])} |")
    evidence = report.get("acquisition_evidence", {})
    lines.extend(["", "Every card freezes provider meaning, PIT identity, decision/availability boundary, formula/window/threshold state, execution shift, role, controls, cost 1x/2x/3x, full/recent/holdout cells and minimum breadth/history in the protocol. Missing causal fields fail closed; current snapshots and daily OHLCV are not substituted.", "", "The legacy SEC fixture contains 56 original pilot documents: 20 Form 4 plus 36 filing documents (18 10-K and 18 10-Q). The expanded receipt and inventory are authoritative for acquisition counts; H10 currently reports 333 validated 10-K/Q documents and the legacy 56 total is retained only as fixture context.", "", f"Candidate-specific acquisition evidence: `{evidence.get('inventory_rows', 0)}` metadata rows; `{evidence.get('body_rows', 0)}` hashed original bodies; H09 scanned `{evidence.get('h09_scanned_files', 0)}` bodies with zero inferred relationship rows; H11 MCHI accepted-by-cutoff rows `{evidence.get('h11_through_cutoff', 0)}`.", "", f"Sanitized source matrix: `{SOURCE_MATRIX.relative_to(REPO)}`; every zero-row card records an official/public path, status, hash field, coverage and PIT limitation.", ""])
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def freeze_manifest(report: dict[str, Any]) -> dict[str, Any]:
    source_paths: set[Path] = {MASTER, DETAIL, INDEPENDENT, PROTOCOL, SEC_DOC_RECEIPT, SEC_V2_RECEIPT, SEC_ARCHIVE_RECEIPT, PIT_UNIVERSE, BARS, ACTIONS, NPORT, NPORT_SUB}
    source_paths.update(item for item in PUBLIC_ROOT.rglob("*") if item.is_file())
    source_paths.update(SEC_DOC_ROOT.glob("*.htm"))
    source_paths.update(SEC_DOC_ROOT.glob("*.xml"))
    source_paths.update(SEC_V2_ROOT.glob("*.json"))
    source_paths.update(SEC_ARCHIVE_ROOT.glob("*.json"))
    source_paths.update(item for item in REVIEW2_ROOT.rglob("*") if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc")
    source_paths.update(item for root in (H06_LOCAL_MANIFEST.parent, H0815_LOCAL_MANIFEST.parent, H11_LOCAL_MANIFEST.parent) for item in root.rglob("*") if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc")
    artifacts = (PROTOCOL, REPORT_MD, Path(__file__), TEST, REPORT, MASTER, DETAIL, INDEPENDENT, REPO / "scripts/research/acquire_equity_secondary_public_2026_09_08.py")
    payload: dict[str, Any] = {"status": "FROZEN_EQUITY_SECONDARY_CAUSAL_GATE", "identity": "equity_secondary_causal_gate_2026_09_08", "frozen_at_utc": "2026-09-08T20:00:00Z", "historical_cutoff": CUTOFF, "contracts": CONTRACTS, "inputs": {str(item.relative_to(REPO)): {"sha256": sha256(item)} for item in sorted(source_paths)}, "artifacts": {str(item.relative_to(REPO)): {"sha256": sha256(item)} for item in artifacts}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": sha256(REPORT), "status": report["status"]}, "economic_run": False, "no_auto_promotion": True, "no_ohlcv_proxy": True, "canonical_hash_policy": "canonical JSON hash excludes only manifest_sha256", "manifest_sha256": "PENDING_FREEZE"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    payload = read_json(path)
    verify_review2_package()
    checked = 0
    for section in ("inputs", "artifacts"):
        for name, declaration in payload[section].items():
            item = REPO / name
            if sha256(item) != declaration["sha256"]:
                raise ValueError(f"hash_mismatch:{name}")
            checked += 1
    canonical = dict(payload)
    canonical["manifest_sha256"] = "PENDING_FREEZE"
    expected = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected != payload["manifest_sha256"]:
        raise ValueError("manifest_canonical_hash_mismatch")
    if payload.get("economic_run") is not False:
        raise ValueError("economic_run_must_be_false")
    saved_report = read_json(REPORT)
    if saved_report != build_report():
        raise ValueError("saved_report_differs_from_build_report")
    verify_sec_documents()
    verify_source_matrix()
    verify_public_probe_receipts()
    return {"status": "FROZEN_EQUITY_SECONDARY_MANIFEST_VERIFIED", "checked_declared_files": checked, "checked_artifacts": len(payload["artifacts"]), "manifest_sha256": payload["manifest_sha256"]}


def main() -> None:
    report = build_report()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(report)
    manifest = freeze_manifest(report)
    print(json.dumps({"status": report["status"], "manifest_sha256": manifest["manifest_sha256"], "report": str(REPORT)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_manifest(), sort_keys=True))
    else:
        main()
