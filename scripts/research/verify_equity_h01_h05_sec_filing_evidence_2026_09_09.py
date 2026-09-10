"""Verify and summarize the frozen H01-H05 SEC filing evidence bundle.

This adapter is deliberately diagnostic.  It records filing identity, PIT
metadata, body hashes, and XBRL tag coverage without turning filing presence
into an admitted feature or an economic result.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from calendar import monthrange
from collections import Counter
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "data/research/external/equity_h01_h05_sec_filings_2026_09_09"
RECEIPT = BUNDLE / "acquisition_receipt.json"
OUTPUT = BUNDLE / "filing_fact_inventory.json"
CAUSAL_OUTPUT = BUNDLE / "causal_fact_inventory.json"
REQUIRED_TAGS = {
    "GrossProfit",
    "Assets",
    "StockholdersEquity",
    "NetIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfDividends",
    "DividendsCommonStock",
    "DividendsCommonStockCash",
    "PaymentsForRepurchaseOfCommonStock",
    "PaymentsForRepurchaseOfEquity",
    "StockRepurchasedDuringPeriodValue",
    "StockRepurchasedAndRetiredDuringPeriodValue",
    "CommonStockSharesOutstanding",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_receipt(path: Path = RECEIPT) -> bool:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        requests = receipt["requests"]
        if receipt.get("status") != "COMPLETE" or receipt.get("request_count") != len(requests):
            return False
        if receipt.get("download_count") != len(requests) or receipt.get("credential_fields_persisted") is not False:
            return False
        keys = set()
        for item in requests:
            key = (item["accession"], item["primary_document"])
            if key in keys or item["status_code"] != 200 or item.get("head_status_code") != 200:
                return False
            keys.add(key)
            if not item["url"].startswith("https://www.sec.gov/") or item.get("content_type") != "text/html":
                return False
            body = REPO / item["output"]
            if not body.is_file() or item["bytes"] != body.stat().st_size or item["sha256"] != sha256(body):
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def verify_catalog(path: Path = OUTPUT) -> bool:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        if catalog.get("status") != "COMPLETE":
            return False
        if catalog.get("request_count") != len(catalog.get("filings", [])):
            return False
        if catalog.get("causal_fact_inventory") != str(CAUSAL_OUTPUT.relative_to(REPO)) or catalog.get("causal_fact_inventory_sha256") != sha256(CAUSAL_OUTPUT):
            return False
        by_key = {(x["accession"], x["primary_document"]): x for x in receipt["requests"]}
        for item in catalog["filings"]:
            key = (item["accession"], item["primary_document"])
            source = by_key.get(key)
            if source is None or item["source_receipt"] != str(RECEIPT.relative_to(REPO)):
                return False
            body = REPO / item["body_path"]
            if not body.is_file() or item["body_sha256"] != sha256(body):
                return False
            if item["body_sha256"] != source.get("sha256"):
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


class _FactParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.counts: Counter[str] = Counter()
        self.contexts: Counter[str] = Counter()
        self.facts: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._current_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attrs_dict = {str(key).lower(): value for key, value in attrs}
        if self._current is not None:
            self._current["text"] += ""
        if "nonfraction" not in lowered:
            return
        name = str(attrs_dict.get("name", ""))
        if not name:
            return
        self.counts[name] += 1
        if name.rsplit(":", 1)[-1] in REQUIRED_TAGS:
            self.contexts[name] += 1
            self._current = {
                "name": name,
                "contextRef": str(attrs_dict.get("contextref", "")),
                "unitRef": str(attrs_dict.get("unitref", "")),
                "decimals": str(attrs_dict.get("decimals", "")),
                "scale": str(attrs_dict.get("scale", "0")),
                "sign": str(attrs_dict.get("sign", "")),
                "text": "",
            }
            self._current_tag = lowered

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and "nonfraction" in tag.lower():
            self.facts.append(dict(self._current))
            self._current = None
            self._current_tag = ""


def _context_map(text: str) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    contexts: dict[str, dict[str, Any]] = {}
    for match in re.finditer(
        r'<xbrli:context\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>(.*?)</xbrli:context\s*>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        body = match.group(2)
        identifier = re.search(r"<xbrli:identifier\b[^>]*>(.*?)</xbrli:identifier\s*>", body, re.I | re.S)
        instant = re.search(r"<xbrli:instant\b[^>]*>(.*?)</xbrli:instant\s*>", body, re.I | re.S)
        start = re.search(r"<xbrli:startDate\b[^>]*>(.*?)</xbrli:startDate\s*>", body, re.I | re.S)
        end = re.search(r"<xbrli:endDate\b[^>]*>(.*?)</xbrli:endDate\s*>", body, re.I | re.S)
        dimensions = [
            {
                "dimension": item.group(1),
                "member": re.sub(r"\s+", " ", item.group(2).strip()),
            }
            for item in re.finditer(
                r"<xbrldi:explicitMember\b[^>]*dimension=[\"']([^\"']+)[\"'][^>]*>(.*?)</xbrldi:explicitMember\s*>",
                body,
                re.I | re.S,
            )
        ]
        contexts[match.group(1)] = {
            "identifier": identifier.group(1).strip() if identifier else "",
            "instant": instant.group(1).strip() if instant else None,
            "start_date": start.group(1).strip() if start else None,
            "end_date": end.group(1).strip() if end else None,
            "dimensions": dimensions,
        }
    units: dict[str, str] = {}
    for match in re.finditer(
        r'<xbrli:unit\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>(.*?)</xbrli:unit\s*>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        measure = re.search(r"<xbrli:measure\b[^>]*>(.*?)</xbrli:measure\s*>", match.group(2), re.I | re.S)
        units[match.group(1)] = measure.group(1).strip() if measure else ""
    return contexts, units


def _numeric_value(raw: str, scale: str, sign: str) -> str | None:
    cleaned = unescape(raw).strip().replace(",", "")
    if not cleaned or cleaned in {"-", "—", "–", "N/A"}:
        return None
    negative_parentheses = cleaned.startswith("(") and cleaned.endswith(")")
    if negative_parentheses:
        cleaned = cleaned[1:-1]
    cleaned = cleaned.replace("$", "").replace("%", "").replace(" ", "")
    try:
        value = Decimal(cleaned)
        if negative_parentheses or sign.strip() == "-":
            value = -abs(value)
        exponent = int(scale or "0")
        value *= Decimal(10) ** exponent
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return format(value, "f")


def _decision_dates() -> list[str]:
    return [
        f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
        for year in range(2022, 2025)
        for month in range(1, 13)
    ]


def _tag_counts(path: Path) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str], set[str], int, list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    parser = _FactParser()
    parser.feed(text)
    parser.close()
    contexts, units = _context_map(text)
    undimensioned: Counter[str] = Counter()
    pit_covered: Counter[str] = Counter()
    identifiers: set[str] = set()
    for node in re.finditer(r"<(?:ix:)?nonfraction\b[^>]*>", text, re.I):
        attrs = {
            key.lower(): value
            for key, value in re.findall(r"([A-Za-z][A-Za-z0-9:_-]*)=[\"']([^\"']*)[\"']", node.group(0))
        }
        name = attrs.get("name", "")
        if name.rsplit(":", 1)[-1] not in REQUIRED_TAGS:
            continue
        context = contexts.get(attrs.get("contextref", ""))
        if context is None:
            continue
        identifiers.add(str(context["identifier"]))
        undimensioned[name] += 1
        period_end = str(context.get("instant") or context.get("end_date") or "")
        if not context["dimensions"] and period_end and period_end <= "2024-12-31":
            pit_covered[name] += 1
    return parser.counts, parser.contexts, undimensioned, pit_covered, identifiers, len(contexts), parser.facts, contexts, units


def _fact_unit_status(tag: str, measure: str) -> tuple[str, str]:
    normalized = measure.lower().replace("{", "").replace("}", "")
    if tag == "CommonStockSharesOutstanding":
        return ("PASS", "shares") if "share" in normalized else ("FAIL", "shares")
    return ("PASS", "USD") if normalized.endswith(":usd") or normalized == "usd" else ("FAIL", "USD")


def _fact_inventory() -> dict[str, Any]:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if not verify_receipt():
        raise RuntimeError("SEC_FILING_RECEIPT_VERIFY_FAILED")
    receipt_sha = sha256(RECEIPT)
    facts: list[dict[str, Any]] = []
    excluded = Counter[str]()
    admitted = Counter[str]()
    for source in receipt["requests"]:
        body_path = REPO / str(source["output"])
        _, _, _, _, _, _, parsed, contexts, units = _tag_counts(body_path)
        for ordinal, raw in enumerate(parsed, start=1):
            tag = raw["name"].rsplit(":", 1)[-1]
            context = contexts.get(raw["contextRef"], {})
            period_end = context.get("instant") or context.get("end_date")
            measure = units.get(raw["unitRef"], "")
            value = _numeric_value(raw["text"], raw["scale"], raw["sign"])
            accepted_date = str(source["accepted"])[:10]
            identity_status = "PASS" if str(context.get("identifier", "")).zfill(10) == str(source["issuer_cik"]).zfill(10) else "FAIL"
            dimension_status = "PASS" if not context.get("dimensions") else "EXCLUDED_DIMENSIONED"
            unit_status, expected_unit = _fact_unit_status(tag, measure)
            period_status = "PASS" if period_end and str(period_end) <= "2024-12-31" else "FAIL"
            pit_status = "PASS" if accepted_date <= "2024-12-31" and period_status == "PASS" else "FAIL"
            value_status = "PASS" if value is not None else "FAIL"
            amendment_status = "AMENDED_FORM" if str(source["form"]).endswith("/A") else "ORIGINAL_FORM"
            statuses = [identity_status, dimension_status, unit_status, period_status, pit_status, value_status]
            admission_status = "PIT_ELIGIBLE_EXACT_TAG" if all(item == "PASS" for item in statuses) else "EXCLUDED"
            if admission_status == "PIT_ELIGIBLE_EXACT_TAG":
                admitted[tag] += 1
            else:
                for reason, status in (
                    ("identity", identity_status),
                    ("dimension", dimension_status),
                    ("unit", unit_status),
                    ("period", period_status),
                    ("pit", pit_status),
                    ("value", value_status),
                ):
                    if status != "PASS":
                        excluded[f"{tag}:{reason}"] += 1
            facts.append(
                {
                    "fact_ordinal": ordinal,
                    "symbol": source["symbol"],
                    "issuer_cik": str(source["issuer_cik"]).zfill(10),
                    "form": source["form"],
                    "filing_date": source["filing_date"],
                    "report_date": source["report_date"],
                    "accepted_at": source["accepted"],
                    "accession": source["accession"],
                    "primary_document": source["primary_document"],
                    "source_url": source["url"],
                    "source_receipt": str(RECEIPT.relative_to(REPO)),
                    "source_receipt_sha256": receipt_sha,
                    "body_path": source["output"],
                    "body_sha256": sha256(body_path),
                    "tag": tag,
                    "qualified_name": raw["name"],
                    "context_ref": raw["contextRef"],
                    "context_identifier": context.get("identifier", ""),
                    "context_instant": context.get("instant"),
                    "context_start_date": context.get("start_date"),
                    "context_end_date": context.get("end_date"),
                    "dimensions": context.get("dimensions", []),
                    "unit_ref": raw["unitRef"],
                    "unit_measure": measure,
                    "expected_unit": expected_unit,
                    "decimals": raw["decimals"],
                    "scale": raw["scale"],
                    "sign": raw["sign"],
                    "value_decimal": value,
                    "amendment_status": amendment_status,
                    "identity_status": identity_status,
                    "dimension_status": dimension_status,
                    "unit_status": unit_status,
                    "period_status": period_status,
                    "pit_status": pit_status,
                    "admission_status": admission_status,
                    "runner_integration_status": "NOT_ADMITTED_TO_FROZEN_R12",
                }
            )
    grid: list[dict[str, Any]] = []
    symbols = sorted({str(item["symbol"]) for item in facts})
    for symbol in symbols:
        symbol_facts = [item for item in facts if item["symbol"] == symbol and item["admission_status"] == "PIT_ELIGIBLE_EXACT_TAG"]
        for decision_date in _decision_dates():
            available = [
                item
                for item in symbol_facts
                if str(item["accepted_at"])[:10] <= decision_date
                and str(item.get("context_instant") or item.get("context_end_date") or "") <= decision_date
            ]
            counts = Counter(str(item["tag"]) for item in available)
            grid.append(
                {
                    "symbol": symbol,
                    "decision_date": decision_date,
                    "available_exact_tag_counts": dict(sorted(counts.items())),
                    "field_evaluation": {
                        "H01": "FACTS_PRESENT_PRICE_SHARE_BASIS_REQUIRED" if counts["StockholdersEquity"] and counts["CommonStockSharesOutstanding"] else "EXACT_TAG_PAIR_INCOMPLETE",
                        "H02": "EXACT_TAG_PAIR_AVAILABLE" if counts["GrossProfit"] and counts["Assets"] else "EXACT_TAG_PAIR_INCOMPLETE",
                        "H03": "EXACT_TAGS_AVAILABLE" if counts["NetIncomeLoss"] and counts["NetCashProvidedByUsedInOperatingActivities"] and counts["Assets"] >= 2 else "EXACT_TAG_SET_INCOMPLETE",
                        "H04": "ANNUAL_10K_PERIOD_SELECTION_REQUIRED" if counts["Assets"] >= 2 else "ANNUAL_ASSET_PAIR_INCOMPLETE",
                        "H05": "PAYOUT_FACTS_PRESENT_PRICE_SHARE_BASIS_REQUIRED" if (counts["PaymentsOfDividendsCommonStock"] or counts["PaymentsOfDividends"] or counts["DividendsCommonStock"] or counts["DividendsCommonStockCash"]) else "PAYOUT_TAG_SET_INCOMPLETE",
                    },
                }
            )
    return {
        "schema": "equity_h01_h05_sec_causal_fact_inventory_v1",
        "status": "COMPLETE",
        "provider": "U.S. Securities and Exchange Commission EDGAR Archives",
        "scope": receipt["scope"],
        "source_receipt": str(RECEIPT.relative_to(REPO)),
        "source_receipt_sha256": receipt_sha,
        "fact_count": len(facts),
        "admitted_fact_count": sum(admitted.values()),
        "admitted_counts_by_tag": dict(sorted(admitted.items())),
        "excluded_counts_by_reason": dict(sorted(excluded.items())),
        "facts": facts,
        "decision_grid_evaluation": grid,
        "decision_grid_evaluation_status": "COMPLETE_DIAGNOSTIC_NO_FROZEN_ROW_MUTATION",
        "decision_grid_row_count": len(grid),
        "credential_fields_persisted": False,
        "admission_policy": "Exact frozen tags are evaluated fact by fact. Admission requires verified source/body, matching issuer identity, dimension-free context, expected unit, period end and accepted_at through 2024-12-31, and a finite numeric value. Fact eligibility does not itself complete a paired H01-H05 decision row.",
    }


def verify_causal_inventory(path: Path = CAUSAL_OUTPUT) -> bool:
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        by_key = {(x["accession"], x["primary_document"]): x for x in receipt["requests"]}
        facts = inventory["facts"]
        grid = inventory["decision_grid_evaluation"]
        if inventory.get("status") != "COMPLETE" or inventory.get("fact_count") != len(facts) or inventory.get("decision_grid_row_count") != len(grid) or len(grid) != 288:
            return False
        if inventory.get("source_receipt") != str(RECEIPT.relative_to(REPO)) or inventory.get("source_receipt_sha256") != sha256(RECEIPT):
            return False
        for fact in facts:
            if fact.get("tag") not in REQUIRED_TAGS:
                return False
            source = by_key.get((fact["accession"], fact["primary_document"]))
            body = REPO / fact["body_path"]
            if source is None or not body.is_file() or fact.get("body_sha256") != sha256(body):
                return False
            if fact.get("body_sha256") != source.get("sha256") or fact.get("source_receipt_sha256") != sha256(RECEIPT):
                return False
            if not fact.get("context_ref") or not fact.get("unit_ref") or fact.get("admission_status") not in {"PIT_ELIGIBLE_EXACT_TAG", "EXCLUDED"}:
                return False
        if any(set(item.get("field_evaluation", {})) != {"H01", "H02", "H03", "H04", "H05"} for item in grid):
            return False
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def build_catalog() -> dict[str, Any]:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if not verify_receipt():
        raise RuntimeError("SEC_FILING_RECEIPT_VERIFY_FAILED")
    filings: list[dict[str, Any]] = []
    for source in receipt["requests"]:
        body_path = REPO / str(source["output"])
        if source.get("status_code") != 200 or not body_path.is_file():
            raise RuntimeError("SEC_FILING_BODY_MISSING_OR_NON_200")
        counts, contexts, undimensioned, pit_covered, identifiers, context_count, _, _, _ = _tag_counts(body_path)
        filings.append(
            {
                "symbol": source["symbol"],
                "issuer_cik": source["issuer_cik"],
                "form": source["form"],
                "filing_date": source["filing_date"],
                "report_date": source["report_date"],
                "accepted": source["accepted"],
                "accession": source["accession"],
                "primary_document": source["primary_document"],
                "source_url": source["url"],
                "source_receipt": str(RECEIPT.relative_to(REPO)),
                "body_path": source["output"],
                "body_sha256": sha256(body_path),
                "required_tag_counts": dict(sorted(contexts.items())),
                "undimensioned_required_tag_counts": dict(sorted(undimensioned.items())),
                "pit_covered_required_tag_counts": dict(sorted(pit_covered.items())),
                "issuer_cik_identifiers": sorted(identifiers),
                "context_count": context_count,
                "gross_profit_tag_count": sum(
                    count for name, count in contexts.items() if name.rsplit(":", 1)[-1] == "GrossProfit"
                ),
                "tag_count": sum(counts.values()),
            }
        )
    return {
        "schema": "equity_h01_h05_sec_filing_fact_inventory_v1",
        "status": "COMPLETE",
        "provider": "U.S. Securities and Exchange Commission EDGAR Archives",
        "scope": receipt["scope"],
        "request_count": len(receipt["requests"]),
        "body_count": len(filings),
        "filings": filings,
        "causal_fact_inventory": str(CAUSAL_OUTPUT.relative_to(REPO)),
        "causal_fact_inventory_sha256": sha256(CAUSAL_OUTPUT) if CAUSAL_OUTPUT.exists() else None,
        "credential_fields_persisted": False,
        "admission_policy": "Filing facts are acquisition evidence only; H01-H05 admission still requires the frozen PIT, unit, period, identity, price/share, and breadth gates.",
    }


def main() -> int:
    catalog = build_catalog()
    causal = _fact_inventory()
    CAUSAL_OUTPUT.write_text(json.dumps(causal, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not verify_causal_inventory():
        print("SEC_CAUSAL_FACT_INVENTORY_VERIFY_FAILED")
        return 1
    catalog["causal_fact_inventory_sha256"] = sha256(CAUSAL_OUTPUT)
    OUTPUT.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not verify_catalog():
        print("SEC_FILING_CATALOG_VERIFY_FAILED")
        return 1
    print(f"SEC_FILING_CATALOG_COMPLETE filings={len(catalog['filings'])} output={OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
