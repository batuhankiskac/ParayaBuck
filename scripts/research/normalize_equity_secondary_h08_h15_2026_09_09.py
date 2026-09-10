"""Build a frozen, read-only local normalization package for H08 and H15.

The package consumes the already receipt-bound SEC bodies in the equity
secondary acquisition area.  It deliberately keeps consensus/surprise and
economic deal fields out unless the source body proves them exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
INPUT_ROOT = REPO / "data/research/external/equity_secondary_public_2026_09_08"
INPUT_RECEIPT = INPUT_ROOT / "sec_batch_receipt.json"
OUT = REPO / "data/research/external/equity_secondary_local_normalization_2026_09_09"
H08_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A"}
EPS_CONCEPTS = {
    "EarningsPerShareBasic": "basic",
    "EarningsPerShareDiluted": "diluted",
    "EarningsPerShareBasicAndDiluted": "basic_and_diluted",
    "NetIncomeLossPerShareBasic": "basic",
    "NetIncomeLossPerShareDiluted": "diluted",
}
TEXT_EPS_ACCESSIONS = {
    "0001292814-26-003970",
    "0001292814-26-002777",
    "0001292814-24-001911",
    "0001292814-22-002112",
    "0001292814-22-000611",
}


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def canonical_sha(value: object) -> str:
    return sha_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _attrs(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*([\"'])(.*?)\2", value, re.S):
        result[match.group(1).lower()] = html.unescape(match.group(3))
    return result


def _inner_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def _contexts(text: str) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"<(?:xbrli:)?context\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?:xbrli:)?context\s*>", re.I | re.S)
    for match in pattern.finditer(text):
        attrs = _attrs(match.group("attrs"))
        context_id = attrs.get("id")
        if not context_id:
            continue
        body = match.group("body")
        identifier = re.search(r"<(?:xbrli:)?identifier\b[^>]*>(.*?)</(?:xbrli:)?identifier\s*>", body, re.I | re.S)
        instant = re.search(r"<(?:xbrli:)?instant\b[^>]*>(.*?)</(?:xbrli:)?instant\s*>", body, re.I | re.S)
        start = re.search(r"<(?:xbrli:)?startDate\b[^>]*>(.*?)</(?:xbrli:)?startDate\s*>", body, re.I | re.S)
        end = re.search(r"<(?:xbrli:)?endDate\b[^>]*>(.*?)</(?:xbrli:)?endDate\s*>", body, re.I | re.S)
        contexts[context_id] = {
            "identifier": _inner_text(identifier.group(1)) if identifier else "",
            "instant": _inner_text(instant.group(1)) if instant else None,
            "start": _inner_text(start.group(1)) if start else None,
            "end": _inner_text(end.group(1)) if end else None,
            "dimensioned": bool(re.search(r"explicitMember|typedMember", body, re.I)),
        }
    return contexts


def _units(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(r"<(?:(?:xbrli|iso4217):)?unit\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?:(?:xbrli|iso4217):)?unit\s*>", re.I | re.S)
    for match in pattern.finditer(text):
        unit_id = _attrs(match.group("attrs")).get("id")
        measure = re.search(r"<(?:xbrli:)?measure\b[^>]*>(.*?)</(?:xbrli:)?measure\s*>", match.group("body"), re.I | re.S)
        if unit_id and measure:
            result[unit_id] = _inner_text(measure.group(1))
    return result


def _number(value: str, scale: str | None, sign: str | None) -> float | None:
    cleaned = value.replace(",", "").replace("−", "-").strip()
    if cleaned in {"", "-", "—", "–", "N/A", "n/a"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        result = float(cleaned)
        if scale:
            result *= 10 ** int(scale)
        if negative or (sign or "").strip() == "-":
            result = -abs(result)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _iso_date(value: str) -> str | None:
    cleaned = html.unescape(value).replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_h08_body(body: bytes, row: dict[str, Any], *, body_path: str, body_sha256: str) -> list[dict[str, Any]]:
    """Extract only dimension-free, unit-bound EPS facts from one SEC body."""
    text = body.decode("utf-8", errors="replace")
    contexts = _contexts(text)
    units = _units(text)
    facts: list[dict[str, Any]] = []
    pattern = re.compile(r"<ix:nonfraction\b(?P<attrs>[^>]*)>(?P<value>.*?)</ix:nonfraction\s*>", re.I | re.S)
    for ordinal, match in enumerate(pattern.finditer(text), start=1):
        attrs = _attrs(match.group("attrs"))
        qualified = attrs.get("name", "")
        local = qualified.rsplit(":", 1)[-1]
        basis = EPS_CONCEPTS.get(local)
        if basis is None:
            continue
        context = contexts.get(attrs.get("contextref", ""))
        unit = units.get(attrs.get("unitref", ""))
        value = _number(_inner_text(match.group("value")), attrs.get("scale"), attrs.get("sign"))
        identifier = str(context.get("identifier", "")) if context else ""
        if not context or context.get("dimensioned") or not unit or value is None:
            continue
        if identifier.zfill(10) != str(row.get("cik", "")).zfill(10):
            continue
        span = {"start": match.start(), "end": match.end(), "encoding": "utf-8-text"}
        facts.append(
            {
                "candidate": "H08",
                "symbol": row.get("symbol"),
                "cik": str(row.get("cik", "")).zfill(10),
                "accession": row.get("accn"),
                "form": row.get("form"),
                "document_role": "body",
                "amendment": str(row.get("form", "")).endswith("/A"),
                "accepted_at": row.get("accepted"),
                "release_at": None,
                "release_at_status": "NO_EXPLICIT_RELEASE_TIMESTAMP_IN_BODY",
                "fiscal_period": {"start": context.get("start"), "end": context.get("end") or context.get("instant")},
                "basis": basis,
                "unit": unit,
                "qualified_tag": qualified,
                "value": value,
                "source_path": body_path,
                "body_sha256": body_sha256,
                "receipt_path": str(INPUT_RECEIPT.relative_to(REPO)),
                "receipt_sha256": sha(INPUT_RECEIPT),
                "source_span": {**span, "sha256": sha_bytes(text[match.start() : match.end()].encode())},
                "duplicate_key": [row.get("symbol"), row.get("report_date"), context.get("end") or context.get("instant"), basis],
            }
        )
    return facts


def _text_candidate(row: dict[str, Any], body_path: str, body_sha256: str, match: re.Match[str], *, value_text: str | None, unit_basis: str, basis: str, reason: str) -> dict[str, Any]:
    text = match.string
    return {
        "candidate": "H08",
        "symbol": row.get("symbol"),
        "cik": str(row.get("cik", "")).zfill(10),
        "accession": row.get("accn"),
        "form": row.get("form"),
        "document_role": "body",
        "accepted_at": row.get("accepted"),
        "release_at": None,
        "value_text": value_text,
        "unit_basis": unit_basis,
        "basis": basis,
        "admitted": False,
        "reason": reason,
        "source_path": body_path,
        "body_sha256": body_sha256,
        "receipt_path": str(INPUT_RECEIPT.relative_to(REPO)),
        "receipt_sha256": sha(INPUT_RECEIPT),
        "receipt_content_validation_error": row.get("content_validation_error") or None,
        "source_span": {"start": match.start(), "end": match.end(), "encoding": "utf-8-text", "text": text[match.start() : match.end()], "sha256": sha_bytes(text[match.start() : match.end()].encode())},
    }


def parse_h08_text_candidates(body: bytes, row: dict[str, Any], *, body_path: str, body_sha256: str) -> list[dict[str, Any]]:
    """Account for numeric EPS-like text/table spans without treating them as iX facts."""
    if str(row.get("accn")) not in TEXT_EPS_ACCESSIONS:
        return []
    text = body.decode("utf-8", errors="replace")
    matches: list[re.Match[str]] = []
    if str(row.get("accn")) in {"0001292814-26-003970", "0001292814-26-002777"}:
        matches = list(re.finditer(r"(?:earnings per share|EPS).{0,140}?(?P<value>R\$\s*[0-9]+(?:\.[0-9]+)?)", text, re.I | re.S))
        return [_text_candidate(row, body_path, body_sha256, match, value_text=match.group("value"), unit_basis="R$/share (text-declared)", basis="normalized_or_unspecified_prose", reason="TEXT_ONLY_NO_IX_CONTEXT_OR_PIT_RELEASE_TIMESTAMP") for match in matches]
    match = re.search(r"(?<!Normalized )EPS\s*(?:<[^>]+>\s*)*\(R\$/shares\)(?:<[^>]+>\s*)*(?P<value>[0-9]+(?:\.[0-9]+)?)", text, re.I | re.S)
    if match:
        return [_text_candidate(row, body_path, body_sha256, match, value_text=match.group("value"), unit_basis="R$/shares (table label)", basis="EPS_LABEL_UNQUALIFIED", reason="TEXT_TABLE_NO_IX_CONTEXT_OR_FISCAL_PERIOD_AND_NO_PIT_RELEASE_TIMESTAMP")]
    return []


def parse_h08_release_candidates(body: bytes, row: dict[str, Any], *, body_path: str, body_sha256: str) -> list[dict[str, Any]]:
    """Retain explicit release-like prose/time spans while refusing release_at inference."""
    text = body.decode("utf-8", errors="replace")
    candidates: list[dict[str, Any]] = []
    date_pattern = re.compile(r"\b(?:issued|announced|released)\s+on\s+(?:[A-Z][a-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})", re.I)
    time_pattern = re.compile(r"\b(?:earnings|quarterly).{0,80}?\bteleconference.{0,180}?\b\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|am|pm)", re.I | re.S)
    for match in date_pattern.finditer(text):
        candidates.append(_text_candidate(row, body_path, body_sha256, match, value_text=None, unit_basis="not_applicable", basis="release_date_prose_candidate", reason="EXPLICIT_PROSE_DATE_IS_NOT_A_RELEASE_TIMESTAMP_OR_AVAILABLE_AT"))
    for match in time_pattern.finditer(text):
        candidates.append(_text_candidate(row, body_path, body_sha256, match, value_text=None, unit_basis="not_applicable", basis="teleconference_time_candidate", reason="CONFERENCE_TIME_DOES_NOT_PROVE_EARNINGS_RELEASE_TIMESTAMP"))
    return candidates


def parse_h08_cue_candidates(body: bytes, row: dict[str, Any], *, body_path: str, body_sha256: str) -> list[dict[str, Any]]:
    """Retain broad text cues separately from the seven numeric candidates.

    These are discovery evidence only.  The parser records the exact source
    span and an explicit non-admission classification; it never turns a cue
    into an EPS, release, consensus, or surprise fact.
    """
    text = body.decode("utf-8", errors="replace")
    candidates: list[dict[str, Any]] = []
    patterns = (
        ("eps_per_share_cue", re.compile(r"\b(?:EPS|earnings\s+per\s+share|per-share)\b", re.I), "CUE_ONLY_NO_PROVABLE_FACT"),
        ("numeric_proximity", re.compile(r"\b(?:EPS|earnings\s+per\s+share|per-share)\b.{0,80}?\(?\s*(?:\$|R\$|€|£)?\s*\d+(?:\.\d+)?", re.I | re.S), "NUMERIC_PROXIMITY_WITHOUT_IX_CONTEXT_OR_PERIOD"),
    )
    for kind, pattern, reason in patterns:
        for match in pattern.finditer(text):
            item = _text_candidate(row, body_path, body_sha256, match, value_text=None, unit_basis="not_applicable", basis=kind, reason=reason)
            item["cue_kind"] = kind
            item["classification"] = "UNKNOWN"
            candidates.append(item)
    return candidates


def _date_match(text: str, pattern: str, *, group: int = 1) -> dict[str, Any] | None:
    match = re.search(pattern, text, re.I | re.S)
    if not match:
        return None
    raw = match.group(group)
    value = _iso_date(raw)
    if value is None:
        return None
    return {"value": value, "text": raw, "start": match.start(group), "end": match.end(group), "sha256": sha_bytes(raw.encode())}


def _date_matches(text: str, pattern: str, *, group: int = 1) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for match in re.finditer(pattern, text, re.I | re.S):
        raw = match.group(group)
        value = _iso_date(raw)
        if value is not None:
            result.append({"value": value, "text": raw, "start": match.start(group), "end": match.end(group), "sha256": sha_bytes(raw.encode())})
    return result


def _literal_span(text: str, value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    match = re.search(re.escape(value), text, re.I)
    if not match:
        return None
    raw = text[match.start() : match.end()]
    return {"text": raw, "start": match.start(), "end": match.end(), "encoding": "utf-8-visible-text", "sha256": sha_bytes(raw.encode())}


def parse_h15_body(body: bytes, row: dict[str, Any], *, body_path: str, body_sha256: str) -> dict[str, Any]:
    """Extract explicitly dated deal metadata while leaving unproven events null."""
    text = body.decode("utf-8", errors="replace")
    visible = _inner_text(text)
    agreement = _date_match(text, r"dated\s+as\s+of\s+([A-Z][a-z]+(?:&nbsp;|\s)+\d{1,2},(?:&nbsp;|\s)*\d{4})")
    amendment = _date_match(text, r"amended\s+and\s+restated.*?dated\s+as\s+of\s+([A-Z][a-z]+(?:&nbsp;|\s)+\d{1,2},(?:&nbsp;|\s)*\d{4})")
    announcement_candidates = _date_matches(text, r"([A-Z][a-z]+(?:&nbsp;|\s)+\d{1,2},(?:&nbsp;|\s)*\d{4})(?=.{0,180}?Date of Announcement)")
    announcement = announcement_candidates[0] if len(announcement_candidates) == 1 else None
    expected = re.search(r"(third\s+calendar\s+quarter\s+of\s+\d{4})", visible, re.I)
    parties = sorted(set(re.findall(r"\b(?:Alumina Limited|Alcoa(?: Corporation| Bidder)?)\b", visible)))
    target = "Alumina Limited" if "Alumina Limited" in parties else None
    acquirer = "Alcoa Bidder" if "Alcoa Bidder" in parties else ("Alcoa" if "Alcoa" in parties else None)
    has_scheme = bool(re.search(r"scheme|deed poll", visible, re.I))
    has_cash = bool(re.search(r"\bcash\b", visible, re.I))
    consideration_type = "cash_and_security_or_scheme" if has_scheme and has_cash else ("cash" if has_cash else None)
    value_match = re.search(r"(?:cash|purchase)\s+consideration\s+of\s+\$\s*([0-9]+(?:\.[0-9]+)?)(?:\s+(million|billion|thousand))?", visible, re.I)
    consideration_value = None
    if value_match:
        consideration_value = {"value": float(value_match.group(1)), "currency": None, "currency_symbol": "$", "currency_status": "AMBIGUOUS", "scale": (value_match.group(2) or "units").lower(), "text": value_match.group(0)}
    return {
        "candidate": "H15",
        "symbol": row.get("symbol"),
        "cik": str(row.get("cik", "")).zfill(10),
        "accession": row.get("accn"),
        "form": row.get("form"),
        "accepted_at": row.get("accepted"),
        "source_path": body_path,
        "body_sha256": body_sha256,
        "receipt_path": str(INPUT_RECEIPT.relative_to(REPO)),
        "receipt_sha256": sha(INPUT_RECEIPT),
        "receipt_content_validation_error": row.get("content_validation_error") or None,
        "target_name": target,
        "acquirer_name": acquirer,
        "party_spans": {name: _literal_span(visible, name) for name in parties},
        "agreement_date": agreement["value"] if agreement else None,
        "amendment_restated_date": amendment["value"] if amendment else None,
        "announcement_at": announcement["value"] if announcement else None,
        "announcement_date_candidates": announcement_candidates,
        "linked_terminal_filings": [],
        "linked_terminal_status": "NO_RECEIPT_BOUND_TERMINAL_EVENT",
        "expected_close_text": expected.group(1) if expected else None,
        "close_at": None,
        "cancel_at": None,
        "break_at": None,
        "delist_at": None,
        "consideration_type": consideration_type,
        "consideration_value": consideration_value,
        "consideration_type_span": _literal_span(visible, "scheme") if has_scheme else (_literal_span(visible, "cash") if has_cash else None),
        "consideration_value_span": _literal_span(visible, value_match.group(0) if value_match else None),
        "raw_consideration_text": value_match.group(0) if value_match else None,
        "scheme_span": _literal_span(visible, "scheme") if has_scheme else None,
        "cash_span": _literal_span(visible, "cash") if has_cash else None,
        "break_fee": None,
        "amendment_or_restatement_present": bool(amendment or re.search(r"amended|restated", visible, re.I)),
        "date_spans": {key: value for key, value in (("agreement_date", agreement), ("amendment_restated_date", amendment), ("announcement_at", announcement)) if value},
        "admitted": bool(target and acquirer and (agreement or announcement)),
        "admission_scope": "lifecycle_metadata_only",
        "economic_admissible": False,
        "reason": "explicit parties and dated agreement metadata; terminal events, borrow and economic terms remain incomplete",
    }


def _latest_receipt_rows(receipt: dict[str, Any], forms: set[str], *, prefer_valid: bool = False, allow_content_error: bool = False) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in receipt.get("rows", []):
        if row.get("accn") and row.get("form") in forms:
            key = str(row["accn"])
            current = latest.get(key)
            row_valid = row.get("status_code") == 200 and row.get("body_path") and not row.get("content_validation_error")
            current_valid = bool(current and current.get("status_code") == 200 and current.get("body_path") and not current.get("content_validation_error"))
            if current is None or (prefer_valid and row_valid and not current_valid) or not prefer_valid:
                latest[key] = row
    return [row for row in latest.values() if row.get("status_code") == 200 and row.get("body_path") and (allow_content_error or not row.get("content_validation_error"))]


def _h08_accession_dispositions(receipt: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Summarize every H08-family accession without silently losing a valid attempt."""
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for ordinal, row in enumerate(receipt.get("rows", [])):
        if row.get("accn") and row.get("form") in H08_FORMS:
            grouped.setdefault(str(row["accn"]), []).append((ordinal, row))
    all_rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for accession in sorted(grouped):
        attempts = grouped[accession]
        valid = [(ordinal, row) for ordinal, row in attempts if row.get("status_code") == 200 and row.get("body_path") and not row.get("content_validation_error")]
        latest_ordinal, latest = attempts[-1]
        latest_valid = latest in [row for _, row in valid]
        preserved = valid[0][1] if valid else None
        disposition = "LATEST_ATTEMPT_VALID" if latest_valid else ("PRIOR_VALID_BODY_PRESERVED_LATEST_CONFLICT" if valid else "NO_VALID_BODY")
        record = {
            "candidate": "H08",
            "accession": accession,
            "form": latest.get("form"),
            "attempt_count": len(attempts),
            "latest_attempt_ordinal": latest_ordinal,
            "latest_attempt": {"status_code": latest.get("status_code"), "body_path": latest.get("body_path"), "body_sha256": latest.get("body_sha256"), "content_validation_error": latest.get("content_validation_error") or None},
            "any_valid_body": bool(valid),
            "any_valid_attempt_count": len(valid),
            "latest_attempt_valid": latest_valid,
            "disposition": disposition,
            "preserved_body": ({"attempt_ordinal": valid[0][0], "body_path": preserved.get("body_path"), "body_sha256": preserved.get("body_sha256"), "accepted": preserved.get("accepted")} if preserved else None),
        }
        all_rows.append(record)
        if valid and not latest_valid:
            conflicts.append(record)
    return all_rows, conflicts


