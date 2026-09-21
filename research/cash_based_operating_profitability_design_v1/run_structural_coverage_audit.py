#!/usr/bin/env python3
"""Counts-only CbOP source pilot; never reads market bars or future returns.

Company Facts is used only as an accession-linked discovery layer.  A row is
not declared economically scoreable here: raw filing contexts, statement
relationships, custom tags, and security identity remain authoritative gates.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/research/external/cbop_design_v1/sample"
RAW = ROOT / "data/research/external/cbop_design_v1/raw_latest"
OUTPUT = Path(__file__).resolve().with_name("STRUCTURAL_COVERAGE_AUDIT.json")

SAMPLE = {
    "0000012927": "BA",
    "0000019617": "JPM",
    "0000040545": "GE",
    "0000050863": "INTC",
    "0000104169": "WMT",
    "0000320193": "AAPL",
    "0000789019": "MSFT",
    "0000796343": "ADBE",
    "0000831001": "C",
    "0000909832": "COST",
    "0001018724": "AMZN",
    "0001045810": "NVDA",
    "0001326801": "META",
    "0001652044": "GOOGL",
}

FLOW_FAMILIES = {
    "revenue": {
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
    },
    "cogs": {"CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"},
    "sga_combined": {"SellingGeneralAndAdministrativeExpense"},
    "selling": {"SellingAndMarketingExpense"},
    "general_admin": {"GeneralAndAdministrativeExpense"},
    "rd": {"ResearchAndDevelopmentExpense"},
}

INSTANT_FAMILIES = {
    "receivables": {"AccountsReceivableNetCurrent"},
    "inventory": {"InventoryNet"},
    "prepaids": {"PrepaidExpenseCurrent"},
    "deferred_revenue": {
        "DeferredRevenueCurrent",
        "ContractWithCustomerLiabilityCurrent",
    },
    "accounts_payable": {"AccountsPayableCurrent"},
    "accrued_expenses": {"AccruedLiabilitiesCurrent"},
    "lagged_assets": {"Assets"},
}

RAW_ACCESSION_RE = re.compile(r"^(?P<accession>\d{18})_")
XMLNS_RE = re.compile(r'xmlns:(?P<prefix>us-gaap|dei)="(?P<uri>[^"]+)"')


class InlineFactParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.names: set[str] = set()
        self._capture: str | None = None
        self._parts: list[str] = []
        self.values: dict[str, set[str]] = defaultdict(set)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        name = attributes.get("name")
        if name:
            self.names.add(name)
        if tag.lower() == "ix:nonnumeric" and name in {"dei:TradingSymbol", "dei:Security12bTitle"}:
            self._capture = name
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "ix:nonnumeric" and self._capture:
            value = " ".join("".join(self._parts).split())
            if value:
                self.values[self._capture].add(value)
            self._capture = None
            self._parts = []


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def accession_with_dashes(value: str) -> str:
    return f"{value[:10]}-{value[10:12]}-{value[12:]}"


def filing_metadata(submission: dict[str, Any], issuer_cik: str) -> dict[str, dict[str, Any]]:
    recent = submission.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    result: dict[str, dict[str, Any]] = {}
    for index, accession in enumerate(accessions):
        if not accession:
            continue
        result[str(accession)] = {
            key: values[index] if isinstance(values, list) and index < len(values) else None
            for key, values in recent.items()
        }
        result[str(accession)]["issuer_cik"] = issuer_cik
    return result


def taxonomy_versions(path: Path) -> dict[str, str] | None:
    if path.suffix != ".htm":
        return None
    declarations = dict(XMLNS_RE.findall(path.read_text(encoding="utf-8", errors="replace")[:100_000]))
    return declarations or None


def receipt(path: Path, metadata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    relative = str(path.relative_to(ROOT))
    retrieved_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    common: dict[str, Any] = {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "retrieved_at_utc": retrieved_at,
        "cache_mtime_utc": retrieved_at,
    }
    if path.parent == CACHE:
        cik = path.stem.rsplit("_", 1)[-1]
        if path.name.startswith("companyfacts_"):
            common.update(
                {
                    "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                    "source_role": "aggregate_discovery_only",
                    "cik": cik,
                    "accession": None,
                    "form": None,
                    "filed": None,
                    "accepted": None,
                    "taxonomy_versions": "multiple_historical_taxonomies_in_aggregate",
                }
            )
        else:
            common.update(
                {
                    "source_url": f"https://data.sec.gov/submissions/CIK{cik}.json",
                    "source_role": "submission_inventory_discovery_only",
                    "cik": cik,
                    "accession": None,
                    "form": None,
                    "filed": None,
                    "accepted": None,
                    "taxonomy_versions": None,
                }
            )
        return common

    match = RAW_ACCESSION_RE.match(path.name)
    if match is None:
        raise ValueError(f"raw cache filename does not encode an accession: {path.name}")
    compact = match.group("accession")
    accession = accession_with_dashes(compact)
    row = metadata.get(accession, {})
    cik = str(row.get("issuer_cik") or "")
    if not cik:
        raise ValueError(f"issuer CIK missing from submissions metadata for {accession}")
    archive_base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}"
    source_url = f"{archive_base}/index.json" if path.name.endswith("_index.json") else f"{archive_base}/{path.name.split('_', 1)[1]}"
    common.update(
        {
            "source_url": source_url,
            "source_role": "raw_accession_authority" if path.suffix == ".htm" else "raw_accession_directory",
            "cik": cik,
            "accession": accession,
            "form": row.get("form"),
            "filed": row.get("filingDate"),
            "accepted": row.get("acceptanceDateTime"),
            "report_date": row.get("reportDate"),
            "taxonomy_versions": taxonomy_versions(path),
        }
    )
    return common


def context_dates(root: ET.Element, context_id: str) -> dict[str, Any]:
    for element in root.iter():
        if not element.tag.endswith("context") or element.attrib.get("id") != context_id:
            continue
        values: dict[str, Any] = {"context_ref": context_id, "dimensions": []}
        for child in element.iter():
            local = child.tag.rsplit("}", 1)[-1]
            if local in {"startDate", "endDate", "instant"}:
                values[local] = (child.text or "").strip()
            elif local == "identifier":
                values["entity_identifier"] = (child.text or "").strip()
                values["entity_identifier_scheme"] = child.attrib.get("scheme")
            elif local in {"explicitMember", "typedMember"}:
                values["dimensions"].append("".join(child.itertext()).strip())
        return values
    raise ValueError(f"missing context {context_id}")


def normalized_usd_millions(element: ET.Element) -> int | float:
    lexical = "".join(element.itertext()).strip().replace(",", "")
    value = float(lexical) * (10 ** int(element.attrib.get("scale", "0")))
    if element.attrib.get("sign") == "-":
        value = -value
    millions = value / 1_000_000
    return int(millions) if millions.is_integer() else millions


def apple_reconstruction_sample() -> dict[str, Any]:
    """Reconcile one raw filing far enough to prove the fail-closed formula gate."""

    path = RAW / "000032019325000079_aapl-20250927.htm"
    root = ET.parse(path).getroot()
    wanted = {
        "revenue": ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "c-1"),
        "cogs": ("us-gaap:CostOfGoodsAndServicesSold", "c-1"),
        "reported_sga_ex_rd": ("us-gaap:SellingGeneralAndAdministrativeExpense", "c-1"),
        "rd": ("us-gaap:ResearchAndDevelopmentExpense", "c-1"),
        "receivables_t": ("us-gaap:AccountsReceivableNetCurrent", "c-20"),
        "receivables_t_minus_1": ("us-gaap:AccountsReceivableNetCurrent", "c-21"),
        "inventory_t": ("us-gaap:InventoryNet", "c-20"),
        "inventory_t_minus_1": ("us-gaap:InventoryNet", "c-21"),
        "deferred_revenue_total_t": ("us-gaap:ContractWithCustomerLiability", "c-20"),
        "deferred_revenue_total_t_minus_1": ("us-gaap:ContractWithCustomerLiability", "c-21"),
        "accounts_payable_t": ("us-gaap:AccountsPayableCurrent", "c-20"),
        "accounts_payable_t_minus_1": ("us-gaap:AccountsPayableCurrent", "c-21"),
        "lagged_assets": ("us-gaap:Assets", "c-21"),
        "other_accrued_liabilities_diagnostic_t": ("us-gaap:OtherAccruedLiabilitiesCurrent", "c-20"),
        "other_accrued_liabilities_diagnostic_t_minus_1": ("us-gaap:OtherAccruedLiabilitiesCurrent", "c-21"),
    }
    selected: dict[str, dict[str, Any]] = {}
    for label, (concept, context_ref) in wanted.items():
        matches = [
            element
            for element in root.iter()
            if element.attrib.get("name") == concept and element.attrib.get("contextRef") == context_ref
        ]
        if not matches:
            raise ValueError(f"missing AAPL reconstruction fact {concept} {context_ref}")
        normalized_matches = [normalized_usd_millions(match) for match in matches]
        duplicate_consistent = len(set(normalized_matches)) == 1
        if not duplicate_consistent:
            raise ValueError(f"inconsistent duplicate AAPL facts {concept} {context_ref}: {normalized_matches}")
        element = matches[0]
        selected[label] = {
            "concept": concept,
            "fact_id": element.attrib.get("id"),
            "matching_fact_count": len(matches),
            "matching_fact_ids": [match.attrib.get("id") for match in matches],
            "matching_raw_lexical_values": ["".join(match.itertext()).strip() for match in matches],
            "matching_normalized_usd_millions": normalized_matches,
            "duplicate_values_exactly_consistent": duplicate_consistent,
            "context": context_dates(root, context_ref),
            "unit_ref": element.attrib.get("unitRef"),
            "raw_lexical": "".join(element.itertext()).strip(),
            "scale": int(element.attrib.get("scale", "0")),
            "decimals": element.attrib.get("decimals"),
            "presentation_sign": element.attrib.get("sign", "+"),
            "normalized_usd_millions": normalized_usd_millions(element),
        }

    value = {key: row["normalized_usd_millions"] for key, row in selected.items()}
    operating_profit = value["revenue"] - value["cogs"] - value["reported_sga_ex_rd"]
    delta_receivables = value["receivables_t"] - value["receivables_t_minus_1"]
    delta_inventory = value["inventory_t"] - value["inventory_t_minus_1"]
    delta_deferred_revenue = value["deferred_revenue_total_t"] - value["deferred_revenue_total_t_minus_1"]
    delta_accounts_payable = value["accounts_payable_t"] - value["accounts_payable_t_minus_1"]
    partial_numerator = (
        operating_profit - delta_receivables - delta_inventory + delta_deferred_revenue + delta_accounts_payable
    )
    return {
        "issuer": "Apple Inc.",
        "cik": "0000320193",
        "accession": "0000320193-25-000079",
        "form": "10-K",
        "filed": "2025-10-31",
        "accepted": "2025-10-31T10:01:26.000Z",
        "report_date": "2025-09-27",
        "source_file": str(path.relative_to(ROOT)),
        "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
        "taxonomy_versions": taxonomy_versions(path),
        "facts": selected,
        "arithmetic_usd_millions": {
            "xsga_proxy": value["reported_sga_ex_rd"] + value["rd"],
            "xrd": value["rd"],
            "xsga_minus_xrd": value["reported_sga_ex_rd"],
            "operating_profit": operating_profit,
            "delta_receivables": delta_receivables,
            "delta_inventory": delta_inventory,
            "delta_deferred_revenue_total": delta_deferred_revenue,
            "delta_accounts_payable": delta_accounts_payable,
            "partial_numerator_before_prepaids_and_accrued_expenses": partial_numerator,
            "lagged_assets": value["lagged_assets"],
            "cbop": None,
        },
        "semantic_findings": {
            "sga_rd": "R&D is a sibling expense outside reported SG&A; XSGA is reconstructed as reported SG&A plus R&D, so XSGA-XRD equals reported SG&A and R&D is not double-counted.",
            "deferred_revenue": "Total ContractWithCustomerLiability is used directly for DRC+DRLT, avoiding current/noncurrent double counting.",
            "prepaids": "No narrow PrepaidExpenseCurrent fact; broader other-current-assets facts are not substituted.",
            "accrued_expenses": "OtherAccruedLiabilitiesCurrent is retained as a diagnostic but rejected as an exact XACC equivalent.",
        },
        "complete_cbop_reconstruction": False,
        "fail_closed_reasons": [
            "missing exact prepaid-expense balance for both instants",
            "no accession-specific proof that OtherAccruedLiabilitiesCurrent is an exact XACC equivalent",
        ],
        "future_returns_joined": False,
    }


def days(row: dict[str, Any]) -> int | None:
    try:
        return (date.fromisoformat(str(row["end"])) - date.fromisoformat(str(row["start"]))).days
    except (KeyError, TypeError, ValueError):
        return None


def accession_acceptance(submission: dict[str, Any]) -> dict[str, str]:
    recent = submission.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    accepted = recent.get("acceptanceDateTime", [])
    return {str(accn): str(timestamp) for accn, timestamp in zip(accessions, accepted) if accn and timestamp}


def observations(payload: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    result: list[tuple[str, str, dict[str, Any]]] = []
    for namespace, facts in payload.get("facts", {}).items():
        for tag, definition in facts.items():
            for unit, rows in definition.get("units", {}).items():
                for row in rows:
                    result.append((f"{namespace}:{tag}", str(unit), row))
    return result


def has_annual_flow(rows: list[tuple[str, str, dict[str, Any]]], tags: set[str]) -> bool:
    for qualified, unit, row in rows:
        if qualified.removeprefix("us-gaap:") not in tags or unit != "USD":
            continue
        duration = days(row)
        if duration is not None and 300 <= duration <= 430:
            return True
    return False


def has_comparative_instants(rows: list[tuple[str, str, dict[str, Any]]], tags: set[str]) -> bool:
    ends = {
        str(row.get("end"))
        for qualified, unit, row in rows
        if qualified.removeprefix("us-gaap:") in tags
        and unit == "USD"
        and not row.get("start")
        and row.get("end")
    }
    return len(ends) >= 2


def main() -> None:
    files = sorted(CACHE.glob("*.json")) + sorted(RAW.glob("*"))
    raw_metadata: dict[str, dict[str, Any]] = {}
    for cik in SAMPLE:
        submission = json.loads((CACHE / f"submissions_{cik}.json").read_text(encoding="utf-8"))
        raw_metadata.update(filing_metadata(submission, cik))
    receipts = [receipt(path, raw_metadata) for path in files]

    issuer_rows: list[dict[str, Any]] = []
    accession_rows: list[dict[str, Any]] = []
    component_missing: Counter[str] = Counter()
    by_decision_year: dict[int, Counter[str]] = defaultdict(Counter)

    for cik, symbol in SAMPLE.items():
        facts_path = CACHE / f"companyfacts_{cik}.json"
        submissions_path = CACHE / f"submissions_{cik}.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        submissions = json.loads(submissions_path.read_text(encoding="utf-8"))
        accepted = accession_acceptance(submissions)
        sic = str(submissions.get("sic", ""))
        issuer_rows.append(
            {
                "cik": cik,
                "symbol_current_for_sample_label_only": symbol,
                "entity_name": facts.get("entityName"),
                "current_submissions_sic": sic,
                "current_sic_financial": sic.isdigit() and 6000 <= int(sic) <= 6999,
            }
        )

        grouped: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
        for qualified, unit, row in observations(facts):
            if row.get("form") != "10-K" or not row.get("accn"):
                continue
            filed = str(row.get("filed", ""))
            if not ("2013-01-01" <= filed <= "2026-06-30"):
                continue
            grouped[str(row["accn"])].append((qualified, unit, row))

        for accn, rows in sorted(grouped.items()):
            filed_values = sorted({str(row.get("filed")) for _, _, row in rows if row.get("filed")})
            fy_values = sorted({str(row.get("fy")) for _, _, row in rows if row.get("fy")})
            filed = filed_values[-1] if filed_values else None
            fiscal_year = int(fy_values[-1]) if fy_values and fy_values[-1].isdigit() else None
            flow = {name: has_annual_flow(rows, tags) for name, tags in FLOW_FAMILIES.items()}
            instant = {name: has_comparative_instants(rows, tags) for name, tags in INSTANT_FAMILIES.items()}
            sga_candidate = flow["sga_combined"] or (flow["selling"] and flow["general_admin"])
            required = {
                "revenue": flow["revenue"],
                "cogs": flow["cogs"],
                "sga_candidate": sga_candidate,
                "rd_explicit": flow["rd"],
                **instant,
            }
            for component, present in required.items():
                if not present:
                    component_missing[component] += 1
            mechanical_complete = all(required.values())
            decision_year = fiscal_year + 1 if fiscal_year is not None else None
            if decision_year is not None:
                by_decision_year[decision_year]["10k_accessions"] += 1
                by_decision_year[decision_year]["mechanical_standard_tag_complete"] += int(mechanical_complete)
            accession_rows.append(
                {
                    "cik": cik,
                    "sample_symbol_label": symbol,
                    "accession": accn,
                    "filed": filed,
                    "acceptance_timestamp_from_current_submissions": accepted.get(accn),
                    "fiscal_year": fiscal_year,
                    "paper_style_decision_year": decision_year,
                    "component_candidates": required,
                    "mechanical_standard_tag_complete": mechanical_complete,
                    "authoritative_raw_semantic_scoreable": None,
                }
            )

    raw_html = sorted(RAW.glob("*.htm"))
    raw_component_counts: Counter[str] = Counter()
    raw_filing_rows: list[dict[str, Any]] = []
    raw_families = {**FLOW_FAMILIES, **INSTANT_FAMILIES}
    for path in raw_html:
        parser = InlineFactParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        names = parser.names
        component_presence = {
            component: any(f"us-gaap:{tag}" in names for tag in tags)
            for component, tags in raw_families.items()
        }
        sga_candidate_present = component_presence["sga_combined"] or (
            component_presence["selling"] and component_presence["general_admin"]
        )
        required_component_presence = {
            "revenue": component_presence["revenue"],
            "cogs": component_presence["cogs"],
            "sga_candidate": sga_candidate_present,
            "rd_explicit": component_presence["rd"],
            **{name: component_presence[name] for name in INSTANT_FAMILIES},
        }
        for component, present in component_presence.items():
            raw_component_counts[component] += int(present)
        trading_symbols = sorted(parser.values["dei:TradingSymbol"])
        security_titles = sorted(parser.values["dei:Security12bTitle"])
        raw_filing_rows.append(
            {
                "file": str(path.relative_to(ROOT)),
                "component_standard_tag_presence": component_presence,
                "required_component_standard_tag_presence": required_component_presence,
                "dei_trading_symbols": trading_symbols,
                "dei_security_12b_titles": security_titles,
                "all_required_standard_tag_families_present": all(required_component_presence.values()),
                "semantic_context_relationship_validation_complete": False,
            }
        )
    reconstruction_sample = apple_reconstruction_sample()
    payload = {
        "schema_version": 1,
        "status": "PILOT_ONLY_INSUFFICIENT_FOR_ECONOMIC_AUTHORIZATION",
        "scope": "counts_and_source_semantics_only_no_market_bars_no_forward_returns",
        "sample_rule": "Fourteen predeclared large issuers spanning standard, R&D, non-calendar/52-53-week, restructuring, and current-SIC financial cases; selection is not outcome-based and is not a universe estimate.",
        "source_authority": {
            "companyfacts_role": "accession-linked standard-tag discovery only",
            "submissions_role": "accession inventory and acceptance-time discovery only",
            "raw_accession_role": "authoritative filing facts, contexts, relationships, SIC, and DEI security identity",
            "frames_used": False,
        },
        "sample_summary": {
            "issuers": len(issuer_rows),
            "current_sic_financial_issuers": sum(row["current_sic_financial"] for row in issuer_rows),
            "companyfacts_10k_accessions_2013_to_2026q2": len(accession_rows),
            "accessions_with_acceptance_in_current_submissions": sum(
                row["acceptance_timestamp_from_current_submissions"] is not None for row in accession_rows
            ),
            "mechanical_standard_tag_complete_accessions": sum(
                row["mechanical_standard_tag_complete"] for row in accession_rows
            ),
            "authoritative_raw_semantic_scoreable_accessions": None,
            "raw_latest_ixbrl_documents_cached": len(raw_html),
            "raw_latest_documents_with_all_required_standard_tag_families": sum(
                row["all_required_standard_tag_families_present"] for row in raw_filing_rows
            ),
        },
        "missing_component_counts": dict(sorted(component_missing.items())),
        "annual_discovery_counts": {
            str(year): dict(sorted(counts.items())) for year, counts in sorted(by_decision_year.items())
        },
        "issuers": issuer_rows,
        "accessions": accession_rows,
        "raw_latest_component_presence_counts": dict(sorted(raw_component_counts.items())),
        "raw_latest_filings": raw_filing_rows,
        "formula_reconstruction_samples": [reconstruction_sample],
        "limitations": [
            "Mechanical tag presence is not CbOP semantic equivalence and is never a scoreability verdict.",
            "Company Facts omits custom and dimensional facts and cannot prove statement/calculation relationships.",
            "A missing standard tag is unknown, never zero.",
            "Current Submissions SIC is not historical PIT SIC; raw accession headers are required.",
            "Current sample ticker labels are not historical CIK-to-security mapping evidence; as-filed DEI class/title/symbol plus entity lifetime is required.",
            "A full accession-package census is intentionally not authorized by this pilot result because external long-only/concentration gates independently reject the design.",
        ],
        "cache_receipts": receipts,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
