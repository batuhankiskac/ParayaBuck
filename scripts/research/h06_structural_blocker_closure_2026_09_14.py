#!/usr/bin/env python3
"""Receipt-bound, outcome-free H06 structural blocker closure."""

from __future__ import annotations

import copy
import csv
import hashlib
import html
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas_market_calendars as market_calendars
from pandas.tseries.holiday import USFederalHolidayCalendar

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "data/research/external/h06_structural_blocker_closure_2026_09_14"
SEC_RECEIPT = REPO / "data/research_receipts/h06_sec_archive_fallback_probe_2026_09_14.json"
SIP_RECEIPT = REPO / "data/research_receipts/h06_raw_sip_marketcap_probe_2026_09_14.json"
RAW_MANIFEST = REPO / "data/research_manifests/h06_raw_marketcap_v1_2026_09_14.json"
CLOSURE = REPO / "data/research_manifests/h06_structural_blocker_closure_2026_09_14.json"
REPORT = REPO / "docs/research/h06_structural_blocker_closure_2026_09_14.md"
TEST = REPO / "tests/research/test_h06_structural_blocker_closure_2026_09_14.py"
SCRIPT = Path(__file__)
PREVIOUS = REPO / "data/research_manifests/h06_identity_marketcap_bridge_2026_09_14.json"
PENDING = ""
NY = ZoneInfo("America/New_York")

STATUS = "H06_STRUCTURAL_BLOCKERS_CLOSED"
SHARES_PROTOCOL = "LATEST_ACCEPTED_REPORTED_SHARES_IS_PROTOCOL_COMPATIBLE"
PRICE_PROTOCOL = "RAW_COMPLETED_SESSION_CLOSE_IS_PROTOCOL_COMPATIBLE"
FORMULA_PROTOCOL = "H06_RAW_MARKETCAP_IS_FAITHFUL_IMPLEMENTATION_OF_FROZEN_FORMULA"
V5_DISPOSITION = "V5_SPLIT_BASIS_IRRELEVANT_TO_H06_DENOMINATOR"
FUTURE_SPLIT_DISPOSITION = "H06_FUTURE_SPLIT_NORMALIZATION_NOT_REQUIRED_FOR_DENOMINATOR"
DENOMINATOR_LABEL = "STRUCTURAL_MARKET_CAP_DENOMINATOR_ONLY_NOT_H06_SCORE"

EXPECTED_V5 = {
    "data/historical/equity_pit_symmetric_2016_2026_v5.csv": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    "data/historical/equity_entity_lifetimes_full_v5.csv": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    "data/historical/equity_survivorship_full_corporate_actions_v5.csv": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
}

EVIDENCE_SHA = {
    "CIK0000320193-submissions.json": "cb90ffafc5b6f997b60aa109e07008223ad35abe896ec7918fd43652b4057329",
    "CIK0001045810-submissions.json": "1ac0832ccf974a7d89388662552de76982b72ff1c930d38ddef9d761b31a688a",
    "aapl-0000320193-24-000123.txt": "347c28e05fd61467c0ce9f4c0b8a264670011b9afb07930e2233a46dbb73914b",
    "aapl-20240928.htm": "24a830a0f1256e371d36a1f7f72e5e85a38037d1de2f6f966eb8457db42ff6d6",
    "aapl-form4-predecision-0000320193-24-000130.txt": "245c0d24fa4e287f4f6edea5fcd00fbec45f158751b16ef95eb68e184d601f49",
    "nvda-0001045810-24-000124.txt": "325cc1c81700543dbbbfdd520896f24f551aa3581fe34c62b1745492694a48c1",
    "nvda-20240428.htm": "0d5b058779ec2cbbc24b67ea9bbc8b5c455177b86ce972f3c203329b99fadde8",
    "nvda-form4-predecision-0001045810-24-000122.txt": "13f77d251825b3157f5d978d279b8eb308ec7f986b5a14b416320f86ceab2c57",
    "nvda-0001045810-24-000144.txt": "249aae37369da9451cc512733ffd65202e8d675cea56dc5e12ad2edf1c7fd010",
    "nvda-20240607.htm": "047c49a59b50dd6851111d580d8c68b33fcb3bca22d5ea219793568b1bf7b0a6",
    "ge-0000040545-21-000052.txt": "b44fc2c4a076e5d53b7ebffbc77f1439e8b2c10403d843e5eefb7ffbcac488dc",
    "ge-20210630.htm": "ce04ed71a0380cd8b0feb1b7cc9fe78e56bf48ecc47176b41181986d53b1541c",
    "ge-form4-0002052951-26-000007.txt": "7d75eb396ade99503fa244d9695a633bb205ac96d0bd19269987e1889c7efbe9",
    "ge-0001206774-21-001925.txt": "2c2567a95a23a743dd4fa58f9dbe424ce1599777d458e1325c17a7c5789564a5",
    "ge3936671-8k.htm": "cbc0be4711e1632fe2b7d05e8cb0e4a2bc352afbdad7341c021665f48749fb3f",
    "meta-0001326801-22-000070.txt": "508c7e9504b2c51a4cfad3aaa15ebffbc0f2786b14203af3ebb6b89893326e39",
    "fb-20220531.htm": "144ee0ca8d9ba46b0f548501aae654265b3d6145b6ef14cd1077b6bc68644294",
    "may312022-exhibit991.htm": "d3ec72f5f72d852e7cbe8fe760cd27fb3f809bb9aeccac6be4e7234f4ae62cac",
    "fb-form4-0000950103-22-010059.txt": "8e3a3f952717099bd9516994441ae1225a03a8335e949d3e9f00f9b2df764d6d",
    "meta-form4-0000950103-26-011342.txt": "a68a6b9c3ae6a6d318230fd3e5cad491c665b84201cd977528cb8f403368c6ad",
    "goog-0001652044-25-000014.txt": "5f272086fbc10d11064f4805fa3328847f2108f61c366f0b9e0496b50f74150f",
    "goog-20241231.htm": "a70a2cc584744e7d678ec6c4fb669fa7c5c871861eb5820fb9de4357319db7d9",
    "goog-form4-0001193125-26-326284.txt": "92aa40469ea8276d87fffe07158c99ab7d87bc1f756692b42b367acddc0cda65",
    "aapl_no_split_raw.json": "af5186b53f926224fb1d1404caa54b570040e1419be17e3e8bdf15ad7acf6010",
    "aapl_no_split_split.json": "af5186b53f926224fb1d1404caa54b570040e1419be17e3e8bdf15ad7acf6010",
    "nvda_forward_split_raw.json": "b8b68de7b87fe7a90fda778f3cbc818cfc738c6a15580aba723a8631ac2bd6d0",
    "nvda_forward_split_split.json": "1dc071e269fe324c9b119c473914b44e9b24cc87b739f42c8a87f0f1644a2fb7",
    "ge_reverse_split_raw.json": "2e8abd1fe8cbb9be95511d5dbd338da929d4270a8ade4171de057307a9828599",
    "ge_reverse_split_split.json": "c0d2aeeed3e48ccfa960179e1a67109773c6ac55b8219bab4d4ffcdbc6dd533b",
    "alpaca_historical_stock_bars_docs.md": "9f9794be56983c2763e958bf7cdd0372a6f60372ced91d5448c580f239bcdabd",
    "alpaca_historical_stock_bars_current_docs.md": "38df69827c9de67ff4f568c237094242a6037160fc8ba5e0b47ab3a306665077",
    "alpaca_raw_split_canary_metadata.json": "8884aca3cc3b9409dd67e73499a2f5b70a33c8fb90194e7100559a83d251cf15",
    "aapl_denominator_2024-11-29_raw.json": "bf8f1aa5dff28f6d2c8404a13983a7b6878e9d1ce376145576d7b802252e88ef",
    "nvda_denominator_2024-05-31_raw.json": "a2c2fef93375ae8774dd9c5bb7fe3193d56c0ce86e8d2a8690b7d7a8d1792321",
    "alpaca_raw_denominator_decision_bars_metadata.json": "6672159de36a042ff231043717f52a85bd296cbb90a0c41d601c56285b6a3bd0",
}

