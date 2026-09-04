#!/usr/bin/env python3
"""Generate the P1 natural-PAPER report from local bars and a read-only SQLite DB.

This command requires a clean, committed checkout because the P1 manifest must
be committed before a PAPER row may be called eligible.  It never starts PAPER,
contacts a provider, changes economics, or writes the runtime database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.backtest.product import load_csv_bars  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.execution.shadow_comparator import dataset_metadata  # noqa: E402
from parayabuck.portfolio import product_strategy_from  # noqa: E402
from parayabuck.portfolio.runtime_models import configuration_digest  # noqa: E402
from parayabuck.research.p1_paper_observation import build_p1_report  # noqa: E402
from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest  # noqa: E402
from parayabuck.universe.scanner import scanner_config_digest  # noqa: E402

_MANIFEST = "data/research_manifests/execution_cost_transferability_p1_paper_observation_2026_09.json"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dataset_input(path: Path, default_market: Market, feed_override: str | None) -> tuple[Path, str, Market]:
    if path.suffix.lower() != ".json":
        if not feed_override:
            raise ValueError("--feed-identity is required when --dataset is a CSV")
        return path, feed_override, default_market
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("bars_path"), str):
        raise ValueError("dataset manifest requires a string bars_path")
    bars_path = Path(payload["bars_path"])
    if not bars_path.is_absolute():
        bars_path = path.parent / bars_path
    feed = feed_override or payload.get("feed_identity") or payload.get("source_feed")
    if not isinstance(feed, str) or not feed.strip():
        raise ValueError("dataset manifest requires feed_identity or --feed-identity")
    return bars_path, feed, Market(str(payload.get("default_market", default_market.value)).lower())


def _committed_epoch(receipt: dict[str, object]) -> None:
    if _git("status", "--porcelain"):
        raise ValueError("P1 report requires a clean worktree so the manifest is committed")
    head = _git("rev-parse", "HEAD")
    if str(receipt.get("manifest_commit")) != head or str(receipt.get("deployed_code_commit")) != head:
        raise ValueError("receipt manifest and deployed commits must equal the committed checkout HEAD")
    tracked = _git("ls-tree", "-r", "--name-only", "HEAD", "--", _MANIFEST)
    if tracked != _MANIFEST:
        raise ValueError("P1 machine manifest is not tracked by the merged commit")
    if str(receipt.get("manifest_git_blob_sha")) != _git("rev-parse", f"HEAD:{_MANIFEST}"):
        raise ValueError("epoch receipt manifest blob does not match HEAD")
    if str(receipt.get("manifest_commit_time")) != _git("show", "-s", "--format=%cI", head):
        raise ValueError("epoch receipt manifest commit time does not match HEAD")
    if str(receipt.get("manifest_sha256")) != _sha256(ROOT / _MANIFEST):
        raise ValueError("epoch receipt manifest SHA-256 does not match HEAD")


def _expected_digests(config: object) -> dict[str, str]:
    risk_limits = portfolio_risk_limits_from_config(config.risk)
    execution_policy = PortfolioExecutionPolicy(
        rebalance_band_pct=config.execution.rebalance_band_pct,
        minimum_order_notional=config.execution.minimum_order_notional,
    )
    result: dict[str, str] = {}
    for market, selection, universe_version in (
        ("equity", config.strategy.equity, scanner_config_digest(config.universe.equity)),
        ("crypto", config.strategy.crypto, crypto_scanner_config_digest(config.universe.crypto)),
    ):
        strategy = product_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
        result[market] = configuration_digest(
            strategy, "paper", selection.maximum_allocation, universe_version, risk_limits, execution_policy
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="operator runtime SQLite database; opened read-only")
    parser.add_argument("--dataset", required=True, help="local CSV or local bars manifest")
    parser.add_argument("--feed-identity", default=None, help="required for a CSV dataset")
    parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    parser.add_argument("--epoch-receipt", required=True, help="immutable P1 epoch receipt JSON")
    parser.add_argument("--as-of", default=None, help="ISO-8601 report cutoff; defaults to current UTC")
    parser.add_argument("--default-market", choices=[item.value for item in Market], default="equity")
    parser.add_argument("--json-output", default=None, help="optional report output path; runtime DB is never written")
    args = parser.parse_args(argv)

    receipt_path = Path(args.epoch_receipt).resolve()
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("--epoch-receipt must name a readable JSON object") from exc
    if not isinstance(receipt, dict):
        raise ValueError("--epoch-receipt must contain a JSON object")
    _committed_epoch(receipt)
    config_path = Path(args.config).resolve()
    if _sha256(config_path) != str(receipt.get("settings_sha256")):
        raise ValueError("settings.yaml SHA-256 does not match the declared sample epoch")
    dataset_path = Path(args.dataset).resolve()
    bars_path, feed, default_market = _dataset_input(dataset_path, Market(args.default_market), args.feed_identity)
    bars = load_csv_bars(bars_path, default_market=default_market)
    metadata = dataset_metadata(bars_path, bars, feed_identity=feed)
    metadata["manifest_path"] = str(dataset_path) if dataset_path.suffix.lower() == ".json" else None
    as_of = datetime.now(UTC) if args.as_of is None else datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    config = load_config(config_path)
    if receipt.get("configuration_digests") != _expected_digests(config):
        raise ValueError("epoch receipt configuration digests do not match the loaded committed configuration")
    report = build_p1_report(
        args.db,
        bars,
        config,
        dataset=metadata,
        contract={
            "epoch_receipt": receipt,
            "manifest_path": str(ROOT / _MANIFEST),
            "strategy_identities": {
                "equity": config.strategy.equity.name,
                "crypto": config.strategy.crypto.name,
            },
        },
        as_of=as_of,
    )
    epoch = report["sample_epoch_receipt"]
    assert isinstance(epoch, dict)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        Path(args.json_output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