def _bound_body(row: dict[str, Any]) -> tuple[bytes, str] | None:
    path = REPO / str(row["body_path"])
    if not path.is_file() or path.stat().st_size != int(row.get("bytes", -1)):
        return None
    digest = sha(path)
    if digest != row.get("body_sha256"):
        return None
    return path.read_bytes(), digest


def _stored_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _resolved_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO / path


def build_package(*, out: Path = OUT) -> dict[str, Any]:
    receipt = json.loads(INPUT_RECEIPT.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    parsed_dir = out / "parsed"
    parsed_dir.mkdir(exist_ok=True)
    h08_rows: list[dict[str, Any]] = []
    h08_text_rows: list[dict[str, Any]] = []
    h08_release_rows: list[dict[str, Any]] = []
    h08_cue_rows: list[dict[str, Any]] = []
    h08_inventory: list[dict[str, Any]] = []
    h08_dispositions, h08_conflicts = _h08_accession_dispositions(receipt)
    valid_h08_rows = _latest_receipt_rows(receipt, H08_FORMS)
    for row in valid_h08_rows:
        bound = _bound_body(row)
        if bound is None:
            continue
        body, digest = bound
        h08_inventory.append({"candidate": "H08", "accession": row.get("accn"), "symbol": row.get("symbol"), "form": row.get("form"), "accepted_at": row.get("accepted"), "body_path": row.get("body_path"), "body_sha256": digest, "amendment": str(row.get("form", "")).endswith("/A"), "document_role": "body"})
        h08_rows.extend(parse_h08_body(body, row, body_path=str(row["body_path"]), body_sha256=digest))
        h08_text_rows.extend(parse_h08_text_candidates(body, row, body_path=str(row["body_path"]), body_sha256=digest))
    valid_accessions = {str(row.get("accn")) for row in valid_h08_rows}
    for row in _latest_receipt_rows(receipt, H08_FORMS, allow_content_error=True):
        if str(row.get("accn")) in valid_accessions:
            continue
        bound = _bound_body(row)
        if bound is None:
            continue
        body, digest = bound
        h08_text_rows.extend(parse_h08_text_candidates(body, row, body_path=str(row["body_path"]), body_sha256=digest))

    # Scan one immutable, receipt-valid body for every accession.  This keeps
    # discovery cues and prior-valid conflict bodies visible while the causal
    # parser remains based on the latest-attempt-valid set above.
    any_valid_rows = {}
    for disposition in h08_dispositions:
        preserved = disposition.get("preserved_body")
        if preserved:
            for row in receipt.get("rows", []):
                if row.get("accn") == disposition["accession"] and row.get("body_path") == preserved.get("body_path") and row.get("body_sha256") == preserved.get("body_sha256") and row.get("status_code") == 200:
                    any_valid_rows[disposition["accession"]] = row
                    break
    for row in any_valid_rows.values():
        bound = _bound_body(row)
        if bound is None:
            continue
        body, digest = bound
        h08_cue_rows.extend(parse_h08_cue_candidates(body, row, body_path=str(row["body_path"]), body_sha256=digest))

    # Release-like prose is also retained from every accession's first
    # receipt-success body.  A later validation error must not erase this
    # source span; its validation disposition is carried on the row.
    first_body_rows: dict[str, dict[str, Any]] = {}
    for row in receipt.get("rows", []):
        if row.get("accn") and row.get("form") in H08_FORMS and row.get("status_code") == 200 and row.get("body_path"):
            first_body_rows.setdefault(str(row["accn"]), row)
    for row in first_body_rows.values():
        bound = _bound_body(row)
        if bound is None:
            continue
        body, digest = bound
        h08_release_rows.extend(parse_h08_release_candidates(body, row, body_path=str(row["body_path"]), body_sha256=digest))

    h15_rows: list[dict[str, Any]] = []
    h15_inventory: list[dict[str, Any]] = []
    for row in _latest_receipt_rows(receipt, {"DEFM14A"}, prefer_valid=True):
        bound = _bound_body(row)
        if bound is None:
            continue
        body, digest = bound
        h15_inventory.append({"candidate": "H15", "accession": row.get("accn"), "symbol": row.get("symbol"), "form": row.get("form"), "accepted_at": row.get("accepted"), "body_path": row.get("body_path"), "body_sha256": digest, "document_role": "body"})
        h15_rows.append(parse_h15_body(body, row, body_path=str(row["body_path"]), body_sha256=digest))

    h08_path = parsed_dir / "h08_actual_eps_rows.json"
    h08_text_path = parsed_dir / "h08_eps_text_candidates.json"
    h08_release_path = parsed_dir / "h08_release_candidates.json"
    h08_cue_path = parsed_dir / "h08_text_cues.json"
    h08_disposition_path = parsed_dir / "h08_accession_dispositions.json"
    h15_path = parsed_dir / "h15_lifecycle_metadata_rows.json"
    h08_path.write_text(json.dumps(h08_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    h08_text_path.write_text(json.dumps(h08_text_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    h08_release_path.write_text(json.dumps(h08_release_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    h08_cue_path.write_text(json.dumps(h08_cue_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    h08_disposition_path.write_text(json.dumps(h08_dispositions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    h15_path.write_text(json.dumps(h15_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    inventory = {
        "schema": "equity_secondary_local_normalization_inventory.v1",
        "run_id": "equity_secondary_local_normalization_2026_09_09",
        "frozen_cutoff": "2026-07-31T23:59:59Z",
        "input_receipt": str(INPUT_RECEIPT.relative_to(REPO)),
        "input_receipt_sha256": sha(INPUT_RECEIPT),
        "h08_valid_bodies": h08_inventory,
        "h15_valid_bodies": h15_inventory,
        "h08_text_candidate_rows": len(h08_text_rows),
        "h08_release_candidate_rows": len(h08_release_rows),
        "h08_text_cue_rows": len(h08_cue_rows),
        "h08_accession_count": len(h08_dispositions),
        "h08_any_valid_body_accessions": sum(bool(row.get("any_valid_body")) for row in h08_dispositions),
        "h08_latest_attempt_valid_accessions": sum(bool(row.get("latest_attempt_valid")) for row in h08_dispositions),
        "h08_conflict_accessions": len(h08_conflicts),
        "h08_conflict_path": _stored_path(h08_disposition_path),
        "consensus_vintage_rows": 0,
        "surprise_rows": 0,
        "economic_run": False,
    }
    inventory_path = out / "normalization_inventory.json"
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = {
        "schema": "equity_secondary_local_normalization_report.v1",
        "run_id": inventory["run_id"],
        "spec_compliance": "PARTIAL_DATA_ONLY",
        "economic_run": False,
        "H08": {"status": "DATA_BLOCKED", "receipt_valid_bodies": len(h08_inventory), "accession_count": len(h08_dispositions), "any_valid_body_accessions": sum(bool(row.get("any_valid_body")) for row in h08_dispositions), "latest_attempt_valid_accessions": sum(bool(row.get("latest_attempt_valid")) for row in h08_dispositions), "conflict_accessions": len(h08_conflicts), "actual_eps_rows": len(h08_rows), "text_eps_candidate_rows": len(h08_text_rows), "text_cue_rows": len(h08_cue_rows), "eps_per_share_cue_rows": sum(row.get("cue_kind") == "eps_per_share_cue" for row in h08_cue_rows), "eps_per_share_cue_bodies": len({row.get("accession") for row in h08_cue_rows if row.get("cue_kind") == "eps_per_share_cue"}), "numeric_proximity_rows": sum(row.get("cue_kind") == "numeric_proximity" for row in h08_cue_rows), "numeric_proximity_bodies": len({row.get("accession") for row in h08_cue_rows if row.get("cue_kind") == "numeric_proximity"}), "release_date_prose_candidate_rows": sum(row.get("basis") == "release_date_prose_candidate" for row in h08_release_rows), "release_date_prose_candidate_bodies": len({row.get("accession") for row in h08_release_rows if row.get("basis") == "release_date_prose_candidate"}), "teleconference_time_candidate_rows": sum(row.get("basis") == "teleconference_time_candidate" for row in h08_release_rows), "teleconference_time_candidate_bodies": len({row.get("accession") for row in h08_release_rows if row.get("basis") == "teleconference_time_candidate"}), "consensus_vintage_rows": 0, "surprise_rows": 0, "release_timestamp_rows": sum(row.get("release_at") is not None for row in h08_rows), "blockers": ["no exact pre-release consensus vintage or surprise field is present in the receipt-valid bodies", "text/table EPS-like spans have no iX context, fiscal period, or PIT release timestamp and remain excluded", "release-like prose and teleconference spans are retained as unresolved candidates; they do not prove release_at", "eight accessions have a prior valid body followed by an invalid/conflicting retry; the prior body is preserved and the conflict is explicit"]},
        "H15": {"status": "PARTIAL_LIFECYCLE_METADATA", "receipt_valid_bodies": len(h15_inventory), "lifecycle_metadata_rows": sum(bool(row.get("admitted")) for row in h15_rows), "announcement_date_candidate_count": sum(len(row.get("announcement_date_candidates", [])) for row in h15_rows), "economic_admissible_rows": 0, "blockers": ["close/cancel/break/delist terminal events are not established by this body", "borrow and complete cash economic terms remain absent"]},
        "inputs": {"receipt_path": str(INPUT_RECEIPT.relative_to(REPO)), "receipt_sha256": sha(INPUT_RECEIPT)},
        "no_consensus_or_surprise_inference": True,
        "no_authoritative_package_mutation": True,
    }
    report_path = out / "normalization_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    parser_receipt = {"schema": "equity_secondary_local_normalization_receipt.v1", "run_id": inventory["run_id"], "runner_path": str(Path(__file__).relative_to(REPO)), "runner_sha256": sha(Path(__file__)), "input_receipt": str(INPUT_RECEIPT.relative_to(REPO)), "input_receipt_sha256": sha(INPUT_RECEIPT), "outputs": {"h08_rows": {"path": _stored_path(h08_path), "sha256": sha(h08_path), "bytes": h08_path.stat().st_size}, "h08_text_rows": {"path": _stored_path(h08_text_path), "sha256": sha(h08_text_path), "bytes": h08_text_path.stat().st_size}, "h08_release_rows": {"path": _stored_path(h08_release_path), "sha256": sha(h08_release_path), "bytes": h08_release_path.stat().st_size}, "h08_cue_rows": {"path": _stored_path(h08_cue_path), "sha256": sha(h08_cue_path), "bytes": h08_cue_path.stat().st_size}, "h08_dispositions": {"path": _stored_path(h08_disposition_path), "sha256": sha(h08_disposition_path), "bytes": h08_disposition_path.stat().st_size}, "h15_rows": {"path": _stored_path(h15_path), "sha256": sha(h15_path), "bytes": h15_path.stat().st_size}, "inventory": {"path": _stored_path(inventory_path), "sha256": sha(inventory_path), "bytes": inventory_path.stat().st_size}, "report": {"path": _stored_path(report_path), "sha256": sha(report_path), "bytes": report_path.stat().st_size}}, "counts": {"H08_accessions": len(h08_dispositions), "H08_any_valid_body_accessions": sum(bool(row.get("any_valid_body")) for row in h08_dispositions), "H08_latest_attempt_valid_accessions": sum(bool(row.get("latest_attempt_valid")) for row in h08_dispositions), "H08_conflict_accessions": len(h08_conflicts), "H08_valid_bodies": len(h08_inventory), "H08_actual_eps_rows": len(h08_rows), "H08_text_eps_candidate_rows": len(h08_text_rows), "H08_text_cue_rows": len(h08_cue_rows), "H08_release_candidate_rows": len(h08_release_rows), "H15_valid_bodies": len(h15_inventory), "H15_metadata_rows": sum(bool(row.get("admitted")) for row in h15_rows)}, "economic_run": False}
    receipt_path = out / "normalization_receipt.json"
    receipt_path.write_text(json.dumps(parser_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts = []
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "normalization_manifest.json" and "__pycache__" not in path.parts:
            artifacts.append({"path": _stored_path(path), "bytes": path.stat().st_size, "sha256": sha(path)})
    manifest = {"schema": "equity_secondary_local_normalization_manifest.v1", "run_id": inventory["run_id"], "artifact_count": len(artifacts), "artifacts": artifacts, "receipt_path": _stored_path(receipt_path), "receipt_sha256": sha(receipt_path), "report_path": _stored_path(report_path), "report_sha256": sha(report_path), "inventory_path": _stored_path(inventory_path), "inventory_sha256": sha(inventory_path), "economic_run": False, "authoritative_package_mutated": False}
    manifest["manifest_sha256"] = canonical_sha(manifest)
    (out / "normalization_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_package(out: Path = OUT) -> dict[str, Any]:
    manifest = json.loads((out / "normalization_manifest.json").read_text(encoding="utf-8"))
    payload = dict(manifest)
    claimed = payload.pop("manifest_sha256", None)
    if claimed != canonical_sha(payload):
        raise ValueError("manifest_self_hash_mismatch")
    if manifest.get("economic_run") or manifest.get("authoritative_package_mutated"):
        raise ValueError("scope_violation")
    for item in manifest["artifacts"]:
        path = _resolved_path(item["path"])
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
            raise ValueError(f"artifact_mismatch:{item['path']}")
    for key in ("receipt", "report", "inventory"):
        path = _resolved_path(manifest[f"{key}_path"])
        if sha(path) != manifest[f"{key}_sha256"]:
            raise ValueError(f"{key}_pin_mismatch")
    receipt = json.loads((out / "normalization_receipt.json").read_text(encoding="utf-8"))
    if receipt.get("input_receipt_sha256") != sha(INPUT_RECEIPT):
        raise ValueError("input_receipt_pin_mismatch")
    h08_rows = json.loads((out / "parsed" / "h08_actual_eps_rows.json").read_text(encoding="utf-8"))
    h08_text_rows = json.loads((out / "parsed" / "h08_eps_text_candidates.json").read_text(encoding="utf-8"))
    h08_release_rows = json.loads((out / "parsed" / "h08_release_candidates.json").read_text(encoding="utf-8"))
    h08_cue_rows = json.loads((out / "parsed" / "h08_text_cues.json").read_text(encoding="utf-8"))
    h08_dispositions = json.loads((out / "parsed" / "h08_accession_dispositions.json").read_text(encoding="utf-8"))
    h15_rows = json.loads((out / "parsed" / "h15_lifecycle_metadata_rows.json").read_text(encoding="utf-8"))
    input_rows = {str(row.get("accn")): row for row in json.loads(INPUT_RECEIPT.read_text(encoding="utf-8")).get("rows", []) if row.get("accn")}
    for row in h08_rows + h08_text_rows + h08_release_rows + h08_cue_rows + h15_rows:
        source = input_rows.get(str(row.get("accession")))
        if not source or row.get("body_sha256") != source.get("body_sha256") or row.get("receipt_sha256") != sha(INPUT_RECEIPT):
            raise ValueError("row_provenance_mismatch")
    if any(row.get("release_at") is not None and not row.get("release_at_status") for row in h08_rows):
        raise ValueError("release_status_missing")
    if len(h08_dispositions) != 1407 or sum(bool(row.get("any_valid_body")) for row in h08_dispositions) != 999 or sum(bool(row.get("latest_attempt_valid")) for row in h08_dispositions) != 991:
        raise ValueError("h08_accession_disposition_counts_mismatch")
    if sum(row.get("disposition") == "PRIOR_VALID_BODY_PRESERVED_LATEST_CONFLICT" for row in h08_dispositions) != 8:
        raise ValueError("h08_conflict_dispositions_mismatch")
    for row in h15_rows:
        value = row.get("consideration_value")
        if value and (value.get("currency") is not None or value.get("currency_status") != "AMBIGUOUS"):
            raise ValueError("h15_currency_inference")
    return {"verified": True, "artifact_count": len(manifest["artifacts"]), "H08_actual_eps_rows": len(h08_rows), "H08_text_cue_rows": len(h08_cue_rows), "H15_metadata_rows": sum(bool(row.get("admitted")) for row in h15_rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify_package() if args.verify else build_package(), indent=2, sort_keys=True))
