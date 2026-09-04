#!/usr/bin/env python3
"""Read-only, offline PAPER execution comparison against local bars."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.backtest.product import load_csv_bars  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.shadow_comparator import dataset_metadata, run_shadow_comparator  # noqa: E402


def _dataset_input(path: Path, default_market: Market) -> tuple[Path, str | None, Market]:
    if path.suffix.lower() != ".json":
        return path, None, default_market
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("bars_path"), str):
        raise ValueError("dataset manifest requires a string bars_path")
    bars_path = Path(payload["bars_path"])
    if not bars_path.is_absolute():
        bars_path = path.parent / bars_path
    market = Market(str(payload.get("default_market", default_market.value)).lower())
    feed = payload.get("feed_identity") or payload.get("source_feed")
    return bars_path, str(feed) if feed is not None else None, market


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="runtime SQLite database (opened mode=ro)")
    parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    parser.add_argument("--dataset", required=True, help="local executable-bars CSV or JSON manifest")
    parser.add_argument("--default-market", choices=[item.value for item in Market], default=Market.EQUITY.value)
    parser.add_argument("--json-output", default=None, help="write JSON to this path; stdout otherwise")
    args = parser.parse_args()
    dataset_path = Path(args.dataset).resolve()
    bars_path, feed, default_market = _dataset_input(dataset_path, Market(args.default_market))
    bars = load_csv_bars(bars_path, default_market=default_market)
    metadata = dataset_metadata(bars_path, bars, feed_identity=feed)
    metadata["manifest_path"] = str(dataset_path) if dataset_path.suffix.lower() == ".json" else None
    report = run_shadow_comparator(args.db, bars, load_config(args.config), dataset=metadata)
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    if args.json_output:
        Path(args.json_output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
