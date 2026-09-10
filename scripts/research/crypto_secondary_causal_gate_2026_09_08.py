"""Research-only acquisition and causal/schema gate for K02/K05/K06/K07/K08/K10/K12.

The gate consumes receipt-backed acquisition artifacts and public probe bodies.
It never substitutes OHLCV for causal fields and never runs economics.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from io import TextIOWrapper
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[2]
OLD_ROOT = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07"
ROOT = REPO / "data/research/external/crypto_secondary_acquisition_2026_09_08"
PROTOCOL = REPO / "docs/research/crypto_secondary_causal_protocol_2026_09_08.md"
REPORT_MD = REPO / "docs/research/crypto_secondary_causal_gate_report_2026_09_08.md"
REPORT = REPO / "data/research_runs/crypto_secondary_causal_gate_2026_09_08/gate_report.json"
MANIFEST = REPO / "data/research_manifests/crypto_secondary_causal_gate_2026_09_08.json"
TEST = REPO / "tests/research/test_crypto_secondary_causal_gate_2026_09_08.py"
VALIDATION_RECEIPT = REPO / "data/research_runs/crypto_secondary_causal_gate_2026_09_08/validation_receipt.json"
FULL_REPORT = REPO / "docs/research/strategy_queue_full_report_2026_09_06.md"
CRYPTO_REPORT = REPO / "docs/research/strategy_queue_crypto_review_2026_09_06.md"
SCREEN_PROTOCOL = REPO / "docs/research/portfolio_expansion_crypto_screen_protocol_2026_09_05.md"
CUTOFF = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
MUTABLE_EVIDENCE_KINDS = frozenset({"public_endpoint", "public_token_route", "issuer_public_archive", "public_announcement"})

K02_RAW = OLD_ROOT / "coinmetrics/k02_nvt_adj_unavailable_btc_eth_2019_2026-07-31.json"
K05_HOME = OLD_ROOT / "other/tokenomist_home.body"
K05_API = OLD_ROOT / "other/tokenomist_api_root.body"
K06_BTC = OLD_ROOT / "farside/btc.html"
K06_BTC_SNAPSHOT = OLD_ROOT / "farside/btc_daily_snapshot.csv"
K07_RAW = OLD_ROOT / "coinmetrics/k07_exchange_flows_btc_eth_2019_2026-07-31.json"
K08_CATALOG = OLD_ROOT / "coinmetrics/k08_stablecoin_catalog_assets.json"
K08_PROBE = OLD_ROOT / "coinmetrics/k08_stablecoin_flow_probe.json"
K10 = OLD_ROOT / "normalized/K10_binance_liquidation_snapshot_pilot.csv"
K12_RAW = OLD_ROOT / "coinmetrics/k12_sopr_unavailable_btc_eth_2019_2026-07-31.json"
K02_RECEIPT = Path(f"{K02_RAW}.receipt.json")
K05_HOME_RECEIPT = Path(f"{K05_HOME}.receipt.json")
K05_API_RECEIPT = Path(f"{K05_API}.receipt.json")
K06_BTC_RECEIPT = Path(f"{K06_BTC}.receipt.json")
K06_BTC_SNAPSHOT_RECEIPT = Path(f"{K06_BTC_SNAPSHOT}.receipt.json")
K07_RECEIPT = Path(f"{K07_RAW}.receipt.json")
K08_CATALOG_RECEIPT = Path(f"{K08_CATALOG}.receipt.json")
K08_PROBE_RECEIPT = Path(f"{K08_PROBE}.receipt.json")
K10_RECEIPT = Path(f"{K10}.receipt.json")
K12_RECEIPT = Path(f"{K12_RAW}.receipt.json")
K06_ETH_PROBE = ROOT / "probes/farside/ethereum_403.body"
K06_BTC_ALL_PROBE = ROOT / "probes/farside/bitcoin_all_data_403.body"
K06_ETH_ALL_PROBE = ROOT / "probes/farside/ethereum_all_data_403.body"
K10_BTC_PROBE = ROOT / "probes/binance/BTCUSD_PERP-liquidationSnapshot-2026-07-30.body"
K10_ETH_PROBE = ROOT / "probes/binance/ETHUSD_PERP-liquidationSnapshot-2026-07-30.body"
TOKENOMIST_PROBE = ROOT / "probes/tokenomist/home.body"
TOKENOMIST_API_PROBE = ROOT / "probes/tokenomist/api_root.body"
K06_ETH_PROBE_RECEIPT = Path(f"{K06_ETH_PROBE}.receipt.json")
K06_BTC_ALL_PROBE_RECEIPT = Path(f"{K06_BTC_ALL_PROBE}.receipt.json")
K06_ETH_ALL_PROBE_RECEIPT = Path(f"{K06_ETH_ALL_PROBE}.receipt.json")
K10_BTC_PROBE_RECEIPT = Path(f"{K10_BTC_PROBE}.receipt.json")
K10_ETH_PROBE_RECEIPT = Path(f"{K10_ETH_PROBE}.receipt.json")
TOKENOMIST_PROBE_RECEIPT = Path(f"{TOKENOMIST_PROBE}.receipt.json")
TOKENOMIST_API_PROBE_RECEIPT = Path(f"{TOKENOMIST_API_PROBE}.receipt.json")
EXPANDED_PROBE_ROOT = ROOT / "probes/expanded"
PROBE_INDEX = EXPANDED_PROBE_ROOT / "probe_inventory.json"
ACQUISITION_SCRIPT = REPO / "scripts/research/acquire_crypto_secondary_public_2026_09_08.py"
K06_ISSUER_SET = ("IBIT", "GBTC", "FBTC", "BITB", "ARKB", "ETHA")
K05_FROZEN_ASSETS = (
    "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD",
    "AVAX/USD", "LINK/USD", "LTC/USD", "BCH/USD", "DOT/USD", "UNI/USD",
    "AAVE/USD", "ARB/USD", "FIL/USD", "RENDER/USD", "ONDO/USD", "HYPE/USD",
    "SHIB/USD", "GRT/USD", "CRV/USD", "PEPE/USD",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug_basis(card: str, kind: str, endpoint: str, slug: str) -> str:
    value = json.dumps({"card": card, "kind": kind, "endpoint": endpoint, "collector_slug": slug}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _zip_recipe_id(card: str, provider: str, endpoint: str, archive_sha256: str | None, members: list[dict[str, Any]]) -> tuple[str, str | None, str | None]:
    names = [str(member.get("name", "")) for member in members]
    source = f"{endpoint} {' '.join(names)}"
    dates = sorted(set(re.findall(r"20\d{2}[-_]\d{2}[-_]\d{2}", source)))
    symbols = sorted({name.split("-", 1)[0] for name in names if "-" in name})
    identity = {"card": card, "provider": provider, "endpoint": endpoint, "archive_sha256": archive_sha256, "date": dates[0] if dates else None, "symbol": symbols[0] if symbols else None, "members": members}
    recipe_id = "crypto_zip_recipe_v1_" + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return recipe_id, identity["date"], identity["symbol"]


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"naive_timestamp:{value}")
    return parsed.astimezone(UTC)


def receipt(path: Path) -> dict[str, Any]:
    return read_json(path)


def _payload_for_receipt(receipt_path: Path) -> Path:
    suffix = ".receipt.json"
    if not receipt_path.name.endswith(suffix):
        raise ValueError(f"receipt_name_required:{receipt_path}")
    return receipt_path.with_name(receipt_path.name[: -len(suffix)])


def _verify_count_semantics(payload_path: Path, item: dict[str, Any], receipt_path: Path) -> None:
    if "row_count" not in item or "count_semantics" not in item:
        raise ValueError(f"receipt_count_semantics_missing:{receipt_path}")
    count = item["row_count"]
    semantics = item["count_semantics"]
    if semantics == "not_applicable":
        if count is not None:
            raise ValueError(f"receipt_count_not_applicable_numeric:{receipt_path}")
        return
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError(f"receipt_row_count_invalid:{receipt_path}")
    if semantics == "csv_data_rows":
        if payload_path.suffix.lower() != ".csv":
            raise ValueError(f"receipt_count_semantics_path_mismatch:{receipt_path}")
        with payload_path.open(newline="", encoding="utf-8") as handle:
            actual_count = max(0, sum(1 for _ in csv.reader(handle)) - 1)
        if actual_count != count:
            raise ValueError(f"receipt_row_count_mismatch:{receipt_path}")
    elif semantics == "json_data_array":
        try:
            body = json.loads(payload_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError(f"receipt_json_count_body_invalid:{receipt_path}") from None
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ValueError(f"receipt_json_count_body_not_tabular:{receipt_path}")
        if len(body["data"]) != count:
            raise ValueError(f"receipt_row_count_mismatch:{receipt_path}")
    elif semantics != "zip_member_data_rows":
        raise ValueError(f"receipt_count_semantics_invalid:{receipt_path}")


def _is_local_artifact(item: dict[str, Any]) -> bool:
    return item.get("receipt_schema") == "local_artifact" or ("endpoint" not in item and isinstance(item.get("schema"), list))


def _verify_transport_fields(item: dict[str, Any], receipt_path: Path) -> None:
    local = _is_local_artifact(item)
    if not local:
        for field in ("endpoint", "final_url"):
            value = item.get(field)
            parsed = urlparse(value) if isinstance(value, str) else None
            if parsed is None or parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"receipt_{field}_https_required:{receipt_path}")
    for field in ("provider", "card", "kind", "timestamp_precision"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            raise ValueError(f"receipt_{field}_required:{receipt_path}")
    retrieved = item.get("retrieved_at_utc")
    if not isinstance(retrieved, str) or not retrieved.strip():
        raise ValueError(f"receipt_retrieval_timestamp_invalid:{receipt_path}")
    try:
        aware(retrieved)
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"receipt_retrieval_timestamp_invalid:{receipt_path}") from None
    if "content_type" not in item or (item["content_type"] is not None and not isinstance(item["content_type"], str)):
        raise ValueError(f"receipt_content_type_required:{receipt_path}")
    if not isinstance(item.get("credential_fields_persisted"), bool):
        raise ValueError(f"receipt_credential_flag_invalid:{receipt_path}")
    if "error" not in item or (item["error"] is not None and not isinstance(item["error"], (str, dict))):
        raise ValueError(f"receipt_error_field_required:{receipt_path}")
    status = item.get("status")
    if status is None:
        if not local and not item.get("error"):
            raise ValueError(f"receipt_status_error_consistency:{receipt_path}")
    elif not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        raise ValueError(f"receipt_status_invalid:{receipt_path}")
    elif 200 <= status < 400 and item.get("error"):
        raise ValueError(f"receipt_status_error_consistency:{receipt_path}")
    elif status >= 400 and isinstance(item.get("error"), str) and item["error"].startswith("HTTPError:"):
        try:
            if int(item["error"].split(":", 1)[1]) != status:
                raise ValueError(f"receipt_status_error_consistency:{receipt_path}")
        except (IndexError, ValueError):
            raise ValueError(f"receipt_status_error_consistency:{receipt_path}") from None


def verify_receipt_pair(payload_path: Path, receipt_path: Path, expected: dict[str, Any] | None = None) -> int:
    """Verify a receipt's payload hash and declared transport metadata.

    Receipts are acquisition evidence, so malformed hashes, missing endpoint/status,
    byte mismatches, and inconsistent row counts fail closed before any gate runs.
    """
    item = read_json(receipt_path)
    declared = item.get("sha256")
    if not isinstance(declared, str) or re.fullmatch(r"[0-9a-fA-F]{64}", declared) is None:
        raise ValueError(f"receipt_sha256_format:{receipt_path}")
    if not payload_path.is_file():
        raise ValueError(f"receipt_payload_missing:{payload_path}")
    actual = sha256(payload_path)
    if actual.lower() != declared.lower():
        raise ValueError(f"receipt_payload_hash_mismatch:{receipt_path}")
    if "bytes" not in item or not isinstance(item["bytes"], int) or item["bytes"] != payload_path.stat().st_size:
        raise ValueError(f"receipt_bytes_mismatch:{receipt_path}")
    _verify_transport_fields(item, receipt_path)
    _verify_count_semantics(payload_path, item, receipt_path)
    if payload_path.suffix.lower() == ".zip" and not isinstance(item.get("recipe"), dict):
        raise ValueError(f"receipt_zip_recipe_missing:{receipt_path}")
    if payload_path.suffix.lower() == ".zip":
        recipe = item["recipe"]
        if not isinstance(recipe.get("recipe_id"), str) or not recipe["recipe_id"].strip() or recipe.get("request_url") != item.get("endpoint") or recipe.get("final_url") != item.get("final_url"):
            raise ValueError(f"receipt_zip_recipe_identity_mismatch:{receipt_path}")
        members_for_id = recipe.get("members", item.get("members", []))
        if not isinstance(members_for_id, list):
            raise ValueError(f"receipt_zip_recipe_members_invalid:{receipt_path}")
        expected_recipe_id, expected_date, expected_symbol = _zip_recipe_id(str(item.get("card")), str(item.get("provider")), str(item.get("endpoint")), item.get("archive_sha256"), members_for_id)
        if recipe.get("recipe_id") != expected_recipe_id or recipe.get("date") != expected_date or recipe.get("symbol") != expected_symbol:
            raise ValueError(f"receipt_zip_recipe_canonical_mismatch:{receipt_path}")
        for source in recipe.get("source_files", []):
            if not isinstance(source, dict):
                continue
            source_receipt = source.get("receipt")
            source_receipt_sha = source.get("receipt_sha256")
            if source_receipt is None and source_receipt_sha is None:
                continue
            if not isinstance(source_receipt, str) or not isinstance(source_receipt_sha, str) or re.fullmatch(r"[0-9a-fA-F]{64}", source_receipt_sha) is None:
                raise ValueError(f"receipt_source_receipt_hash_invalid:{receipt_path}")
            source_receipt_path = receipt_path.parent.parent / source_receipt
            if not source_receipt_path.is_file() or sha256(source_receipt_path).lower() != source_receipt_sha.lower():
                raise ValueError(f"receipt_source_receipt_hash_mismatch:{receipt_path}")
        for path_key, hash_key in (("source_archive", "source_archive_sha256"), ("source_receipt", "source_receipt_sha256")):
            source_path_value = recipe.get(path_key)
            source_hash = recipe.get(hash_key)
            if source_path_value is None and source_hash is None:
                continue
            if not isinstance(source_path_value, str) or not isinstance(source_hash, str) or re.fullmatch(r"[0-9a-fA-F]{64}", source_hash) is None:
                raise ValueError(f"receipt_{hash_key}_invalid:{receipt_path}")
            source_path = REPO / source_path_value
            if not source_path.is_file():
                source_path = receipt_path.parent.parent / source_path_value
            if not source_path.is_file() or sha256(source_path).lower() != source_hash.lower():
                raise ValueError(f"receipt_{hash_key}_mismatch:{receipt_path}")
    if payload_path.suffix.lower() == ".zip" and item.get("status") == 200:
        if not item["recipe"].get("recipe_id"):
            raise ValueError(f"receipt_zip_recipe_missing:{receipt_path}")
        if item.get("archive_sha256") != declared or item["recipe"].get("archive_sha256") != declared:
            raise ValueError(f"receipt_archive_hash_mismatch:{receipt_path}")
        source_archive = item["recipe"].get("source_archive")
        source_archive_sha = item["recipe"].get("source_archive_sha256")
        binding = item["recipe"].get("receipt_binding")
        if not isinstance(binding, dict) or binding.get("kind") != "self_contained_raw_http":
            raise ValueError(f"receipt_zip_receipt_binding_missing:{receipt_path}")
        for field in ("endpoint", "final_url", "status", "retrieved_at_utc", "content_type", "body_sha256"):
            if binding.get(field) != item.get(field) and not (field == "body_sha256" and binding.get(field) == item.get("sha256")):
                raise ValueError(f"receipt_zip_receipt_binding_mismatch:{field}:{receipt_path}")
        if source_archive is not None or source_archive_sha is not None:
            if not isinstance(source_archive, str) or not isinstance(source_archive_sha, str) or re.fullmatch(r"[0-9a-fA-F]{64}", source_archive_sha) is None:
                raise ValueError(f"receipt_zip_source_archive_binding_invalid:{receipt_path}")
            source_archive_path = REPO / source_archive
            if not source_archive_path.is_file() or source_archive_path.resolve() != payload_path.resolve() or sha256(source_archive_path).lower() != source_archive_sha.lower():
                raise ValueError(f"receipt_zip_source_archive_binding_mismatch:{receipt_path}")
        members = item.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError(f"receipt_zip_members_missing:{receipt_path}")
        with ZipFile(payload_path) as archive:
            infos = {info.filename: info for info in archive.infolist()}
            if set(infos) != {member.get("name") for member in members if isinstance(member, dict)}:
                raise ValueError(f"receipt_zip_member_names_mismatch:{receipt_path}")
            for member in members:
                if not isinstance(member, dict) or member.get("name") not in infos:
                    raise ValueError(f"receipt_zip_member_invalid:{receipt_path}")
                info = infos[member["name"]]
                if member.get("crc") != info.CRC or member.get("size") != info.file_size:
                    raise ValueError(f"receipt_zip_member_identity_mismatch:{receipt_path}")
                if info.is_dir():
                    continue
                with archive.open(info) as stream:
                    reader = csv.reader(TextIOWrapper(stream, encoding="utf-8", errors="replace", newline=""))
                    header = next(reader, [])
                    first = next(reader, None)
                    count = 0 if first is None else 1
                    last = first
                    for row in reader:
                        count += 1
                        last = row
                if member.get("header") != header or member.get("row_count") != count or member.get("first_id") != (first[0] if first else None) or member.get("last_id") != (last[0] if last else None):
                    raise ValueError(f"receipt_zip_member_content_mismatch:{receipt_path}")
            if item["row_count"] != sum(member.get("row_count", 0) for member in members if isinstance(member, dict) and not (infos[member["name"]].is_dir())):
                raise ValueError(f"receipt_zip_row_count_mismatch:{receipt_path}")
    # Some normalized receipts retain a hash for a separately named raw body.
    for ref_key, hash_key in (("raw_html", "raw_html_sha256"), ("archive", "archive_sha256"), ("body", "body_sha256")):
        if ref_key in item and hash_key in item:
            ref_name = item[ref_key]
            declared_ref = item[hash_key]
            if not isinstance(ref_name, str) or not isinstance(declared_ref, str) or re.fullmatch(r"[0-9a-fA-F]{64}", declared_ref) is None:
                raise ValueError(f"receipt_{hash_key}_format:{receipt_path}")
            ref_path = receipt_path.parent.parent / ref_name
            if not ref_path.is_file():
                ref_path = OLD_ROOT / ref_name
            if not ref_path.is_file() or sha256(ref_path).lower() != declared_ref.lower():
                raise ValueError(f"receipt_{hash_key}_mismatch:{receipt_path}")
    recipe = item.get("recipe")
    if isinstance(recipe, dict):
        source_files = recipe.get("source_files", [])
        if not isinstance(source_files, list):
            raise ValueError(f"receipt_source_files_invalid:{receipt_path}")
        for source in source_files:
            source_name = source.get("path", source.get("file")) if isinstance(source, dict) else None
            if not isinstance(source, dict) or not isinstance(source_name, str):
                raise ValueError(f"receipt_source_entry_invalid:{receipt_path}")
            source_path = receipt_path.parent.parent / source_name
            if not source_path.is_file():
                source_path = OLD_ROOT / source_name
            source_sha = source.get("sha256")
            if not source_path.is_file() or not isinstance(source_sha, str) or re.fullmatch(r"[0-9a-fA-F]{64}", source_sha) is None or sha256(source_path).lower() != source_sha.lower():
                raise ValueError(f"receipt_source_hash_mismatch:{receipt_path}:{source_name}")
            endpoint = source.get("endpoint")
            if endpoint is not None and (not isinstance(endpoint, str) or not endpoint.strip()):
                raise ValueError(f"receipt_source_endpoint_invalid:{receipt_path}")
    if expected is not None:
        for field in ("endpoint", "final_url", "status", "bytes", "sha256", "provider", "card", "kind", "content_type", "retrieved_at_utc", "timestamp_precision", "credential_fields_persisted", "attempts", "retry_policy", "terminal_status", "collector_slug", "slug_derivation", "slug_basis_sha256", "row_count", "count_semantics", "recipe", "error", "mutable_evidence_rule"):
            if field not in item or field not in expected:
                raise ValueError(f"receipt_inventory_field_missing:{field}:{receipt_path}")
            if item.get(field) != expected.get(field):
                raise ValueError(f"receipt_inventory_field_mismatch:{field}:{receipt_path}")
    return 1


def verify_receipt(receipt_path: Path) -> int:
    return verify_receipt_pair(_payload_for_receipt(receipt_path), receipt_path)


def verified_receipt(receipt_path: Path) -> dict[str, Any]:
    """Read transport/count metadata only after the payload hash is verified."""
    verify_receipt(receipt_path)
    return receipt(receipt_path)


def derive_asset_specific_evidence(announcement_body: str, confirmation_body: str, source_probe_ids: list[str] | None = None) -> dict[str, Any]:
    """Derive token and unlock identity only from structured route evidence."""
    text = f"{announcement_body}\n{confirmation_body}"
    contract_ids = sorted(set(re.findall(r'(?i)(?:contract(?:_address|Address)|token_contract|contract_id)\s*[=:]\s*["\']?(0x[a-f0-9]{40})', text)))
    unlock_ids = sorted(set(re.findall(r'(?i)(?:transaction(?:_hash|Hash)|tx_hash|unlock_id)\s*[=:]\s*["\']?(0x[a-f0-9]{64})', text)))
    missing = []
    if not contract_ids:
        missing.append("contract identifier")
    if not unlock_ids:
        missing.append("unlock transaction identifier")
    reason = "verified route bodies contain " + ", ".join(missing) + " missing" if missing else "verified route bodies contain contract and unlock identifiers"
    return {"contract_status": "RESOLVED" if contract_ids else "UNRESOLVED", "unlock_id_status": "RESOLVED" if unlock_ids else "UNRESOLVED", "contract_ids": contract_ids, "unlock_ids": unlock_ids, "reason": reason, "evidence_source_probes": list(source_probe_ids or [])}


def source_ref(path: Path, *, provider: str, role: str, receipt_path: Path | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path.relative_to(REPO)),
        "sha256": sha256(path),
        "provider": provider,
        "role": role,
    }
    if receipt_path is not None:
        item["receipt"] = {
            "path": str(receipt_path.relative_to(REPO)),
            "sha256": sha256(receipt_path),
            "status": receipt(receipt_path).get("status"),
            "retrieved_at_utc": receipt(receipt_path).get("retrieved_at_utc"),
            "endpoint": receipt(receipt_path).get("endpoint"),
        }
    return item


def expanded_inventory() -> dict[str, Any]:
    return read_json(PROBE_INDEX) if PROBE_INDEX.is_file() else {"probes": []}


def expanded_probes(card: str) -> list[dict[str, Any]]:
    return [item for item in verify_expanded_inventory()["probes"] if item.get("card") == card]


def verify_expanded_inventory(path: Path = PROBE_INDEX) -> dict[str, Any]:
    inventory = read_json(path)
    if inventory.get("status") != "PUBLIC_PROBES_CAPTURED_NO_ECONOMICS" or inventory.get("acquisition_state") != "CURRENT_ACQUISITION_RUN_CLOSED_WITH_BLOCKERS" or inventory.get("next_stage_approval") != "NOT_APPROVED" or inventory.get("historical_cutoff") != "2026-07-31T23:59:59Z":
        raise ValueError("expanded_inventory_header_invalid")
    if inventory.get("credential_fields_persisted") is not False:
        raise ValueError("expanded_inventory_credential_flag_invalid")
    for field in ("retrieved_at_utc", "frozen_at_utc"):
        try:
            aware(str(inventory[field]))
        except (KeyError, ValueError, TypeError):
            raise ValueError(f"expanded_inventory_{field}_invalid") from None
    collection = inventory.get("collection_window")
    if not isinstance(collection, dict) or set(collection) != {"start_utc", "end_utc"}:
        raise ValueError("expanded_inventory_collection_window_invalid")
    try:
        collection_start, collection_end = aware(str(collection["start_utc"])), aware(str(collection["end_utc"]))
    except (ValueError, TypeError):
        raise ValueError("expanded_inventory_collection_window_invalid") from None
    if collection_start > collection_end or aware(str(inventory["frozen_at_utc"])) < collection_end:
        raise ValueError("expanded_inventory_collection_window_order_invalid")
    probes = inventory.get("probes")
    if not isinstance(probes, list):
        raise ValueError("expanded_inventory_probes_required")
    seen: set[str] = set()
    for item in probes:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            raise ValueError("expanded_inventory_record_invalid")
        slug = item["slug"]
        if slug in seen:
            raise ValueError(f"expanded_inventory_duplicate_slug:{slug}")
        seen.add(slug)
        body_name = item.get("body")
        receipt_name = item.get("receipt")
        if not isinstance(body_name, str) or not isinstance(receipt_name, str):
            raise ValueError(f"expanded_inventory_paths_missing:{slug}")
        body = REPO / body_name
        receipt_path = REPO / receipt_name
        if body.resolve().parent != EXPANDED_PROBE_ROOT.resolve() or receipt_path != Path(f"{body}.receipt.json"):
            raise ValueError(f"expanded_inventory_path_binding:{slug}")
        for field in ("slug", "body", "receipt", "mutable_current_evidence"):
            if field not in item:
                raise ValueError(f"expanded_inventory_field_missing:{field}:{slug}")
        if item["body"] != body_name or item["receipt"] != receipt_name or not isinstance(item["mutable_current_evidence"], bool):
            raise ValueError(f"expanded_inventory_field_mismatch:{slug}")
        if item.get("collector_slug") != slug or item.get("slug_derivation") != "collector_declaration_v1" or item.get("slug_basis_sha256") != _slug_basis(str(item.get("card")), str(item.get("kind")), str(item.get("endpoint")), slug):
            raise ValueError(f"expanded_inventory_slug_binding:{slug}")
        if item.get("mutable_evidence_rule") != "kind_in_frozen_mutable_evidence_kinds_v1" or item["mutable_current_evidence"] != (item.get("kind") in MUTABLE_EVIDENCE_KINDS):
            raise ValueError(f"expanded_inventory_mutability_binding:{slug}")
        verify_receipt_pair(body, receipt_path, expected=item)
    if inventory.get("probe_count") != len(probes):
        raise ValueError("expanded_inventory_probe_count_mismatch")
    counts_by_card: dict[str, int] = {}
    for item in probes:
        counts_by_card[item["card"]] = counts_by_card.get(item["card"], 0) + 1
    if inventory.get("counts_by_card") != counts_by_card or inventory.get("credential_fields_persisted_count") != sum(bool(item["credential_fields_persisted"]) for item in probes):
        raise ValueError("expanded_inventory_counts_mismatch")
    retrieved = sorted(aware(str(item["retrieved_at_utc"])) for item in probes)
    if retrieved and (retrieved[0] != collection_start or retrieved[-1] != collection_end):
        raise ValueError("expanded_inventory_collection_window_records_mismatch")
    by_slug = {item["slug"]: item for item in probes}
    records = inventory.get("asset_specific_source_records")
    if not isinstance(records, list) or {record.get("asset") for record in records} != set(K05_FROZEN_ASSETS):
        raise ValueError("expanded_inventory_asset_records_invalid")
    for record in records:
        announcement = by_slug.get(record.get("announcement_probe"))
        confirmation = by_slug.get(record.get("confirmation_probe"))
        if announcement is None or confirmation is None:
            raise ValueError("expanded_inventory_asset_route_missing")
        evidence = derive_asset_specific_evidence((REPO / announcement["body"]).read_text(encoding="utf-8", errors="replace"), (REPO / confirmation["body"]).read_text(encoding="utf-8", errors="replace"), [announcement["slug"], confirmation["slug"]])
        for field in ("contract_status", "unlock_id_status", "contract_ids", "unlock_ids", "reason", "evidence_source_probes"):
            if record.get(field) != evidence[field]:
                raise ValueError(f"expanded_inventory_asset_evidence_mismatch:{record.get('asset')}:{field}")
    bound = inventory.get("bound_source_records")
    if not isinstance(bound, list) or not bound:
        raise ValueError("bound_source_records_required")
    for record in bound:
        body = REPO / str(record.get("body", ""))
        receipt_path = REPO / str(record.get("receipt", ""))
        if record.get("card") != "K07" or not body.is_file() or not receipt_path.is_file():
            raise ValueError("bound_source_record_invalid")
        receipt_item = read_json(receipt_path)
        verify_receipt_pair(body, receipt_path)
        for field, value in (("endpoint", receipt_item.get("endpoint")), ("status", receipt_item.get("status")), ("row_count", receipt_item.get("row_count")), ("count_semantics", receipt_item.get("count_semantics"))):
            if record.get(field) != value:
                raise ValueError(f"bound_source_field_mismatch:{field}")
        if record.get("body_sha256") != sha256(body) or record.get("receipt_sha256") != sha256(receipt_path):
            raise ValueError("bound_source_hash_mismatch")
    return inventory


CONTRACTS: dict[str, dict[str, Any]] = {
    "K02": {
        "provider": "Coin Metrics adjusted-transfer methodology",
        "universe": "configured BTC/ETH assets with complete provider history",
        "cadence": "daily completed observations",
        "field_meaning": "NVTAdj is market value divided by adjusted transfer value; adjusted-transfer version and chain policy are required",
        "formula": "NVTAdj = market_value / adjusted_transfer_value",
        "decision_clock": "completed daily observation",
        "availability_boundary": "available_at <= decision_time",
        "signal_formula_window_threshold": "one-period lag; within-asset only; smoothing, direction and threshold unspecified by authoritative review",
        "execution_shift": "one-period lag, next tradable spot open",
        "portfolio_role": "slow usage/value state, long/cash",
        "controls": "incumbent, candidate, passive/cash or matched-exposure control, shared-capital diagnostic",
        "cost_stress": [1.0, 2.0, 3.0],
        "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "not specified by source review"},
        "minimum_breadth_history": "BTC and ETH with complete daily history across the selected window",
    },
    "K05": {
        "provider": "token-specific issuer/governance primary announcement plus chain confirmation",
        "universe": "frozen configured 22-pair pool; every asset requires primary event evidence",
        "cadence": "event-only",
        "field_meaning": "scheduled vesting/unlock and actual on-chain unlock, including beneficiary class",
        "formula": "published-before-decision event; exact direction/window/eligibility unspecified by authoritative review",
        "decision_clock": "announcement publication before decision_time",
        "availability_boundary": "immutable capture and publication_time <= decision_time",
        "signal_formula_window_threshold": "unspecified until a pre-result event manifest freezes it",
        "execution_shift": "event availability to next tradable spot open",
        "portfolio_role": "token supply pressure event, long/cash",
        "controls": "incumbent, candidate, passive/cash or matched-exposure control, shared-capital diagnostic",
        "cost_stress": [1.0, 2.0, 3.0],
        "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "not specified by source review"},
        "minimum_breadth_history": "every selected event must have announcement, schedule, actual transfer and amendments",
        "configured_assets": K05_FROZEN_ASSETS,
    },
    "K06": {
        "provider": "issuer-published spot BTC/ETH ETF creations/redemptions; SEC filings for treatment audit",
        "universe": "BTC/ETH only; ETF set frozen before economics",
        "cadence": "daily issuer publication",
        "field_meaning": "net creations/redemptions aggregated by underlying; cash versus in-kind retained",
        "formula": "daily net ETF flow; exact threshold unspecified by authoritative review",
        "decision_clock": "issuer publication time",
        "availability_boundary": "publication_time and available_at <= decision_time",
        "signal_formula_window_threshold": "at least one decision interval lag; event window and direction frozen before economics",
        "execution_shift": "one decision interval, next tradable spot open",
        "portfolio_role": "institutional flow, long/cash",
        "controls": "incumbent, candidate, passive/cash or matched-exposure control, shared-capital diagnostic",
        "cost_stress": [1.0, 2.0, 3.0],
        "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "post-2024 untouched; exact dates unspecified by review"},
        "minimum_breadth_history": "daily BTC/ETH ETF set history with creation/redemption and exchange-calendar coverage",
        "issuer_set": K06_ISSUER_SET,
        "mapping_status": "route probes only; series/CIK mapping and complete creation/redemption history remain unproven",
    },
    "K07": {
        "provider": "Glassnode or equivalent PIT exchange-wallet flow provider",
        "universe": "BTC/ETH pilot with frozen exchange-label universe",
        "cadence": "daily or hourly only when provider supplies available_at",
        "field_meaning": "labeled exchange inflow minus outflow; native-unit and USD-normalized versions remain separate",
        "formula": "net_flow = FlowInEx - FlowOutEx; sign frozen with metric version",
        "decision_clock": "completed provider interval",
        "availability_boundary": "PIT snapshot or immutable vintage with available_at <= decision_time",
        "signal_formula_window_threshold": "one-period lag; threshold unspecified by authoritative review",
        "execution_shift": "one-period lag, next tradable spot open",
        "portfolio_role": "exchange liquidity flow, long/cash",
        "controls": "incumbent, candidate, passive/cash or matched-exposure control, shared-capital diagnostic",
        "cost_stress": [1.0, 2.0, 3.0],
        "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "not specified by source review"},
        "minimum_breadth_history": "BTC/ETH flow, label-set hash per vintage, confirmations, venue scope and exclusions",
    },
    "K08": {
        "provider": "Coin Metrics or Chi-compatible stablecoin flow provider with frozen contracts/labels",
        "universe": "BTC/ETH spot target; USDT and USDC series separate",
        "cadence": "completed interval; one-hour result in source paper is evidence only",
        "field_meaning": "stablecoin net exchange flow excluding mint, burn and bridge movements",
        "formula": "net exchange flow by stablecoin contract; no minting-as-buying shortcut",
        "decision_clock": "completed provider interval",
        "availability_boundary": "PIT flow with available_at <= decision_time",
        "signal_formula_window_threshold": "one-period lag; exact interval and direction unspecified until frozen",
        "execution_shift": "one decision interval, next tradable spot open",
        "portfolio_role": "deployable exchange liquidity, long/cash",
        "controls": "incumbent, candidate, passive/cash or matched-exposure control, shared-capital diagnostic",
        "cost_stress": [1.0, 2.0, 3.0],
        "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "not specified by source review"},
        "minimum_breadth_history": "USDT/USDC contract identity, bridge/mint/burn filters, exchange labels and cross-venue coverage",
    },
    "K10": {
        "provider": "Deribit/Kraken liquidation schemas; Binance archive may be used for a venue pilot",
        "universe": "BTC/ETH spot target; venue aggregation frozen",
        "cadence": "event-driven liquidation plus spot/OI context",
        "field_meaning": "venue-tagged liquidation volume, side/quantity, OI delta, mark/index and spot context",
        "formula": "event detector after available liquidation; fixed wait/hold intervals selected before results",
        "decision_clock": "confirmed liquidation event availability",
        "availability_boundary": "event publication and available_at <= decision_time",
        "signal_formula_window_threshold": "threshold, wait and hold must be frozen before economics",
        "execution_shift": "fixed wait interval, next tradable spot open",
        "portfolio_role": "forced-selling rebound, long/cash spot; no derivatives execution",
        "controls": "incumbent, candidate, passive/cash or matched-exposure control, shared-capital diagnostic",
        "cost_stress": [1.0, 2.0, 3.0],
        "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "not specified by source review"},
        "minimum_breadth_history": "venue event semantics, duplicate policy, OI units, aggregation, latency and shock fill cost",
    },
    "K12": {
        "provider": "Glassnode entity-adjusted SOPR or Coin Metrics SOPR with immutable PIT method",
        "universe": "BTC first, then assets with identical documented semantics",
        "cadence": "daily completed observation",
        "field_meaning": "spent-output value divided by creation value; entity-adjusted clustering policy retained",
        "formula": "SOPR = spent_output_value / creation_value; level or fixed deviation from one unspecified",
        "decision_clock": "completed daily observation",
        "availability_boundary": "immutable metric vintage with available_at <= decision_time",
        "signal_formula_window_threshold": "one-period lag; exact level/deviation threshold unspecified by authoritative review",
        "execution_shift": "one-period lag, next tradable spot open",
        "portfolio_role": "on-chain loss realization, long/cash",
        "controls": "incumbent, candidate, passive/cash or matched-exposure control, shared-capital diagnostic",
        "cost_stress": [1.0, 2.0, 3.0],
        "cells": {"full": "2022-01-01..2026-07-31", "recent": "2024-01-01..2026-07-31", "holdout": "not specified by source review"},
        "minimum_breadth_history": "UTXO/account method, entity-cluster version, internal transfers, chain coverage and immutable lag",
    },
}


def gate_k02() -> dict[str, Any]:
    raw = read_json(K02_RAW)
    r = verified_receipt(K02_RECEIPT)
    catalog = next((item for item in expanded_probes("K02") if item.get("slug") == "k02_catalog_assets_btc_eth"), None)
    metric_catalog = next((item for item in expanded_probes("K02") if item.get("slug") == "k02_catalog_asset_metrics_all"), None)
    catalog_metrics: dict[str, list[str]] = {}
    if catalog:
        body = read_json(REPO / catalog["body"])
        catalog_metrics = {row["asset"]: sorted(metric["metric"] for metric in row.get("metrics", [])) for row in body.get("data", []) if row.get("asset") in {"btc", "eth"}}
    probes = expanded_probes("K02")
    statuses = {item["slug"]: item.get("status") for item in probes}
    coverage_rows = sum(item.get("row_count") or 0 for item in probes if item.get("slug", "").startswith("k02_timeseries_"))
    return {"status": _derived_pit_status(coverage_rows, 0, {"raw": r.get("status"), **statuses}), "coverage_rows": coverage_rows, "attempted_window": "2019-01-01..2026-07-31", "http_status": r.get("status"), "error": raw.get("error"), "catalog_metrics": catalog_metrics, "metric_catalog_status": metric_catalog.get("status") if metric_catalog else None, "metric_probe_statuses": statuses, "available_at_rows": 0, "economic_signal_rows": 0, "blockers": ["catalog capability is visible, but adjusted-transfer metrics are unavailable under supplied Coin Metrics access", "asset-metrics catalog uses the documented no-assets route; adjusted-transfer names were enumerated but no community entitlement was returned", "chain-specific PIT/revision policy absent"], "source": source_ref(K02_RAW, provider="coinmetrics_community", role="full-target probe", receipt_path=K02_RECEIPT)}


def gate_k05() -> dict[str, Any]:
    verified_receipt(K05_HOME_RECEIPT)
    verified_receipt(K05_API_RECEIPT)
    home = K05_HOME.read_text(encoding="utf-8", errors="replace")
    api = K05_API.read_text(encoding="utf-8", errors="replace")
    inventory = verify_expanded_inventory()
    expanded = [item for item in inventory["probes"] if item.get("card") == "K05"]
    by_slug = {item["slug"]: item.get("status") for item in expanded}
    records = inventory.get("asset_specific_source_records", [])
    configured_routes = {}
    for record in records:
        announcement = next(item for item in expanded if item["slug"] == record["announcement_probe"])
        confirmation = next(item for item in expanded if item["slug"] == record["confirmation_probe"])
        evidence = derive_asset_specific_evidence((REPO / announcement["body"]).read_text(encoding="utf-8", errors="replace"), (REPO / confirmation["body"]).read_text(encoding="utf-8", errors="replace"), [announcement["slug"], confirmation["slug"]])
        configured_routes[record["asset"]] = {"announcement": announcement.get("status"), "confirmation_docs": confirmation.get("status"), "confirmation_probe": confirmation["slug"], **evidence}
    coverage_rows = sum(int(route["contract_status"] == "RESOLVED" and route["unlock_id_status"] == "RESOLVED") for route in configured_routes.values())
    return {"status": _derived_pit_status(coverage_rows, 0, by_slug), "coverage_rows": coverage_rows, "configured_assets": K05_FROZEN_ASSETS, "configured_asset_route_statuses": configured_routes, "current_page_evidence": {"title_present": "Token Unlocks" in home, "bytes": len(home)}, "api_root": {"status": verified_receipt(K05_API_RECEIPT).get("status"), "body": api[:120]}, "expanded_probe_statuses": by_slug, "publication_time_rows": 0, "available_at_rows": 0, "economic_signal_rows": 0, "blockers": ["public docs/pages expose schedules and current labels but no immutable token-specific announcement/vesting archive", "no actual unlock transfer confirmation", "confirmation routes are documentation only; no asset-specific transaction IDs were captured", "duplicate generic chain documentation endpoints are collapsed and referenced by multiple assets", "current page is not a historical PIT event feed"], "sources": [source_ref(K05_HOME, provider="tokenomist_public", role="prior current page evidence", receipt_path=K05_HOME_RECEIPT), source_ref(K05_API, provider="tokenomist_public", role="prior API capability probe", receipt_path=K05_API_RECEIPT), source_ref(TOKENOMIST_PROBE, provider="tokenomist_public", role="current page refresh", receipt_path=TOKENOMIST_PROBE_RECEIPT), source_ref(TOKENOMIST_API_PROBE, provider="tokenomist_public", role="API root refresh", receipt_path=TOKENOMIST_API_PROBE_RECEIPT)]}


def _derived_pit_status(rows: int, available_at_rows: int, statuses: dict[str, Any]) -> str:
    """Derive partial versus blocked state from verified evidence, never a fixed label."""
    if rows > 0 and available_at_rows == 0 and any(status == 200 for status in statuses.values()):
        return "PIT_PENDING"
    return "DATA_ACCESS_BLOCKED"


def gate_k06() -> dict[str, Any]:
    verified_receipt(K06_BTC_RECEIPT)
    verified_receipt(K06_BTC_SNAPSHOT_RECEIPT)
    rows = list(csv.DictReader(K06_BTC_SNAPSHOT.open(newline="", encoding="utf-8")))
    dates = [datetime.strptime(row["date"], "%d %b %Y").replace(tzinfo=UTC) for row in rows]
    cutoff_rows = sum(value <= CUTOFF for value in dates)
    expanded = expanded_probes("K06")
    statuses = {item["slug"]: item.get("status") for item in expanded}
    issuer_mapping_attempts = {ticker: {"status": statuses.get(f"k06_sec_{ticker.lower()}_fund_search"), "mapping": "UNRESOLVED"} for ticker in K06_ISSUER_SET}
    available_at_rows = 0
    card_status = _derived_pit_status(len(rows), available_at_rows, statuses)
    return {"status": card_status, "btc_snapshot_rows": len(rows), "btc_snapshot_start": min(dates).date().isoformat() if dates else None, "btc_snapshot_end": max(dates).date().isoformat() if dates else None, "btc_rows_through_cutoff": cutoff_rows, "issuer_set": K06_ISSUER_SET, "mapping_status": "SEC ticker mapping attempts are receipt-backed but exact fund/series CIK identity remains unresolved", "issuer_mapping_attempts": issuer_mapping_attempts, "eth_page_status": statuses.get("k06_farside_eth_page"), "eth_probe_body_sha256": sha256(K06_ETH_PROBE), "all_data_probe_status": {"bitcoin": statuses.get("k06_farside_wp_page997"), "ethereum": statuses.get("k06_farside_wp_eth_slug")}, "expanded_probe_statuses": statuses, "publication_time_rows": 0, "available_at_rows": available_at_rows, "economic_signal_rows": 0, "blockers": ["BTC snapshot is post-cutoff current page data", "Farside current/WP pages do not provide immutable historical daily vintages", "SEC ticker mapping attempts returned access-blocked responses", "issuer publication and creation/redemption vintages are absent", "ETF set, holiday and underlying mapping are not frozen"], "sources": [source_ref(K06_BTC, provider="farside_public", role="BTC current page", receipt_path=K06_BTC_RECEIPT), source_ref(K06_BTC_SNAPSHOT, provider="farside_public_aggregator", role="BTC current snapshot", receipt_path=K06_BTC_SNAPSHOT_RECEIPT), source_ref(K06_ETH_PROBE, provider="farside_public", role="ETH access probe", receipt_path=K06_ETH_PROBE_RECEIPT), source_ref(K06_BTC_ALL_PROBE, provider="farside_public", role="BTC historical all-data access probe", receipt_path=K06_BTC_ALL_PROBE_RECEIPT), source_ref(K06_ETH_ALL_PROBE, provider="farside_public", role="ETH historical all-data access probe", receipt_path=K06_ETH_ALL_PROBE_RECEIPT)]}


def gate_k07() -> dict[str, Any]:
    data = read_json(K07_RAW)["data"]
    if not isinstance(data, list) or not data:
        raise ValueError("k07_data_missing")
    times = [aware(row["time"]) for row in data]
    assets = sorted({row["asset"] for row in data})
    statuses = sorted({row.get("FlowInExNtv-status") for row in data})
    status_times = [aware(row["FlowInExNtv-status-time"]) for row in data]
    late_status = sum(status_time > event for status_time, event in zip(status_times, times, strict=True))
    counts = {asset: sum(row["asset"] == asset for row in data) for asset in assets}
    expanded_statuses = {item["slug"]: item.get("status") for item in expanded_probes("K07")}
    return {"status": _derived_pit_status(len(data), 0, {"raw": verified_receipt(K07_RECEIPT).get("status"), **expanded_statuses}), "rows": len(data), "assets": counts, "event_start": min(times).isoformat().replace("+00:00", "Z"), "event_end": max(times).isoformat().replace("+00:00", "Z"), "provider_statuses": statuses, "rows_with_status_time_after_event": late_status, "expanded_probe_statuses": expanded_statuses, "publication_time_rows": 0, "available_at_rows": 0, "economic_signal_rows": 0, "formula_semantics": "FlowInExNtv - FlowOutExNtv and FlowInExUSD - FlowOutExUSD remain separate descriptive fields", "blockers": ["flash/current status and status-time are mutable provider labels, not PIT vintages", "event-day plus a publication schedule cannot reconstruct prior label sets", "no label-set hash or exchange scope per historical timestamp", "no explicit available_at field"], "source": source_ref(K07_RAW, provider="coinmetrics_community", role="full-target exchange-flow acquisition", receipt_path=K07_RECEIPT)}


def gate_k08() -> dict[str, Any]:
    verified_receipt(K08_CATALOG_RECEIPT)
    catalog = read_json(K08_CATALOG)
    assets = {row["asset"]: {m["metric"] for m in row.get("metrics", [])} for row in catalog.get("data", []) if row.get("asset") in {"usdt", "usdc"}}
    flow_metrics = {asset: sorted(metric for metric in metrics if "flow" in metric.lower() or "ex" in metric.lower()) for asset, metrics in assets.items()}
    probe = read_json(K08_PROBE)
    expanded_statuses = {item["slug"]: item.get("status") for item in expanded_probes("K08")}
    flow_rows = len(probe.get("data", [])) if isinstance(probe.get("data"), list) else 0
    return {"status": _derived_pit_status(flow_rows, 0, {"raw": verified_receipt(K08_PROBE_RECEIPT).get("status"), **expanded_statuses}), "catalog_assets": sorted(assets), "catalog_flow_metrics": flow_metrics, "expanded_probe_statuses": expanded_statuses, "flow_rows": flow_rows, "probe_http_status": verified_receipt(K08_PROBE_RECEIPT).get("status"), "probe_error": probe.get("error"), "publication_time_rows": 0, "available_at_rows": 0, "economic_signal_rows": 0, "blockers": ["catalog exposes no exchange-flow metric for USDT/USDC", "documented exact flow endpoint rejected requested metric", "contract, mint/burn/bridge exclusions, labels and PIT flow are absent"], "sources": [source_ref(K08_CATALOG, provider="coinmetrics_community", role="stablecoin catalog capability", receipt_path=K08_CATALOG_RECEIPT), source_ref(K08_PROBE, provider="coinmetrics_community", role="stablecoin flow probe", receipt_path=K08_PROBE_RECEIPT)]}


def gate_k10() -> dict[str, Any]:
    verified_receipt(K10_RECEIPT)
    rows = list(csv.DictReader(K10.open(newline="", encoding="utf-8")))
    events = [aware(row["event_time_utc"]) for row in rows]
    identity_fields = ("venue", "instrument", "contract_type", "event_time_utc", "time", "side", "price", "average_price", "original_quantity", "accumulated_fill_quantity", "order_id", "event_id", "liquidation_id")
    keys = [tuple(row.get(field, "") for field in identity_fields) for row in rows]
    duplicates = len(keys) - len(set(keys))
    expanded = expanded_probes("K10")
    expanded_statuses = {item["slug"]: item.get("status") for item in expanded}
    archive_samples = {}
    for item in expanded:
        if item.get("status") == 200 and item.get("slug", "").startswith("k10_binance_cm_") and str(item.get("body", "")).endswith(".zip"):
            archive_receipt = REPO / item["receipt"]
            archive_samples[item["slug"]] = {"status": receipt(archive_receipt).get("status"), "receipt_sha256": sha256(archive_receipt), "archive_sha256": receipt(archive_receipt).get("archive_sha256"), "recipe": receipt(archive_receipt).get("recipe"), "members": receipt(archive_receipt).get("members")}
    cm_context_samples = {slug: sample for slug, sample in archive_samples.items() if "metrics" in slug}
    cm_context_rows = sum(member.get("row_count", 0) for sample in cm_context_samples.values() for member in sample.get("members", []))
    liquidation_dates = sorted({match.group(1).replace("_", "-") for slug in archive_samples if "metrics" not in slug for match in [re.search(r"(\d{4}_\d{2}_\d{2})$", slug)] if match})
    available_at_rows = sum(bool(row["available_at"].strip()) for row in rows)
    card_status = _derived_pit_status(len(rows), available_at_rows, expanded_statuses)
    return {"status": card_status, "rows": len(rows), "instruments": sorted({row["instrument"] for row in rows}), "event_start": min(events).isoformat().replace("+00:00", "Z"), "event_end": max(events).isoformat().replace("+00:00", "Z"), "coverage_days": len({event.date().isoformat() for event in events}), "rows_with_blank_available_at": len(rows) - available_at_rows, "available_at_rows": available_at_rows, "duplicate_event_records": duplicates, "archive_probe_2026_07_30": {"BTCUSD_PERP": {"status": expanded_statuses.get("k10_binance_um_btc_2026"), "body_sha256": next((item["sha256"] for item in expanded if item["slug"] == "k10_binance_um_btc_2026"), None), "receipt_sha256": next((sha256(REPO / item["receipt"]) for item in expanded if item["slug"] == "k10_binance_um_btc_2026"), None)}, "ETHUSD_PERP": {"status": expanded_statuses.get("k10_binance_um_eth_2026"), "body_sha256": next((item["sha256"] for item in expanded if item["slug"] == "k10_binance_um_eth_2026"), None), "receipt_sha256": next((sha256(REPO / item["receipt"]) for item in expanded if item["slug"] == "k10_binance_um_eth_2026"), None)}}, "archive_samples": archive_samples, "cm_context_samples": sorted(cm_context_samples), "cm_context_rows": cm_context_rows, "cm_liquidation_archive_dates": liquidation_dates, "cm_liquidation_archive_span": {"start": liquidation_dates[0] if liquidation_dates else None, "end": liquidation_dates[-1] if liquidation_dates else None, "days": len(liquidation_dates)}, "expanded_probe_statuses": expanded_statuses, "same_instrument_venue_join": False, "publication_time_rows": 0, "economic_signal_rows": 0, "blockers": ["only one 2024-01-02 pilot day, not full/recent target", "all rows lack available_at", "259 duplicate event records have no distinct order identity", "2026-07-30 public archive probes returned 404", "CM BTCUSD_PERP and ETHUSD_PERP metrics context archives are acquired, but no timestamp/availability join with liquidation rows is admitted", "mark/index archive attempts returned 404", "venue aggregation and liquidation latency are absent"], "sources": [source_ref(K10, provider="binance_vision_public_archive", role="liquidation snapshot pilot", receipt_path=K10_RECEIPT), source_ref(K10_BTC_PROBE, provider="binance_vision_public_archive", role="2026-07-30 BTC archive probe", receipt_path=K10_BTC_PROBE_RECEIPT), source_ref(K10_ETH_PROBE, provider="binance_vision_public_archive", role="2026-07-30 ETH archive probe", receipt_path=K10_ETH_PROBE_RECEIPT)]}


def gate_k12() -> dict[str, Any]:
    raw = read_json(K12_RAW)
    r = verified_receipt(K12_RECEIPT)
    probes = expanded_probes("K12")
    statuses = {item["slug"]: item.get("status") for item in probes}
    coverage_rows = len(raw.get("data", [])) if isinstance(raw.get("data"), list) else 0
    return {"status": _derived_pit_status(coverage_rows, 0, {"raw": r.get("status"), **statuses}), "coverage_rows": coverage_rows, "attempted_window": "2019-01-01..2026-07-31", "http_status": r.get("status"), "error": raw.get("error"), "expanded_probe_statuses": statuses, "available_at_rows": 0, "economic_signal_rows": 0, "blockers": ["SOPR unavailable under supplied Coin Metrics access", "no UTXO/account creation-spend history", "no entity-cluster PIT version or revision policy"], "source": source_ref(K12_RAW, provider="coinmetrics_community", role="full-target SOPR probe", receipt_path=K12_RECEIPT)}


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    return value


def build_report() -> dict[str, Any]:
    verify_expanded_inventory()
    cards = {"K02": gate_k02(), "K05": gate_k05(), "K06": gate_k06(), "K07": gate_k07(), "K08": gate_k08(), "K10": gate_k10(), "K12": gate_k12()}
    probe_index = read_json(PROBE_INDEX) if PROBE_INDEX.is_file() else {"probes": []}
    return canonicalize({"status": "DONE_WITH_CONCERNS", "acquisition_state": "CURRENT_ACQUISITION_RUN_CLOSED_WITH_BLOCKERS", "next_stage_approval": "NOT_APPROVED", "economic_run": False, "historical_cutoff": CUTOFF.isoformat().replace("+00:00", "Z"), "validation": {"scope": "crypto_secondary_only", "test_count": 36, "receipt_path": str(VALIDATION_RECEIPT.relative_to(REPO))}, "cards": cards, "contracts": CONTRACTS, "acquisition_exhaustion": probe_index, "authoritative_sources": {str(path.relative_to(REPO)): sha256(path) for path in (FULL_REPORT, CRYPTO_REPORT, SCREEN_PROTOCOL, PROTOCOL)}, "constraints": {"no_ohlcv_proxy": True, "no_runtime_mutation": True, "no_paper_live": True, "no_auto_promotion": True, "no_economic_cells": True}, "overall_blockers": "All seven cards remain data/PIT blocked or partial; the current acquisition run is closed with blockers, and no candidate is approved for a next research or economic stage."})


def _report_card_counts(report: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {card: {key: value for key, value in data.items() if isinstance(value, int) and not isinstance(value, bool)} for card, data in report["cards"].items()}


def freeze_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    source_paths: set[Path] = {K02_RAW, K02_RECEIPT, K05_HOME, K05_HOME_RECEIPT, K05_API, K05_API_RECEIPT, K06_BTC, K06_BTC_RECEIPT, K06_BTC_SNAPSHOT, K06_BTC_SNAPSHOT_RECEIPT, K07_RAW, K07_RECEIPT, K08_CATALOG, K08_CATALOG_RECEIPT, K08_PROBE, K08_PROBE_RECEIPT, K10, K10_RECEIPT, K12_RAW, K12_RECEIPT, K06_ETH_PROBE, K06_ETH_PROBE_RECEIPT, K06_BTC_ALL_PROBE, K06_BTC_ALL_PROBE_RECEIPT, K06_ETH_ALL_PROBE, K06_ETH_ALL_PROBE_RECEIPT, K10_BTC_PROBE, K10_BTC_PROBE_RECEIPT, K10_ETH_PROBE, K10_ETH_PROBE_RECEIPT, TOKENOMIST_PROBE, TOKENOMIST_PROBE_RECEIPT, TOKENOMIST_API_PROBE, TOKENOMIST_API_PROBE_RECEIPT, VALIDATION_RECEIPT}
    for item in read_json(PROBE_INDEX).get("probes", []):
        source_paths.update({REPO / item["body"], REPO / item["receipt"]})
    source_paths.add(PROBE_INDEX)
    artifacts = (PROTOCOL, REPORT_MD, Path(__file__), ACQUISITION_SCRIPT, TEST, REPORT, FULL_REPORT, CRYPTO_REPORT, SCREEN_PROTOCOL)
    payload: dict[str, Any] = {"status": "FROZEN_SECONDARY_CAUSAL_GATE", "identity": "crypto_secondary_causal_gate_2026_09_08", "frozen_at_utc": "2026-09-08T20:00:00Z", "historical_cutoff": CUTOFF.isoformat().replace("+00:00", "Z"), "contracts": CONTRACTS, "artifacts": {str(item.relative_to(REPO)): {"sha256": sha256(item), "kind": "protocol_or_gate_artifact"} for item in artifacts}, "inputs": {str(item.relative_to(REPO)): {"sha256": sha256(item)} for item in sorted(source_paths)}, "validation_receipt": {"path": str(VALIDATION_RECEIPT.relative_to(REPO)), "sha256": sha256(VALIDATION_RECEIPT), "test_count": 36}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": sha256(REPORT), "status": report["status"], "economic_run": report["economic_run"], "card_statuses": {card: data["status"] for card, data in report["cards"].items()}, "card_counts": _report_card_counts(report)}, "pit_policy": "explicit timezone-aware publication/vintage/available_at required; retrieval time and mutable labels never substitute", "economic_run": False, "no_auto_promotion": True, "canonical_hash_policy": "canonical JSON hash excludes only manifest_sha256", "manifest_sha256": "PENDING_FREEZE"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    payload = read_json(path)
    checked = 0
    for section in ("inputs", "artifacts"):
        for name, declaration in payload[section].items():
            item = REPO / name
            if sha256(item) != declaration["sha256"]:
                raise ValueError(f"hash_mismatch:{name}")
            if item.name.endswith(".receipt.json"):
                verify_receipt(item)
            checked += 1
    validation_decl = payload.get("validation_receipt")
    if not isinstance(validation_decl, dict) or validation_decl.get("path") not in payload["inputs"] or sha256(REPO / validation_decl["path"]) != validation_decl.get("sha256") or validation_decl.get("test_count") != 36:
        raise ValueError("validation_receipt_pin_mismatch")
    canonical = dict(payload)
    canonical["manifest_sha256"] = "PENDING_FREEZE"
    expected = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected != payload["manifest_sha256"]:
        raise ValueError("manifest_canonical_hash_mismatch")
    report = read_json(REPO / payload["report"]["path"])
    report_decl = payload["report"]
    if report_decl["path"] not in payload["artifacts"] or payload["artifacts"][report_decl["path"]]["sha256"] != report_decl["sha256"]:
        raise ValueError("report_artifact_pin_mismatch")
    if sha256(REPO / report_decl["path"]) != report_decl["sha256"] or report.get("status") != report_decl["status"] or report.get("economic_run") != report_decl["economic_run"] or {card: data.get("status") for card, data in report["cards"].items()} != report_decl["card_statuses"] or _report_card_counts(report) != report_decl["card_counts"]:
        raise ValueError("report_pin_mismatch")
    if report != build_report():
        raise ValueError("saved_report_canonical_mismatch")
    if report.get("economic_run") is not False or any(card.get("economic_signal_rows") != 0 for card in report["cards"].values()):
        raise ValueError("economic_output_present")
    return {"status": "FROZEN_SECONDARY_MANIFEST_VERIFIED", "checked_declared_files": checked, "checked_artifacts": len(payload["artifacts"]), "manifest_sha256": payload["manifest_sha256"]}


def verify() -> dict[str, Any]:
    """Verify the frozen package without writing files or requesting data."""
    result = verify_manifest()
    return {**result, "report_sha256": sha256(REPORT), "read_only": True, "network_free": True}


def main() -> None:
    if "--verify" in sys.argv[1:]:
        print(json.dumps(verify(), sort_keys=True))
        return
    report = build_report()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if read_json(REPORT) != report:
        raise ValueError("saved_report_canonical_mismatch")
    manifest = freeze_manifest()
    print(json.dumps({"status": report["status"], "manifest_sha256": manifest["manifest_sha256"], "report": str(REPORT)}))


if __name__ == "__main__":
    main()