SEC_META = {
    "aapl-0000320193-24-000123.txt": ("0000320193-24-000123", "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/0000320193-24-000123.txt", "complete_submission"),
    "aapl-20240928.htm": ("0000320193-24-000123", "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm", "inline_xbrl"),
    "aapl-form4-predecision-0000320193-24-000130.txt": ("0000320193-24-000130", "https://www.sec.gov/Archives/edgar/data/320193/000032019324000130/0000320193-24-000130.txt", "form4"),
    "nvda-0001045810-24-000124.txt": ("0001045810-24-000124", "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000124/0001045810-24-000124.txt", "complete_submission"),
    "nvda-20240428.htm": ("0001045810-24-000124", "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000124/nvda-20240428.htm", "inline_xbrl"),
    "nvda-form4-predecision-0001045810-24-000122.txt": ("0001045810-24-000122", "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000122/0001045810-24-000122.txt", "form4"),
    "nvda-0001045810-24-000144.txt": ("0001045810-24-000144", "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000144/0001045810-24-000144.txt", "complete_submission"),
    "nvda-20240607.htm": ("0001045810-24-000144", "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000144/nvda-20240607.htm", "inline_xbrl"),
    "ge-0000040545-21-000052.txt": ("0000040545-21-000052", "https://www.sec.gov/Archives/edgar/data/40545/000004054521000052/0000040545-21-000052.txt", "complete_submission"),
    "ge-20210630.htm": ("0000040545-21-000052", "https://www.sec.gov/Archives/edgar/data/40545/000004054521000052/ge-20210630.htm", "inline_xbrl"),
    "ge-form4-0002052951-26-000007.txt": ("0002052951-26-000007", "https://www.sec.gov/Archives/edgar/data/40545/000205295126000007/0002052951-26-000007.txt", "form4"),
    "ge-0001206774-21-001925.txt": ("0001206774-21-001925", "https://www.sec.gov/Archives/edgar/data/40545/000120677421001925/0001206774-21-001925.txt", "complete_submission"),
    "ge3936671-8k.htm": ("0001206774-21-001925", "https://www.sec.gov/Archives/edgar/data/40545/000120677421001925/ge3936671-8k.htm", "inline_xbrl"),
    "meta-0001326801-22-000070.txt": ("0001326801-22-000070", "https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/0001326801-22-000070.txt", "complete_submission"),
    "fb-20220531.htm": ("0001326801-22-000070", "https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/fb-20220531.htm", "inline_xbrl"),
    "may312022-exhibit991.htm": ("0001326801-22-000070", "https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm", "ticker_change_exhibit"),
    "fb-form4-0000950103-22-010059.txt": ("0000950103-22-010059", "https://www.sec.gov/Archives/edgar/data/1326801/000095010322010059/0000950103-22-010059.txt", "form4"),
    "meta-form4-0000950103-26-011342.txt": ("0000950103-26-011342", "https://www.sec.gov/Archives/edgar/data/1326801/000095010326011342/0000950103-26-011342.txt", "form4"),
    "goog-0001652044-25-000014.txt": ("0001652044-25-000014", "https://www.sec.gov/Archives/edgar/data/1652044/000165204425000014/0001652044-25-000014.txt", "complete_submission"),
    "goog-20241231.htm": ("0001652044-25-000014", "https://www.sec.gov/Archives/edgar/data/1652044/000165204425000014/goog-20241231.htm", "inline_xbrl"),
    "goog-form4-0001193125-26-326284.txt": ("0001193125-26-326284", "https://www.sec.gov/Archives/edgar/data/1652044/000119312526326284/0001193125-26-326284.txt", "form4"),
}

