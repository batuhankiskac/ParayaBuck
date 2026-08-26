#!/usr/bin/env python3
"""Deterministic crypto universe parity audit — research ↔ PAPER.

No orders placed. Offline portion works without broker credentials.

Outputs canonical JSON to:
  data/research/crypto_foundation_2026_08/crypto_universe_parity_audit.json
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

# Ensure src on path
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from parayabuck.config import load_config
from parayabuck.universe.crypto_scanner import CRYPTO_CATEGORIES, crypto_scanner_config_digest


def sha_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def sorted_join(items) -> str:
    return ",".join(sorted(items))


def load_historical_dataset(path: Path):
    if not path.exists():
        return {
            "exists": False,
            "row_count": 0,
            "unique_symbols": [],
            "first_ts": {},
            "last_ts": {},
            "counts": {},
            "coverage": {},
        }
    counts = Counter()
    first = {}
    last = {}
    rows = 0
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows += 1
            sym = row.get("symbol") or row.get("Symbol") or ""
            ts = row.get("timestamp") or row.get("Timestamp") or ""
            if not sym:
                continue
            counts[sym] += 1
            if sym not in first:
                first[sym] = ts
            last[sym] = ts
    uniq = sorted(counts.keys())
    return {
        "exists": True,
        "row_count": rows,
        "unique_symbols": uniq,
        "first_ts": {k: first[k] for k in sorted(first)},
        "last_ts": {k: last[k] for k in sorted(last)},
        "counts": {k: counts[k] for k in sorted(counts)},
    }


def detect_candidate_pool_policy() -> str:
    pre = (ROOT / "src/parayabuck/universe/preflight.py").read_text()
    # Detect new vs old logic
    if "configured_pool" in pre and "trade_symbols" in pre and "broker_active" in pre:
        return "trade_symbols ∩ broker_active_tradable (authoritative configured pool)"
    if "candidate_symbols = tuple(sorted(by_symbol))" in pre:
        return "all_broker_active_usd (legacy, before fix)"
    return "unknown"


def main() -> None:
    config = load_config()
    crypto = config.universe.crypto
    strat = config.strategy.crypto

    # config digests
    raw_yaml = (ROOT / "config/settings.yaml").read_text()
    config_digest = hashlib.sha256(raw_yaml.encode()).hexdigest()
    scanner_digest = crypto_scanner_config_digest(crypto)

    # strategy provenance
    from parayabuck.portfolio import portfolio_strategy_from

    strategy = portfolio_strategy_from(strat.name, strat.parameters, timeframe=strat.timeframe)
    params_digest = hashlib.sha256(
        json.dumps(strat.parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    obs_name = crypto.observation_strategy_name
    obs_version = crypto.observation_strategy_version
    obs_timeframe = crypto.observation_strategy_timeframe
    obs_params_digest = crypto.observation_strategy_parameters_digest

    # symbols
    trade_symbols = list(crypto.trade_symbols)
    pinned = list(crypto.pinned_symbols)
    allowlist = list(crypto.allowlist_symbols)
    denylist = list(crypto.denylist_symbols)

    # category coverage
    missing = [s for s in trade_symbols if s not in CRYPTO_CATEGORIES]
    unknown = [s for s in trade_symbols if CRYPTO_CATEGORIES.get(s) == "unknown"]
    coverage = {s: CRYPTO_CATEGORIES.get(s, "unknown") for s in sorted(trade_symbols)}
    by_category = Counter(coverage.values())

    # historical dataset
    dataset_path = ROOT / "data/historical/crypto_expanded_1h.csv"
    hist = load_historical_dataset(dataset_path)
    dataset_symbols = hist["unique_symbols"] if hist["exists"] else []
    config_only = sorted(set(trade_symbols) - set(dataset_symbols))
    dataset_only = sorted(set(dataset_symbols) - set(trade_symbols))
    intersect = sorted(set(trade_symbols) & set(dataset_symbols))

    # broker intersection (if env available, try, else unavailable)
    broker_status = "unavailable"
    broker_symbols = []
    try:
        import os

        key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        if key and secret:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.enums import AssetClass, AssetStatus
            from alpaca.trading.requests import GetAssetsRequest

            trading = TradingClient(key, secret, paper=True)
            assets = trading.get_all_assets(GetAssetsRequest(asset_class=AssetClass.CRYPTO, status=AssetStatus.ACTIVE))
            syms = []
            for a in assets:
                raw = str(getattr(a, "symbol", "")).strip().upper()
                # normalize via helper
                from parayabuck.core.models import Market
                from parayabuck.markets.symbols import canonical_symbol

                try:
                    # try to infer
                    if raw.endswith("USD") and len(raw) > 3:
                        # legacy no slash
                        cand = f"{raw[:-3]}/USD" if "/" not in raw else raw
                        sym = canonical_symbol(cand, Market.CRYPTO)
                        if sym.endswith("/USD"):
                            syms.append(sym)
                except Exception:
                    continue
            broker_symbols = sorted(set(syms))
            broker_status = "available"
        else:
            broker_status = "unavailable (no credentials)"
    except Exception as exc:
        broker_status = f"unavailable ({exc.__class__.__name__}: {exc})"

    # candidate pool policy
    policy = detect_candidate_pool_policy()

    # observation identity check
    active_path = Path(crypto.active_universe_path)
    persisted = {}
    persisted_status = "missing"
    if active_path.exists():
        try:
            payload = json.loads(active_path.read_text())
            persisted = {
                "selection_timestamp": payload.get("selection_timestamp"),
                "data_timestamp": payload.get("data_timestamp"),
                "candidate_symbols": payload.get("candidate_symbols"),
                "selected_symbols": payload.get("selected_symbols"),
                "observation_set": payload.get("observation_set"),
                "observation_strategy_name": payload.get("observation_strategy_name"),
                "observation_strategy_version": payload.get("observation_strategy_version"),
                "observation_strategy_timeframe": payload.get("observation_strategy_timeframe"),
                "observation_strategy_parameters_digest": payload.get("observation_strategy_parameters_digest"),
                "config_digest": payload.get("config_digest"),
                "scanner_version": payload.get("scanner_version"),
            }
            # validate via function
            from parayabuck.universe.crypto_scanner import validate_active_crypto_universe

            _, persisted_status = validate_active_crypto_universe(crypto)
        except Exception as exc:
            persisted_status = f"invalid ({exc})"

    # deterministic hashes
    hashes = {
        "trade_symbols_sha256": sha_hex(sorted_join(trade_symbols)),
        "dataset_symbols_sha256": sha_hex(sorted_join(dataset_symbols)) if dataset_symbols else "",
        "config_digest_sha256": config_digest,
        "scanner_digest_sha256": scanner_digest,
        "params_digest_sha256": params_digest,
        "combined_sha256": sha_hex(config_digest + scanner_digest + params_digest),
    }

    artifact = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        "config": {
            "mode": config.runtime.mode.value,
            "live_enabled": config.live.enabled,
            "hard_dollar_cap": config.live.hard_dollar_cap,
            "config_digest_sha256": config_digest,
            "scanner_digest_sha256": scanner_digest,
            "crypto_strategy": {
                "name": strat.name,
                "version": strategy.version,
                "timeframe": strategy.timeframe,
                "parameters": strat.parameters,
                "parameters_digest_sha256": params_digest,
                "maximum_allocation": strat.maximum_allocation,
                "risk": {
                    "max_crypto_exposure_pct": config.risk.max_crypto_exposure_pct,
                    "max_crypto_position_pct": config.risk.max_crypto_position_pct,
                    "max_crypto_positions": config.risk.max_crypto_positions,
                },
            },
            "crypto_universe": {
                "enabled": crypto.enabled,
                "dynamic_selection_enabled": crypto.dynamic_selection_enabled,
                "maximum_stream_symbols": crypto.maximum_stream_symbols,
                "trade_symbols_count": len(trade_symbols),
                "trade_symbols": sorted(trade_symbols),
                "pinned_symbols": sorted(pinned),
                "allowlist_symbols": sorted(allowlist),
                "denylist_symbols": sorted(denylist),
                "trade_symbols_sha256": hashes["trade_symbols_sha256"],
            },
        },
        "category_metadata": {
            "coverage": coverage,
            "by_category": dict(sorted(by_category.items())),
            "missing": missing,
            "unknown": unknown,
            "coverage_complete": len(missing) == 0,
            "maximum_per_category": crypto.scanner.maximum_per_category,
            "maximum_meme_symbols": crypto.scanner.maximum_meme_symbols,
            "maximum_high_beta_symbols": crypto.scanner.maximum_high_beta_symbols,
        },
        "broker": {
            "status": broker_status,
            "available_symbols_count": len(broker_symbols) if broker_symbols else 0,
            "available_symbols": broker_symbols[:50] if broker_symbols else [],
            "intersection_with_config": sorted(set(trade_symbols) & set(broker_symbols)) if broker_symbols else [],
        },
        "historical_dataset": {
            "path": str(dataset_path),
            "exists": hist["exists"],
            "row_count": hist.get("row_count", 0),
            "unique_symbols": dataset_symbols,
            "unique_count": len(dataset_symbols),
            "counts": hist.get("counts", {}),
            "first_timestamp": hist.get("first_ts", {}),
            "last_timestamp": hist.get("last_ts", {}),
            "config_only_symbols": config_only,
            "dataset_only_symbols": dataset_only,
            "intersection_symbols": intersect,
            "dataset_symbols_sha256": hashes["dataset_symbols_sha256"],
        },
        "candidate_pool_policy": {
            "policy": policy,
            "description": "configured trade_symbols ∩ broker active/tradable (PAPER); configured ∩ bar availability (research PIT)",
        },
        "observation_provenance": {
            "config_observation_strategy_name": obs_name,
            "config_observation_strategy_version": obs_version,
            "config_observation_strategy_timeframe": obs_timeframe,
            "config_observation_strategy_parameters_digest": obs_params_digest,
            "persisted_snapshot": persisted,
            "persisted_validation_status": persisted_status,
            "scanner_config_digest_excludes_observation": True,
        },
        "hashes": hashes,
        "maximum_stream_symbols": crypto.maximum_stream_symbols,
    }

    out_dir = ROOT / "data/research/crypto_foundation_2026_08"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "crypto_universe_parity_audit.json"
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True))
    print(f"Audit artifact written to {out_path}")
    print(json.dumps(artifact, indent=2, sort_keys=True))
    # quick human summary
    print("\n--- SUMMARY ---")
    print(f"Config trade symbols ({len(trade_symbols)}): {sorted(trade_symbols)}")
    print(f"Missing categories: {missing or 'none'}")
    print(f"Policy: {policy}")
    print(f"Dataset unique: {len(dataset_symbols)} row_count={hist.get('row_count', 0)}")
    print(f"Config only: {config_only}")
    print(f"Dataset only: {dataset_only}")
    print(f"Persisted status: {persisted_status}")
    print(f"Hashes: {hashes}")


if __name__ == "__main__":
    main()
