#!/usr/bin/env python3
"""Bounded official enrichment for the frozen H06 late-stage target set.

This module is outcome-free.  It downloads only the ten frozen SEC cover
documents and thirteen frozen raw SIP denominator bars, binds every response
to a SHA-256 receipt, and deterministically dispositions all core-pass rows.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import subprocess
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "data/research/local/h06_population_2026_09_15/h06_population.sqlite3"
LOCAL = REPO / "data/research/local/h06_final_enrichment_2026_09_15"
SEC_CACHE = LOCAL / "sec"
SIP_CACHE = LOCAL / "sip"
LOCAL_RESULT = LOCAL / "final_enrichment.json"
MANIFEST = REPO / "data/research_manifests/h06_full_structural_admission_2026_09_14.json"
RECEIPT = REPO / "data/research_receipts/h06_final_enrichment_receipt_2026_09_15.json"
REPORT = REPO / "docs/research/h06_full_structural_admission_2026_09_14.md"
LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
ACTIONS = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CORE_MANIFEST = REPO / "data/research_manifests/h06_offline_core_population_2026_09_15.json"

BASE = "b9a136cb9d5bce725ba1e7b416653794c30be3c6"
STATUS = "H06_FULL_STRUCTURAL_ADMISSION_READY"
EXPECTED_LOGICAL = {
    "normalized_relevant_transactions": "21d6b17b7f62e437f9cf3d7d09ba056ca885e9af6d4f14ebf7964d3ca6d368d3",
    "lineage_versions": "481d69a4dcf5e3fc5dbf88028af572c14a2f7b31b69b0f4a67b6416f0f84a8c8",
    "routine_history": "9481a165f616c070f0208eadd70ae57231253d76cbef21350c3aef0c92077da5",
    "owner_year_classification": "2351916ba14538d52c8d48f2f3d131f5a10030ec58f110a5b82ec5275b7e28f0",
    "signal_core_gates": "442059d6bb335ed10c40dc0714387a7e82067deeed1688ca69e9b1e2476d138f",
    "monthly_decisions": "e352ad2b9764102b1e88c4140dac824574ade40b42dc176a943856f97a65c611",
    "transaction_decision_map": "ecb75dd2d7f685974ca8566a281e611fa8111fe4beacb14e01b7341825a0574f",
    "cover_enrichment_targets": "2a54744e41ecb42c44971c850c42d39ea19fa20efca5393408ea13a43fac9c53",
    "raw_sip_targets": "9410cd967156ccc38e3b08dc999f6f2709522d96fbe9bc906141a1cd8b2e1ef8",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def self_hash(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.setdefault("integrity", {}).pop("self_sha256", None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value.setdefault("integrity", {})["self_sha256"] = self_hash(value)
    return value


def verify_seal(value: Mapping[str, Any]) -> None:
    if value.get("integrity", {}).get("self_sha256") != self_hash(value):
        raise ValueError("self_hash_mismatch")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    with part.open("wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, path)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(UTC)


def _env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"ALPACA_API_KEY", "ALPACA_SECRET_KEY"}:
            result[key.strip()] = value.strip().strip("\"'")
    return result


def credentials() -> tuple[str, str]:
    local = _env_file(REPO / ".env")
    key = os.environ.get("ALPACA_API_KEY", local.get("ALPACA_API_KEY", ""))
    secret = os.environ.get("ALPACA_SECRET_KEY", local.get("ALPACA_SECRET_KEY", ""))
    if not key or not secret:
        raise ValueError("ALPACA_CREDENTIALS_UNAVAILABLE")
    return key, secret


def sec_user_agent() -> str:
    name = subprocess.run(["git", "config", "user.name"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "config", "user.email"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("SEC_CONTACT_IDENTITY_UNAVAILABLE")
    return f"ParayaBuck H06 research {name} {email}"


class RateLimiter:
    def __init__(self, minimum_interval: float = 0.26) -> None:
        self.minimum_interval = minimum_interval
        self.last_start = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delay = self.minimum_interval - (now - self.last_start)
        if delay > 0:
            time.sleep(delay)
        self.last_start = time.monotonic()


def _request(url: str, *, headers: Mapping[str, str], params: Mapping[str, str] | None, limiter: RateLimiter) -> bytes:
    limiter.wait()
    response = requests.get(url, headers=dict(headers), params=dict(params or {}), timeout=45)
    if response.status_code != 200:
        raise ValueError(f"HTTP_{response.status_code}:{url}")
    return bytes(response.content)


ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", re.DOTALL)


def attrs(text: str) -> dict[str, str]:
    return {name.casefold(): html.unescape(value) for name, _, value in ATTR_RE.findall(text)}


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).replace("\xa0", " ").split())


def tagged(document: str, local_name: str) -> list[tuple[dict[str, str], str]]:
    pattern = re.compile(
        rf"<(?:[\w.-]+:)?{re.escape(local_name)}\b([^>]*)>(.*?)</(?:[\w.-]+:)?{re.escape(local_name)}\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    return [(attrs(raw_attrs), body) for raw_attrs, body in pattern.findall(document)]


def parse_contexts(document: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for attributes, body in tagged(document, "context"):
        context_id = attributes.get("id")
        if not context_id:
            continue
        identifiers = tagged(body, "identifier")
        instants = tagged(body, "instant")
        dimensions = []
        for member_attrs, member_body in tagged(body, "explicitMember"):
            dimensions.append({"dimension": member_attrs.get("dimension", ""), "member": clean_text(member_body)})
        result[context_id] = {
            "issuer_cik": clean_text(identifiers[0][1]).zfill(10) if identifiers else None,
            "instant": clean_text(instants[0][1]) if instants else None,
            "dimensions": sorted(dimensions, key=lambda item: (item["dimension"], item["member"])),
        }
    return result


def parse_units(document: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for attributes, body in tagged(document, "unit"):
        unit_id = attributes.get("id")
        measures = tagged(body, "measure")
        if unit_id and measures:
            result[unit_id] = clean_text(measures[0][1]).split(":")[-1].casefold()
    return result


def parse_facts(document: str, local_name: str) -> list[dict[str, Any]]:
    result = []
    for attributes, body in tagged(document, local_name):
        name = attributes.get("name", "")
        result.append({
            "name": name,
            "local_name": name.split(":")[-1].casefold(),
            "context_ref": attributes.get("contextref"),
            "unit_ref": attributes.get("unitref"),
            "decimals": attributes.get("decimals"),
            "scale": attributes.get("scale", "0"),
            "sign": attributes.get("sign", ""),
            "text": clean_text(body),
        })
    return result


def classify_title(title: str) -> str:
    text = title.casefold()
    forbidden = ("preferred", "warrant", "depositary", "unit", "note", "debenture", "fund", "etf")
    if any(word in text for word in forbidden):
        return "NON_COMMON_EQUITY"
    return "COMMON_EQUITY_EXACT" if ("common stock" in text or "common shares" in text) else "IDENTITY_AMBIGUOUS"


def compatible_titles(form4_title: str, cover_title: str) -> bool:
    left = " ".join(form4_title.casefold().replace("capital stock", "common stock").split())
    right = " ".join(cover_title.casefold().replace("capital stock", "common stock").split())
    if "series a" in left:
        return "series a" in right and "common" in right
    return "common" in left and "common" in right


def parse_cover(document_bytes: bytes, *, issuer_cik: str, symbol: str, transaction_titles: Iterable[str]) -> dict[str, Any]:
    document = document_bytes.decode("utf-8", errors="replace")
    contexts = parse_contexts(document)
    units = parse_units(document)
    nonnumeric = parse_facts(document, "nonNumeric")
    numeric = parse_facts(document, "nonFraction")
    symbols = [row for row in nonnumeric if row["local_name"] == "tradingsymbol" and row["text"].upper() == symbol.upper()]
    candidates = []
    for symbol_fact in symbols:
        context_id = symbol_fact["context_ref"]
        context = contexts.get(str(context_id), {})
        titles = [row for row in nonnumeric if row["local_name"] == "security12btitle" and row["context_ref"] == context_id]
        exchanges = [row for row in nonnumeric if row["local_name"] == "securityexchangename" and row["context_ref"] == context_id]
        if titles:
            candidates.append({"context_id": context_id, "context": context, "title": titles[0]["text"], "exchange": exchanges[0]["text"] if exchanges else None})
    if not candidates:
        return {"identity_status": "IDENTITY_MISSING", "shares_status": "SHARES_FACT_MISSING", "reason": "target_trading_symbol_or_cover_title_missing"}
    if len({(row["context_id"], row["title"]) for row in candidates}) != 1:
        return {"identity_status": "IDENTITY_AMBIGUOUS", "shares_status": "SHARES_FACT_MISSING", "reason": "multiple_target_symbol_cover_contexts"}
    cover = candidates[0]
    if cover["context"].get("issuer_cik") != issuer_cik:
        return {"identity_status": "IDENTITY_AMBIGUOUS", "shares_status": "SHARES_FACT_MISSING", "reason": "cover_context_cik_mismatch", "cover": cover}
    class_status = classify_title(cover["title"])
    if class_status != "COMMON_EQUITY_EXACT":
        return {"identity_status": class_status, "shares_status": "SHARES_FACT_MISSING", "reason": "cover_not_exact_common_equity", "cover": cover}
    if not all(compatible_titles(title, cover["title"]) for title in transaction_titles):
        return {"identity_status": "IDENTITY_AMBIGUOUS", "shares_status": "SHARES_FACT_MISSING", "reason": "transaction_title_incompatible", "cover": cover}

    target_dimensions = cover["context"].get("dimensions", [])
    common_contexts = set()
    for title_fact in (row for row in nonnumeric if row["local_name"] == "security12btitle"):
        if classify_title(title_fact["text"]) == "COMMON_EQUITY_EXACT":
            context = contexts.get(str(title_fact["context_ref"]), {})
            common_contexts.add(canonical(context.get("dimensions", [])).decode())
    single_common_class = len(common_contexts) == 1
    share_candidates = []
    anomaly = False
    for fact in numeric:
        if fact["local_name"] != "entitycommonstocksharesoutstanding":
            continue
        context = contexts.get(str(fact["context_ref"]), {})
        if context.get("issuer_cik") != issuer_cik or not context.get("instant"):
            continue
        if units.get(str(fact["unit_ref"]), str(fact["unit_ref"]).casefold()) != "shares":
            continue
        dimensions = context.get("dimensions", [])
        if target_dimensions and dimensions != target_dimensions and not (single_common_class and not dimensions):
            continue
        if not target_dimensions and dimensions:
            continue
        try:
            cleaned = fact["text"].replace(",", "").replace("$", "")
            value = Decimal(cleaned) * (Decimal(10) ** int(fact["scale"] or "0"))
            if fact["sign"] == "-":
                value = -value
            if not value.is_finite() or value <= 0:
                continue
            decimals = str(fact["decimals"] or "")
            if decimals and decimals.upper() != "INF" and not re.fullmatch(r"-?\d+", decimals):
                anomaly = True
                continue
        except (InvalidOperation, ValueError, OverflowError):
            anomaly = True
            continue
        share_candidates.append({
            "context_id": fact["context_ref"], "instant": context["instant"],
            "dimensions": dimensions, "unit": "shares", "decimals": fact["decimals"],
            "scale": fact["scale"], "value": format(value, "f"),
        })
    if not share_candidates:
        reason = "shares_scale_or_decimals_anomaly" if anomaly else "exact_class_shares_context_missing"
        return {"identity_status": "IDENTITY_EXACT", "shares_status": "SHARES_FACT_MISSING", "reason": reason, "cover": cover}
    latest_instant = max(row["instant"] for row in share_candidates)
    latest = [row for row in share_candidates if row["instant"] == latest_instant]
    if len({row["value"] for row in latest}) != 1:
        return {"identity_status": "IDENTITY_EXACT", "shares_status": "SHARES_FACT_CONFLICT", "reason": "conflicting_same_class_context", "cover": cover}
    return {"identity_status": "IDENTITY_EXACT", "shares_status": "SHARES_FACT_EXACT", "reason": None, "cover": cover, "shares_fact": latest[0]}


def load_lifetimes() -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    with LIFETIMES.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            result.setdefault(row["canonical_symbol"], []).append(row)
            if row["historical_symbol"] != row["canonical_symbol"]:
                result.setdefault(row["historical_symbol"], []).append(row)
    return result


def exact_lifetime(rows: Iterable[Mapping[str, str]], *, symbol: str, decision_at: str, candidate_id: str) -> bool:
    cutoff = parse_utc(decision_at)
    matches = []
    for row in rows:
        if symbol not in {row.get("canonical_symbol"), row.get("historical_symbol")}:
            continue
        start = parse_utc(str(row["valid_from"]))
        end = parse_utc(str(row["valid_to"])) if row.get("valid_to") else None
        if start <= cutoff and (end is None or cutoff <= end):
            matches.append(row)
    return len(matches) == 1 and matches[0].get("entity_id") == candidate_id


def has_intervening_split(symbol: str, instant: str, decision_session: str) -> bool:
    with ACTIONS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["action_type"] != "stock_split" or row["symbol"] != symbol:
                continue
            if instant < row["ex_date"] <= decision_session:
                return True
    return False


def fetch_all() -> dict[str, Any]:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    cover_targets = [dict(row) for row in connection.execute("SELECT * FROM cover_enrichment_targets ORDER BY issuer_cik,decision_timestamp")]
    sip_targets = [dict(row) for row in connection.execute("SELECT * FROM raw_sip_targets ORDER BY symbol,decision_session")]
    if len(cover_targets) != 13 or len({row["cover_accession"] for row in cover_targets}) != 10 or len(sip_targets) != 13:
        raise ValueError("frozen_target_cardinality_mismatch")
    limiter = RateLimiter()
    sec_headers = {"User-Agent": sec_user_agent(), "Accept-Encoding": "identity"}
    sec_records: dict[str, dict[str, Any]] = {}
    for row in cover_targets:
        accession = row["cover_accession"]
        if accession in sec_records:
            continue
        compact = accession.replace("-", "")
        cik = str(int(row["issuer_cik"]))
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{compact}/{row['cover_primary_document']}"
        path = SEC_CACHE / f"{accession}_{row['cover_primary_document']}"
        if not path.is_file():
            atomic_write(path, _request(url, headers=sec_headers, params=None, limiter=limiter))
        sec_records[accession] = {"path": str(path.relative_to(REPO)), "source_url": url, "bytes": path.stat().st_size, "sha256": sha256_file(path)}

    api_key, secret = credentials()
    sip_headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json", "Accept-Encoding": "identity"}
    sip_records: dict[str, dict[str, Any]] = {}
    for row in sip_targets:
        symbol, session = row["symbol"], row["decision_session"]
        target_id = f"{symbol}_{session}"
        path = SIP_CACHE / f"{target_id}.json"
        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
        if not path.is_file():
            start = f"{session}T00:00:00Z"
            end = (datetime.fromisoformat(session) + timedelta(days=1)).date().isoformat() + "T00:00:00Z"
            params = {"timeframe": "1Day", "start": start, "end": end, "feed": "sip", "adjustment": "raw", "limit": "1000"}
            atomic_write(path, _request(url, headers=sip_headers, params=params, limiter=limiter))
        sip_records[target_id] = {"path": str(path.relative_to(REPO)), "source_url": url, "request": {"symbol": symbol, "session": session, "timeframe": "1Day", "feed": "sip", "adjustment": "raw"}, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    return {"cover_targets": cover_targets, "sip_targets": sip_targets, "sec_records": sec_records, "sip_records": sip_records}


def raw_close(record: Mapping[str, Any], *, symbol: str, session: str) -> Decimal | None:
    payload = json.loads((REPO / str(record["path"])).read_text(encoding="utf-8"))
    bars = payload.get("bars", [])
    exact = [row for row in bars if str(row.get("t", ""))[:10] == session]
    if len(exact) != 1:
        return None
    try:
        value = Decimal(str(exact[0]["c"]))
    except (KeyError, InvalidOperation):
        return None
    return value if value.is_finite() and value > 0 else None


def build(acquired: Mapping[str, Any]) -> dict[str, Any]:
    core = json.loads(CORE_MANIFEST.read_text(encoding="utf-8"))
    verify_seal(core)
    observed = {name: body["logical_sha256"] for name, body in core["logical_tables"].items()}
    if any(observed.get(name) != expected for name, expected in EXPECTED_LOGICAL.items()):
        raise ValueError("core_logical_hash_mismatch")
    lifetimes = load_lifetimes()
    enrichment: dict[tuple[str, str, str], dict[str, Any]] = {}
    target_dispositions = []
    for target in acquired["cover_targets"]:
        target["decision_session"] = str(target["decision_timestamp"])[:10]
        key = (target["issuer_cik"], target["form4_symbol"], target["decision_timestamp"])
        sec_record = acquired["sec_records"][target["cover_accession"]]
        parsed = parse_cover(
            (REPO / sec_record["path"]).read_bytes(), issuer_cik=target["issuer_cik"],
            symbol=target["form4_symbol"], transaction_titles=json.loads(target["transaction_security_titles"]),
        )
        if parsed["identity_status"] == "IDENTITY_EXACT" and not exact_lifetime(
            lifetimes.get(target["form4_symbol"], []), symbol=target["form4_symbol"],
            decision_at=target["decision_timestamp"], candidate_id=target["v5_candidate_entity_id"],
        ):
            parsed.update(identity_status="IDENTITY_AMBIGUOUS", shares_status="SHARES_FACT_MISSING", reason="v5_lifetime_not_exact")
        if parsed["shares_status"] == "SHARES_FACT_EXACT":
            fact = parsed["shares_fact"]
            if fact["instant"] > target["decision_session"]:
                parsed.update(shares_status="SHARES_FACT_MISSING", reason="fact_instant_after_decision")
        sip_id = f"{target['form4_symbol']}_{target['decision_session']}"
        close = raw_close(acquired["sip_records"][sip_id], symbol=target["form4_symbol"], session=target["decision_session"])
        denominator_status = "RAW_MARKET_CAP_BASIS_MISSING"
        market_cap = None
        if parsed["identity_status"] == "IDENTITY_EXACT" and parsed["shares_status"] == "SHARES_FACT_EXACT" and close is not None:
            fact = parsed["shares_fact"]
            if has_intervening_split(target["form4_symbol"], fact["instant"], target["decision_session"]):
                denominator_status = "RAW_MARKET_CAP_BASIS_SPLIT_BLOCKED"
            else:
                market_cap = Decimal(fact["value"]) * close
                denominator_status = "RAW_MARKET_CAP_BASIS_EXACT" if market_cap.is_finite() and market_cap > 0 else "RAW_MARKET_CAP_BASIS_MISSING"
        result = {
            "issuer_cik": target["issuer_cik"], "symbol": target["form4_symbol"],
            "decision_session": target["decision_session"], "decision_timestamp": target["decision_timestamp"],
            "cover_accession": target["cover_accession"], "cover_accepted_at": target["cover_accepted_at"],
            "cover_available_at": target["cover_available_at"], "identity_status": parsed["identity_status"],
            "shares_status": parsed["shares_status"], "denominator_status": denominator_status,
            "reason": parsed.get("reason"), "cover": parsed.get("cover"), "shares_fact": parsed.get("shares_fact"),
            "raw_close": format(close, "f") if close is not None else None,
            "market_cap": format(market_cap, "f") if market_cap is not None else None,
            "sec_source_sha256": sec_record["sha256"], "sip_source_sha256": acquired["sip_records"][sip_id]["sha256"],
            "true_holdout": bool(target["true_holdout"]),
        }
        enrichment[key] = result
        target_dispositions.append(result)

    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    query = """SELECT g.row_id,n.accession,n.source_transaction_id,n.reporting_owner_cik,n.issuer_cik,
        n.issuer_symbol,n.transaction_date,n.available_at,g.information_session,n.security_title,n.shares,n.price,
        g.identity_precheck,g.v5_candidate_entity_id,m.decision_session,m.decision_timestamp,g.true_holdout
        FROM signal_core_gates g JOIN normalized_relevant_transactions n USING(row_id)
        LEFT JOIN transaction_decision_map m USING(row_id)
        WHERE g.core_status='CORE_SIGNAL_GATES_PASS_IDENTITY_DENOMINATOR_PENDING'
        ORDER BY g.row_id,m.decision_timestamp"""
    mapped_rows = []
    owner_dispositions: dict[str, str] = {}
    for dbrow in connection.execute(query):
        row = dict(dbrow)
        precheck = row["identity_precheck"]
        disposition = "IDENTITY_MISSING" if precheck == "IDENTITY_PRECHECK_MISSING" else ("NON_COMMON_EQUITY" if precheck == "IDENTITY_PRECHECK_NON_COMMON" else "IDENTITY_AMBIGUOUS")
        enriched = None
        if row["decision_timestamp"] and precheck == "IDENTITY_PRECHECK_UNIQUE_V5_SYMBOL":
            enriched = enrichment.get((row["issuer_cik"], row["issuer_symbol"], row["decision_timestamp"]))
            disposition = enriched["identity_status"] if enriched else "IDENTITY_MISSING"
        final = bool(enriched and disposition == "IDENTITY_EXACT" and enriched["denominator_status"] == "RAW_MARKET_CAP_BASIS_EXACT")
        owner_dispositions[row["row_id"]] = "FINAL_ADMITTED" if final else disposition
        if row["decision_timestamp"]:
            econ_id = hashlib.sha256(f"{row['accession']}|{row['source_transaction_id']}".encode()).hexdigest()
            mapped_rows.append({
                **{key: row[key] for key in ("row_id", "accession", "source_transaction_id", "reporting_owner_cik", "issuer_cik", "issuer_symbol", "transaction_date", "available_at", "information_session", "security_title", "shares", "price", "decision_session", "decision_timestamp")},
                "economic_transaction_id": econ_id, "identity_status": disposition,
                "denominator_status": enriched["denominator_status"] if enriched else "RAW_MARKET_CAP_BASIS_NOT_TARGETED",
                "market_cap": enriched["market_cap"] if enriched else None,
                "final_admitted": final, "true_holdout": bool(row["true_holdout"]),
            })
    all_core_ids = {row[0] for row in connection.execute("SELECT row_id FROM signal_core_gates WHERE core_status='CORE_SIGNAL_GATES_PASS_IDENTITY_DENOMINATOR_PENDING'")}
    if len(all_core_ids) != 85 or set(owner_dispositions) != all_core_ids:
        raise ValueError("core_row_disposition_incomplete")
    admitted = [row for row in mapped_rows if row["final_admitted"]]
    unique_transactions = {row["economic_transaction_id"] for row in admitted}
    positive_issuer_months = {(row["issuer_cik"], row["decision_session"]) for row in admitted}
    counts = {
        "core_pass_rows": len(all_core_ids),
        "identity_exact_owner_rows": sum(value == "FINAL_ADMITTED" for value in owner_dispositions.values()),
        "identity_excluded_owner_rows": sum(value != "FINAL_ADMITTED" for value in owner_dispositions.values()),
        "denominator_exact_targets": sum(row["denominator_status"] == "RAW_MARKET_CAP_BASIS_EXACT" for row in target_dispositions),
        "denominator_excluded_targets": sum(row["denominator_status"] != "RAW_MARKET_CAP_BASIS_EXACT" for row in target_dispositions),
        "final_admitted_owner_observations": len({row["row_id"] for row in admitted}),
        "final_admitted_row_decisions": len(admitted),
        "canonical_unique_economic_transactions": len(unique_transactions),
        "issuer_months_with_positive_scores": len(positive_issuer_months),
        "decisions_with_positive_scores": len({row["decision_session"] for row in admitted}),
    }
    local = seal({
        "schema": "h06-final-enrichment-local-v1", "status": STATUS, "source_commit": BASE,
        "target_dispositions": target_dispositions, "owner_dispositions": owner_dispositions,
        "economic_inputs": mapped_rows, "counts": counts, "integrity": {},
    })
    atomic_write(LOCAL_RESULT, json.dumps(local, indent=2, sort_keys=True, ensure_ascii=False).encode() + b"\n")
    return local


def tracked_artifacts(local: Mapping[str, Any], acquired: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    files = sorted([*acquired["sec_records"].values(), *acquired["sip_records"].values()], key=lambda row: row["path"])
    receipt = seal({
        "schema": "h06-final-enrichment-receipt-v1", "status": "H06_FINAL_ENRICHMENT_ACQUIRED",
        "source_commit": BASE, "network_used": True, "credentials_persisted": False,
        "request_rate_max_starts_per_second": 4, "cover_targets_acquired": 10, "raw_sip_targets_acquired": 13,
        "files": files, "local_result": {"path": str(LOCAL_RESULT.relative_to(REPO)), "sha256": sha256_file(LOCAL_RESULT), "bytes": LOCAL_RESULT.stat().st_size},
        "integrity": {},
    })
    dispositions = local["target_dispositions"]
    manifest = seal({
        "schema": "h06-full-structural-admission-v2", "status": STATUS, "source_commit": BASE,
        "cutoff": "2026-07-31T23:59:59Z", "structural_admission_performed": True,
        "core_logical_hashes": EXPECTED_LOGICAL, "counts": local["counts"],
        "target_dispositions": dispositions, "owner_dispositions": local["owner_dispositions"],
        "economic_input_rows": local["economic_inputs"],
        "enrichment_receipt": {"path": str(RECEIPT.relative_to(REPO)), "sha256": hashlib.sha256((json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()).hexdigest()},
        "economic_run": False, "outcomes_accessed": False,
        "economic_transaction_id_rule": "sha256(accession + '|' + source_transaction_id); reporting owner CIK excluded; count once per decision",
        "boundaries": ["raw SIP bars are denominator only", "no canonical V5 outcome prices read", "no scoring/ranking/returns", "no PAPER/LIVE/registry/V3 mutation"],
        "integrity": {},
    })
    c = local["counts"]
    report = "\n".join([
        "# H06 full structural admission — 2026-09-15", "", f"Status: `{STATUS}`.", "",
        "The frozen 13-target enrichment set is fully dispositioned. Raw SIP observations are denominator-only; no post-decision return was read or calculated.", "",
        "## Counts", "", f"- Core-pass owner rows: `{c['core_pass_rows']}`.",
        f"- Final-admitted owner observations: `{c['final_admitted_owner_observations']}`.",
        f"- Final-admitted row/decision mappings: `{c['final_admitted_row_decisions']}`.",
        f"- Canonical unique economic transactions: `{c['canonical_unique_economic_transactions']}`.",
        f"- Positive-score issuer-months: `{c['issuer_months_with_positive_scores']}` across `{c['decisions_with_positive_scores']}` decisions.", "",
        "## Frozen de-duplication", "", "The canonical economic transaction ID excludes reporting-owner CIK. Any transaction with at least one admitted owner is counted once per decision.", "",
        "## Boundary", "", "No economic score, ranking, outcome return, V3 change, PAPER/LIVE change, registry update, or promotion occurred.", "",
    ])
    return receipt, manifest, report


def write_tracked(receipt: Mapping[str, Any], manifest: Mapping[str, Any], report: str) -> None:
    atomic_write(RECEIPT, json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False).encode() + b"\n")
    atomic_write(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode() + b"\n")
    atomic_write(REPORT, report.encode())


def verify() -> dict[str, Any]:
    for path in (LOCAL_RESULT, RECEIPT, MANIFEST):
        payload = json.loads(path.read_text(encoding="utf-8"))
        verify_seal(payload)
    local = json.loads(LOCAL_RESULT.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if local["status"] != STATUS or manifest["status"] != STATUS or len(manifest["owner_dispositions"]) != 85 or len(manifest["target_dispositions"]) != 13:
        raise ValueError("structural_status_or_cardinality_invalid")
    if manifest["enrichment_receipt"]["sha256"] != sha256_file(RECEIPT):
        raise ValueError("enrichment_receipt_hash_mismatch")
    if receipt["local_result"]["sha256"] != sha256_file(LOCAL_RESULT):
        raise ValueError("local_result_hash_mismatch")
    for row in receipt["files"]:
        path = REPO / row["path"]
        if not path.is_file() or path.stat().st_size != row["bytes"] or sha256_file(path) != row["sha256"]:
            raise ValueError(f"cache_custody_mismatch:{row['path']}")
    return {"status": "H06_FULL_STRUCTURAL_ADMISSION_VERIFIED", "counts": manifest["counts"], "read_only": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(), sort_keys=True))
        return 0
    acquired = fetch_all()
    local = build(acquired)
    receipt, manifest, report = tracked_artifacts(local, acquired)
    write_tracked(receipt, manifest, report)
    print(json.dumps(verify(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