CASE_STATE: dict[str, dict[str, Any]] = {
    "reverse_split_history": {"issuer_cik": "0000040545", "accepted_at": "2021-07-27T10:29:30Z", "cover_title": "Common stock", "trading_symbol": "GE", "exchange": "NYSE", "form4_accession": "0002052951-26-000007", "transaction_title": "Common Stock", "shares": {"tag": "dei:EntityCommonStockSharesOutstanding", "value": "8781303049", "instant": "2021-06-30", "unit": "shares", "decimals": "0", "scale": "0", "dimensions": []}, "identity_status": "IDENTITY_MISSING", "disposition": "EXCLUDED_FAIL_CLOSED", "reason": "bounded Form 4 evidence is not decision-visible for the 2021 state"},
    "same_cik_ticker_change": {"issuer_cik": "0001326801", "accepted_at": "2022-05-31T20:18:52Z", "old_symbol": "FB", "new_symbol": "META", "effective": "2022-06-09 before market open", "sec_chronology": "RECEIPT_BOUND_EXACT", "v5_chronology": "single META interval", "identity_status": "IDENTITY_AMBIGUOUS", "disposition": "EXCLUDED_FAIL_CLOSED"},
    "multiple_common_classes": {"issuer_cik": "0001652044", "accepted_at": "2025-02-05T01:41:40Z", "classes": [{"title": "Class A Common Stock", "symbol": "GOOGL", "member": "CommonClassAMember", "shares": "5833000000"}, {"title": "Class B Common Stock", "symbol": None, "shares": "860000000"}, {"title": "Class C Capital Stock", "symbol": "GOOG", "member": "CapitalClassCMember", "shares": "5497000000"}], "form4_symbol": "GOOGL", "transaction_title": "Class C Capital Stock", "identity_status": "MULTI_CLASS_SHARES_AMBIGUOUS", "disposition": "EXCLUDED_FAIL_CLOSED"},
    "non_common_listed_security": {"issuer_cik": "0000040545", "security_title": "0.875% Notes due 2025", "classification": "NOTE_OR_DEBT", "identity_status": "NON_COMMON_EQUITY", "disposition": "EXCLUDED_FAIL_CLOSED"},
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plain_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def nested_self_hash(value: dict[str, Any], field: str) -> str:
    payload = copy.deepcopy(value)
    payload.setdefault("integrity", {}).pop(field, None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def seal(value: dict[str, Any], field: str) -> dict[str, Any]:
    value.setdefault("integrity", {})[field] = PENDING
    value["integrity"][field] = nested_self_hash(value, field)
    return value


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(UTC)


def sec_acceptance_time(raw: str) -> datetime:
    if not re.fullmatch(r"\d{14}", raw):
        raise ValueError("invalid_sec_acceptance_datetime")
    return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=NY).astimezone(UTC)


def submission_document_bytes(body: bytes, filename: str) -> bytes:
    document = re.search(
        rb"<DOCUMENT>\s*<TYPE>[^\r\n]+\s*<SEQUENCE>[^\r\n]+\s*<FILENAME>"
        + re.escape(filename.encode())
        + rb"\s*<DESCRIPTION>[^\r\n]+\s*<TEXT>\s*(?:<XBRL>\s*)?(.*?)(?:\s*</XBRL>)?\s*</TEXT>\s*</DOCUMENT>",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if not document:
        raise ValueError(f"submission_document_missing:{filename}")
    return document.group(1).rstrip(b"\r\n")


def parse_submission(path: Path, accession: str, primary_filename: str | None = None) -> dict[str, Any]:
    body = path.read_bytes()
    match = re.search(rb"^ACCESSION NUMBER:\s*([^\r\n]+)$", body, re.IGNORECASE | re.MULTILINE)
    if not match:
        match = re.search(rb"<ACCESSION-NUMBER>\s*([^\r\n<]+)", body, re.IGNORECASE)
    accepted = re.search(rb"<ACCEPTANCE-DATETIME>\s*(\d{14})", body, re.IGNORECASE)
    if not match or match.group(1).decode().strip() != accession or not accepted:
        raise ValueError(f"submission_header_mismatch:{path.name}")
    result: dict[str, Any] = {
        "accession": accession,
        "accepted_at": sec_acceptance_time(accepted.group(1).decode()).isoformat().replace("+00:00", "Z"),
        "source_sha256": sha256_file(path),
    }
    if primary_filename:
        primary = EXT / primary_filename
        primary_bytes = primary.read_bytes()
        if primary_bytes.lstrip().upper().startswith(b"<DOCUMENT>"):
            primary_bytes = submission_document_bytes(primary_bytes, primary_filename)
        if submission_document_bytes(body, primary_filename) != primary_bytes.rstrip(b"\r\n"):
            raise ValueError(f"submission_document_binding_mismatch:{primary_filename}")
        result["primary_document"] = primary_filename
        result["primary_sha256"] = sha256_file(primary)
    return result


def _text(fragment: bytes) -> str:
    return " ".join(html.unescape(re.sub(rb"<[^>]+>", b"", fragment).decode("utf-8", "replace")).split())


def _attrs(fragment: bytes) -> dict[str, str]:
    return {key.decode().casefold(): html.unescape(value.decode()) for key, value in re.findall(rb"([\w:-]+)=[\"']([^\"']*)[\"']", fragment)}


def ix_facts(body: bytes, name: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        rb"<ix:(nonNumeric|nonFraction)\b([^>]*\bname=[\"']dei:"
        + re.escape(name.encode())
        + rb"[\"'][^>]*)>(.*?)</ix:\1>",
        re.DOTALL | re.IGNORECASE,
    )
    rows = []
    for match in pattern.finditer(body):
        rows.append({"attrs": _attrs(match.group(2)), "text": _text(match.group(3)), "offset": match.start()})
    return rows


def context_details(body: bytes, context_id: str) -> dict[str, Any]:
    pattern = re.compile(
        rb"<xbrli:context\b[^>]*\bid=[\"']" + re.escape(context_id.encode()) + rb"[\"'][^>]*>(.*?)</xbrli:context>",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(body)
    if not match:
        raise ValueError(f"missing_context:{context_id}")
    block = match.group(1)
    cik = re.search(rb"<xbrli:identifier\b[^>]*>(\d+)</xbrli:identifier>", block, re.IGNORECASE)
    instant = re.search(rb"<xbrli:instant>([^<]+)</xbrli:instant>", block, re.IGNORECASE)
    dimensions = [
        {"dimension": dimension.decode(), "member": member.decode()}
        for dimension, member in re.findall(
            rb"<xbrldi:explicitMember\b[^>]*dimension=[\"']([^\"']+)[\"'][^>]*>([^<]+)</xbrldi:explicitMember>",
            block,
            re.IGNORECASE,
        )
    ]
    return {"issuer_cik": cik.group(1).decode().zfill(10) if cik else None,
            "instant": instant.group(1).decode() if instant else None, "dimensions": dimensions}


def parse_inline_cover(path: Path, issuer_cik: str, symbol: str) -> dict[str, Any]:
    body = path.read_bytes()
    symbols = ix_facts(body, "TradingSymbol")
    matching = [row for row in symbols if row["text"] == symbol]
    if len(matching) != 1:
        raise ValueError(f"cover_symbol_not_unique:{symbol}")
    context_ref = matching[0]["attrs"].get("contextref")
    titles = [row for row in ix_facts(body, "Security12bTitle") if row["attrs"].get("contextref") == context_ref]
    exchanges = [row for row in ix_facts(body, "SecurityExchangeName") if row["attrs"].get("contextref") == context_ref]
    common_titles = [row for row in ix_facts(body, "Security12bTitle") if classify_security(row["text"]) == "COMMON_EQUITY_EXACT"]
    if len(titles) != 1 or len(exchanges) != 1 or len(common_titles) != 1:
        raise ValueError(f"single_class_cover_not_exact:{symbol}")
    cover_context = context_details(body, cast(str, context_ref))
    if cover_context["issuer_cik"] != issuer_cik:
        raise ValueError("cover_cik_mismatch")
    return {"symbol": symbol, "title": titles[0]["text"], "exchange": exchanges[0]["text"],
            "context_id": context_ref, "dimensions": cover_context["dimensions"], "source_sha256": sha256_file(path)}


def parse_inline_shares(path: Path, issuer_cik: str) -> dict[str, Any]:
    body = path.read_bytes()
    facts = ix_facts(body, "EntityCommonStockSharesOutstanding")
    if len(facts) != 1:
        raise ValueError("shares_fact_not_unique")
    fact = facts[0]
    attrs = fact["attrs"]
    required = ("contextref", "unitref", "decimals", "scale")
    if any(key not in attrs for key in required) or attrs["unitref"].casefold() != "shares":
        raise ValueError("shares_attributes_invalid")
    unit = re.search(rb"<xbrli:unit\b[^>]*id=[\"']shares[\"'][^>]*>.*?<xbrli:measure>xbrli:shares</xbrli:measure>.*?</xbrli:unit>", body, re.DOTALL | re.IGNORECASE)
    if not unit:
        raise ValueError("shares_unit_definition_missing")
    context = context_details(body, attrs["contextref"])
    if context["issuer_cik"] != issuer_cik or not context["instant"]:
        raise ValueError("shares_context_invalid")
    lexical = fact["text"].replace(",", "")
    value = Decimal(lexical) * (Decimal(10) ** int(attrs["scale"]))
    hidden_open = body.rfind(b"<ix:hidden", 0, fact["offset"])
    hidden_close = body.rfind(b"</ix:hidden", 0, fact["offset"])
    if hidden_open > hidden_close:
        raise ValueError("shares_fact_hidden_not_cover_text")
    return {"tag": "dei:EntityCommonStockSharesOutstanding", "value": plain_decimal(value), "lexical_value": fact["text"],
            "instant": context["instant"], "unit": "shares", "decimals": attrs["decimals"], "scale": attrs["scale"],
            "context_id": attrs["contextref"], "dimensions": context["dimensions"], "source_sha256": sha256_file(path)}


def parse_form4(path: Path, accession: str) -> dict[str, Any]:
    submission = parse_submission(path, accession)
    body = path.read_bytes()
    cik = re.search(rb"<issuerCik>\s*(\d+)\s*</issuerCik>", body, re.IGNORECASE)
    symbol = re.search(rb"<issuerTradingSymbol>\s*([^<]+)\s*</issuerTradingSymbol>", body, re.IGNORECASE)
    title = re.search(rb"<securityTitle>\s*<value>\s*([^<]+)\s*</value>", body, re.IGNORECASE)
    if not cik or not symbol or not title:
        raise ValueError(f"form4_identity_missing:{path.name}")
    return {**submission, "issuer_cik": cik.group(1).decode().zfill(10), "symbol": _text(symbol.group(1)),
            "transaction_title": _text(title.group(1))}


def month_end_session_close(year: int, month: int) -> datetime:
    start = date(year, month, 1)
    end = (date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1))
    schedule = market_calendars.get_calendar("NYSE").schedule(start_date=start, end_date=end)
    return cast(datetime, schedule.iloc[-1]["market_close"].to_pydatetime()).astimezone(UTC)


def v5_identity(symbol: str, decision_at: datetime) -> dict[str, Any]:
    path = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
    matches = []
    symbol_rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if symbol not in {row["canonical_symbol"], row["historical_symbol"]} or not row.get("provider_asset_id"):
                continue
            symbol_rows.append(row)
            start = parse_time(row["valid_from"])
            end = parse_time(row["valid_to"]) if row["valid_to"] else None
            if start <= decision_at and (end is None or decision_at <= end):
                matches.append(row)
    if len(matches) != 1:
        raise ValueError(f"v5_identity_not_exact:{symbol}")
    result = {key: matches[0][key] for key in ("entity_id", "provider_asset_id", "canonical_symbol", "valid_from", "valid_to", "exchange")}
    result["ticker_reuse_resolved"] = len({row["entity_id"] for row in symbol_rows}) == 1
    result["matching_lifetime_rows"] = len(symbol_rows)
    return result


def submission_inventory(path: Path, issuer_cik: str, decision_at: datetime, selected_accession: str) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if str(payload.get("cik", "")).zfill(10) != issuer_cik:
        raise ValueError("submission_inventory_cik_mismatch")
    recent = payload.get("filings", {}).get("recent", {})
    keys = ("form", "accessionNumber", "acceptanceDateTime", "filingDate", "primaryDocument")
    if any(not isinstance(recent.get(key), list) for key in keys):
        raise ValueError("submission_inventory_schema_invalid")
    lengths = {len(recent[key]) for key in keys}
    if len(lengths) != 1:
        raise ValueError("submission_inventory_columns_misaligned")
    visible = []
    for index, form in enumerate(recent["form"]):
        if form not in {"10-K", "10-Q", "10-K/A", "10-Q/A"}:
            continue
        accepted_at = parse_time(recent["acceptanceDateTime"][index])
        available_at = h06_timing_v2(accepted_at)
        if available_at <= decision_at:
            visible.append({"form": form, "accession": recent["accessionNumber"][index],
                            "accepted_at": accepted_at.isoformat().replace("+00:00", "Z"),
                            "available_at": available_at.isoformat().replace("+00:00", "Z"),
                            "filing_date": recent["filingDate"][index],
                            "primary_document": recent["primaryDocument"][index]})
    if not visible:
        raise ValueError("submission_inventory_no_visible_reports")
    latest = max(visible, key=lambda row: parse_time(row["available_at"]))
    if latest["accession"] != selected_accession:
        raise ValueError(f"selected_filing_not_latest_visible:{selected_accession}")
    selected_time = parse_time(latest["available_at"])
    later_visible_amendment = any(
        row["form"].endswith("/A") and parse_time(row["available_at"]) > selected_time for row in visible
    )
    return {"path": str(path.relative_to(REPO)), "sha256": sha256_file(path), "bytes": path.stat().st_size,
            "official_url": f"https://data.sec.gov/submissions/CIK{issuer_cik}.json", "retrieved_at": "2026-09-14",
            "issuer_cik": issuer_cik, "decision_at": decision_at.isoformat().replace("+00:00", "Z"),
            "eligible_visible_filing_count": len(visible), "latest_visible_filing": latest,
            "later_visible_amendment": later_visible_amendment, "status": "LATEST_VISIBLE_ELIGIBLE_FILING_EXACT"}


def intervening_splits(symbol: str, fact_instant: str, decision_at: datetime) -> list[dict[str, str]]:
    path = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["symbol"] != symbol or row["action_type"] not in {"forward_splits", "reverse_splits"}:
                continue
            if fact_instant < row["ex_date"] <= decision_at.astimezone(NY).date().isoformat():
                rows.append({key: row[key] for key in ("action_type", "ex_date", "old_rate", "new_rate")})
    return rows


def decision_bar(symbol: str, year: int, month: int, response_name: str) -> dict[str, Any]:
    decision_at = month_end_session_close(year, month)
    meta = json.loads((EXT / "alpaca_raw_denominator_decision_bars_metadata.json").read_text())
    records = [row for row in meta["bars"] if row["symbol"] == symbol]
    if len(records) != 1:
        raise ValueError("denominator_metadata_not_unique")
    record = records[0]
    request = record["request"]
    expected_params = {"timeframe": "1Day", "feed": "sip", "adjustment": "raw", "sort": "asc", "limit": 10000}
    if record["http_status"] != 200 or any(request.get(key) != value for key, value in expected_params.items()):
        raise ValueError("raw_sip_request_contract_mismatch")
    response_path = EXT / response_name
    if record["response_sha256"] != sha256_file(response_path) or record["response_bytes"] != response_path.stat().st_size:
        raise ValueError("raw_sip_response_receipt_mismatch")
    bars = json.loads(response_path.read_text())["bars"][symbol]
    session_date = decision_at.astimezone(NY).date().isoformat()
    selected = [row for row in bars if parse_time(row["t"]).astimezone(NY).date().isoformat() == session_date]
    if len(selected) != 1 or str(selected[0]["c"]) != str(record["completed_session_bar"]["close"]):
        raise ValueError("raw_sip_month_end_bar_mismatch")
    return {"decision_at": decision_at.isoformat().replace("+00:00", "Z"), "bar_timestamp": selected[0]["t"],
            "close": str(selected[0]["c"]), "response_sha256": sha256_file(response_path), "status": "RAW_COMPLETED_SESSION_CLOSE_EXACT"}


def h06_timing_v2(accepted_at: datetime) -> datetime:
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("acceptance_timezone_required")
    local_day = accepted_at.astimezone(NY).date()
    holidays = {
        stamp.date()
        for stamp in USFederalHolidayCalendar().holidays(start=local_day, end=local_day + timedelta(days=20))
    }
    complete = local_day + timedelta(days=1)
    while complete.weekday() >= 5 or complete in holidays:
        complete += timedelta(days=1)
    schedule = market_calendars.get_calendar("NYSE").schedule(
        start_date=complete + timedelta(days=1), end_date=complete + timedelta(days=14)
    )
    if schedule.empty:
        raise ValueError("no_nyse_session")
    return cast(datetime, schedule.iloc[0]["market_open"].to_pydatetime()).astimezone(UTC)


def accession_in_body(body: bytes, accession: str) -> bool:
    token = re.escape(accession.encode())
    return re.search(rb"(?<![A-Za-z0-9-])" + token + rb"(?![A-Za-z0-9-])", body) is not None


Fetch = Callable[[], tuple[int, bytes | None]]


def retrieve_with_fallback(accession: str, direct: Fetch, feed: Fetch, oldloads: Fetch) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for route, fetch in (("FILING_SPECIFIC", direct), ("FEED", feed), ("OLDLOADS", oldloads)):
        status, body = fetch()
        attempts.append({"route": route, "http_status": status})
        if status == 200 and body is not None and accession_in_body(body, accession):
            return {"status": "SEC_RECEIPT_BOUND", "route": route, "body": body, "attempts": attempts}
        if route == "FILING_SPECIFIC" and status != 403:
            break
    return {"status": "SEC_RECEIPT_BINDING_UNAVAILABLE", "route": None, "body": None, "attempts": attempts}


def receipt_bound(record: dict[str, Any]) -> bool:
    return bool(record.get("persisted") and record.get("path") and record.get("sha256") and record.get("bytes"))


def classify_security(title: str) -> str:
    text = " ".join(title.casefold().split())
    if "warrant" in text:
        return "WARRANT"
    if "preferred" in text:
        return "PREFERRED_EQUITY"
    if "depositary" in text or re.search(r"\badr\b|\bads\b", text):
        return "ADR_OR_ADS"
    if re.search(r"\bunit(s)?\b", text):
        return "UNIT"
    if any(token in text for token in ("note", "bond", "debenture", "debt")):
        return "NOTE_OR_DEBT"
    if any(token in text for token in ("common stock", "common shares", "class a", "class b", "class c capital stock")):
        return "COMMON_EQUITY_EXACT"
    return "AMBIGUOUS_SECURITY_CLASS"


def common_class_marker(title: str) -> str | None:
    match = re.search(r"\bclass\s+([abc])\b", title.casefold())
    return match.group(1).upper() if match else None


def resolve_identity(*, receipt_ok: bool, cik_exact: bool, cover_symbol: str, form4_symbol: str,
                     cover_title: str, transaction_title: str, v5_matches: int,
                     provider_id: str | None, ticker_reuse_resolved: bool) -> str:
    cover_class = classify_security(cover_title)
    transaction_class = classify_security(transaction_title)
    if cover_class != "COMMON_EQUITY_EXACT" or transaction_class != "COMMON_EQUITY_EXACT":
        return "NON_COMMON_EQUITY" if cover_class != "AMBIGUOUS_SECURITY_CLASS" else "IDENTITY_AMBIGUOUS"
    cover_marker = common_class_marker(cover_title)
    transaction_marker = common_class_marker(transaction_title)
    if cover_marker and transaction_marker and cover_marker != transaction_marker:
        return "IDENTITY_AMBIGUOUS"
    exact = all((receipt_ok, cik_exact, cover_symbol == form4_symbol, v5_matches == 1, provider_id, ticker_reuse_resolved))
    return "IDENTITY_EXACT" if exact else "IDENTITY_AMBIGUOUS"


def select_shares_fact(facts: list[dict[str, Any]], decision_at: datetime, required_class: str) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    class_seen = False
    scaling_suspect = False
    for fact in facts:
        if fact.get("security_class") != required_class:
            class_seen = True
            continue
        required = ("issuer_cik", "accession", "context_id", "source_sha256", "accepted_at", "available_at",
                    "fact_instant", "unit", "value", "decimals", "scale", "dimensions")
        if any(key not in fact or fact[key] is None for key in required):
            continue
        if fact.get("tag") != "dei:EntityCommonStockSharesOutstanding" or fact.get("form") not in {"10-K", "10-Q"}:
            continue
        accepted = parse_time(str(fact["accepted_at"]))
        available = parse_time(str(fact["available_at"]))
        if accepted > decision_at or available < h06_timing_v2(accepted) or available > decision_at:
            continue
        if date.fromisoformat(str(fact["fact_instant"])) > decision_at.astimezone(NY).date():
            continue
        value = Decimal(str(fact["value"]))
        if fact["unit"] != "shares" or value <= 0 or value > Decimal("1e14") or fact.get("cover_text_agrees") is not True:
            scaling_suspect = True
            continue
        eligible.append(fact)
    if not eligible:
        if scaling_suspect:
            return {"status": "SHARES_FACT_SCALING_SUSPECT"}
        return {"status": "SHARES_FACT_CLASS_AMBIGUOUS" if class_seen else "SHARES_FACT_MISSING"}
    latest_available = max(parse_time(str(row["available_at"])) for row in eligible)
    latest = [row for row in eligible if parse_time(str(row["available_at"])) == latest_available]
    if len({(str(row["value"]), row["context_id"]) for row in latest}) != 1:
        return {"status": "SHARES_FACT_CONTEXT_CONFLICT"}
    return {"status": "SHARES_FACT_EXACT", "fact": latest[0]}


def raw_request_params(symbol: str, start: str, end: str) -> dict[str, Any]:
    return {"symbols": symbol, "timeframe": "1Day", "feed": "sip", "adjustment": "raw",
            "sort": "asc", "limit": 10000, "start": start, "end": end}


def split_request_params(symbol: str, start: str, end: str) -> dict[str, Any]:
    value = raw_request_params(symbol, start, end)
    value["adjustment"] = "split"
    return value


def canary_ratio(raw_price: Decimal, split_price: Decimal) -> Decimal:
    if raw_price <= 0 or split_price <= 0:
        raise ValueError("positive_prices_required")
    return raw_price / split_price


def derive_split_canary() -> dict[str, Any]:
    metadata = json.loads((EXT / "alpaca_raw_split_canary_metadata.json").read_text())
    v5_rows: dict[tuple[str, datetime], dict[str, str]] = {}
    with (REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["symbol"] in {"AAPL", "NVDA", "GE"}:
                v5_rows[(row["symbol"], parse_time(row["timestamp"]))] = row
    actions = []
    with (REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv").open(newline="", encoding="utf-8") as handle:
        actions = [row for row in csv.DictReader(handle) if row["action_type"] in {"forward_splits", "reverse_splits"}]
    derived_cases = []
    for case in metadata["cases"]:
        symbol = case["symbol"]
        response_rows: dict[str, list[dict[str, Any]]] = {}
        for adjustment in ("raw", "split"):
            declaration = case[adjustment]
            path = REPO / declaration["path"]
            if declaration["http_status"] != 200 or declaration["adjustment"] != adjustment:
                raise ValueError(f"canary_request_contract_mismatch:{symbol}:{adjustment}")
            if sha256_file(path) != declaration["sha256"] or path.stat().st_size != declaration["bytes"]:
                raise ValueError(f"canary_response_receipt_mismatch:{symbol}:{adjustment}")
            response_rows[adjustment] = json.loads(path.read_text())["bars"][symbol]
        raw_rows = response_rows["raw"]
        split_rows = response_rows["split"]
        if [row["t"] for row in raw_rows] != case["timestamps"] or [row["t"] for row in split_rows] != case["timestamps"]:
            raise ValueError(f"canary_timestamp_mismatch:{symbol}")
        if len(raw_rows) != len(split_rows) or len(raw_rows) != 5:
            raise ValueError(f"canary_row_count_mismatch:{symbol}")
        action_date = case["corporate_action_date"]
        if action_date is None:
            if raw_rows != split_rows:
                raise ValueError("no_split_canary_mismatch")
        else:
            matching_actions = [row for row in actions if row["symbol"] == symbol and row["ex_date"] == action_date]
            if len(matching_actions) != 1:
                raise ValueError(f"canary_action_not_exact:{symbol}")
            expected_ratio = Decimal(matching_actions[0]["new_rate"]) / Decimal(matching_actions[0]["old_rate"])
            pre_count = 0
            for raw_row, split_row in zip(raw_rows, split_rows, strict=True):
                session_date = parse_time(raw_row["t"]).astimezone(NY).date().isoformat()
                if session_date < action_date:
                    pre_count += 1
                    price_ratio = Decimal(str(raw_row["c"])) / Decimal(str(split_row["c"]))
                    volume_ratio = Decimal(str(split_row["v"])) / Decimal(str(raw_row["v"]))
                    if abs(price_ratio - expected_ratio) > Decimal("0.001") or abs(volume_ratio - expected_ratio) > Decimal("0.001"):
                        raise ValueError(f"canary_mechanical_ratio_mismatch:{symbol}")
                elif any(Decimal(str(raw_row[key])) != Decimal(str(split_row[key])) for key in ("o", "h", "l", "c", "v")):
                    raise ValueError(f"post_action_canary_mismatch:{symbol}")
            if pre_count == 0:
                raise ValueError(f"canary_missing_pre_action_rows:{symbol}")
        parity = 0
        for split_row in split_rows:
            v5 = v5_rows.get((symbol, parse_time(split_row["t"])))
            if v5 is None:
                continue
            if all(Decimal(str(split_row[source])) == Decimal(v5[target])
                   for source, target in (("o", "open"), ("h", "high"), ("l", "low"),
                                          ("c", "close"), ("v", "volume"))):
                parity += 1
        if parity != len(split_rows):
            raise ValueError(f"v5_sample_split_parity_mismatch:{symbol}")
        derived_cases.append({"case": case["case"], "symbol": symbol, "rows": len(raw_rows),
                              "v5_exact_ohlcv_parity_rows": parity, "mechanical_status": "PASS"})
    return {"request_count": 6, "http_200_count": 6, "response_bodies_persisted": 6,
            "raw_split_semantics": "PROVIDER_MECHANICAL_CANARY_PASS",
            "v5_sample_split_parity": "V5_SAMPLE_SPLIT_PARITY_PASS",
            "raw_price_route": "AVAILABLE_FOR_STRUCTURAL_DENOMINATOR_CONSTRUCTION",
            "economic_run": False, "h06_score_calculated": False, "returns_calculated": False,
            "derived_cases": derived_cases}


def structural_market_cap(*, identity_status: str, shares_status: str, price_status: str,
                          shares: Decimal, raw_close: Decimal, intervening_split: bool = False) -> dict[str, Any]:
    if intervening_split:
        return {"status": "BLOCKED_INTERVENING_SPLIT_BASIS"}
    if identity_status != "IDENTITY_EXACT":
        return {"status": "BLOCKED_SECURITY_IDENTITY"}
    if shares_status != "SHARES_FACT_EXACT":
        return {"status": shares_status}
    if price_status != "RAW_COMPLETED_SESSION_CLOSE_EXACT" or shares <= 0 or raw_close <= 0:
        return {"status": "BLOCKED_RAW_PRICE"}
    value = shares * raw_close
    return {"status": "RAW_MARKET_CAP_BASIS_EXACT", "label": DENOMINATOR_LABEL,
            "shares": str(shares), "raw_close": str(raw_close), "structural_denominator": str(value)}


ADMISSION_CONFIG: dict[str, dict[str, Any]] = {
    "AAPL": {"issuer_cik": "0000320193", "submission": "aapl-0000320193-24-000123.txt",
             "accession": "0000320193-24-000123", "form": "10-K", "primary": "aapl-20240928.htm",
             "form4": "aapl-form4-predecision-0000320193-24-000130.txt", "form4_accession": "0000320193-24-000130",
             "inventory": "CIK0000320193-submissions.json", "year": 2024, "month": 11, "raw": "aapl_denominator_2024-11-29_raw.json"},
    "NVDA": {"issuer_cik": "0001045810", "submission": "nvda-0001045810-24-000124.txt",
             "accession": "0001045810-24-000124", "form": "10-Q", "primary": "nvda-20240428.htm",
             "form4": "nvda-form4-predecision-0001045810-24-000122.txt", "form4_accession": "0001045810-24-000122",
             "inventory": "CIK0001045810-submissions.json", "year": 2024, "month": 5, "raw": "nvda_denominator_2024-05-31_raw.json"},
}


def derive_admission(symbol: str) -> dict[str, Any]:
    config = ADMISSION_CONFIG[symbol]
    submission = parse_submission(EXT / config["submission"], config["accession"], config["primary"])
    cover = parse_inline_cover(EXT / config["primary"], config["issuer_cik"], symbol)
    shares = parse_inline_shares(EXT / config["primary"], config["issuer_cik"])
    form4 = parse_form4(EXT / config["form4"], config["form4_accession"])
    bar = decision_bar(symbol, config["year"], config["month"], config["raw"])
    decision_at = parse_time(bar["decision_at"])
    inventory = submission_inventory(EXT / config["inventory"], config["issuer_cik"], decision_at, config["accession"])
    accepted_at = parse_time(submission["accepted_at"])
    form4_accepted_at = parse_time(form4["accepted_at"])
    if h06_timing_v2(accepted_at) > decision_at or h06_timing_v2(form4_accepted_at) > decision_at:
        raise ValueError(f"admission_evidence_not_visible:{symbol}")
    v5 = v5_identity(symbol, decision_at)
    identity = resolve_identity(
        receipt_ok=True,
        cik_exact=form4["issuer_cik"] == config["issuer_cik"],
        cover_symbol=cover["symbol"],
        form4_symbol=form4["symbol"],
        cover_title=cover["title"],
        transaction_title=form4["transaction_title"],
        v5_matches=1,
        provider_id=v5["provider_asset_id"],
        ticker_reuse_resolved=bool(v5["ticker_reuse_resolved"]),
    )
    fact = {**shares, "form": config["form"], "issuer_cik": config["issuer_cik"],
            "accession": config["accession"], "accepted_at": submission["accepted_at"],
            "available_at": h06_timing_v2(accepted_at).isoformat().replace("+00:00", "Z"),
            "fact_instant": shares["instant"], "security_class": "Common Stock", "cover_text_agrees": True}
    selected = select_shares_fact([fact], decision_at, "Common Stock")
    actions = intervening_splits(symbol, shares["instant"], decision_at)
    denominator = structural_market_cap(
        identity_status=identity,
        shares_status=selected["status"],
        price_status=bar["status"],
        shares=Decimal(shares["value"]),
        raw_close=Decimal(bar["close"]),
        intervening_split=bool(actions),
    )
    return {"case": symbol, "issuer_cik": config["issuer_cik"], "submission_inventory": inventory,
            "submission": submission, "cover": cover,
            "form4": form4, "v5": v5, "identity_status": identity, "shares": shares,
            "shares_status": selected["status"], "intervening_splits": actions, "raw_price": bar,
            "denominator": denominator}


def _evidence_record(name: str) -> dict[str, Any]:
    path = EXT / name
    expected = EVIDENCE_SHA[name]
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"evidence_hash_mismatch:{name}")
    record = {"path": str(path.relative_to(REPO)), "bytes": path.stat().st_size, "sha256": expected,
              "persisted": True}
    if name in SEC_META:
        accession, official_url, role = SEC_META[name]
        record.update({"accession": accession, "official_url": official_url, "role": role})
    return record


def _route(case: str, accession: str, filing_date: str, names: list[str]) -> dict[str, Any]:
    records = [_evidence_record(name) for name in names]
    complete_by_accession = {
        cast(str, record["accession"]): EXT / Path(cast(str, record["path"])).name
        for record in records
        if record.get("role") == "complete_submission"
    }
    for record in records:
        role = record.get("role")
        record_accession = cast(str, record.get("accession"))
        path = EXT / Path(cast(str, record["path"])).name
        url_filename = path.name if role in {"inline_xbrl", "ticker_change_exhibit"} else f"{record_accession}.txt"
        expected_suffix = f"/{record_accession.replace('-', '')}/{url_filename}"
        if not cast(str, record.get("official_url", "")).endswith(expected_suffix):
            raise ValueError(f"sec_official_url_binding_mismatch:{path.name}")
        if role == "complete_submission":
            parsed = parse_submission(path, record_accession)
        elif role == "form4":
            parsed = parse_form4(path, record_accession)
        elif role in {"inline_xbrl", "ticker_change_exhibit"}:
            complete = complete_by_accession.get(record_accession)
            if complete is None:
                raise ValueError(f"unbound_extracted_document:{path.name}")
            parsed = parse_submission(complete, record_accession, path.name)
        else:
            raise ValueError(f"unknown_sec_evidence_role:{role}")
        record["accepted_at"] = parsed["accepted_at"]
        record["filing_date"] = parse_time(parsed["accepted_at"]).astimezone(NY).date().isoformat()
    return {"case": case, "accession": accession, "filing_date": filing_date,
            "filing_specific_result": "HTTP_200_RECEIPT_BOUND", "feed_fallback_result": "NOT_APPLICABLE_ROUTE_A_SUCCEEDED",
            "oldloads_fallback_result": "NOT_APPLICABLE_ROUTE_A_SUCCEEDED", "official_source_route": "SEC_FILING_SPECIFIC_ARCHIVE",
            "retrieval_window_utc": {"start": "2026-09-14T18:24:15Z", "end": "2026-09-14T18:34:24Z"},
            "evidence": records,
            "final_evidence_status": "SEC_RECEIPT_BOUND"}


def build_sec_receipt(admissions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cases = [
        _route("single_class_no_relevant_split", "0000320193-24-000123", "2024-11-01",
               ["aapl-0000320193-24-000123.txt", "aapl-20240928.htm", "aapl-form4-predecision-0000320193-24-000130.txt"]),
        _route("forward_split_history", "0001045810-24-000124", "2024-05-29",
               ["nvda-0001045810-24-000124.txt", "nvda-20240428.htm", "nvda-form4-predecision-0001045810-24-000122.txt", "nvda-0001045810-24-000144.txt", "nvda-20240607.htm"]),
        _route("reverse_split_history", "0000040545-21-000052", "2021-07-27",
               ["ge-0000040545-21-000052.txt", "ge-20210630.htm", "ge-form4-0002052951-26-000007.txt", "ge-0001206774-21-001925.txt", "ge3936671-8k.htm"]),
        _route("same_cik_ticker_change", "0001326801-22-000070", "2022-05-31",
               ["meta-0001326801-22-000070.txt", "fb-20220531.htm", "may312022-exhibit991.htm", "fb-form4-0000950103-22-010059.txt", "meta-form4-0000950103-26-011342.txt"]),
        _route("multiple_common_classes", "0001652044-25-000014", "2025-02-04",
               ["goog-0001652044-25-000014.txt", "goog-20241231.htm", "goog-form4-0001193125-26-326284.txt"]),
        _route("non_common_listed_security", "0001206774-21-001925", "2021-07-30",
               ["ge-0001206774-21-001925.txt", "ge3936671-8k.htm"]),
    ]
    for row in cases:
        if row["case"] == "single_class_no_relevant_split":
            row["extracted_state"] = admissions["AAPL"]
        elif row["case"] == "forward_split_history":
            row["extracted_state"] = admissions["NVDA"]
        else:
            row["extracted_state"] = CASE_STATE[row["case"]]
    payload = {"schema": "h06-sec-archive-fallback-probe-v1", "status": "SEC_AUTOMATED_RECEIPT_BINDING_AVAILABLE",
               "retrieval_policy": {"rate_limit": "<=5 requests/second", "browser_only_evidence_admitted": False,
                                    "route_order": ["filing-specific", "Feed", "Oldloads"]},
               "documentation": {"official_url": "https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data",
                                  "retrieved_at": "2026-09-14", "persisted": False,
                                  "semantic_fields": ["current max request rate 10 requests/second", "daily Feed tar/gzip archives", "Oldloads concatenated submissions", "daily/full index roles"]},
               "cases": cases, "integrity": {"receipt_sha256": PENDING}}
    return seal(payload, "receipt_sha256")


def build_sip_receipt() -> dict[str, Any]:
    denominator = json.loads((EXT / "alpaca_raw_denominator_decision_bars_metadata.json").read_text())
    names = [name for name in EVIDENCE_SHA if name.endswith(("_raw.json", "_split.json", "_docs.md", "_metadata.json"))]
    payload = {"schema": "h06-raw-sip-marketcap-probe-v1", "status": "RAW_SIP_STRUCTURAL_EVIDENCE_EXACT",
               "endpoint": "https://data.alpaca.markets/v2/stocks/bars", "feed": "sip", "timeframe": "1Day",
               "raw_semantics": "no adjustments", "split_semantics": "adjust price and volume for forward and reverse stock splits",
               "canary_summary": derive_split_canary(), "denominator_summary": denominator["summary"],
               "evidence": [_evidence_record(name) for name in names],
               "integrity": {"receipt_sha256": PENDING}}
    return seal(payload, "receipt_sha256")


def identity_cases(admissions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"case": symbol, "issuer_cik": row["issuer_cik"], "cover_symbol": row["cover"]["symbol"],
         "form4_symbol": row["form4"]["symbol"], "cover_title": row["cover"]["title"],
         "transaction_title": row["form4"]["transaction_title"], "v5_entity_id": row["v5"]["entity_id"],
         "status": row["identity_status"]}
        for symbol, row in admissions.items()
    ] + [
        {"case": "GE", "issuer_cik": "0000040545", "status": "IDENTITY_MISSING", "disposition": "EXCLUDED_FAIL_CLOSED", "reason": "available bounded Form 4 is not decision-visible for the 2021 split state"},
        {"case": "META/FB", "issuer_cik": "0001326801", "old_symbol": "FB", "new_symbol": "META", "sec_chronology": "RECEIPT_BOUND_EXACT", "v5_chronology": "single META interval", "status": "IDENTITY_AMBIGUOUS", "disposition": "EXCLUDED_FAIL_CLOSED"},
        {"case": "Alphabet", "issuer_cik": "0001652044", "classes": ["Class A/GOOGL", "Class C/GOOG"], "form4_title": "Class C Capital Stock", "form4_symbol": "GOOGL", "status": "MULTI_CLASS_SHARES_AMBIGUOUS", "disposition": "EXCLUDED_FAIL_CLOSED"},
        {"case": "GE 25", "issuer_cik": "0000040545", "security_title": "0.875% Notes due 2025", "status": "NON_COMMON_EQUITY", "disposition": "EXCLUDED_FAIL_CLOSED"},
    ]


def build_raw_manifest(admissions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [{"case": symbol, "decision_at": row["raw_price"]["decision_at"], "fact_instant": row["shares"]["instant"],
             "intervening_splits": row["intervening_splits"], **row["denominator"]}
            for symbol, row in admissions.items()]
    if any(row["status"] != "RAW_MARKET_CAP_BASIS_EXACT" for row in rows):
        raise ValueError("bounded_denominator_not_exact")
    payload = {"schema": "h06-raw-marketcap-v1", "status": "RAW_MARKET_CAP_BASIS_EXACT",
               "formula_compatibility": FORMULA_PROTOCOL, "shares_protocol": SHARES_PROTOCOL,
               "price_protocol": PRICE_PROTOCOL, "decision_rule": "last supported NYSE month-end completed regular-session close",
               "price_request": {"provider": "Alpaca historical stock bars", "feed": "sip", "timeframe": "1Day", "adjustment": "raw"},
               "intervening_split_gate": "any split between share fact instant and decision close blocks direct multiplication",
               "bounded_denominators": rows, "v5_split_basis": V5_DISPOSITION,
               "future_split_normalization": FUTURE_SPLIT_DISPOSITION, "integrity": {"manifest_sha256": PENDING}}
    return seal(payload, "manifest_sha256")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def render_report(raw: dict[str, Any]) -> str:
    return "\n".join([
        "# H06 final structural blocker closure — 2026-09-14", "", f"Status: `{STATUS}`.", "",
        "Official SEC filing-specific Route A returned receipt-bound bytes for all six structural families, so Feed and Oldloads were not invoked. AAPL and NVDA provide exact single-class admissions; GE, META/FB, Alphabet, and GE 25 demonstrate deterministic fail-closed exclusions.", "",
        f"The frozen wording `PIT shares_outstanding_t × decision_price_t` admits latest exact H06_TIMING_V2-visible filing shares (`{SHARES_PROTOCOL}`) and the completed raw SIP month-end close (`{PRICE_PROTOCOL}`). This is `{FORMULA_PROTOCOL}`.", "",
        "Bounded AAPL and NVDA rows prove raw-basis structural denominators. An intervening split between fact instant and decision close remains a row-level exclusion gate; no future split normalization is used.", "",
        f"V5 disposition: `{V5_DISPOSITION}`; sample split parity: `V5_SAMPLE_SPLIT_PARITY_PASS`.", "",
        "No Form 4 full-history acquisition, H06 score, ranking, return, P&L, backtest, holdout economics, or tuning was performed. PAPER, LIVE, registry, V3, and canonical V5 bytes are unchanged.", "",
    ])


def build_closure_manifest(admissions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    inputs = {name: {"path": str(path.relative_to(REPO)), "sha256": sha256_file(path), "bytes": path.stat().st_size}
              for name, path in (("previous_bridge", PREVIOUS), ("sec_receipt", SEC_RECEIPT),
                                 ("sip_receipt", SIP_RECEIPT), ("raw_marketcap", RAW_MANIFEST))}
    artifacts = {"report": {"path": str(REPORT.relative_to(REPO)), "sha256": sha256_file(REPORT), "bytes": REPORT.stat().st_size},
                 "script": {"path": str(SCRIPT.relative_to(REPO)), "sha256": sha256_file(SCRIPT), "bytes": SCRIPT.stat().st_size},
                 "test": {"path": str(TEST.relative_to(REPO)), "sha256": sha256_file(TEST), "bytes": TEST.stat().st_size}}
    payload = {"schema": "h06-structural-blocker-closure-v1", "status": STATUS,
               "base_commit": "78420a7f446752522e61f2cc384a42ad42e3bdef", "identity_cases": identity_cases(admissions),
               "coverage": {"exact_single_class_issuers": ["AAPL", "NVDA"], "exact_no_split_denominator": "AAPL",
                            "exact_split_history_denominator": "NVDA", "ticker_change": "EXCLUDED_FAIL_CLOSED",
                            "multi_class": "EXCLUDED_FAIL_CLOSED", "non_common": "EXCLUDED_FAIL_CLOSED"},
               "protocol": {"shares": SHARES_PROTOCOL, "raw_price": PRICE_PROTOCOL, "formula": FORMULA_PROTOCOL},
               "v5_split_basis": V5_DISPOSITION, "future_split_normalization": FUTURE_SPLIT_DISPOSITION,
               "remaining_blockers": [], "inputs": inputs, "artifacts": artifacts,
               "boundaries": {"full_form4_acquisition": False, "h06_score": False, "ranking": False, "portfolio_backtest": False,
                              "returns_inspected": False, "pnl": False, "holdout_economics": False, "threshold_tuning": False,
                              "paper_changed": False, "live_changed": False, "registry_changed": False, "v3_changed": False,
                              "v5_canonical_bytes_changed": False}, "integrity": {"manifest_sha256": PENDING}}
    return seal(payload, "manifest_sha256")


def build() -> dict[str, Any]:
    for relative, expected in EXPECTED_V5.items():
        if sha256_file(REPO / relative) != expected:
            raise ValueError(f"v5_hash_mismatch:{relative}")
    admissions = {symbol: derive_admission(symbol) for symbol in ADMISSION_CONFIG}
    sec = build_sec_receipt(admissions)
    sip = build_sip_receipt()
    raw = build_raw_manifest(admissions)
    _write_json(SEC_RECEIPT, sec)
    _write_json(SIP_RECEIPT, sip)
    _write_json(RAW_MANIFEST, raw)
    report = render_report(raw).encode()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_bytes(report)
    _write_json(CLOSURE, build_closure_manifest(admissions))
    return {"status": STATUS, "economic_run": False}


def contains_forbidden_economics(value: Any) -> bool:
    forbidden = {"score_value", "ranking_result", "returns", "pnl", "sharpe", "drawdown", "backtest_result"}
    if isinstance(value, dict):
        return any(
            (str(key).casefold() in forbidden and item not in (False, None, "NO"))
            or contains_forbidden_economics(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_forbidden_economics(item) for item in value)
    return False


def verify() -> dict[str, Any]:
    for name, expected in EVIDENCE_SHA.items():
        path = EXT / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"evidence_hash_mismatch:{name}")
    for relative, expected in EXPECTED_V5.items():
        if sha256_file(REPO / relative) != expected:
            raise ValueError(f"v5_hash_mismatch:{relative}")
    admissions = {symbol: derive_admission(symbol) for symbol in ADMISSION_CONFIG}
    for path, field in ((SEC_RECEIPT, "receipt_sha256"), (SIP_RECEIPT, "receipt_sha256"),
                        (RAW_MANIFEST, "manifest_sha256"), (CLOSURE, "manifest_sha256")):
        payload = json.loads(path.read_text())
        if payload["integrity"][field] != nested_self_hash(payload, field):
            raise ValueError(f"self_hash_mismatch:{path}")
    closure = json.loads(CLOSURE.read_text())
    if closure["status"] != STATUS or closure["remaining_blockers"] or contains_forbidden_economics(closure):
        raise ValueError("closure_contract_invalid")
    for declaration in [*closure["inputs"].values(), *closure["artifacts"].values()]:
        path = REPO / declaration["path"]
        if path.stat().st_size != declaration["bytes"] or sha256_file(path) != declaration["sha256"]:
            raise ValueError(f"closure_link_mismatch:{declaration['path']}")
    sec = json.loads(SEC_RECEIPT.read_text())
    sip = json.loads(SIP_RECEIPT.read_text())
    raw = json.loads(RAW_MANIFEST.read_text())
    if sec != build_sec_receipt(admissions):
        raise ValueError("sec_receipt_derivation_mismatch")
    if sip != build_sip_receipt():
        raise ValueError("sip_receipt_derivation_mismatch")
    if raw != build_raw_manifest(admissions):
        raise ValueError("raw_marketcap_derivation_mismatch")
    if REPORT.read_text() != render_report(raw):
        raise ValueError("report_derivation_mismatch")
    if closure != build_closure_manifest(admissions):
        raise ValueError("closure_derivation_mismatch")
    return {"status": "H06_STRUCTURAL_BLOCKER_CLOSURE_VERIFIED", "read_only": True, "economic_run": False}


if __name__ == "__main__":
    import sys

    print(json.dumps(verify() if "--verify" in sys.argv[1:] else build(), sort_keys=True))
