#!/usr/bin/env python3
"""Diagnostic only: dump one Stage B arm's non-sleeve events for divergence analysis.

Wraps ``run_stage_b.main`` unchanged and additionally writes the equity/crypto
decision, fill and latch events (sleeve symbols excluded) so two arms can be
compared to find where their V3/crypto paths first diverge.

    .venv/bin/python research/sleeve_candidates_v2/stage_b/diagnose_divergence.py \
        --events-out /tmp/x.json.gz -- --arm C --candidate tbill_sweep_v1 --cost 1.0 --out /tmp/run.json.gz
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_stage_b  # noqa: E402

from research.physical_availability_clock_v2 import shared_replay as shared  # noqa: E402
from research.sleeve_candidates_v2.stage_b import composite as sleeve  # noqa: E402

KEPT = {"decision", "fill", "buy_intent_deferred", "buy_intent_cancelled", "order_rejected", "risk_halt"}


def main() -> int:
    separator = sys.argv.index("--")
    events_out = sys.argv[sys.argv.index("--events-out") + 1]
    sys.argv = [sys.argv[0], *sys.argv[separator + 1 :]]
    original_run = shared.SharedAccountReplay.run

    def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = original_run(self, *args, **kwargs)
        kept = [
            {key: value for key, value in event.items() if key != "client_order_id"}
            for event in result.events
            if event.get("symbol") not in sleeve.ETF_SLEEVE_SYMBOLS
            and (event["event"] in KEPT or "halt" in event["event"] or "latch" in event["event"])
        ]
        with gzip.open(events_out, "wt", encoding="utf-8") as handle:
            json.dump({"event_types": sorted({event["event"] for event in result.events}), "events": kept}, handle)
        return result

    shared.SharedAccountReplay.run = run
    return run_stage_b.main()


if __name__ == "__main__":
    raise SystemExit(main())
