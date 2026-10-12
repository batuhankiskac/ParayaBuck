#!/usr/bin/env python3
"""Adapt the archived Stage B harness modules (copied into ./stage_b/research/) to current code.

Sources: tag archive/research/sleeve-candidates-v2-v3-2026-09
(research/physical_availability_clock_v2/*.py, research/sleeve_candidates_v2/stage_b/*.py).
The adaptations touch imports and keyword arguments of code removed from
``src`` since (crypto strategies, numeric history, crypto fee); none touches
the execution model. Idempotent: re-running leaves patched files unchanged.

tbill_sweep_v1 copy of the e2_integration_v1 file (tag
archive/research/e2-integration-v1-2026-10); PATCHES unchanged.

Setup (from the repository root; the patched harness copies, the ``src``
symlink and the duplicated data copies are not committed because they are
byte copies of tagged or committed files):

    D=research/tbill_sweep_v1; S=$D/stage_b
    mkdir -p $S/research/physical_availability_clock_v2 $S/research/sleeve_candidates_v2/stage_b
    T=archive/research/sleeve-candidates-v2-v3-2026-09
    for f in run_historical shared_replay timing; do
      git show $T:research/physical_availability_clock_v2/$f.py > $S/research/physical_availability_clock_v2/$f.py
    done
    for f in composite run_stage_b; do
      git show $T:research/sleeve_candidates_v2/stage_b/$f.py > $S/research/sleeve_candidates_v2/stage_b/$f.py
    done
    cp data/historical/equity_entity_lifetimes_full_v5.csv data/historical/tbill_sweep_v1_etf_*.csv \\
        $S/data/historical/
    ln -s ../../../src $S/src
    python $D/adapt_harness.py
    research/tbill_sweep_v1/run_stage_b.sh <python>      # 8 runs, ~1-2 s each
    python research/tbill_sweep_v1/evaluate_stage_b.py   # writes STAGE_B_RESULTS.json

Committed: ``stage_b/research/paper_config_v2/run_stage_b.py`` (the
e2_integration_v1 runner plus the research-only sweep shim),
``stage_b/research/paper_config_v2/evaluate.py`` (byte copy from tag
archive/research/trend-robustness-v2-2026-10), ``stage_b/configs``,
``stage_b/data/historical/substitute_v5_*_qqq_spy_qld.csv`` (byte copies from
the e2_integration_v1 tag) and ``stage_b/runs``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent / "stage_b" / "research"

PATCHES: dict[str, list[tuple[str, str]]] = {
    "physical_availability_clock_v2/run_historical.py": [
        (
            "from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402\n",
            "def build_numeric_history(*a, **k):  # removed from src (da6190b); the engine no longer reads it\n"
            "    return None\n\n\n",
        ),
        (
            "from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1"
            "  # noqa: E402\n",
            "class CryptoLowVarianceHysteresis1dV1:  # crypto removed from src (crypto_removal_v1)\n"
            "    decide = None\n\n\n",
        ),
        ("        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,\n", ""),
        (
            "from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402\n",
            "def aggregate_historical_bars(*a, **k):  # crypto-only (hourly -> daily); removed from src\n"
            "    raise RuntimeError('crypto replay is not supported on current code')\n\n\n",
        ),
        (
            "        ordinary_alpha_excluded_symbols=(\n"
            "            config.universe.equity.ordinary_alpha_excluded_symbols if market == Market.EQUITY else ()\n"
            "        ),\n",
            "",
        ),
    ],
    "sleeve_candidates_v2/stage_b/run_stage_b.py": [
        (
            "from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402\n",
            "def build_numeric_history(*a, **k):  # removed from src (da6190b)\n    return None\n\n\n",
        ),
        ("            crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * multiplier,\n", ""),
        (
            "            ordinary_alpha_excluded_symbols=(\n"
            "                config.universe.equity.ordinary_alpha_excluded_symbols if market == Market.EQUITY else ()\n"
            "            ),\n",
            "",
        ),
    ],
    "physical_availability_clock_v2/shared_replay.py": [
        ("session_date_for(timestamp, market)", "session_date_for(timestamp)"),
        # 77e8639 removed persisted engine state (state/state_path kwargs).
        (
            "super().__init__(strategy, policy, state=None, state_path=None, **kwargs)",
            "super().__init__(strategy, policy, **kwargs)",
        ),
        # 77e8639 dropped the (persisted-state) cutoff; cutoff=None was a no-op.
        (
            "_prepare_corporate_actions(replay.corporate_actions, cutoff=None)",
            "_prepare_corporate_actions(replay.corporate_actions)",
        ),
    ],
}


def main() -> None:
    for relative, patches in PATCHES.items():
        path = ROOT / relative
        text = path.read_text()
        for old, new in patches:
            if old in text:
                text = text.replace(old, new)
            elif new and new in text:
                continue
            elif not new:
                continue
            else:
                raise SystemExit(f"{relative}: patch target not found: {old!r}")
        path.write_text(text)
        print("patched", relative)


if __name__ == "__main__":
    main()
