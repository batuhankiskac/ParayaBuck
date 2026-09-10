"""Capture public secondary crypto acquisition probes with sanitized receipts.

This is an evidence collector only. It sends unauthenticated GET requests, never
persists request headers or secrets, and makes no economic or current-label claim.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from io import BytesIO, TextIOWrapper
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[2]
OLD_ROOT = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07"
OUT = REPO / "data/research/external/crypto_secondary_acquisition_2026_09_08/probes/expanded"
K07_RAW = OLD_ROOT / "coinmetrics/k07_exchange_flows_btc_eth_2019_2026-07-31.json"
UA = "ParayaBuck-research/1.0 (public evidence probe)"
MUTABLE_EVIDENCE_KINDS = frozenset({
    "public_endpoint", "public_token_route", "issuer_public_archive", "public_announcement",
})


def _slug_basis(card: str, kind: str, endpoint: str, slug: str) -> str:
    value = json.dumps({"card": card, "kind": kind, "endpoint": endpoint, "collector_slug": slug}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _zip_recipe_id(card: str, provider: str, endpoint: str, archive_sha256: str | None, members: list[dict[str, object]]) -> tuple[str, str | None, str | None]:
    names = [str(member.get("name", "")) for member in members]
    source = f"{endpoint} {' '.join(names)}"
    dates = sorted(set(re.findall(r"20\d{2}[-_]\d{2}[-_]\d{2}", source)))
    symbols = sorted({name.split("-", 1)[0] for name in names if "-" in name})
    identity = {"card": card, "provider": provider, "endpoint": endpoint, "archive_sha256": archive_sha256, "date": dates[0] if dates else None, "symbol": symbols[0] if symbols else None, "members": members}
    recipe_id = "crypto_zip_recipe_v1_" + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return recipe_id, identity["date"], identity["symbol"]


def _u(slug: str, url: str, card: str, kind: str = "public_endpoint") -> dict[str, str]:
    return {"slug": slug, "url": url, "card": card, "kind": kind}


PROBES = [
    _u("k02_catalog_assets_btc_eth", "https://community-api.coinmetrics.io/v4/catalog/assets?assets=btc%2Ceth", "K02"),
    _u("k02_catalog_asset_metrics_all", "https://community-api.coinmetrics.io/v4/catalog/asset-metrics", "K02"),
    _u("k02_timeseries_nvtadj", "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc%2Ceth&metrics=NVTAdj&frequency=1d&start_time=2026-07-28&end_time=2026-07-31&page_size=100", "K02"),
    _u("k02_timeseries_txtfrvaladjntv", "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc%2Ceth&metrics=TxTfrValAdjNtv&frequency=1d&start_time=2026-07-28&end_time=2026-07-31&page_size=100", "K02"),
    _u("k02_timeseries_txtfrvaladjusd", "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc%2Ceth&metrics=TxTfrValAdjUSD&frequency=1d&start_time=2026-07-28&end_time=2026-07-31&page_size=100", "K02"),
    _u("k02_docs_asset_metrics", "https://docs.coinmetrics.io/api/v4/catalog/asset-metrics", "K02", "public_documentation"),
    _u("k05_tokenomist_docs", "https://docs.tokenomist.ai/", "K05", "public_documentation"),
    _u("k05_tokenomist_enterprise", "https://enterprise.tokenomist.ai/", "K05", "public_documentation"),
    _u("k05_tokenomist_emission_crypto_market", "https://tokenomist.ai/emission/crypto-market?category=all&unlock-type=all&interval=week&range=2-months", "K05"),
    _u("k05_tokenomist_announcement_channel", "https://telegram.me/Tokenomist_Announce", "K05", "public_announcement"),
    _u("k06_farside_wp_page997", "https://farside.co.uk/wp-json/wp/v2/pages/997", "K06"),
    _u("k06_farside_wp_btc_slug", "https://farside.co.uk/wp-json/wp/v2/pages?slug=btc", "K06"),
    _u("k06_farside_wp_eth_slug", "https://farside.co.uk/wp-json/wp/v2/pages?slug=eth", "K06"),
    _u("k06_farside_eth_page", "https://farside.co.uk/eth/", "K06"),
    _u("k06_ishares_ibit_page", "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust", "K06", "issuer_public_archive"),
    _u("k06_sec_ibit_filing", "https://www.sec.gov/Archives/edgar/data/1980994/000119312525076003/d943052d424b3.htm", "K06", "sec_filing"),
    _u("k06_grayscale_gbtc_issuer", "https://etfs.grayscale.com/gbtc", "K06", "issuer_public_archive"),
    _u("k06_fidelity_fbtc_issuer", "https://www.fidelity.com/etfs/fbtc", "K06", "issuer_public_archive"),
    _u("k06_bitwise_bitb_issuer", "https://bitbetf.com/", "K06", "issuer_public_archive"),
    _u("k06_ark_arkb_issuer", "https://www.ark-funds.com/funds/arkb", "K06", "issuer_public_archive"),
    _u("k06_sec_ishares_etha_filing_route", "https://www.sec.gov/edgar/browse/?CIK=2000638", "K06", "sec_filing"),
    _u("k07_glassnode_transactions_docs", "https://docs.glassnode.com/basic-api/endpoints/transactions", "K07", "public_documentation"),
    _u("k07_glassnode_pit_docs", "https://research.glassnode.com/introducing-point-in-time-data/", "K07", "public_documentation"),
    _u("k07_coinmetrics_timeseries_docs", "https://docs.coinmetrics.io/api/v4/timeseries/asset-metrics", "K07", "public_documentation"),
    _u("k07_coinmetrics_catalog_assets_btc_eth", "https://community-api.coinmetrics.io/v4/catalog/assets?assets=btc%2Ceth", "K07", "public_catalog"),
    _u("k07_coinmetrics_catalog_asset_metrics", "https://community-api.coinmetrics.io/v4/catalog/asset-metrics", "K07", "public_catalog"),
    _u("k12_catalog_assets_btc_eth", "https://community-api.coinmetrics.io/v4/catalog/assets?assets=btc%2Ceth", "K12"),
    _u("k12_timeseries_sopr", "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc%2Ceth&metrics=SOPR&frequency=1d&start_time=2026-07-28&end_time=2026-07-31&page_size=100", "K12"),
    _u("k08_catalog_assets_usdt_usdc", "https://community-api.coinmetrics.io/v4/catalog/assets?assets=usdt%2Cusdc", "K08"),
    _u("k08_catalog_asset_metrics_usdt_usdc", "https://community-api.coinmetrics.io/v4/catalog/asset-metrics?assets=usdt%2Cusdc", "K08"),
    _u("k08_timeseries_flowinout_exntv", "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=usdt%2Cusdc&metrics=FlowInExNtv%2CFlowOutExNtv&frequency=1d&start_time=2026-07-28&end_time=2026-07-31&page_size=100", "K08"),
    _u("k08_glassnode_transactions_docs", "https://docs.glassnode.com/basic-api/endpoints/transactions", "K08", "public_documentation"),
    _u("k08_circle_usdc_transparency", "https://www.circle.com/en/transparency", "K08", "issuer_public_archive"),
    _u("k08_tether_transparency", "https://tether.to/en/transparency/", "K08", "issuer_public_archive"),
    _u("k10_binance_um_btc_2024", "https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2024-01-02.zip", "K10"),
    _u("k10_binance_um_eth_2024", "https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/ETHUSDT/ETHUSDT-liquidationSnapshot-2024-01-02.zip", "K10"),
    _u("k10_binance_um_btc_2026", "https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-2026-07-30.zip", "K10"),
    _u("k10_binance_um_eth_2026", "https://data.binance.vision/data/futures/um/daily/liquidationSnapshot/ETHUSDT/ETHUSDT-liquidationSnapshot-2026-07-30.zip", "K10"),
    _u("k10_binance_cm_btc_2024_01_03", "https://data.binance.vision/data/futures/cm/daily/liquidationSnapshot/BTCUSD_PERP/BTCUSD_PERP-liquidationSnapshot-2024-01-03.zip", "K10"),
    _u("k10_binance_cm_eth_2024_01_03", "https://data.binance.vision/data/futures/cm/daily/liquidationSnapshot/ETHUSD_PERP/ETHUSD_PERP-liquidationSnapshot-2024-01-03.zip", "K10"),
    _u("k10_binance_um_listing", "https://data.binance.vision/?prefix=data/futures/um/daily/liquidationSnapshot/", "K10", "public_listing"),
    _u("k10_binance_cm_listing", "https://data.binance.vision/?prefix=data/futures/cm/daily/liquidationSnapshot/", "K10", "public_listing"),
    _u("k10_binance_force_orders_btc", "https://fapi.binance.com/fapi/v1/forceOrders?symbol=BTCUSDT&limit=100", "K10"),
    _u("k10_binance_all_force_orders_btc", "https://fapi.binance.com/fapi/v1/allForceOrders?symbol=BTCUSDT&limit=100", "K10"),
    _u("k10_deribit_liquidation_docs", "https://support.deribit.com/hc/en-us/articles/25944769313309-Liquidations", "K10", "public_documentation"),
    _u("k10_kraken_liquidation_docs", "https://docs.kraken.com/api/docs/futures-api/charts/market-analytics", "K10", "public_documentation"),
    _u("k10_deribit_liquidation_api", "https://www.deribit.com/api/v2/public/get_liquidation_orders?instrument_name=BTC-PERPETUAL&count=10", "K10", "public_endpoint"),
    _u("k10_kraken_liquidation_api", "https://futures.kraken.com/derivatives/api/v3/liquidationevents?symbol=PI_XBTUSD", "K10", "public_endpoint"),
    _u("k12_glassnode_sopr_docs", "https://docs.glassnode.com/basic-api/endpoints/indicators/sopr", "K12", "public_documentation"),
    _u("k12_glassnode_entity_adjusted_docs", "https://docs.glassnode.com/basic-api/endpoints/indicators/entity-adjusted-sopr", "K12", "public_documentation"),
]

# A bounded CM-only date grid keeps contract identity explicit while testing
# archive continuity around the known 2024 sample and the cutoff-era dates.
for _date in ("2024-01-01", "2024-01-03", "2024-01-04", "2026-07-29", "2026-07-30", "2026-07-31"):
    for _instrument in ("BTCUSD_PERP", "ETHUSD_PERP"):
        _date_slug = _date.replace("-", "_")
        _base = f"https://data.binance.vision/data/futures/cm/daily/liquidationSnapshot/{_instrument}/{_instrument}-liquidationSnapshot-{_date}.zip"
        _slug = f"k10_binance_cm_{_instrument.lower().replace('_', '')}_{_date_slug}"
        if not any(item["url"] == _base for item in PROBES):
            PROBES.append(_u(_slug, _base, "K10", "public_archive_bounded_cm_date_grid"))

for _instrument in ("BTCUSD_PERP", "ETHUSD_PERP"):
    _instrument_slug = _instrument.lower().replace("_", "")
    for _family in ("metrics", "markPriceKlines", "indexPriceKlines"):
        _url = f"https://data.binance.vision/data/futures/cm/daily/{_family}/{_instrument}/{_instrument}-{_family}-2024-01-03.zip"
        PROBES.append(_u(f"k10_binance_cm_{_instrument_slug}_{_family}_2024_01_03", _url, "K10", "public_archive_cm_context_attempt"))

# Official SEC ticker searches are retained as mapping/filing attempts. They
# do not imply that a filing contains daily creations, redemptions, or flows.
PROBES.extend([
    _u("k06_sec_company_tickers", "https://www.sec.gov/files/company_tickers.json", "K06", "sec_identity_catalog"),
    _u("k06_sec_company_tickers_exchange", "https://www.sec.gov/files/company_tickers_exchange.json", "K06", "sec_identity_catalog"),
])
for _ticker in ("IBIT", "GBTC", "FBTC", "BITB", "ARKB", "ETHA"):
    PROBES.append(_u(f"k06_sec_{_ticker.lower()}_fund_search", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={_ticker}&type=NPORT-P&count=100", "K06", "sec_fund_mapping_and_history_attempt"))
    PROBES.append(_u(f"k06_sec_{_ticker.lower()}_etf_series_search", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={_ticker}&type=497&count=100", "K06", "sec_etf_series_identity_attempt"))

# Frozen by the configured 22-pair research pool. These route probes establish
# only whether primary announcement and confirmation documentation is reachable;
# they never turn current pages into historical or PIT event observations.
K05_ASSET_ROUTES = {
    "BTC/USD": ("https://bitcoincore.org/en/releases/", "https://bitcoincore.org/en/doc/26.0.0/rpc/rawtransactions/getrawtransaction/"),
    "ETH/USD": ("https://blog.ethereum.org/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "SOL/USD": ("https://solana.com/news", "https://solana.com/docs/rpc/http/gettransaction"),
    "XRP/USD": ("https://xrpl.org/blog", "https://xrpl.org/docs/references/http-websocket-apis/public-api-methods/ledger-methods/ledger"),
    "ADA/USD": ("https://cardanofoundation.org/blog", "https://docs.cardano.org/cardano-components/cardano-db-sync"),
    "DOGE/USD": ("https://dogecoin.com/blog/", "https://dogecoincore.com/docs/api/"),
    "AVAX/USD": ("https://www.avax.network/blog", "https://build.avax.network/docs/api-reference/c-chain/transaction-issuance"),
    "LINK/USD": ("https://blog.chain.link/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "LTC/USD": ("https://litecoin.org/news/", "https://litecoin-project.github.io/"),
    "BCH/USD": ("https://bitcoincash.org/", "https://documentation.cash/"),
    "DOT/USD": ("https://polkadot.network/blog", "https://wiki.polkadot.com/learn/essentials/transactions/"),
    "UNI/USD": ("https://blog.uniswap.org/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "AAVE/USD": ("https://governance.aave.com/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "ARB/USD": ("https://forum.arbitrum.foundation/", "https://docs.arbitrum.io/build-decentralized-apps/reference/node-interface"),
    "FIL/USD": ("https://filecoin.io/blog/", "https://docs.filecoin.io/reference/json-rpc/"),
    "RENDER/USD": ("https://rendernetwork.com/blog", "https://solana.com/docs/rpc/http/gettransaction"),
    "ONDO/USD": ("https://ondo.finance/news", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "HYPE/USD": ("https://hyperfoundation.org/news", "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint"),
    "SHIB/USD": ("https://blog.shib.io/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "GRT/USD": ("https://thegraph.com/blog/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "CRV/USD": ("https://resources.curve.fi/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
    "PEPE/USD": ("https://www.pepe.vip/", "https://ethereum.org/en/developers/apis/json-rpc/#eth_gettransactionbyhash"),
}

K05_ASSET_SOURCE_RECORDS = []
_confirmation_slugs: dict[str, str] = {}
for _asset, (_announcement, _confirmation) in K05_ASSET_ROUTES.items():
    _asset_slug = _asset.lower().replace("/", "_")
    announcement_slug = f"k05_{_asset_slug}_official_archive"
    PROBES.append(_u(announcement_slug, _announcement, "K05", "configured_asset_official_archive"))
    confirmation_slug = _confirmation_slugs.get(_confirmation)
    if confirmation_slug is None:
        confirmation_slug = f"k05_chain_{len(_confirmation_slugs) + 1:02d}_transaction_confirmation_docs"
        _confirmation_slugs[_confirmation] = confirmation_slug
        PROBES.append(_u(confirmation_slug, _confirmation, "K05", "configured_asset_immutable_confirmation_route"))
    K05_ASSET_SOURCE_RECORDS.append({"asset": _asset, "announcement_probe": announcement_slug, "confirmation_probe": confirmation_slug})


def _safe_name(slug: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", slug)


def _presence_only() -> list[str]:
    """Report existing local/provider exports by path presence; never read them."""
    hits: list[str] = []
    for root in (REPO / "data", REPO / "artifacts", REPO / "exports", REPO / "tmp"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and any(term in path.name.lower() for term in ("coinmetric", "provider", "export", "vintage", "pit")):
                hits.append(str(path.relative_to(REPO)))
    return sorted(hits)


def _json_row_count(data: bytes) -> tuple[int | None, str]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "not_applicable"
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        return len(value["data"]), "json_data_array"
    return None, "not_applicable"


def _derive_asset_evidence(announcement_body: str, confirmation_body: str, source_probe_ids: list[str]) -> dict[str, object]:
    text = f"{announcement_body}\n{confirmation_body}"
    contract_ids = sorted(set(re.findall(r'(?i)(?:contract(?:_address|Address)|token_contract|contract_id)\s*[=:]\s*["\']?(0x[a-f0-9]{40})', text)))
    unlock_ids = sorted(set(re.findall(r'(?i)(?:transaction(?:_hash|Hash)|tx_hash|unlock_id)\s*[=:]\s*["\']?(0x[a-f0-9]{64})', text)))
    missing = []
    if not contract_ids:
        missing.append("contract identifier")
    if not unlock_ids:
        missing.append("unlock transaction identifier")
    reason = "verified route bodies contain " + ", ".join(missing) + " missing" if missing else "verified route bodies contain contract and unlock identifiers"
    return {"contract_status": "RESOLVED" if contract_ids else "UNRESOLVED", "unlock_id_status": "RESOLVED" if unlock_ids else "UNRESOLVED", "contract_ids": contract_ids, "unlock_ids": unlock_ids, "reason": reason, "evidence_source_probes": source_probe_ids}


def probe(item: dict[str, str]) -> dict[str, object]:
    slug = _safe_name(item["slug"])
    is_archive = item["url"].split("?", 1)[0].endswith(".zip")
    body_path = OUT / f"{slug}.{'zip' if is_archive else 'body'}"
    receipt_path = Path(f"{body_path}.receipt.json")
    status: int | None = None
    error: str | None = None
    content_type: str | None = None
    final_url = item["url"]
    data = b""
    retryable = item["slug"] in {"k05_shib_usd_official_archive", "k05_ada_usd_official_archive", "k10_deribit_liquidation_api"}
    attempts = 2 if retryable else 1
    attempts_used = 0
    for attempt in range(attempts):
        attempts_used = attempt + 1
        try:
            request = Request(item["url"], headers={"User-Agent": UA, "Accept": "*/*"})
            with urlopen(request, timeout=12) as response:
                status = int(response.status)
                content_type = response.headers.get("Content-Type")
                final_url = response.geturl()
                data = response.read()
            error = None
            break
        except HTTPError as exc:
            status = int(exc.code)
            content_type = exc.headers.get("Content-Type") if exc.headers else None
            try:
                data = exc.read()
            except Exception:
                data = b""
            error = f"HTTPError:{exc.code}"
            if not (retryable and exc.code == 429 and attempt + 1 < attempts):
                break
            time.sleep(1.0)
        except (URLError, TimeoutError, OSError) as exc:
            error = f"{type(exc).__name__}:{exc}"
            if not (retryable and attempt + 1 < attempts):
                break
            time.sleep(1.0)
    OUT.mkdir(parents=True, exist_ok=True)
    body_path.write_bytes(data)
    receipt_data: dict[str, object] = {
        "provider": item["url"].split("/", 3)[2],
        "endpoint": item["url"],
        "final_url": final_url,
        "retrieved_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "timestamp_precision": "second",
        "status": status,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": content_type,
        "card": item["card"],
        "kind": item["kind"],
        "credential_fields_persisted": False,
        "attempts": attempts_used,
        "retry_policy": "bounded_retry_once_after_429_or_timeout" if retryable else "single_attempt",
        "terminal_status": None,
        "collector_slug": item["slug"],
        "slug_derivation": "collector_declaration_v1",
        "slug_basis_sha256": _slug_basis(item["card"], item["kind"], item["url"], item["slug"]),
        "recipe": None,
        "error": None,
    }
    if is_archive:
        receipt_data["recipe"] = {
            "recipe_id": "crypto_secondary_public_zip_probe_v2",
            "archive_identity": "archive SHA plus exact member name/CRC/size/header/row count/first-last ID",
            "request_url": item["url"],
            "final_url": final_url,
        }
    row_count: int | None
    count_semantics: str
    if is_archive and status == 200:
        members: list[dict[str, object]] = []
        total_rows = 0
        with ZipFile(BytesIO(data)) as archive:
            for info in archive.infolist():
                member: dict[str, object] = {"name": info.filename, "crc": info.CRC, "size": info.file_size}
                if not info.is_dir():
                    with archive.open(info) as raw_member:
                        text = TextIOWrapper(raw_member, encoding="utf-8", errors="replace", newline="")
                        reader = csv.reader(text)
                        header = next(reader, [])
                        first_row = next(reader, None)
                        row_count = 0 if first_row is None else 1
                        last_row = first_row
                        for row in reader:
                            row_count += 1
                            last_row = row
                        total_rows += row_count
                        member.update({"header": header, "row_count": row_count, "first_id": first_row[0] if first_row else None, "last_id": last_row[0] if last_row else None})
                members.append(member)
        receipt_data["archive_sha256"] = receipt_data["sha256"]
        receipt_data["members"] = members
        receipt_data["recipe"]["archive_sha256"] = receipt_data["archive_sha256"]
        receipt_data["recipe"]["source_archive"] = str(body_path.relative_to(REPO))
        receipt_data["recipe"]["source_archive_sha256"] = receipt_data["sha256"]
        receipt_data["recipe"]["receipt_binding"] = {"kind": "self_contained_raw_http", "endpoint": receipt_data["endpoint"], "final_url": receipt_data["final_url"], "status": receipt_data["status"], "retrieved_at_utc": receipt_data["retrieved_at_utc"], "content_type": receipt_data["content_type"], "body_sha256": receipt_data["sha256"]}
        row_count, count_semantics = total_rows, "zip_member_data_rows"
    elif is_archive:
        row_count, count_semantics = None, "not_applicable"
    elif item["url"].split("?", 1)[0].endswith(".json") or content_type and "json" in content_type.lower():
        row_count, count_semantics = _json_row_count(data)
    else:
        row_count, count_semantics = None, "not_applicable"
    receipt_data["row_count"] = row_count
    receipt_data["count_semantics"] = count_semantics
    if error:
        receipt_data["error"] = error
    if retryable and attempts_used >= attempts and status != 200:
        receipt_data["terminal_status"] = "ACCESS_RETRY_EXHAUSTED_FOR_FROZEN_RUN"
    if is_archive:
        members = receipt_data.get("members", []) if isinstance(receipt_data.get("members", []), list) else []
        recipe_id, recipe_date, recipe_symbol = _zip_recipe_id(item["card"], str(receipt_data["provider"]), item["url"], receipt_data.get("archive_sha256"), members)
        receipt_data["recipe"]["recipe_id"] = recipe_id
        receipt_data["recipe"]["date"] = recipe_date
        receipt_data["recipe"]["symbol"] = recipe_symbol
    receipt_path.write_text(json.dumps(receipt_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"slug": item["slug"], "provider": receipt_data["provider"], "card": item["card"], "kind": item["kind"], "endpoint": item["url"], "final_url": final_url, "status": status, "bytes": len(data), "body": str(body_path.relative_to(REPO)), "receipt": str(receipt_path.relative_to(REPO)), "sha256": receipt_data["sha256"], "error": error, "content_type": content_type, "retrieved_at_utc": receipt_data["retrieved_at_utc"], "timestamp_precision": receipt_data["timestamp_precision"], "credential_fields_persisted": False, "attempts": attempts_used, "retry_policy": receipt_data["retry_policy"], "terminal_status": receipt_data["terminal_status"], "collector_slug": receipt_data["collector_slug"], "slug_derivation": receipt_data["slug_derivation"], "slug_basis_sha256": receipt_data["slug_basis_sha256"], "row_count": row_count, "count_semantics": count_semantics, "recipe": receipt_data.get("recipe"), "mutable_current_evidence": item["kind"] in MUTABLE_EVIDENCE_KINDS, "mutable_evidence_rule": "kind_in_frozen_mutable_evidence_kinds_v1"}


def main() -> None:
    results = [probe(item) for item in PROBES]
    result_by_slug = {item["slug"]: item for item in results}
    asset_records = []
    for record in K05_ASSET_SOURCE_RECORDS:
        announcement = result_by_slug[record["announcement_probe"]]
        confirmation = result_by_slug[record["confirmation_probe"]]
        evidence = _derive_asset_evidence(Path(announcement["body"]).read_text(encoding="utf-8", errors="replace"), Path(confirmation["body"]).read_text(encoding="utf-8", errors="replace"), [record["announcement_probe"], record["confirmation_probe"]])
        asset_records.append({**record, **evidence, "admission": "BLOCKED" if evidence["contract_status"] != "RESOLVED" or evidence["unlock_id_status"] != "RESOLVED" else "PENDING_PIT"})
    retrieved = sorted(item["retrieved_at_utc"] for item in results)
    index = {
        "status": "PUBLIC_PROBES_CAPTURED_NO_ECONOMICS",
        "acquisition_state": "CURRENT_ACQUISITION_RUN_CLOSED_WITH_BLOCKERS",
        "next_stage_approval": "NOT_APPROVED",
        "retrieved_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "frozen_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "collection_window": {"start_utc": retrieved[0], "end_utc": retrieved[-1]},
        "probe_count": len(results),
        "counts_by_card": {card: sum(item["card"] == card for item in results) for card in sorted({item["card"] for item in results})},
        "credential_fields_persisted_count": sum(bool(item["credential_fields_persisted"]) for item in results),
        "mutable_evidence_rule": "kind_in_frozen_mutable_evidence_kinds_v1",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "credential_fields_persisted": False,
        "pit_conclusion": "Current public documentation, catalog rows, and labels do not establish immutable historical vintages; K07 remains PIT blocked. Event-day plus a publication schedule cannot reconstruct prior labels.",
        "next_public_or_authorized_steps": {
            "K02": "Check only already-authorized local/provider exports by path presence; otherwise a provisioned metric entitlement is required for adjusted-transfer/NVTAdj observations, followed separately by a PIT/revision vintage source.",
            "K05": "For each selected configured asset, obtain an issuer/governance announcement archive plus transaction-level confirmation and amendment history; current Tokenomist pages are insufficient.",
            "K06": "Obtain immutable issuer/SEC historical creation-redemption records with publication timestamps and frozen ETF mapping; Farside WP/current pages are only mutable evidence.",
            "K07": "Obtain a documented PIT/vintage endpoint or immutable label-set snapshots with available_at, exchange scope and label hash; schedule timing alone cannot supply this.",
            "K08": "Obtain an exact stablecoin exchange-flow provider/export with contract, bridge/mint/burn filters, labels and available_at; catalog membership is insufficient.",
            "K10": "Extend CM liquidation archives and acquire same-instrument/venue context with available_at; UM and CM data remain separate, and public forceOrders access is credential-blocked.",
            "K12": "Obtain SOPR observations with immutable metric/entity-cluster vintage and available_at; current catalog access does not establish that history.",
        },
        "authorized_local_paths_present_only": _presence_only(),
        "asset_specific_source_records": asset_records,
        "bound_source_records": [{"card": "K07", "body": str(K07_RAW.relative_to(REPO)), "receipt": str(Path(f"{K07_RAW}.receipt.json").relative_to(REPO)), "body_sha256": hashlib.sha256(K07_RAW.read_bytes()).hexdigest(), "receipt_sha256": hashlib.sha256(Path(f"{K07_RAW}.receipt.json").read_bytes()).hexdigest(), "endpoint": json.loads(Path(f"{K07_RAW}.receipt.json").read_text())["endpoint"], "status": json.loads(Path(f"{K07_RAW}.receipt.json").read_text())["status"], "row_count": json.loads(Path(f"{K07_RAW}.receipt.json").read_text())["row_count"], "count_semantics": json.loads(Path(f"{K07_RAW}.receipt.json").read_text())["count_semantics"]}],
        "probes": results,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "probe_inventory.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": index["status"], "probes": len(results), "inventory": str(OUT / "probe_inventory.json")}))


if __name__ == "__main__":
    main()
