#!/usr/bin/env python3
"""Fail-closed contract and execution runner for the M3 economic experiment.

The default and contract-validation commands remain non-economic.  The
explicit execution command is available only after the separately committed
execution authorization manifest binds the exact A26 implementation, B26
manifest, accepted upstream identities, local input bytes, observation
boundaries, and output directory.  It has no broker or network path.

Allowed commands::

    python scripts/research_equity_m3_economic.py --validate-contract
    python scripts/research_equity_m3_economic.py --synthetic-fixture

The real command consumes only staged local accepted artifacts and writes
ignored raw output plus compact result artifacts for the append-only C26/D26
chain.
"""

from __future__ import annotations

import argparse
import ast
import copy
import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, NoReturn

import numpy as np
import yaml

_REPO_BOOTSTRAP = Path(__file__).resolve().parents[1]
for _path in (_REPO_BOOTSTRAP / "src", _REPO_BOOTSTRAP / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from compact_equity_m3_candidate_ocp_attestation_r2 import (  # noqa: E402
    AttestationError,
    validate_r2_receipt,
)
from m3_data_gate_helpers import (  # noqa: E402
    canonical_digest,
    load_csv_reference_rows,
    semantic_rows_digest,
)
from research_equity_m3_m4 import ProxyMultifactorResidualMomentum  # noqa: E402

from parayabuck.backtest.numeric_history import NumericHistoryView, build_numeric_history  # noqa: E402
from parayabuck.backtest.portfolio_engine import OrderSide  # noqa: E402
from parayabuck.backtest.terminal_aware_engine import (  # noqa: E402
    TerminalAwareResearchEngine,
    TerminalInfo,
)
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot  # noqa: E402
from parayabuck.config import BacktestConfig, ExecutionConfig, RiskLimits  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3  # noqa: E402
from parayabuck.portfolio.models import TargetPortfolio  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

MISSION = "M3_ECONOMIC_MISSION_PREDECLARATION_R1"
EXECUTION_MISSION = "M3_ECONOMIC_MISSION_EXECUTION_R1"
PREDECLARED_VERDICT = "M3_ECONOMIC_MISSION_PREDECLARED"
BLOCKED_VERDICT = "M3_ECONOMIC_PREDECLARATION_BLOCKED"
PASS_VERDICT = "M3_ECONOMIC_GATE_PASS"
FAIL_VERDICT = "M3_ECONOMIC_GATE_FAIL"
ECONOMIC_BLOCKED_VERDICT = "M3_ECONOMIC_GATE_BLOCKED"

PREDECL_STARTING_HEAD = "06aaf78a7ea75aa3d97404916d5107194534fe6c"
STARTING_HEAD = "b487d25ae1abf4140f536462463a8e83b97090bb"
R5_RESULT_COMMIT = "3c2f67d31772f2fba118137ad262bd8ffdb737d8"
R5_RECEIPT_DIGEST = "41afb58aca2fb38f4f0d301fc169938809b1cb9afcfa5c9855d97e292916abeb"
R5_MANIFEST_COMMIT = "2f6ce2aaf347b9e0257d4ae31e9fdcc90cdcafe4"
R2_RECEIPT_DIGEST = "8a576e650060c27e7f4a0c6f0fd8a532bc2509df26fd58c0587656ad47f766b6"
R2_CORRECTIVE_CI_RUN = "33678608824"
R2_CORRECTIVE_CI_HEAD = "154ffd6925327ea6283903f3f1c1098992efef3e"
R2_IMPLEMENTATION_COMMIT = "6e8067dfa816d2de138004babf5d5f47ab327642"
R2_MANIFEST_COMMIT = "4ebb5fa2353eba43794b48fc5b00a6cfe0cd902"
R2_RESULT_COMMIT = "154ffd6925327ea6283903f3f1c1098992efef3e"
E24_COMMIT = PREDECL_STARTING_HEAD
E24_PARENT = "f05056198f2519cfd396b1b05d3779bd7e7c2ace"
E25_COMMIT = STARTING_HEAD

R5_RECEIPT_PATH = "data/research_receipts/equity_m3_data_gate_2026_09_r5.json"
R5_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r5.json"
R5_PIT_MANIFEST_PATH = "data/research_manifests/equity_m3_pit_population_2026_09_r5.json"
R2_RECEIPT_PATH = "data/research_receipts/equity_m3_candidate_ocp_2026_09_r2.json"
R2_MANIFEST_PATH = "data/research_manifests/equity_m3_candidate_ocp_remote_attestation_2026_09_r2.json"
OLD_OCP_RECEIPT_PATH = "data/research_receipts/equity_m3_candidate_ocp_2026_09.json"
CONFIG_PATH = "config/settings.yaml"

MANIFEST_PATH = "data/research_manifests/equity_m3_economic_2026_09_r1.json"
RECEIPT_PATH = "data/research_receipts/equity_m3_economic_predeclaration_2026_09_r1.json"
REPORT_PATH = "docs/equity_m3_economic_predeclaration_2026_09_r1.md"
RAW_OUTPUT_DIR = "data/research/generated/equity_m3_economic_2026_09_r1"

EXECUTION_MANIFEST_PATH = "data/research_manifests/equity_m3_economic_execution_2026_09_r1.json"
EXECUTION_RECEIPT_PATH = "data/research_receipts/equity_m3_economic_2026_09_r1.json"
EXECUTION_REPORT_PATH = "docs/equity_m3_economic_2026_09_r1.md"
EXECUTION_RAW_OUTPUT_DIR = RAW_OUTPUT_DIR
R5_PIT_PROJECTION_SOURCE = Path("/private/tmp/m3-r5-b18-pit.json")
R5_PIT_PROJECTION_LABEL = "<external:m3-r5-b18-pit.json>"
CANONICAL_BARS_PATH = "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIME_PATH = "data/historical/equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTIONS_PATH = "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
IWM_BUNDLE_PATH = "data/research/external/m3_iwm_raw_2026_09_r4"
IWM_SIDECAR_PATH = "data/research/generated/m3_data_gate_r4/iwm_normalized.csv"
OCP_CONTROL_PATH = "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/control_universe.json"
OCP_CANDIDATE_PATH = "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/m3_universe.json"
EXECUTION_IMPLEMENTATION_PATHS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    "scripts/compact_equity_m3_economic_predeclaration.py",
    "scripts/compact_equity_m3_economic_result.py",
    "scripts/research_equity_m3_economic.py",
    "tests/test_research_equity_m3_economic.py",
    "tests/test_research_equity_m3_economic_execution.py",
)

IMPLEMENTATION_PATHS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    "scripts/compact_equity_m3_economic_predeclaration.py",
    "scripts/research_equity_m3_economic.py",
    "tests/fixtures/equity_m3_economic/README.md",
    "tests/test_research_equity_m3_economic.py",
)

FROZEN_PARAMETERS: dict[str, object] = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}

DATA_IDENTITIES: dict[str, object] = {
    "canonical_equity_v5": {
        "raw_sha256": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
        "crlf_normalized_sha256": "df42733b640e6dea17e1ed11115846933a86d2c525810dd3f473acc32e6d0db3",
        "manifest_git_blob": "a366944ec26d6f9edff82e9e892fe397f3aed112",
    },
    "lifetime": {
        "raw_sha256": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    },
    "corporate_actions": {
        "raw_sha256": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
    },
    "iwm_raw_bundle": {
        "raw_sha256": "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258",
    },
    "iwm_source_receipt": {
        "raw_sha256": "2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003",
    },
    "iwm_normalized_sidecar": {
        "raw_sha256": "8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2",
        "semantic_row_sha256": "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837",
    },
    "composite_semantic": {
        "sha256": "5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda",
    },
    "r5_evaluation": {
        "pit_raw_sha256": "657f2d00716ca33dd4142f7bb6166fcd72f2344fad20678456c731b292fd77ac",
        "projection_digest_sha256": "7e1a5ee098e23397d8f4d77e7d73e79c8491c28b089854f9174a8c0049a7b240",
        "candidate_universe_digest_sha256": "2a0c5b987d40cefed46c4f2ded595e1759e13c8744e2bc003f2da4befac2106e",
        "candidate_decision_eligibility_digest_sha256": "ab5ee2d5c1bc58f711f1961dff505563adf67c02d67917c1d067431136514a32",
        "matrix_merkle_root": "99187e5e0d94348b87e6c89aae3612d4bbd40e3795e6844289daf740d18073d9",
    },
}

OBSERVATION_IDENTITIES: dict[str, object] = {
    "control_C": {
        "strategy": "equity_residual_momentum_v3",
        "version": "3.0.0",
        "market": "equity",
        "timeframe": "1d",
        "boundary": "C|C",
        "strategy_parameter_digest_sha256": "d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa",
        "semantic_observation_universe_hash": "e1f0e2ee7420a9d43a8fe69e2048e4a5a3e135f3d3ffc5d2bb8b425fdb487eb1",
        "ordered_observation_merkle_root": "d6ec0d61b100ab527ee2978ddcd6210c09f866b03cc62637525c9ac4f2d6509a",
    },
    "candidate_X": {
        "strategy": "equity_proxy_multifactor_residual_momentum_v1",
        "version": "1.0.0",
        "market": "equity",
        "timeframe": "1d",
        "boundary": "X|X",
        "strategy_parameter_digest_sha256": "d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa",
        "semantic_observation_universe_hash": "eb9b3df623a2fdcb5b02c9c174fbf629a355f4faf018df9989de8703f8907f6f",
        "ordered_observation_merkle_root": "cee257f5053570c2b5957adf5b6b4b8b2c0943273790b20135a24ed5ffbe568f",
    },
}

CANDIDATE_OCP_EVIDENCE: dict[str, object] = {
    "snapshots": 2658,
    "sequence_changes": 2384,
    "membership_changes": 2381,
    "scanner_metadata_mismatches": 0,
    "direct_builder_parity": "9/9",
    "future_bar_mutation_failures": 0,
    "reference_order": ["SPY", "IWM", "QQQ", "SH", "PSQ", "DOG"],
    "maximum_stream_symbols": 30,
    "total_reserved_pins": 6,
    "effective_candidate_observation_capacity": 24,
}

STRATEGY_CONTRACT: dict[str, object] = {
    "control": {
        "name": "equity_residual_momentum_v3",
        "version": "3.0.0",
        "observation_boundary": "C|C",
        "execution_strategy": "fresh V3 instance",
    },
    "candidate": {
        "name": "equity_proxy_multifactor_residual_momentum_v1",
        "version": "1.0.0",
        "observation_boundary": "X|X",
        "execution_strategy": "fresh M3 instance",
    },
    "m3_formula": ("r_stock - beta_spy*r_SPY - beta_size*(r_IWM-r_SPY) - beta_growth*(r_QQQ-r_SPY)"),
    "shared_parameters": FROZEN_PARAMETERS,
    "residual_semantics": {
        "fit": "demeaned X and demeaned stock returns",
        "apply": "apply coefficients to non-demeaned factors",
        "intercept": "no explicit intercept",
        "convention": "alpha-retaining residual",
    },
    "skip_semantics": {
        "beta_fit": "all 273 returns remain in beta fitting",
        "momentum": "newest 21 residual returns excluded only from compounding",
    },
}

REFERENCE_CONTRACT: dict[str, object] = {
    "observation_only_references": ["SPY", "IWM", "QQQ"],
    "full_pin_order": ["SPY", "IWM", "QQQ", "SH", "PSQ", "DOG"],
    "references_outside_candidate_membership": True,
    "references_outside_targets": True,
    "references_outside_executable_symbols": True,
    "reference_bars_available_to_m3": True,
    "maximum_stream_symbols": 30,
    "effective_candidate_observation_capacity": 24,
}

EVALUATION_WINDOWS: dict[str, object] = {
    "FULL": {
        "warmup_source_start": "2016-01-04",
        "metric_start": "2017-02-06",
        "inclusive_end": "2026-07-30",
    },
    "RECENT": {
        "warmup_before_metric_start_allowed": True,
        "metric_start": "2022-01-03",
        "inclusive_end": "2026-07-30",
        "separate_backtest_from_full": True,
    },
}

ROBUSTNESS_PERIODS = [
    {"start": "2017-02-06", "end": "2019-12-31"},
    {"start": "2020-01-02", "end": "2021-12-31"},
    {"start": "2022-01-03", "end": "2023-12-29"},
    {"start": "2024-01-02", "end": "2026-07-30"},
]

TIMING_CONTRACT: dict[str, object] = {
    "completed_bar_signal_timing": (
        "The completed regular-session bar is ingested before the decision for that session timestamp."
    ),
    "decision_timestamp": "the first bar timestamp of the completed session cycle",
    "pit_snapshot": "latest snapshot with effective_at <= exact decision timestamp",
    "order_creation_timestamp": "the decision timestamp",
    "same_bar_fill": "forbidden; pending orders are filled before the next decision cycle",
    "execution": "next tradable bar only",
    "corporate_action_ordering": "corporate actions apply before fills in the session cycle",
    "terminal_map": "T1 terminal lifecycle is applied after the final decision and identically for C and X",
    "final_valuation": "last included evaluation session after terminal handling and explicit end liquidation",
    "near_window_end": (
        "an order created on 2026-07-30 has no eligible next execution bar, "
        "is not silently filled, and is excluded from economics"
    ),
    "post_window_data": "bars after 2026-07-30 cannot enter the reported window",
}

BASELINE_CONFIGURATION: dict[str, object] = {
    "equity_sleeve": {"maximum_allocation": 0.55},
    "risk": {
        "max_position_pct": 0.10,
        "max_positions": 6,
        "cash_buffer_pct": 0.40,
        "max_gross_exposure_pct": 0.60,
        "daily_loss_limit_pct": 0.005,
        "max_drawdown_pct": 0.05,
    },
    "backtest": {
        "initial_cash": 100_000,
        "slippage_bps": 2,
        "spread_bps": 2,
        "commission": 0,
        "equity_use_effective_dated_fees": True,
        "equity_taf_per_share_fallback": 0.000195,
        "equity_taf_cap_fallback": 9.79,
        "equity_cat_per_share_fallback": 0.000003,
        "equity_sec_fee_rate_fallback": 0.0000206,
    },
    "execution": {"rebalance_band_pct": 0.005, "minimum_order_notional": 10},
    "research_execution": {
        "benchmark_symbol": "SPY",
        "terminal_policy": "T1",
        "enforce_drawdown_halt": False,
        "risk_free_rate": 0,
        "annualization_sessions": 252,
    },
}

COST_SCENARIOS: dict[str, object] = {
    "BASELINE_1X": {"multiplier": 1, "slippage_bps": 2, "spread_bps": 2, "hard_acceptance": True},
    "STRESS_2X": {"multiplier": 2, "slippage_bps": 4, "spread_bps": 4, "hard_acceptance": True},
    "STRESS_3X": {"multiplier": 3, "slippage_bps": 6, "spread_bps": 6, "hard_acceptance": False},
    "STRESS_5X": {"multiplier": 5, "slippage_bps": 10, "spread_bps": 10, "hard_acceptance": False},
}
COST_SCENARIO_CONTRACT = {
    "multiplied_only": ["slippage_bps", "spread_bps"],
    "not_multiplied": ["commission", "TAF", "CAT", "SEC fees", "other effective-dated regulatory fees"],
    "same_values_for_control_and_candidate": True,
}

STRATEGY_METRICS = [
    "net total return",
    "CAGR",
    "annualized volatility",
    "Sharpe ratio",
    "Sortino ratio",
    "Calmar ratio",
    "maximum drawdown",
    "ending equity",
    "turnover",
    "average cash ratio",
    "average gross exposure",
    "maximum gross exposure",
    "rebalance count",
    "order count",
    "fill count",
    "commission cost",
    "spread cost",
    "slippage cost",
    "regulatory fees",
    "total estimated trading cost",
]
ACTIVE_METRICS = [
    "active total return",
    "CAGR difference",
    "annualized-return difference",
    "volatility difference",
    "Sharpe difference",
    "Sortino difference",
    "Calmar difference",
    "maximum-drawdown difference",
    "turnover ratio",
    "trading-cost ratio",
    "average-cash difference",
    "average-gross-exposure difference",
    "weekly active-return mean",
    "weekly active-return volatility",
    "information ratio",
    "weekly active hit rate",
    "maximum active drawdown",
    "active return for every frozen robustness period",
    "active return for every calendar year",
]
METRIC_CONTRACT: dict[str, object] = {
    "per_strategy": STRATEGY_METRICS,
    "candidate_minus_control": ACTIVE_METRICS,
    "conventions": {
        "daily_return": "consecutive daily end-of-session equity values",
        "annualization_factor": 252,
        "risk_free_rate": 0,
        "weekly_value": "final available NYSE session of each calendar week",
        "active_wealth": "paired C and X returns over identical dates",
        "drawdown": "positive maximum-drawdown magnitude",
        "omission_policy": "bad and negative results are never omitted",
    },
}

BOOTSTRAP_CONTRACT: dict[str, object] = {
    "method": "moving-block bootstrap on weekly active returns",
    "seed": 20260902,
    "block_length_weeks": 4,
    "resamples": 10_000,
    "statistic": "mean weekly X-minus-C return",
    "reported": ["bootstrap median", "5th percentile", "95th percentile", "probability mean active return > 0"],
    "diagnostic_only": True,
}

ACCEPTANCE_CRITERIA: dict[str, object] = {
    "verdicts": [PASS_VERDICT, FAIL_VERDICT, ECONOMIC_BLOCKED_VERDICT],
    "blocked_on": [
        "upstream receipt or input identity failure",
        "C|C or X|X identity mismatch",
        "observation, scanner, timestamp, execution, accounting, cost, reproducibility, or isolation failure",
        "missing required result, incomplete scenario, or incomplete period",
    ],
    "fail_when": "integrity passes but one or more economic criteria fails",
    "pass_criteria": [
        "BASELINE_1X FULL M3 CAGR minus V3 CAGR >= +0.50 percentage points",
        "BASELINE_1X RECENT M3 CAGR minus V3 CAGR >= +1.00 percentage points",
        "BASELINE_1X active total return positive in FULL and RECENT",
        "FULL and RECENT Sharpe difference >= -0.05",
        "FULL maximum-drawdown difference <= +5.00 percentage points",
        "RECENT maximum-drawdown difference <= +3.00 percentage points",
        "FULL and RECENT turnover ratio <= 1.50",
        "STRESS_2X active total return positive in FULL and RECENT",
        "BASELINE_1X active total return positive in at least three of four robustness periods",
        "immediate repeat and C-then-X/X-then-C results reproduce exactly",
        "all integrity and no-lookahead invariants pass",
    ],
    "stress_diagnostics": ["STRESS_3X", "STRESS_5X"],
    "thresholds_frozen_before_results": True,
}

FEED_TRANSFERABILITY_BOUNDARY: dict[str, object] = {
    "historical_feed": "sip",
    "paper_live_stream_feed": "iex",
    "feed_match": False,
    "economic_pass_scope": "M3 historical economic research PASS only",
    "not_established": ["PAPER readiness", "LIVE readiness", "promotion readiness"],
    "next_required_technical_gate": "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE",
    "human_promotion_only": True,
}

RAW_RESULT_CONTRACT: dict[str, object] = {
    "ignored_output_directory": RAW_OUTPUT_DIR,
    "future_artifacts": [
        "manifest snapshot",
        "input identity receipt",
        "control and candidate observation identity validation",
        "per-window/per-cost equity curves",
        "daily returns",
        "weekly active returns",
        "fills",
        "cost ledger",
        "exposure series",
        "robustness-period results",
        "bootstrap result",
        "acceptance-criteria evaluation",
        "reproducibility hashes",
        "raw mission digest",
    ],
    "tracked_artifacts_policy": "only compact result receipts and reports may later be tracked",
}

MANDATORY_PREDECLARATION_GATES: tuple[str, ...] = (
    "starting_head",
    "clean_worktree",
    "upstream_r5_receipt_identity",
    "upstream_r5_verdict",
    "upstream_ocp_r2_receipt_identity",
    "upstream_ocp_r2_verdict",
    "upstream_ocp_r2_ci_binding",
    "old_unversioned_ocp_rejected",
    "data_identity_contract",
    "observation_identity_contract",
    "control_strategy_identity",
    "candidate_strategy_identity",
    "same_run_control_contract",
    "C_C_boundary_contract",
    "X_X_boundary_contract",
    "X_C_economic_use_forbidden",
    "reference_role_contract",
    "capacity_contract",
    "evaluation_window_contract",
    "warmup_metric_separation",
    "execution_timing_contract",
    "terminal_policy_contract",
    "corporate_action_contract",
    "baseline_cost_contract",
    "stress_cost_contract",
    "metric_contract",
    "bootstrap_contract",
    "acceptance_criteria_frozen",
    "no_optimization_contract",
    "feed_transferability_boundary",
    "execution_authorization_required",
    "default_no_execution",
    "synthetic_pass_fixture",
    "synthetic_fail_fixture",
    "synthetic_blocked_fixture",
    "no_real_economic_execution",
    "production_isolation",
    "receipt_forbidden_real_result_fields",
    "receipt_self_digest",
)

CI_PYTHON_MATRIX = {"3.12": "PASS", "3.13": "PASS", "3.14": "PASS"}
CI_REQUIRED_STAGES = {
    "ruff_lint": "PASS",
    "ruff_format": "PASS",
    "mypy": "PASS",
    "focused_ocp_tests": "PASS",
    "focused_r2_attestation_tests": "PASS",
    "focused_m3_data_gate_tests": "PASS",
    "focused_acquisition_pit_tests": "PASS",
    "focused_economic_predeclaration_tests": "PASS",
    "full_pytest": "PASS",
    "production_isolation": "PASS",
    "build": "PASS",
    "wheel_smoke": "PASS",
    "dependency_audit": "PASS",
    "secret_scan": "PASS",
}

FORBIDDEN_RESULT_KEYS: frozenset[str] = frozenset(
    {
        "return",
        "net_return",
        "gross_return",
        "pnl",
        "cagr",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown",
        "turnover",
        "trade_count",
        "fill_count",
        "portfolio_targets",
        "economic_candidate_scores",
        "economic_candidate_ranking",
    }
)

NO_EXECUTION_EXIT = 3
BLOCKED_EXIT = 20
PENDING_C25_SELF_COMMIT = "PENDING_C25_SELF_COMMIT"
PENDING_C26_SELF_COMMIT = "PENDING_C26_SELF_COMMIT"
PENDING_D26_SELF_COMMIT = "PENDING_D26_SELF_COMMIT"
EXECUTION_RESULT_SCHEMA = "m3-economic-execution-result-v1"
EXECUTION_RESULT_FILENAME = "mission_result.json"
PREDECL_IMPLEMENTATION_COMMIT = "db1ce1c5abc316a75d5972fb9138ffd77da1ca6a"
PREDECL_MANIFEST_COMMIT = "7f0bd86cce6d9ccbadef5bbc1e49e6bb9c18d052"
PREDECL_RESULT_COMMIT = "a005dcd5ae3afd1ed90985836a9b61fc43d19d4b"
PREDECL_CI_RUN = "33711180326"
PREDECL_CI_HEAD = PREDECL_RESULT_COMMIT

# The execution mission has a separate contract from the already-closed
# predeclaration.  Keeping these values separate prevents a result receipt or
# an execution commit from being mistaken for the earlier declaration chain.
PREDECL_D25_COMMIT = "960f0eaa42e300b95d1de2e8d3cd34626fb6d2a7"
PREDECL_E25_COMMIT = STARTING_HEAD
PREDECL_RECEIPT_DIGEST = "a1e05cee72de803adf78b4eb8c51abefcbf85651a445d4d96c9c3e805cbd226f"
PREDECL_RECEIPT_RAW_SHA256 = "d631b387ad581b5386514e60db3950e838ec3910b9c2a00200c21c6484bbc6fb"

EXECUTION_CI_PYTHON_MATRIX = {"3.12": "PASS", "3.13": "PASS", "3.14": "PASS"}
EXECUTION_CI_REQUIRED_STAGES = {
    "ruff_lint": "PASS",
    "ruff_format": "PASS",
    "mypy": "PASS",
    "focused_ocp_tests": "PASS",
    "focused_r2_attestation_tests": "PASS",
    "focused_m3_data_gate_tests": "PASS",
    "focused_acquisition_pit_tests": "PASS",
    "focused_economic_predeclaration_tests": "PASS",
    "focused_economic_execution_tests": "PASS",
    "full_pytest": "PASS",
    "production_isolation": "PASS",
    "build": "PASS",
    "wheel_smoke": "PASS",
    "dependency_audit": "PASS",
    "secret_scan": "PASS",
}

NON_TRADABLE_REFERENCES = frozenset({"SPY", "IWM", "QQQ"})
TRADABLE_RESERVED_INVERSE_HEDGES = frozenset({"SH", "PSQ", "DOG"})
FULL_PIN_ORDER = ("SPY", "IWM", "QQQ", "SH", "PSQ", "DOG")
PASS_ORDERS: dict[str, tuple[str, str]] = {
    "A": ("C", "X"),
    "B": ("C", "X"),
    "C": ("X", "C"),
}

ACCEPTED_OCP_RAW_SHA256 = {
    "control_C": "2bdedc4f495960bac839c98e5fdd54c12d7935c8070684645619361cf7131509",
    "candidate_X": "1b67283e9d0d53bac6be22cb880ba1180ad1ba438995f29ab18a8a27d09435f2",
}

EXECUTION_ADDENDUM: dict[str, object] = {
    "active_wealth": "product of (1+X_daily)/(1+C_daily) over paired dates",
    "active_drawdown": "drawdown of active wealth, with initial wealth anchor 1.0",
    "duplicate_terminal_timestamps": "retain the last post-liquidation value",
    "initial_cash_anchor": "anchor initial cash immediately before the first metric session",
    "robustness_and_year_slices": "slice the BASELINE FULL paired curves without independent resets",
    "ratio_zero_denominator": {
        "zero_over_zero": 1.0,
        "positive_over_zero": "positive infinity; corresponding acceptance criterion fails",
        "negative_denominator": "BLOCKED",
        "non_finite": "BLOCKED",
    },
    "bootstrap": {
        "generator": "numpy.random.Generator(numpy.random.PCG64(20260902))",
        "method": "moving-block",
        "block_length_weeks": 4,
        "resamples": 10_000,
        "percentile": "numpy.percentile linear method",
    },
}

EXECUTION_ACCEPTED_INPUTS: dict[str, object] = {
    "canonical_bars": {
        "path": CANONICAL_BARS_PATH,
        "raw_sha256": DATA_IDENTITIES["canonical_equity_v5"]["raw_sha256"],  # type: ignore[index]
    },
    "lifetime": {"path": LIFETIME_PATH, "raw_sha256": DATA_IDENTITIES["lifetime"]["raw_sha256"]},  # type: ignore[index]
    "corporate_actions": {
        "path": CORPORATE_ACTIONS_PATH,
        "raw_sha256": DATA_IDENTITIES["corporate_actions"]["raw_sha256"],  # type: ignore[index]
    },
    "iwm_raw_bundle": {"path": IWM_BUNDLE_PATH, "raw_sha256": DATA_IDENTITIES["iwm_raw_bundle"]["raw_sha256"]},  # type: ignore[index]
    "iwm_source_receipt": {
        "path": f"{IWM_BUNDLE_PATH}/source_receipt.json",
        "raw_sha256": DATA_IDENTITIES["iwm_source_receipt"]["raw_sha256"],  # type: ignore[index]
    },
    "iwm_normalized_sidecar": {
        "path": IWM_SIDECAR_PATH,
        "raw_sha256": DATA_IDENTITIES["iwm_normalized_sidecar"]["raw_sha256"],  # type: ignore[index]
        "semantic_row_sha256": DATA_IDENTITIES["iwm_normalized_sidecar"]["semantic_row_sha256"],  # type: ignore[index]
    },
    "r5_pit_projection": {
        "path": R5_PIT_PROJECTION_LABEL,
        "raw_sha256": DATA_IDENTITIES["r5_evaluation"]["pit_raw_sha256"],  # type: ignore[index]
    },
    "ocp_control_C": {"path": OCP_CONTROL_PATH, "raw_sha256": ACCEPTED_OCP_RAW_SHA256["control_C"]},
    "ocp_candidate_X": {"path": OCP_CANDIDATE_PATH, "raw_sha256": ACCEPTED_OCP_RAW_SHA256["candidate_X"]},
}

PREDECLARATION_CLOSURE: dict[str, object] = {
    "starting_head": E24_COMMIT,
    "implementation_commit": PREDECL_IMPLEMENTATION_COMMIT,
    "manifest_commit": PREDECL_MANIFEST_COMMIT,
    "result_commit": PREDECL_RESULT_COMMIT,
    "closure_commit": PREDECL_D25_COMMIT,
    "final_commit": PREDECL_E25_COMMIT,
    "receipt_path": RECEIPT_PATH,
    "receipt_self_digest_sha256": PREDECL_RECEIPT_DIGEST,
    "receipt_raw_sha256": PREDECL_RECEIPT_RAW_SHA256,
    "remote_ci_run": PREDECL_CI_RUN,
    "remote_ci_head": PREDECL_CI_HEAD,
}


class ContractError(ValueError):
    """A fail-closed contract or provenance error."""


class NoExecutionRequested(ContractError):
    """The default invocation intentionally did not execute economics."""


def _fail(message: str) -> NoReturn:
    raise ContractError(message)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_head() -> str:
    return _git("rev-parse", "HEAD")


def git_status() -> list[str]:
    return _git("status", "--short", "--untracked-files=all").splitlines()


def git_parents(commit: str) -> list[str]:
    fields = _git("rev-list", "--parents", "-n", "1", commit).split()
    return fields[1:]


def changed_paths(parent: str, commit: str) -> list[str]:
    return _git("diff-tree", "--no-commit-id", "--name-only", "-r", parent, commit).splitlines()


def _is_ancestor(ancestor: str, commit: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, commit],
        cwd=REPO,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def tracked_blob(commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{path}"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout


def tracked_blob_identity(commit: str, path: str) -> dict[str, str]:
    blob = tracked_blob(commit, path)
    return {
        "commit": commit,
        "path": path,
        "git_blob_id": _git("rev-parse", f"{commit}:{path}"),
        "raw_sha256": hashlib.sha256(blob).hexdigest(),
    }


def _json_blob(commit: str, path: str) -> dict[str, object]:
    try:
        value = json.loads(tracked_blob(commit, path))
    except (UnicodeDecodeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise ContractError(f"tracked JSON artifact is invalid: {commit}:{path}") from exc
    if not isinstance(value, dict):
        _fail(f"tracked JSON artifact must be an object: {commit}:{path}")
    return value


def _yaml_blob(commit: str, path: str) -> dict[str, object]:
    try:
        value = yaml.safe_load(tracked_blob(commit, path))
    except (UnicodeDecodeError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        raise ContractError(f"tracked YAML artifact is invalid: {commit}:{path}") from exc
    if not isinstance(value, dict):
        _fail(f"tracked YAML artifact must be an object: {commit}:{path}")
    return value


def _nested(mapping: Mapping[str, object], *keys: str) -> object:
    value: object = mapping
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            _fail("missing configuration field: " + ".".join(keys))
        value = value[key]
    return value


def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        _fail(f"{field} mismatch: expected {expected!r}, got {actual!r}")


def _forbidden_keys(value: object) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).lower() in FORBIDDEN_RESULT_KEYS:
                    found.add(str(key))
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def validate_baseline_configuration(config: Mapping[str, object]) -> dict[str, object]:
    """Verify frozen values against the tracked settings YAML representation."""

    expected_paths: dict[tuple[str, ...], object] = {
        ("strategy", "equity", "maximum_allocation"): 0.55,
        ("strategy", "equity", "parameters"): FROZEN_PARAMETERS,
        ("risk", "max_position_pct"): 0.10,
        ("risk", "max_positions"): 6,
        ("risk", "cash_buffer_pct"): 0.40,
        ("risk", "max_gross_exposure_pct"): 0.60,
        ("risk", "daily_loss_limit_pct"): 0.005,
        ("risk", "max_drawdown_pct"): 0.05,
        ("backtest", "initial_cash"): 100_000,
        ("backtest", "slippage_bps"): 2,
        ("backtest", "spread_bps"): 2,
        ("backtest", "commission"): 0,
        ("backtest", "equity_use_effective_dated_fees"): True,
        ("backtest", "equity_taf_per_share"): 0.000195,
        ("backtest", "equity_taf_cap"): 9.79,
        ("backtest", "equity_cat_per_share"): 0.000003,
        ("backtest", "equity_sec_fee_rate"): 0.0000206,
        ("execution", "rebalance_band_pct"): 0.005,
        ("execution", "minimum_order_notional"): 10,
        ("universe", "equity", "maximum_stream_symbols"): 30,
        ("universe", "equity", "reference_symbols"): ["SPY"],
        ("universe", "equity", "inverse_trade_symbols"): ["SH", "PSQ", "DOG"],
        ("alpaca", "stock_historical_feed"): "sip",
        ("alpaca", "stock_stream_feed"): "iex",
    }
    for path, expected in expected_paths.items():
        _require_equal(_nested(config, *path), expected, ".".join(path))
    if (
        canonical_digest(
            {key: config.get(key) for key in ("data", "universe", "strategy", "risk", "backtest", "execution")}
        )
        != "c8ca8e7f0b8059fdf85aa874c905ad4294ae235053e66c68e132341efbd0195d"
    ):
        _fail("config semantic digest does not match the accepted R5 configuration")
    return copy.deepcopy(BASELINE_CONFIGURATION)


def _r5_identity_projection(receipt: Mapping[str, object], manifest: Mapping[str, object]) -> dict[str, object]:
    accepted = manifest.get("accepted_r4_inputs")
    if not isinstance(accepted, Mapping):
        _fail("R5 manifest accepted_r4_inputs is missing")
    r4_inputs = receipt.get("r4_immutable_inputs")
    if not isinstance(r4_inputs, Mapping):
        _fail("R5 receipt immutable input section is missing")
    sidecar = r4_inputs.get("normalized_sidecar")
    composite = r4_inputs.get("composite")
    raw_bundle = r4_inputs.get("raw_bundle")
    source_receipt = r4_inputs.get("source_receipt")
    if not all(isinstance(item, Mapping) for item in (sidecar, composite, raw_bundle, source_receipt)):
        _fail("R5 receipt immutable input identities are incomplete")
    projection = receipt.get("terminal_snapshot_decision")
    pit = receipt.get("pit_population")
    numerical = receipt.get("numerical_feasibility")
    if not isinstance(projection, Mapping) or not isinstance(pit, Mapping) or not isinstance(numerical, Mapping):
        _fail("R5 evaluation identity sections are incomplete")
    r5_pit_manifest = _json_blob(R5_RESULT_COMMIT, R5_PIT_MANIFEST_PATH)
    _require_equal(
        r5_pit_manifest.get("mission"),
        "equity_m3_pit_scanner_population_2026_09_r5",
        "R5 PIT manifest mission",
    )
    _require_equal(r5_pit_manifest.get("schema_version"), "1.0.0", "R5 PIT manifest schema")
    result = {
        "canonical_equity_v5": {
            "raw_sha256": composite.get("canonical_parent_raw_sha256"),
            "crlf_normalized_sha256": composite.get("canonical_parent_normalized_sha256"),
            "manifest_git_blob": composite.get("canonical_manifest_git_blob"),
        },
        "lifetime": {"raw_sha256": accepted.get("lifetime_raw_sha256")},
        "corporate_actions": {"raw_sha256": accepted.get("corporate_actions_raw_sha256")},
        "iwm_raw_bundle": {"raw_sha256": raw_bundle.get("raw_bundle_sha256")},
        "iwm_source_receipt": {"raw_sha256": source_receipt.get("raw_sha256")},
        "iwm_normalized_sidecar": {
            "raw_sha256": sidecar.get("raw_sha256"),
            "semantic_row_sha256": sidecar.get("normalized_semantic_row_sha256"),
        },
        "composite_semantic": {"sha256": composite.get("semantic_digest_sha256")},
        "r5_evaluation": {
            "pit_raw_sha256": r5_pit_manifest.get("raw_pit_sha256"),
            "projection_digest_sha256": projection.get("projection_digest_sha256"),
            "candidate_universe_digest_sha256": pit.get("candidate_universe_digest_sha256"),
            "candidate_decision_eligibility_digest_sha256": pit.get("candidate_decision_eligibility_digest_sha256"),
            "matrix_merkle_root": numerical.get("matrix_digest_merkle_root"),
        },
    }
    return result


def validate_r5_receipt_payload(
    receipt: Mapping[str, object],
    manifest: Mapping[str, object],
    *,
    expected_receipt_digest: str = R5_RECEIPT_DIGEST,
) -> dict[str, object]:
    """Validate the accepted R5 receipt using tracked Git blobs only."""

    recorded = receipt.get("self_digest_sha256")
    body = dict(receipt)
    body.pop("self_digest_sha256", None)
    _require_equal(recorded, expected_receipt_digest, "R5 receipt self-digest field")
    _require_equal(canonical_digest(body), expected_receipt_digest, "R5 receipt self-digest")
    required = {
        "schema_version": "5.0.0",
        "mission": "equity_m3_data_gate_2026_09_r5",
        "data_gate_verdict": "M3_DATA_GATE_PASS",
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_ready": False,
        "blocked_reasons": [],
        "forbidden_economic_fields": [],
    }
    for field, expected in required.items():
        _require_equal(receipt.get(field), expected, f"R5 receipt {field}")
    gates = receipt.get("mandatory_gate_results")
    if not isinstance(gates, Mapping) or len(gates) != 38:
        _fail("R5 receipt must contain exactly 38 mandatory gates")
    if any(not isinstance(value, Mapping) or value.get("status") != "PASS" for value in gates.values()):
        _fail("R5 receipt mandatory gates are not all PASS")
    _require_equal(receipt.get("run_commit"), R5_MANIFEST_COMMIT, "R5 receipt manifest run commit")
    proof = receipt.get("manifest_only_proof")
    if not isinstance(proof, Mapping):
        _fail("R5 manifest-only proof is missing")
    _require_equal(proof.get("manifest_path"), R5_MANIFEST_PATH, "R5 manifest path")
    _require_equal(proof.get("manifest_commit"), R5_MANIFEST_COMMIT, "R5 manifest commit")
    _require_equal(_r5_identity_projection(receipt, manifest), DATA_IDENTITIES, "R5 data identities")
    pit = receipt.get("pit_population")
    inventory = receipt.get("reference_inventory")
    if not isinstance(pit, Mapping) or not isinstance(inventory, Mapping):
        _fail("R5 PIT/reference evidence is missing")
    _require_equal(pit.get("snapshot_count"), 2658, "R5 snapshot count")
    _require_equal(pit.get("unique_candidate_count"), 889, "R5 unique candidate count")
    for symbol in ("SPY", "IWM", "QQQ"):
        row = inventory.get(symbol)
        if not isinstance(row, Mapping):
            _fail(f"R5 reference inventory is missing {symbol}")
        _require_equal(row.get("normalized_row_count"), 2658, f"R5 {symbol} row count")
        _require_equal(row.get("missing_symbol_count"), 0, f"R5 {symbol} missing symbol count")
    return {
        "receipt_path": R5_RECEIPT_PATH,
        "manifest_path": R5_MANIFEST_PATH,
        "result_commit": R5_RESULT_COMMIT,
        "self_digest_sha256": expected_receipt_digest,
        "verdict": "M3_DATA_GATE_PASS",
        "mandatory_gate_count": 38,
        "mandatory_gate_status": "PASS",
        "data_identities": copy.deepcopy(DATA_IDENTITIES),
    }


def _validate_r2_payload(payload: Mapping[str, object]) -> None:
    """Validate the R2 receipt without reading the ignored C23 raw output."""

    from compact_equity_m3_candidate_ocp_attestation_r2 import (  # noqa: PLC0415
        _validate_receipt_common,
        _validate_substantive_receipt_claim,
    )

    _validate_receipt_common(payload)
    _validate_substantive_receipt_claim(payload)


def old_unversioned_ocp_rejected() -> bool:
    """Prove current consumer routing cannot select the historical receipt."""

    source = tracked_blob(git_head(), "scripts/research_equity_m3_m4.py").decode("utf-8")
    if "M3_CANDIDATE_OCP_RECEIPT = REPO / R2_RECEIPT_PATH" not in source:
        return False
    if OLD_OCP_RECEIPT_PATH in source.split("M3_CANDIDATE_OCP_RECEIPT =", 1)[-1].split("\n", 1)[0]:
        return False
    old = _json_blob(git_head(), OLD_OCP_RECEIPT_PATH)
    implementation = old.get("implementation_identity")
    manifest = old.get("manifest_identity")
    remote_ci = old.get("remote_ci")
    return (
        isinstance(implementation, Mapping)
        and implementation.get("commit") != R2_IMPLEMENTATION_COMMIT
        and isinstance(manifest, Mapping)
        and manifest.get("commit") != R2_MANIFEST_COMMIT
        and isinstance(remote_ci, Mapping)
        and remote_ci.get("head_sha") is None
    )


def validate_upstream() -> dict[str, object]:
    """Validate R5/R2 prerequisites and the immutable old-receipt boundary."""

    current = git_head()
    r5_receipt = _json_blob(current, R5_RECEIPT_PATH)
    r5_manifest = _json_blob(current, R5_MANIFEST_PATH)
    r5_result_receipt = _json_blob(R5_RESULT_COMMIT, R5_RECEIPT_PATH)
    _require_equal(r5_receipt, r5_result_receipt, "current R5 receipt bytes")
    _require_equal(
        tracked_blob(current, R5_MANIFEST_PATH),
        tracked_blob(R5_MANIFEST_COMMIT, R5_MANIFEST_PATH),
        "current R5 manifest bytes",
    )
    r5 = validate_r5_receipt_payload(r5_receipt, r5_manifest)

    r2_receipt = _json_blob(current, R2_RECEIPT_PATH)
    _require_equal(
        hashlib.sha256(tracked_blob(current, R2_RECEIPT_PATH)).hexdigest(),
        hashlib.sha256(tracked_blob(E24_COMMIT, R2_RECEIPT_PATH)).hexdigest(),
        "R2 receipt immutable bytes",
    )
    try:
        validate_r2_receipt(r2_receipt, receipt_path=REPO / R2_RECEIPT_PATH)
    except (AttestationError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ContractError("accepted R2 candidate-OCP receipt failed validation") from exc
    _require_equal(r2_receipt.get("self_digest_sha256"), R2_RECEIPT_DIGEST, "R2 receipt self-digest")
    _require_equal(r2_receipt.get("verdict"), "M3_CANDIDATE_OCP_ATTESTATION_R2_PASS", "R2 receipt verdict")
    _require_equal(r2_receipt.get("blocked_reasons"), [], "R2 blocked reasons")
    _require_equal(r2_receipt.get("candidate_economics_run"), False, "R2 candidate economics")
    _require_equal(r2_receipt.get("economic_metrics_present"), False, "R2 economic metrics")
    _require_equal(r2_receipt.get("economic_mission_authorized"), False, "R2 economic authorization")
    _require_equal(
        r2_receipt.get("next_required_mission"),
        "M3_ECONOMIC_MISSION_PREDECLARATION",
        "R2 next mission",
    )
    corrective = r2_receipt.get("corrective_remote_ci")
    if not isinstance(corrective, Mapping):
        _fail("R2 corrective CI binding is missing")
    _require_equal(corrective.get("run_id"), R2_CORRECTIVE_CI_RUN, "R2 corrective CI run")
    _require_equal(corrective.get("head_sha"), R2_CORRECTIVE_CI_HEAD, "R2 corrective CI head")
    _require_equal(corrective.get("status"), "PASS", "R2 corrective CI status")
    _require_equal(corrective.get("conclusion"), "success", "R2 corrective CI conclusion")
    _require_equal(corrective.get("python_matrix"), CI_PYTHON_MATRIX, "R2 corrective Python matrix")
    if any(value != "PASS" for value in corrective.get("required_stages", {}).values()):
        _fail("R2 corrective CI has a non-PASS required stage")
    if not old_unversioned_ocp_rejected():
        _fail("old unversioned OCP receipt is not rejected by current routing")
    return {
        "data_gate": r5,
        "ocp_r2": {
            "receipt_path": R2_RECEIPT_PATH,
            "self_digest_sha256": R2_RECEIPT_DIGEST,
            "verdict": "M3_CANDIDATE_OCP_ATTESTATION_R2_PASS",
            "blocked_reasons": [],
            "corrective_ci_run": R2_CORRECTIVE_CI_RUN,
            "corrective_ci_head": R2_CORRECTIVE_CI_HEAD,
            "candidate_economics_run": False,
            "economic_metrics_present": False,
            "economic_mission_authorized": False,
            "next_required_mission": "M3_ECONOMIC_MISSION_PREDECLARATION",
        },
        "old_unversioned_ocp_rejected": True,
    }


def _validate_implementation_commit(implementation_commit: str) -> None:
    parents = git_parents(implementation_commit)
    if parents != [PREDECL_STARTING_HEAD]:
        _fail("A25 implementation must be a direct child of E24")
    if sorted(changed_paths(PREDECL_STARTING_HEAD, implementation_commit)) != sorted(IMPLEMENTATION_PATHS):
        _fail("A25 implementation changed paths do not match the declared scope")


def _source_identities(implementation_commit: str) -> dict[str, dict[str, str]]:
    paths = (
        *IMPLEMENTATION_PATHS,
        "config/settings.yaml",
        "scripts/research_equity_m3_m4.py",
        "scripts/research_equity_m3_candidate_ocp.py",
        "scripts/compact_equity_m3_candidate_ocp_attestation_r2.py",
        "scripts/research_equity_m3_data_gate_r5.py",
        "scripts/research_v3_equity_roadmap.py",
        "data/research_manifests/equity_m3_data_gate_2026_09_r5.json",
        "data/research_manifests/equity_m3_candidate_ocp_remote_attestation_2026_09_r2.json",
        "data/research_manifests/equity_m3_pit_population_2026_09_r5.json",
        "data/research_receipts/equity_m3_data_gate_2026_09_r5.json",
        "data/research_receipts/equity_m3_candidate_ocp_2026_09_r2.json",
        "docs/equity_m3_data_gate_2026_09_r5.md",
        "docs/equity_m3_candidate_ocp_2026_09_r2.md",
        "docs/equity_v3_alpha_roadmap_2026_08.md",
        "docs/equity_v3_current_parity_control_2026_08.md",
        "docs/equity_v3_alpha_execution_2026_08.md",
        "src/parayabuck/backtest/product.py",
        "src/parayabuck/backtest/portfolio_engine.py",
        "src/parayabuck/backtest/terminal_aware_engine.py",
        "src/parayabuck/backtest/terminal_policy.py",
        "src/parayabuck/backtest/universe.py",
        "src/parayabuck/backtest/regulatory_fees.py",
        "src/parayabuck/universe/observation_set.py",
        "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
        "src/parayabuck/execution/portfolio.py",
    )
    return {path: tracked_blob_identity(implementation_commit, path) for path in paths}


def _validate_strategy_contract(config: Mapping[str, object]) -> None:
    _require_equal(_nested(config, "strategy", "equity", "name"), "equity_residual_momentum_v3", "control strategy")
    _require_equal(
        _nested(config, "strategy", "equity", "parameters"),
        FROZEN_PARAMETERS,
        "shared frozen strategy parameters",
    )
    if (
        EquityResidualMomentum1dV3.name != "equity_residual_momentum_v3"
        or EquityResidualMomentum1dV3.version != "3.0.0"
    ):
        _fail("frozen control strategy implementation identity drifted")
    if (
        ProxyMultifactorResidualMomentum.name != "equity_proxy_multifactor_residual_momentum_v1"
        or ProxyMultifactorResidualMomentum.version != "1.0.0"
    ):
        _fail("frozen candidate strategy implementation identity drifted")


def validate_pair_contract(control: Mapping[str, object], candidate: Mapping[str, object]) -> None:
    """Validate same-run inputs while explicitly forbidding X|C economics."""

    _require_equal(control.get("observation_boundary"), "C|C", "control observation boundary")
    _require_equal(candidate.get("observation_boundary"), "X|X", "candidate observation boundary")
    for field in (
        "scanner_pool_digest",
        "scanner_timestamp_digest",
        "terminal_map_digest",
        "corporate_action_digest",
        "cost_model",
        "initial_cash",
        "evaluation_sessions",
    ):
        _require_equal(candidate.get(field), control.get(field), f"same-run {field}")
    if candidate.get("diagnostic_boundary") == "X|C" and candidate.get("used_for_economics") is True:
        _fail("X|C is diagnostic only and cannot be used economically")


def validate_reference_contract(
    candidate_membership: Sequence[str],
    target_symbols: Sequence[str],
    executable_symbols: Sequence[str],
) -> None:
    # SPY/IWM/QQQ are observation-only factors.  SH/PSQ/DOG are reserved
    # inverse-ETF pins, but remain tradable candidate symbols when the frozen
    # scanner and strategy independently rank/gate them into the portfolio.
    references = {str(symbol).strip().upper() for symbol in REFERENCE_CONTRACT["observation_only_references"]}
    for label, values in (
        ("candidate membership", candidate_membership),
        ("target symbols", target_symbols),
        ("executable symbols", executable_symbols),
    ):
        overlap = references.intersection(str(symbol).strip().upper() for symbol in values)
        if overlap:
            _fail(f"reference symbols entered {label}: {sorted(overlap)}")
    _require_equal(len(REFERENCE_CONTRACT["full_pin_order"]), 6, "reserved pin count")
    _require_equal(REFERENCE_CONTRACT["maximum_stream_symbols"], 30, "maximum stream capacity")
    _require_equal(REFERENCE_CONTRACT["effective_candidate_observation_capacity"], 24, "candidate capacity")


def validate_fill_timing(
    order_timestamp: datetime,
    fill_timestamp: datetime | None,
    window_end: datetime,
    *,
    next_execution_bar_exists: bool,
) -> None:
    """Reject same-session/post-window fills and silent terminal fills."""

    if order_timestamp.tzinfo is None or window_end.tzinfo is None:
        _fail("timing timestamps must be timezone-aware")
    if fill_timestamp is not None:
        if fill_timestamp <= order_timestamp:
            _fail("same-bar fill is forbidden")
        if fill_timestamp > window_end:
            _fail("post-window fill is forbidden")
        if not next_execution_bar_exists:
            _fail("fill exists without a valid next execution bar")
    elif next_execution_bar_exists:
        _fail("missing fill for an order with a valid next execution bar")


def validate_event_sequence(events: Sequence[Mapping[str, object]], window_end: datetime) -> None:
    orders: dict[str, tuple[datetime, bool]] = {}
    for event in events:
        kind = str(event.get("kind"))
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, datetime):
            _fail("synthetic event timestamps must be datetime values")
        if kind == "order":
            order_id = str(event.get("order_id"))
            orders[order_id] = (timestamp, bool(event.get("next_execution_bar_exists", True)))
        elif kind == "fill":
            order_id = str(event.get("order_id"))
            if order_id not in orders:
                _fail("fill has no prior order")
            order_timestamp, next_exists = orders[order_id]
            validate_fill_timing(
                order_timestamp,
                timestamp,
                window_end,
                next_execution_bar_exists=next_exists,
            )


def _finite(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        _fail(f"synthetic metric {field} is missing or non-finite")
    return float(value)


def _metric(fixture: Mapping[str, object], window: str, side: str, field: str) -> float:
    windows = fixture.get("windows")
    if not isinstance(windows, Mapping):
        _fail("synthetic fixture windows are missing")
    row = windows.get(window)
    if not isinstance(row, Mapping) or not isinstance(row.get(side), Mapping):
        _fail(f"synthetic fixture {window}/{side} metrics are missing")
    return _finite(row[side].get(field), f"{window}/{side}/{field}")  # type: ignore[index]


def _active(fixture: Mapping[str, object], window: str, field: str) -> float:
    windows = fixture.get("windows")
    row = windows.get(window) if isinstance(windows, Mapping) else None
    if not isinstance(row, Mapping) or not isinstance(row.get("active"), Mapping):
        _fail(f"synthetic fixture {window}/active metrics are missing")
    return _finite(row["active"].get(field), f"{window}/active/{field}")  # type: ignore[index]


def evaluate_synthetic_economic_fixture(fixture: Mapping[str, object]) -> dict[str, object]:
    """Evaluate synthetic economic-shaped values against frozen criteria only."""

    integrity = fixture.get("integrity")
    if not isinstance(integrity, Mapping) or any(value is not True for value in integrity.values()):
        return {"verdict": ECONOMIC_BLOCKED_VERDICT, "criteria": {}, "blocked_reason": "integrity failure"}
    checks = {
        "full_cagr_gap": _metric(fixture, "FULL", "candidate", "cagr") - _metric(fixture, "FULL", "control", "cagr")
        >= 0.005,
        "recent_cagr_gap": _metric(fixture, "RECENT", "candidate", "cagr")
        - _metric(fixture, "RECENT", "control", "cagr")
        >= 0.010,
        "full_active_total_return_positive": _active(fixture, "FULL", "active_total_return") > 0,
        "recent_active_total_return_positive": _active(fixture, "RECENT", "active_total_return") > 0,
        "full_sharpe_non_degradation": _metric(fixture, "FULL", "candidate", "sharpe")
        - _metric(fixture, "FULL", "control", "sharpe")
        >= -0.05,
        "recent_sharpe_non_degradation": _metric(fixture, "RECENT", "candidate", "sharpe")
        - _metric(fixture, "RECENT", "control", "sharpe")
        >= -0.05,
        "full_drawdown_limit": _metric(fixture, "FULL", "candidate", "max_drawdown")
        - _metric(fixture, "FULL", "control", "max_drawdown")
        <= 0.05,
        "recent_drawdown_limit": _metric(fixture, "RECENT", "candidate", "max_drawdown")
        - _metric(fixture, "RECENT", "control", "max_drawdown")
        <= 0.03,
        "full_turnover_limit": _metric(fixture, "FULL", "candidate", "turnover")
        / _metric(fixture, "FULL", "control", "turnover")
        <= 1.50,
        "recent_turnover_limit": _metric(fixture, "RECENT", "candidate", "turnover")
        / _metric(fixture, "RECENT", "control", "turnover")
        <= 1.50,
        "full_stress_2x_positive": _active(fixture, "FULL", "stress_2x_active_total_return") > 0,
        "recent_stress_2x_positive": _active(fixture, "RECENT", "stress_2x_active_total_return") > 0,
        "robustness_periods": sum(
            value > 0
            for value in fixture.get("robustness_active_returns", [])
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        >= 3,
        "immediate_repeat_exact": integrity.get("immediate_repeat_exact") is True,
        "order_independent_exact": integrity.get("order_independent_exact") is True,
    }
    return {
        "verdict": PASS_VERDICT if all(checks.values()) else FAIL_VERDICT,
        "criteria": checks,
        "blocked_reason": None,
    }


def _synthetic_fixture(kind: Literal["pass", "fail", "blocked"]) -> dict[str, object]:
    fixture: dict[str, object] = {
        "windows": {
            "FULL": {
                "control": {"cagr": 0.080, "sharpe": 0.80, "max_drawdown": 0.15, "turnover": 1.00},
                "candidate": {"cagr": 0.095, "sharpe": 0.79, "max_drawdown": 0.16, "turnover": 1.10},
                "active": {"active_total_return": 0.25, "stress_2x_active_total_return": 0.10},
            },
            "RECENT": {
                "control": {"cagr": 0.070, "sharpe": 0.60, "max_drawdown": 0.18, "turnover": 1.00},
                "candidate": {"cagr": 0.090, "sharpe": 0.58, "max_drawdown": 0.20, "turnover": 1.20},
                "active": {"active_total_return": 0.18, "stress_2x_active_total_return": 0.04},
            },
        },
        "robustness_active_returns": [0.04, -0.01, 0.03, 0.02],
        "integrity": {
            "input_hashes": True,
            "execution_invariants": True,
            "accounting": True,
            "immediate_repeat_exact": True,
            "order_independent_exact": True,
        },
    }
    if kind == "fail":
        full = fixture["windows"]["FULL"]  # type: ignore[index]
        full["candidate"]["cagr"] = 0.083  # type: ignore[index]
    elif kind == "blocked":
        fixture["integrity"]["input_hashes"] = False  # type: ignore[index]
    return fixture


def run_synthetic_fixture(kind: Literal["pass", "fail", "blocked"]) -> dict[str, object]:
    expected = {"pass": PASS_VERDICT, "fail": FAIL_VERDICT, "blocked": ECONOMIC_BLOCKED_VERDICT}[kind]
    result = evaluate_synthetic_economic_fixture(_synthetic_fixture(kind))
    _require_equal(result.get("verdict"), expected, f"synthetic {kind} verdict")
    return {"fixture": kind, **result}


def _validate_source_has_no_broker_path() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = {"alpaca", "requests", "httpx", "websocket", "broker"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0].lower() for alias in node.names}
            if names.intersection(forbidden_imports):
                _fail("dedicated economic runner imports a broker/network module")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0].lower() in forbidden_imports:
                _fail("dedicated economic runner imports a broker/network module")


def _validate_predeclaration_closure(commit: str) -> dict[str, object]:
    """Validate the immutable E25 predeclaration closure before execution."""

    receipt = _json_blob(commit, RECEIPT_PATH)
    raw = tracked_blob(commit, RECEIPT_PATH)
    _require_equal(hashlib.sha256(raw).hexdigest(), PREDECL_RECEIPT_RAW_SHA256, "predeclaration receipt raw SHA")
    _require_equal(receipt.get("self_digest_sha256"), PREDECL_RECEIPT_DIGEST, "predeclaration receipt digest field")
    body = dict(receipt)
    body.pop("self_digest_sha256", None)
    _require_equal(canonical_digest(body), PREDECL_RECEIPT_DIGEST, "predeclaration receipt self-digest")
    _require_equal(receipt.get("verdict"), PREDECLARED_VERDICT, "predeclaration closure verdict")
    _require_equal(receipt.get("blocked_reasons"), [], "predeclaration closure blockers")
    _require_equal(receipt.get("economic_execution_run"), False, "predeclaration economic execution")
    _require_equal(receipt.get("economic_results_present"), False, "predeclaration economic results")
    _require_equal(receipt.get("real_execution_authorized"), False, "predeclaration authorization")
    topology = receipt.get("commit_topology")
    if not isinstance(topology, Mapping):
        _fail("predeclaration closure topology is missing")
    expected_topology = {
        "starting_head": E24_COMMIT,
        "implementation_commit": PREDECL_IMPLEMENTATION_COMMIT,
        "manifest_commit": PREDECL_MANIFEST_COMMIT,
        "local_result_commit": PREDECL_RESULT_COMMIT,
        "manifest_parent": PREDECL_IMPLEMENTATION_COMMIT,
        "result_parent": PREDECL_MANIFEST_COMMIT,
    }
    for field, expected in expected_topology.items():
        _require_equal(topology.get(field), expected, f"predeclaration topology {field}")
    remote = receipt.get("remote_ci")
    if not isinstance(remote, Mapping):
        _fail("predeclaration closure remote CI is missing")
    _require_equal(remote.get("status"), "PASS", "predeclaration remote CI status")
    _require_equal(remote.get("run_id"), PREDECL_CI_RUN, "predeclaration remote CI run")
    _require_equal(remote.get("head_sha"), PREDECL_CI_HEAD, "predeclaration remote CI head")
    _require_equal(remote.get("python_matrix"), CI_PYTHON_MATRIX, "predeclaration remote Python matrix")
    _require_equal(
        remote.get("required_stages"),
        CI_REQUIRED_STAGES,
        "predeclaration remote required stages",
    )
    return {
        "final_commit": commit,
        "receipt_path": RECEIPT_PATH,
        "receipt_self_digest_sha256": PREDECL_RECEIPT_DIGEST,
        "receipt_raw_sha256": PREDECL_RECEIPT_RAW_SHA256,
        "remote_ci_run": PREDECL_CI_RUN,
        "remote_ci_head": PREDECL_CI_HEAD,
    }


def _validate_execution_implementation_commit(implementation_commit: str) -> None:
    parents = git_parents(implementation_commit)
    if len(parents) != 1 or not _is_ancestor(STARTING_HEAD, implementation_commit):
        _fail("A26 implementation must be a linear descendant of E25")
    if sorted(_git("diff", "--name-only", STARTING_HEAD, implementation_commit).splitlines()) != sorted(
        EXECUTION_IMPLEMENTATION_PATHS
    ):
        _fail("A26 implementation changed paths do not match the declared scope")


def _execution_source_identities(implementation_commit: str) -> dict[str, dict[str, str]]:
    paths = (
        *EXECUTION_IMPLEMENTATION_PATHS,
        "config/settings.yaml",
        "scripts/research_equity_m3_m4.py",
        "scripts/compact_equity_m3_candidate_ocp_attestation_r2.py",
        "scripts/research_equity_m3_data_gate_r5.py",
        "scripts/research_v3_equity_roadmap.py",
        R5_MANIFEST_PATH,
        R5_PIT_MANIFEST_PATH,
        R5_RECEIPT_PATH,
        R2_MANIFEST_PATH,
        R2_RECEIPT_PATH,
        "docs/equity_m3_data_gate_2026_09_r5.md",
        "docs/equity_m3_candidate_ocp_2026_09_r2.md",
        "src/parayabuck/backtest/portfolio_engine.py",
        "src/parayabuck/backtest/terminal_aware_engine.py",
        "src/parayabuck/backtest/universe.py",
        "src/parayabuck/backtest/numeric_history.py",
        "src/parayabuck/backtest/regulatory_fees.py",
        "src/parayabuck/portfolio/base.py",
        "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
        "src/parayabuck/execution/portfolio.py",
    )
    return {path: tracked_blob_identity(implementation_commit, path) for path in paths}


def validate_execution_manifest_payload(
    manifest: Mapping[str, object],
    implementation_commit: str,
    *,
    manifest_commit: str | None = None,
) -> None:
    """Validate the strong, pre-result B26 authorization binding."""

    _require_equal(manifest.get("schema_version"), "1.0.0", "execution manifest schema")
    _require_equal(manifest.get("mission"), EXECUTION_MISSION, "execution manifest mission")
    _require_equal(
        manifest.get("repository_url"), "https://github.com/batuhankiskac/ParayaBuck", "execution repository"
    )
    _require_equal(manifest.get("branch"), "main", "execution branch")
    for field, expected in {
        "declared_before_results": True,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "real_execution_authorized": True,
        "human_approval": True,
        "operator_approval": True,
        "economic_execution_run": False,
        "economic_results_present": False,
        "no_network_market_data": True,
        "no_credential_access": True,
        "no_ocp_replay": True,
        "no_m3_data_gate_replay": True,
        "no_iwm_refetch": True,
        "no_optimization": True,
        "no_promotion": True,
        "no_paper_live_change": True,
    }.items():
        _require_equal(manifest.get(field), expected, f"execution manifest {field}")
    _require_equal(manifest.get("operator_id"), "human_operator", "execution operator")
    _require_equal(
        manifest.get("authorization_id"),
        "M3_ECONOMIC_EXECUTION_R1_2026_09_03",
        "execution authorization id",
    )
    _require_equal(manifest.get("output_directory"), EXECUTION_RAW_OUTPUT_DIR, "execution output directory")
    if _forbidden_keys(manifest):
        _fail("execution authorization contains a result field")

    topology = manifest.get("commit_topology")
    if not isinstance(topology, Mapping):
        _fail("execution manifest commit topology is missing")
    _validate_execution_implementation_commit(implementation_commit)
    implementation_parent = git_parents(implementation_commit)[0]
    expected_topology = {
        "starting_head": STARTING_HEAD,
        "implementation_parent": implementation_parent,
        "implementation_commit": implementation_commit,
        "authorization_manifest_path": EXECUTION_MANIFEST_PATH,
        "authorization_parent_rule": "B26 direct child of A26",
        "result_parent_rule": "C26 direct child of B26",
        "closure_rule": "D26 then E26 fast-forward only",
    }
    for field, expected in expected_topology.items():
        _require_equal(topology.get(field), expected, f"execution topology {field}")
    _require_equal(
        manifest.get("manifest_identity"),
        {
            "path": EXECUTION_MANIFEST_PATH,
            "commit_bound_at_runtime": True,
            "git_blob_bound_at_runtime": True,
        },
        "execution manifest identity",
    )

    implementation = manifest.get("implementation_identity")
    if not isinstance(implementation, Mapping):
        _fail("execution implementation identity is missing")
    _require_equal(implementation.get("commit"), implementation_commit, "execution implementation commit")
    _require_equal(implementation.get("parent"), implementation_parent, "execution implementation parent")
    _require_equal(
        implementation.get("tree"),
        _git("rev-parse", f"{implementation_commit}^{{tree}}"),
        "execution implementation tree",
    )
    _require_equal(
        implementation.get("changed_paths"),
        sorted(EXECUTION_IMPLEMENTATION_PATHS),
        "execution implementation changed paths",
    )
    sources = manifest.get("source_identities")
    if not isinstance(sources, Mapping):
        _fail("execution source identities are missing")
    expected_sources = _execution_source_identities(implementation_commit)
    _require_equal(set(sources), set(expected_sources), "execution source identity paths")
    for path, actual in expected_sources.items():
        identity = sources.get(path)
        if not isinstance(identity, Mapping):
            _fail(f"execution source identity is invalid: {path}")
        _require_equal(identity, actual, f"execution source identity {path}")

    _require_equal(manifest.get("predeclaration_closure"), PREDECLARATION_CLOSURE, "predeclaration closure binding")
    _require_equal(manifest.get("exact_data_identities"), DATA_IDENTITIES, "execution data identities")
    _require_equal(
        manifest.get("exact_observation_identities"),
        OBSERVATION_IDENTITIES,
        "execution observation identities",
    )
    _require_equal(manifest.get("exact_execution_addendum"), EXECUTION_ADDENDUM, "execution addendum")
    _require_equal(manifest.get("accepted_inputs"), EXECUTION_ACCEPTED_INPUTS, "execution accepted inputs")
    _require_equal(manifest.get("reference_contract"), REFERENCE_CONTRACT, "execution reference contract")
    _require_equal(manifest.get("full_pin_order"), list(FULL_PIN_ORDER), "execution pin order")
    _require_equal(manifest.get("non_tradable_references"), sorted(NON_TRADABLE_REFERENCES), "non-tradable references")
    _require_equal(
        manifest.get("tradable_reserved_inverse_hedges"),
        sorted(TRADABLE_RESERVED_INVERSE_HEDGES),
        "tradable inverse hedges",
    )
    _require_equal(manifest.get("evaluation_windows"), EVALUATION_WINDOWS, "execution windows")
    _require_equal(manifest.get("cost_scenarios"), COST_SCENARIOS, "execution costs")
    _require_equal(manifest.get("cost_scenario_contract"), COST_SCENARIO_CONTRACT, "execution cost rules")
    _require_equal(manifest.get("metric_contract"), METRIC_CONTRACT, "execution metrics")
    _require_equal(manifest.get("bootstrap_contract"), BOOTSTRAP_CONTRACT, "execution bootstrap")
    _require_equal(manifest.get("robustness_periods"), ROBUSTNESS_PERIODS, "execution robustness periods")
    _require_equal(manifest.get("timing_contract"), TIMING_CONTRACT, "execution timing")
    _require_equal(manifest.get("baseline_configuration"), BASELINE_CONFIGURATION, "execution baseline config")
    _require_equal(manifest.get("strategy_contract"), STRATEGY_CONTRACT, "execution strategy contract")
    _require_equal(manifest.get("acceptance_criteria"), ACCEPTANCE_CRITERIA, "execution acceptance criteria")
    _require_equal(
        manifest.get("feed_transferability_boundary"), FEED_TRANSFERABILITY_BOUNDARY, "execution feed boundary"
    )
    _require_equal(
        manifest.get("pass_orders"),
        {key: list(value) for key, value in PASS_ORDERS.items()},
        "execution pass order",
    )
    _require_equal(manifest.get("execution_count"), 48, "execution count")
    runner_guards = manifest.get("runner_guards")
    if not isinstance(runner_guards, Mapping):
        _fail("execution runner guards are missing")
    for field, expected in {
        "parameter_overrides": False,
        "date_overrides": False,
        "cost_overrides": False,
        "strategy_overrides": False,
        "paper_live_path": False,
        "broker_path": False,
        "promotion_path": False,
        "result_manifest_reuse": False,
        "fresh_engine_per_cell": True,
        "authorization_required": True,
    }.items():
        _require_equal(runner_guards.get(field), expected, f"execution runner guard {field}")
    _require_equal(
        runner_guards.get("allowed_commands"),
        [
            "--validate-contract",
            "--synthetic-fixture",
            "--build-execution-authorization",
            "--execute-predeclared-economic-mission",
        ],
        "execution runner commands",
    )
    _require_equal(
        manifest.get("next_required_mission"),
        "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE",
        "execution next mission",
    )
    if manifest_commit is not None:
        if git_parents(manifest_commit) != [implementation_commit]:
            _fail("B26 authorization manifest is not a direct child of A26")
        if changed_paths(implementation_commit, manifest_commit) != [EXECUTION_MANIFEST_PATH]:
            _fail("B26 must change exactly the execution authorization manifest")
        _require_equal(
            tracked_blob(manifest_commit, EXECUTION_MANIFEST_PATH),
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
            "execution authorization Git bytes",
        )


def build_execution_authorization_manifest(implementation_commit: str) -> dict[str, object]:
    """Build B26's one-time human authorization from the clean A26 tree."""

    if git_status():
        _fail("execution authorization construction requires a clean worktree")
    _require_equal(git_head(), implementation_commit, "execution implementation current HEAD")
    _validate_execution_implementation_commit(implementation_commit)
    validate_upstream()
    _validate_predeclaration_closure(STARTING_HEAD)
    config = _yaml_blob(implementation_commit, CONFIG_PATH)
    baseline = validate_baseline_configuration(config)
    _validate_strategy_contract(config)
    _validate_source_has_no_broker_path()
    implementation_parent = git_parents(implementation_commit)[0]
    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "mission": EXECUTION_MISSION,
        "repository_url": "https://github.com/batuhankiskac/ParayaBuck",
        "branch": "main",
        "declared_before_results": True,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "human_approval": True,
        "operator_approval": True,
        "operator_id": "human_operator",
        "authorization_id": "M3_ECONOMIC_EXECUTION_R1_2026_09_03",
        "real_execution_authorized": True,
        "economic_execution_run": False,
        "economic_results_present": False,
        "output_directory": EXECUTION_RAW_OUTPUT_DIR,
        "commit_topology": {
            "starting_head": STARTING_HEAD,
            "implementation_parent": implementation_parent,
            "implementation_commit": implementation_commit,
            "authorization_manifest_path": EXECUTION_MANIFEST_PATH,
            "authorization_parent_rule": "B26 direct child of A26",
            "result_parent_rule": "C26 direct child of B26",
            "closure_rule": "D26 then E26 fast-forward only",
        },
        "implementation_identity": {
            "commit": implementation_commit,
            "parent": implementation_parent,
            "tree": _git("rev-parse", f"{implementation_commit}^{{tree}}"),
            "changed_paths": sorted(EXECUTION_IMPLEMENTATION_PATHS),
        },
        "manifest_identity": {
            "path": EXECUTION_MANIFEST_PATH,
            "commit_bound_at_runtime": True,
            "git_blob_bound_at_runtime": True,
        },
        "predeclaration_closure": copy.deepcopy(PREDECLARATION_CLOSURE),
        "exact_data_identities": copy.deepcopy(DATA_IDENTITIES),
        "exact_observation_identities": copy.deepcopy(OBSERVATION_IDENTITIES),
        "exact_execution_addendum": copy.deepcopy(EXECUTION_ADDENDUM),
        "accepted_inputs": copy.deepcopy(EXECUTION_ACCEPTED_INPUTS),
        "reference_contract": copy.deepcopy(REFERENCE_CONTRACT),
        "full_pin_order": list(FULL_PIN_ORDER),
        "non_tradable_references": sorted(NON_TRADABLE_REFERENCES),
        "tradable_reserved_inverse_hedges": sorted(TRADABLE_RESERVED_INVERSE_HEDGES),
        "evaluation_windows": copy.deepcopy(EVALUATION_WINDOWS),
        "robustness_periods": copy.deepcopy(ROBUSTNESS_PERIODS),
        "timing_contract": copy.deepcopy(TIMING_CONTRACT),
        "baseline_configuration": baseline,
        "cost_scenarios": copy.deepcopy(COST_SCENARIOS),
        "cost_scenario_contract": copy.deepcopy(COST_SCENARIO_CONTRACT),
        "metric_contract": copy.deepcopy(METRIC_CONTRACT),
        "bootstrap_contract": copy.deepcopy(BOOTSTRAP_CONTRACT),
        "strategy_contract": copy.deepcopy(STRATEGY_CONTRACT),
        "acceptance_criteria": copy.deepcopy(ACCEPTANCE_CRITERIA),
        "feed_transferability_boundary": copy.deepcopy(FEED_TRANSFERABILITY_BOUNDARY),
        "pass_orders": {key: list(value) for key, value in PASS_ORDERS.items()},
        "execution_count": 48,
        "runner_guards": {
            "dedicated_runner": "scripts/research_equity_m3_economic.py",
            "allowed_commands": [
                "--validate-contract",
                "--synthetic-fixture",
                "--build-execution-authorization",
                "--execute-predeclared-economic-mission",
            ],
            "parameter_overrides": False,
            "date_overrides": False,
            "cost_overrides": False,
            "strategy_overrides": False,
            "paper_live_path": False,
            "broker_path": False,
            "promotion_path": False,
            "result_manifest_reuse": False,
            "fresh_engine_per_cell": True,
            "authorization_required": True,
        },
        "no_network_market_data": True,
        "no_credential_access": True,
        "no_ocp_replay": True,
        "no_m3_data_gate_replay": True,
        "no_iwm_refetch": True,
        "no_optimization": True,
        "no_promotion": True,
        "no_paper_live_change": True,
        "source_identities": _execution_source_identities(implementation_commit),
        "next_required_mission": "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE",
    }
    validate_execution_manifest_payload(manifest, implementation_commit)
    return manifest


def validate_execution_authorization(path: Path) -> dict[str, object]:
    """Validate B26 as a tracked artifact and bind it to the current HEAD."""

    expected_path = (REPO / EXECUTION_MANIFEST_PATH).resolve()
    if path.resolve() != expected_path:
        _fail("execution authorization path must be the tracked B26 manifest")
    if not path.is_file():
        _fail("execution authorization artifact is missing")
    current = git_head()
    if git_status():
        _fail("execution requires a clean worktree")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("execution authorization artifact is invalid") from exc
    if not isinstance(value, dict):
        _fail("execution authorization artifact must be an object")
    tracked = tracked_blob(current, EXECUTION_MANIFEST_PATH)
    _require_equal(path.read_bytes(), tracked, "execution authorization tracked bytes")
    topology = value.get("commit_topology")
    if not isinstance(topology, Mapping) or not isinstance(topology.get("implementation_commit"), str):
        _fail("execution authorization implementation binding is missing")
    implementation_commit = str(topology["implementation_commit"])
    validate_execution_manifest_payload(value, implementation_commit, manifest_commit=current)
    _require_equal(_git("branch", "--show-current"), "main", "execution branch")
    _validate_predeclaration_closure(STARTING_HEAD)
    return value


def guard_real_execution(
    *,
    explicit_execution_flag: bool,
    execution_authorization: Path | None,
) -> dict[str, object]:
    """Validate the explicit execution boundary and return the bound manifest."""

    if not explicit_execution_flag:
        _fail("real execution requires the explicit execution flag")
    if execution_authorization is None:
        _fail("real execution requires --execution-authorization")
    return validate_execution_authorization(execution_authorization)


class NonTradableReferenceUniverseAdapter:
    """Research-only PIT adapter that separates observation from execution."""

    def __init__(self, universe: PointInTimeUniverse) -> None:
        self._universe = universe
        self.snapshots = universe.snapshots

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        if market == Market.EQUITY and str(symbol).strip().upper() in NON_TRADABLE_REFERENCES:
            return False
        return self._universe.contains(symbol, market, timestamp)

    def __getattr__(self, name: str) -> object:
        return getattr(self._universe, name)


class _ScopedNumericHistoryView:
    """Immutable per-context numeric view; it cannot widen streamed symbols."""

    def __init__(self, base: object, allowed_symbols: Iterable[str]) -> None:
        self._base = base
        self._allowed = frozenset(allowed_symbols)
        self.market = getattr(base, "market", Market.EQUITY)
        self.timeframe = getattr(base, "timeframe", "1d")

    def series(self, symbol: str) -> object | None:
        if symbol not in self._allowed:
            return None
        method = getattr(self._base, "series", None)
        return method(symbol) if callable(method) else None

    def symbols(self) -> tuple[str, ...]:
        base_symbols = getattr(self._base, "symbols", None)
        values = base_symbols() if callable(base_symbols) else ()
        return tuple(symbol for symbol in values if symbol in self._allowed)

    @property
    def series_by_symbol(self) -> dict[str, object]:
        return {symbol: series for symbol in self.symbols() if (series := self.series(symbol)) is not None}

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._allowed and self.series(symbol) is not None


class AuditedTerminalResearchEngine(TerminalAwareResearchEngine):
    """Terminal engine with explicit observation, target, fill and expiry audits."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.order_events: list[dict[str, object]] = []
        self.context_audit: list[dict[str, object]] = []
        self.holdings_audit: list[dict[str, object]] = []
        self.expired_orders: list[dict[str, object]] = []

    def _build_context(self, timestamp: datetime) -> PortfolioContext:
        context = super()._build_context(timestamp)
        expected = self._effective_observation_symbols(timestamp)
        expected_set = set(expected or ())
        actual_set = set(context.history)
        if expected is not None and actual_set != expected_set:
            _fail(
                "observation visibility mismatch at "
                f"{timestamp.isoformat()}: expected {sorted(expected_set)}, got {sorted(actual_set)}"
            )
        if any(bars[-1].timestamp != timestamp for bars in context.history.values() if bars):
            _fail("observation context contains a bar from a different decision timestamp")
        if expected is not None and any(not bars for bars in context.history.values()):
            _fail("observation context contains an empty symbol history")
        scoped = _ScopedNumericHistoryView(context.numeric_history, actual_set) if context.numeric_history else None
        if scoped is not None and set(scoped.symbols()) - actual_set:
            _fail("NumericHistoryView widened the effective observation context")
        context = replace(context, numeric_history=scoped)
        self.context_audit.append(
            {
                "timestamp": timestamp.isoformat(),
                "effective_observation_symbols": list(expected or ()),
                "context_symbols": sorted(actual_set),
                "numeric_symbols": list(scoped.symbols()) if scoped is not None else [],
            }
        )
        return context

    def _apply_target(self, target: TargetPortfolio) -> None:
        target_symbols = [str(weight.symbol).upper() for weight in target.weights]
        validate_reference_contract([], target_symbols, [])
        super()._apply_target(target)
        if any(str(order.symbol).strip().upper() in NON_TRADABLE_REFERENCES for order in self._pending):
            _fail("non-tradable reference entered the pending order set")
        self.order_events.extend(
            {
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": order.qty,
                "created_at": order.created_at.isoformat(),
                "decision_timestamp": order.decision_timestamp.isoformat(),
            }
            for order in self._pending
        )

    def _fill_side(self, bar: Bar, side: OrderSide) -> None:
        before = len(self._fills)
        super()._fill_side(bar, side)
        for fill in self._fills[before:]:
            if str(fill.symbol).strip().upper() in NON_TRADABLE_REFERENCES:
                _fail("non-tradable reference entered the fill ledger")
            validate_fill_timing(
                fill.decision_timestamp,
                fill.timestamp,
                self._audit_window_end,
                next_execution_bar_exists=True,
            )

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        super()._record_holdings_snapshot(timestamp)
        equity = self._equity()
        invested = sum(
            position.qty * self._last_price.get(position.symbol, position.avg_price)
            for position in self._positions.values()
        )
        gross = invested / equity if equity > 0 else 0.0
        concentration = max(
            (
                position.qty * self._last_price.get(position.symbol, position.avg_price) / equity
                for position in self._positions.values()
            ),
            default=0.0,
        )
        self.holdings_audit.append(
            {
                "timestamp": timestamp.isoformat(),
                "equity": equity,
                "cash": self._cash,
                "invested_capital": invested,
                "gross_exposure": gross,
                "concentration": concentration,
                "holdings_count": len(self._positions),
            }
        )

    def run(
        self,
        bars: list[Bar],
        warmup: list[Bar] | None = None,
        **kwargs: Any,
    ) -> object:
        default_end = max((bar.timestamp for bar in bars), default=datetime.now(UTC))
        self._audit_window_end = kwargs.pop("audit_window_end", default_end)
        result = super().run(bars, warmup=warmup, **kwargs)
        end = self._audit_window_end
        for fill in self._fills:
            if fill.timestamp > end:
                _fail("fill occurred after the evaluation window")
            if fill.timestamp <= fill.decision_timestamp:
                _fail("fill occurred on the decision bar")
        last_timestamp = result.curve_timestamps[-1] if result.curve_timestamps else end
        for order in self._pending:
            if order.created_at > last_timestamp:
                _fail("pending order has a future creation timestamp")
            self.expired_orders.append(
                {
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "qty": order.qty,
                    "created_at": order.created_at.isoformat(),
                    "expired_at": end.isoformat(),
                    "reason": "no_next_bar_or_partial_unfilled_at_window_end",
                }
            )
        if result.curve_timestamps:
            # The parent appends a duplicate terminal timestamp after final
            # liquidation.  Expose that last, post-liquidation accounting state
            # to the exposure audit as well.
            if not self.holdings_audit or self.holdings_audit[-1]["timestamp"] != last_timestamp.isoformat():
                equity = self._equity()
                self.holdings_audit.append(
                    {
                        "timestamp": last_timestamp.isoformat(),
                        "equity": equity,
                        "cash": self._cash,
                        "invested_capital": 0.0,
                        "gross_exposure": 0.0,
                        "concentration": 0.0,
                        "holdings_count": len(self._positions),
                    }
                )
        return result


@dataclass(frozen=True)
class _ExecutionWindow:
    name: str
    metric_start: datetime
    metric_end: datetime
    warmup_by_strategy: dict[str, tuple[Bar, ...]]
    ordered_by_strategy: dict[str, tuple[Bar, ...]]
    numeric_by_strategy: dict[str, NumericHistoryView]


@dataclass(frozen=True)
class _LoadedExecutionInputs:
    staged_inputs: dict[str, dict[str, object]]
    bars_by_symbol: dict[str, tuple[Bar, ...]]
    terminal_map: dict[str, TerminalInfo]
    corporate_actions: tuple[CorporateAction, ...]
    universes: dict[str, PointInTimeUniverse]


def _parse_execution_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _fail("execution input timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"invalid execution input timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("execution input timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_execution_file(
    source: Path,
    staging_dir: Path,
    *,
    expected_sha256: str,
) -> tuple[Path, dict[str, object]]:
    try:
        from m3_data_gate_helpers import stage_file  # noqa: PLC0415

        artifact = stage_file(source, staging_dir, expected_sha256=expected_sha256)
    except Exception as exc:
        raise ContractError(f"accepted input could not be staged: {source}") from exc
    _require_equal(_sha256_path(artifact.staged_path), expected_sha256, f"staged input {source.name} SHA")
    return artifact.staged_path, {
        "source_path": str(source),
        "staged_path": str(artifact.staged_path.relative_to(REPO.parent))
        if artifact.staged_path.is_relative_to(REPO.parent)
        else str(artifact.staged_path),
        "raw_sha256": artifact.raw_sha256,
        "crlf_normalized_sha256": artifact.crlf_normalized_sha256,
        "byte_size": artifact.byte_size,
        "parse_source": "content_addressed_staged_file",
    }


def _stage_execution_inputs(raw_root: Path) -> tuple[dict[str, dict[str, object]], dict[str, Path]]:
    staged: dict[str, dict[str, object]] = {}
    paths: dict[str, Path] = {}
    regular_inputs = {
        "canonical_bars": (REPO / CANONICAL_BARS_PATH, DATA_IDENTITIES["canonical_equity_v5"]["raw_sha256"]),  # type: ignore[index]
        "lifetime": (REPO / LIFETIME_PATH, DATA_IDENTITIES["lifetime"]["raw_sha256"]),  # type: ignore[index]
        "corporate_actions": (REPO / CORPORATE_ACTIONS_PATH, DATA_IDENTITIES["corporate_actions"]["raw_sha256"]),  # type: ignore[index]
        "iwm_normalized_sidecar": (REPO / IWM_SIDECAR_PATH, DATA_IDENTITIES["iwm_normalized_sidecar"]["raw_sha256"]),  # type: ignore[index]
        "r5_pit_projection": (R5_PIT_PROJECTION_SOURCE, DATA_IDENTITIES["r5_evaluation"]["pit_raw_sha256"]),  # type: ignore[index]
        "ocp_control_C": (REPO / OCP_CONTROL_PATH, ACCEPTED_OCP_RAW_SHA256["control_C"]),
        "ocp_candidate_X": (REPO / OCP_CANDIDATE_PATH, ACCEPTED_OCP_RAW_SHA256["candidate_X"]),
    }
    for label, (source, expected) in regular_inputs.items():
        if not isinstance(expected, str):
            _fail(f"accepted input hash is not a string: {label}")
        staged_path, identity = _stage_execution_file(
            Path(source), raw_root / "inputs" / label, expected_sha256=expected
        )
        staged[label] = identity
        paths[label] = staged_path

    try:
        normalized_rows, _quality = load_csv_reference_rows(paths["iwm_normalized_sidecar"], reference_symbols=("IWM",))
    except Exception as exc:
        raise ContractError("accepted normalized IWM sidecar failed canonical validation") from exc
    _require_equal(
        semantic_rows_digest(normalized_rows["IWM"]),
        DATA_IDENTITIES["iwm_normalized_sidecar"]["semantic_row_sha256"],  # type: ignore[index]
        "IWM normalized semantic rows",
    )

    # The raw IWM bundle is a directory, so stage every accepted file through
    # the same content-addressed staging boundary and recompute its declared
    # bundle digest from the staged bytes below.
    bundle_source = REPO / IWM_BUNDLE_PATH
    receipt_source = bundle_source / "source_receipt.json"
    page_source = bundle_source / "pages" / "000001.json"
    receipt_path, receipt_identity = _stage_execution_file(
        receipt_source,
        raw_root / "inputs" / "iwm_raw_bundle" / "source_receipt",
        expected_sha256=DATA_IDENTITIES["iwm_source_receipt"]["raw_sha256"],  # type: ignore[index]
    )
    page_path, page_identity = _stage_execution_file(
        page_source,
        raw_root / "inputs" / "iwm_raw_bundle" / "page_000001",
        expected_sha256="7886ea77e9fcada1551acfcaeaff92ffc3c2d55ec562149dd7ff07ad44d178d7",
    )
    staged["iwm_source_receipt"] = receipt_identity
    staged["iwm_raw_bundle_page_000001"] = page_identity
    paths["iwm_source_receipt"] = receipt_path
    paths["iwm_raw_bundle_page_000001"] = page_path

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, Mapping):
        _fail("staged IWM source receipt is not an object")
    pages = receipt.get("pages")
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], Mapping):
        _fail("staged IWM source receipt page contract is invalid")
    page = pages[0]
    bundle_payload = {
        "encoding": "m3-raw-bundle-v1",
        "source_receipt_sha256": receipt_identity["raw_sha256"],
        "source_receipt_crlf_normalized_sha256": receipt_identity["crlf_normalized_sha256"],
        "pages": [
            {
                "relative_path": page.get("relative_path"),
                "raw_sha256": page_identity["raw_sha256"],
                "crlf_normalized_sha256": page_identity["crlf_normalized_sha256"],
                "byte_size": page_identity["byte_size"],
                "row_count": page.get("row_count"),
                "input_page_token": page.get("input_page_token"),
                "output_next_page_token": page.get("output_next_page_token"),
            }
        ],
    }
    _require_equal(
        canonical_digest(bundle_payload),
        DATA_IDENTITIES["iwm_raw_bundle"]["raw_sha256"],  # type: ignore[index]
        "IWM raw bundle digest",
    )
    staged["iwm_raw_bundle"] = {
        "source_path": str(bundle_source),
        "raw_sha256": DATA_IDENTITIES["iwm_raw_bundle"]["raw_sha256"],  # type: ignore[index]
        "files": [receipt_identity, page_identity],
        "parse_source": "content_addressed_staged_file",
    }
    return staged, paths


def _bar_from_row(row: Mapping[str, str], *, source: str) -> Bar:
    symbol = str(row.get("symbol", "")).strip().upper()
    timeframe = str(row.get("timeframe", "")).strip().lower()
    if timeframe in {"1day", "1-day", "day"}:
        timeframe = "1d"
    if not symbol or timeframe != "1d":
        _fail(f"invalid bar identity in {source}")
    timestamp = _parse_execution_timestamp(row.get("timestamp"))
    try:
        values = {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"invalid numeric bar in {source}: {symbol}") from exc
    if any(not math.isfinite(value) for value in values.values()) or any(
        values[key] <= 0 for key in ("open", "high", "low", "close")
    ):
        _fail(f"non-positive or non-finite bar in {source}: {symbol}")
    market = str(row.get("market", "equity")).strip().lower()
    if market != "equity":
        _fail(f"execution input contains a non-equity bar: {symbol}")
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=values["open"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
        volume=values["volume"],
        market=Market.EQUITY,
    )


def _load_staged_bars(path: Path, allowed_symbols: set[str], end: datetime) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume"}
        if not required.issubset(set(reader.fieldnames or ())):
            _fail(f"execution bar input has an incomplete header: {path.name}")
        for row in reader:
            symbol = str(row.get("symbol", "")).strip().upper()
            if symbol not in allowed_symbols:
                continue
            bar = _bar_from_row(row, source=path.name)
            if bar.timestamp <= end:
                grouped[symbol].append(bar)
    for symbol, bars in grouped.items():
        bars.sort(key=lambda item: item.timestamp)
        unique: dict[datetime, Bar] = {}
        for bar in bars:
            previous = unique.get(bar.timestamp)
            if previous is not None and previous != bar:
                _fail(f"conflicting duplicate accepted bars: {symbol} {bar.timestamp.isoformat()}")
            unique[bar.timestamp] = bar
        grouped[symbol] = list(unique.values())
    return grouped


def _load_lifetimes(path: Path, symbols: set[str], end: datetime) -> dict[str, TerminalInfo]:
    terminal: dict[str, TerminalInfo] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("canonical_symbol", "")).strip().upper()
            if symbol not in symbols:
                continue
            last_value = row.get("last_observed_bar")
            if not last_value:
                continue
            last_bar = _parse_execution_timestamp(last_value)
            if last_bar <= end and str(row.get("current_status", "active")) != "active":
                terminal[symbol] = TerminalInfo(
                    entity_id=str(row.get("entity_id", "")),
                    symbol=symbol,
                    display_symbol=symbol,
                    last_bar=last_bar,
                    terminal_event_class=str(row.get("terminal_event_class", "POSSIBLE_DELISTING")),
                    evidence_class=str(row.get("evidence_class", "POSSIBLE_DELISTING")),
                    should_haircut=True,
                )
    return terminal


def _load_corporate_actions(path: Path, symbols: set[str], end: datetime) -> tuple[CorporateAction, ...]:
    actions: list[CorporateAction] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol", "")).strip().upper()
            if symbol not in symbols or row.get("action_type") != "cash_dividends":
                continue
            ex_date = str(row.get("ex_date", "")).strip()
            if not ex_date:
                continue
            try:
                timestamp = datetime.fromisoformat(ex_date).replace(tzinfo=UTC)
                amount = float(row.get("amount") or row.get("rate") or row.get("cash_amount") or "0")
            except (TypeError, ValueError) as exc:
                raise ContractError(f"invalid corporate action for {symbol}") from exc
            if timestamp <= end and amount > 0 and math.isfinite(amount):
                actions.append(CorporateAction(timestamp, symbol, CorporateActionType.DIVIDEND, amount))
    return tuple(sorted(actions, key=lambda item: (item.timestamp, item.symbol)))


def _expected_strategy_identity(strategy_key: str) -> dict[str, object]:
    identity = OBSERVATION_IDENTITIES["control_C" if strategy_key == "C" else "candidate_X"]
    parameters = [[key, FROZEN_PARAMETERS[key]] for key in sorted(FROZEN_PARAMETERS)]
    return {
        "market": "equity",
        "name": identity["strategy"],  # type: ignore[index]
        "parameter_digest_sha256": identity["strategy_parameter_digest_sha256"],  # type: ignore[index]
        "parameters": parameters,
        "timeframe": "1d",
        "version": identity["version"],  # type: ignore[index]
    }


def _load_accepted_universe(
    *,
    strategy_key: str,
    path: Path,
    r5_projection: Mapping[str, object],
) -> PointInTimeUniverse:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"accepted OCP artifact is invalid: {path}") from exc
    if not isinstance(payload, Mapping):
        _fail(f"accepted OCP artifact is not an object: {path.name}")
    expected_identity = _expected_strategy_identity(strategy_key)
    _require_equal(payload.get("mission"), "M3_CANDIDATE_OCP_GATE", f"OCP {strategy_key} mission")
    _require_equal(payload.get("schema_version"), "m3-ocp-observation-basis-v1", f"OCP {strategy_key} schema")
    _require_equal(payload.get("strategy_identity"), expected_identity, f"OCP {strategy_key} strategy identity")
    observation_identity = OBSERVATION_IDENTITIES["control_C" if strategy_key == "C" else "candidate_X"]
    _require_equal(
        payload.get("semantic_universe_hash"),
        observation_identity["semantic_observation_universe_hash"],  # type: ignore[index]
        f"OCP {strategy_key} semantic universe",
    )
    _require_equal(
        payload.get("ordered_observation_merkle_root"),
        observation_identity["ordered_observation_merkle_root"],  # type: ignore[index]
        f"OCP {strategy_key} observation Merkle root",
    )
    snapshots = payload.get("snapshots")
    r5_snapshots = r5_projection.get("snapshots")
    if not isinstance(snapshots, list) or not isinstance(r5_snapshots, list):
        _fail(f"OCP {strategy_key} or R5 snapshots are missing")
    _require_equal(len(snapshots), 2658, f"OCP {strategy_key} snapshot count")
    _require_equal(len(r5_snapshots), 2658, "R5 snapshot count for OCP")
    built: list[UniverseSnapshot] = []
    for index, (raw_snapshot, r5_snapshot) in enumerate(zip(snapshots, r5_snapshots, strict=True)):
        if not isinstance(raw_snapshot, Mapping) or not isinstance(r5_snapshot, Mapping):
            _fail(f"OCP {strategy_key} snapshot {index} is invalid")
        effective_at = _parse_execution_timestamp(raw_snapshot.get("effective_at"))
        _require_equal(
            effective_at,
            _parse_execution_timestamp(r5_snapshot.get("effective_at")),
            f"OCP {strategy_key} snapshot {index} timestamp",
        )
        _require_equal(
            _parse_execution_timestamp(raw_snapshot.get("latest_input_timestamp")),
            _parse_execution_timestamp(r5_snapshot.get("latest_input_timestamp")),
            f"OCP {strategy_key} snapshot {index} PIT input timestamp",
        )
        _require_equal(raw_snapshot.get("scanner_version"), "equity-scanner-v3", f"OCP {strategy_key} scanner")
        _require_equal(
            raw_snapshot.get("scanner_config_digest"),
            "d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392",
            f"OCP {strategy_key} scanner config",
        )
        _require_equal(
            raw_snapshot.get("eligible_candidate_symbols"),
            r5_snapshot.get("eligible_symbols"),
            f"OCP {strategy_key} eligible candidate pool {index}",
        )
        eligible = [str(symbol) for symbol in r5_snapshot.get("eligible_symbols", [])]
        expected_product = list(dict.fromkeys((*FULL_PIN_ORDER, *eligible)))
        _require_equal(
            raw_snapshot.get("product_visible_symbols"),
            expected_product,
            f"OCP {strategy_key} product-visible pool {index}",
        )
        _require_equal(raw_snapshot.get("pinned_symbols"), list(FULL_PIN_ORDER), f"OCP {strategy_key} pins {index}")
        _require_equal(
            raw_snapshot.get("reference_pin_symbols"),
            list(NON_TRADABLE_REFERENCES),
            f"OCP {strategy_key} reference pins {index}",
        )
        observation = tuple(str(symbol) for symbol in raw_snapshot.get("observation_set", []))
        if len(observation) > 30 or observation[: len(FULL_PIN_ORDER)] != FULL_PIN_ORDER:
            _fail(f"OCP {strategy_key} observation capacity/order is invalid at {index}")
        if not set(observation).issubset(expected_product):
            _fail(f"OCP {strategy_key} observation set widens product membership at {index}")
        validate_reference_contract(eligible, [], [])
        built.append(
            UniverseSnapshot(
                effective_at=effective_at,
                symbols=frozenset(expected_product),
                scanner_version="equity-scanner-v3",
                config_digest="d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392",
                candidate_count=int(raw_snapshot.get("candidate_count", 0)),
                score_breakdown={},
                eliminated={},
                sector_metadata_coverage=1.0,
                observation_set=observation,
                maximum_stream_symbols=int(raw_snapshot.get("maximum_stream_symbols", 0)),
                pinned_symbols=tuple(str(symbol) for symbol in raw_snapshot.get("pinned_symbols", [])),
                observation_strategy_name=str(raw_snapshot.get("observation_strategy_name", "")),
                observation_strategy_version=str(raw_snapshot.get("observation_strategy_version", "")),
            )
        )
    return PointInTimeUniverse(
        market=Market.EQUITY,
        snapshots=tuple(built),
        methodology=f"accepted M3 OCP R2 observation basis {strategy_key}",
    )


def _load_execution_inputs(raw_root: Path) -> _LoadedExecutionInputs:
    staged, paths = _stage_execution_inputs(raw_root)
    try:
        r5_projection = json.loads(paths["r5_pit_projection"].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("staged R5 PIT projection is invalid") from exc
    if not isinstance(r5_projection, Mapping):
        _fail("staged R5 PIT projection is not an object")
    _require_equal(r5_projection.get("mission"), "equity_m3_pit_scanner_population_2026_09_r5", "R5 projection mission")
    _require_equal(r5_projection.get("schema_version"), "m3-pit-scanner-population-v1", "R5 projection schema")
    projection = r5_projection.get("r5_projection")
    _require_equal(
        projection.get("projection_digest_sha256") if isinstance(projection, Mapping) else None,
        DATA_IDENTITIES["r5_evaluation"]["projection_digest_sha256"],  # type: ignore[index]
        "R5 projection digest",
    )
    _require_equal(r5_projection.get("market"), "EQUITY", "R5 projection market")
    _require_equal(r5_projection.get("timeframe"), "1d", "R5 projection timeframe")
    _require_equal(r5_projection.get("scanner_versions"), ["equity-scanner-v3"], "R5 projection scanner")
    _require_equal(
        r5_projection.get("scanner_config_digests"),
        ["d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392"],
        "R5 projection scanner config",
    )
    _require_equal(r5_projection.get("reference_symbols"), ["SPY", "IWM", "QQQ"], "R5 projection references")
    _require_equal(r5_projection.get("alpha_ranking_used"), False, "R5 projection alpha ranking")
    population = r5_projection.get("population")
    if not isinstance(population, Mapping):
        _fail("R5 projection population is missing")
    for field, expected in {
        "snapshot_count": 2658,
        "unique_candidate_count": 889,
        "candidate_universe_digest_sha256": DATA_IDENTITIES["r5_evaluation"]["candidate_universe_digest_sha256"],  # type: ignore[index]
        "candidate_decision_eligibility_digest_sha256": DATA_IDENTITIES["r5_evaluation"][
            "candidate_decision_eligibility_digest_sha256"
        ],  # type: ignore[index]
    }.items():
        _require_equal(population.get(field), expected, f"R5 projection population {field}")
    strict_prior = r5_projection.get("strict_prior_proof")
    if not isinstance(strict_prior, Mapping):
        _fail("R5 projection strict-prior proof is missing")
    _require_equal(strict_prior.get("status"), "PASS", "R5 strict-prior status")
    _require_equal(strict_prior.get("violating_snapshots"), [], "R5 strict-prior violations")

    c_universe = _load_accepted_universe(strategy_key="C", path=paths["ocp_control_C"], r5_projection=r5_projection)
    x_universe = _load_accepted_universe(strategy_key="X", path=paths["ocp_candidate_X"], r5_projection=r5_projection)
    universes = {"C": c_universe, "X": x_universe}
    symbols = set().union(
        *(set(snapshot.observation_set) for universe in universes.values() for snapshot in universe.snapshots)
    )
    symbols.update(FULL_PIN_ORDER)
    bars_by_symbol: dict[str, list[Bar]] = defaultdict(list)
    canonical_bars = _load_staged_bars(
        paths["canonical_bars"],
        symbols - {"IWM"},
        datetime(2026, 7, 30, 23, 59, tzinfo=UTC),
    )
    iwm_bars = _load_staged_bars(paths["iwm_normalized_sidecar"], {"IWM"}, datetime(2026, 7, 30, 23, 59, tzinfo=UTC))
    for source in (canonical_bars, iwm_bars):
        for symbol, bars in source.items():
            bars_by_symbol[symbol].extend(bars)
    for symbol, bars in bars_by_symbol.items():
        bars.sort(key=lambda item: item.timestamp)
        if not bars:
            _fail(f"accepted execution input has no bars for {symbol}")
    missing = sorted(symbols - set(bars_by_symbol))
    if missing:
        _fail(f"accepted execution inputs are missing streamed symbols: {missing[:20]}")
    terminal_map = _load_lifetimes(paths["lifetime"], set(bars_by_symbol), datetime(2026, 7, 30, 23, 59, tzinfo=UTC))
    actions = _load_corporate_actions(
        paths["corporate_actions"], set(bars_by_symbol), datetime(2026, 7, 30, 23, 59, tzinfo=UTC)
    )
    return _LoadedExecutionInputs(
        staged_inputs=staged,
        bars_by_symbol={symbol: tuple(bars) for symbol, bars in bars_by_symbol.items()},
        terminal_map=terminal_map,
        corporate_actions=actions,
        universes=universes,
    )


def _strategy_observation_symbols(universe: PointInTimeUniverse) -> set[str]:
    return set().union(*(set(snapshot.observation_set) for snapshot in universe.snapshots))


def _build_execution_windows(inputs: _LoadedExecutionInputs) -> dict[str, _ExecutionWindow]:
    windows: dict[str, _ExecutionWindow] = {}
    warmup_start = datetime(2016, 1, 4, tzinfo=UTC)
    metric_end = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
    for name, raw_window in EVALUATION_WINDOWS.items():
        if not isinstance(raw_window, Mapping) or not isinstance(raw_window.get("metric_start"), str):
            _fail(f"execution window {name} is invalid")
        metric_start = datetime.fromisoformat(str(raw_window["metric_start"])).replace(tzinfo=UTC)
        warmup_by_strategy: dict[str, tuple[Bar, ...]] = {}
        ordered_by_strategy: dict[str, tuple[Bar, ...]] = {}
        numeric_by_strategy: dict[str, NumericHistoryView] = {}
        for strategy_key in ("C", "X"):
            symbols = _strategy_observation_symbols(inputs.universes[strategy_key])
            selected: list[Bar] = []
            for symbol in symbols:
                selected.extend(inputs.bars_by_symbol.get(symbol, ()))
            selected.sort(key=lambda item: (item.timestamp, item.symbol))
            warmup = tuple(bar for bar in selected if warmup_start <= bar.timestamp < metric_start)
            ordered = tuple(bar for bar in selected if metric_start <= bar.timestamp <= metric_end)
            if not warmup or not ordered:
                _fail(f"execution window {name}/{strategy_key} has no warmup or metric bars")
            _require_equal(
                ordered[0].timestamp >= metric_start,
                True,
                f"execution window {name}/{strategy_key} metric start",
            )
            _require_equal(
                ordered[-1].timestamp <= metric_end,
                True,
                f"execution window {name}/{strategy_key} metric end",
            )
            warmup_by_strategy[strategy_key] = warmup
            ordered_by_strategy[strategy_key] = ordered
            numeric_by_strategy[strategy_key] = build_numeric_history(
                [*warmup, *ordered], market=Market.EQUITY, timeframe="1d"
            )
        windows[name] = _ExecutionWindow(
            name=name,
            metric_start=metric_start,
            metric_end=metric_end,
            warmup_by_strategy=warmup_by_strategy,
            ordered_by_strategy=ordered_by_strategy,
            numeric_by_strategy=numeric_by_strategy,
        )
    return windows


def _execution_config() -> object:
    """Build only the offline engine configuration from tracked YAML bytes.

    ``load_config`` also loads dotenv/environment values for the application
    runtime.  The economic mission has no reason to inspect that state, so it
    constructs the small typed configuration needed by the backtest directly
    from the already validated tracked YAML representation.
    """

    raw = _yaml_blob(git_head(), CONFIG_PATH)
    baseline = validate_baseline_configuration(raw)
    raw_backtest = raw.get("backtest")
    raw_execution = raw.get("execution")
    raw_risk = raw.get("risk")
    raw_strategy = raw.get("strategy")
    if not all(isinstance(value, Mapping) for value in (raw_backtest, raw_execution, raw_risk, raw_strategy)):
        _fail("tracked execution configuration is incomplete")
    backtest = BacktestConfig(**dict(raw_backtest))
    execution = ExecutionConfig(**dict(raw_execution))
    risk = RiskLimits(**dict(raw_risk))
    equity_strategy = raw_strategy.get("equity")
    if not isinstance(equity_strategy, Mapping):
        _fail("tracked equity strategy configuration is incomplete")
    _require_equal(
        float(equity_strategy.get("maximum_allocation", 0.0)),
        baseline["equity_sleeve"]["maximum_allocation"],  # type: ignore[index]
        "execution maximum allocation",
    )
    return SimpleNamespace(
        backtest=backtest,
        execution=execution,
        risk=risk,
        strategy=SimpleNamespace(
            equity=SimpleNamespace(maximum_allocation=float(equity_strategy["maximum_allocation"]))
        ),
    )


def _make_execution_engine(
    config: object,
    inputs: _LoadedExecutionInputs,
    window: _ExecutionWindow,
    strategy_key: str,
    cost_name: str,
) -> AuditedTerminalResearchEngine:
    strategy: EquityResidualMomentum1dV3
    if strategy_key == "C":
        strategy = EquityResidualMomentum1dV3(dict(FROZEN_PARAMETERS))
    elif strategy_key == "X":
        strategy = ProxyMultifactorResidualMomentum(dict(FROZEN_PARAMETERS))
    else:
        _fail(f"unknown execution strategy: {strategy_key}")
    cost = COST_SCENARIOS.get(cost_name)
    if not isinstance(cost, Mapping):
        _fail(f"unknown cost scenario: {cost_name}")
    multiplier = float(cost.get("multiplier", 0))
    backtest = getattr(config, "backtest")
    execution = getattr(config, "execution")
    risk = getattr(config, "risk")
    _require_equal(
        float(cost.get("slippage_bps", -1)),
        float(getattr(backtest, "slippage_bps")) * multiplier,
        f"{cost_name} slippage",
    )
    _require_equal(
        float(cost.get("spread_bps", -1)),
        float(getattr(backtest, "spread_bps")) * multiplier,
        f"{cost_name} spread",
    )
    _require_equal(bool(getattr(backtest, "equity_use_effective_dated_fees")), True, "effective-dated fee mode")
    universe = NonTradableReferenceUniverseAdapter(inputs.universes[strategy_key])
    engine = AuditedTerminalResearchEngine(
        strategy=strategy,
        rebalance_policy=rebalance_policy_for(strategy),
        terminal_map=dict(inputs.terminal_map),
        policy="T1",
        starting_equity=float(getattr(backtest, "initial_cash")),
        slippage_bps=float(getattr(backtest, "slippage_bps")) * multiplier,
        spread_bps=float(getattr(backtest, "spread_bps")) * multiplier,
        commission=float(getattr(backtest, "commission")),
        crypto_taker_fee_bps=0.0,
        equity_taf_per_share=float(getattr(backtest, "equity_taf_per_share")),
        equity_taf_cap=float(getattr(backtest, "equity_taf_cap")),
        equity_cat_per_share=float(getattr(backtest, "equity_cat_per_share")),
        equity_sec_fee_rate=float(getattr(backtest, "equity_sec_fee_rate")),
        equity_use_effective_dated_fees=bool(getattr(backtest, "equity_use_effective_dated_fees")),
        benchmark_symbol="SPY",
        risk_limits=portfolio_risk_limits_from_config(risk),
        maximum_allocation=float(getattr(getattr(config, "strategy").equity, "maximum_allocation")),
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=float(getattr(execution, "rebalance_band_pct")),
            minimum_order_notional=float(getattr(execution, "minimum_order_notional")),
        ),
        enforce_drawdown_halt=False,
        universe=universe,
        universe_version=f"m3-economic-r1:{strategy_key}",
    )
    return engine


def _deduplicated_curve(result: object, starting_equity: float) -> tuple[list[datetime], list[float]]:
    raw_timestamps = tuple(getattr(result, "curve_timestamps", ()))
    raw_values = tuple(float(value) for value in getattr(result, "equity_curve", ()))
    if len(raw_timestamps) != len(raw_values):
        _fail("engine curve timestamp/value lengths differ")
    if not raw_timestamps:
        return [], []
    by_timestamp: dict[datetime, float] = {}
    for timestamp, value in zip(raw_timestamps, raw_values, strict=True):
        if timestamp.tzinfo is None or not math.isfinite(value) or value <= 0:
            _fail("engine curve contains an invalid timestamp or equity")
        by_timestamp[timestamp] = value
    timestamps = sorted(by_timestamp)
    values = [by_timestamp[timestamp] for timestamp in timestamps]
    anchor = timestamps[0] - timedelta(microseconds=1)
    return [anchor, *timestamps], [float(starting_equity), *values]


def _sample_stddev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _maximum_drawdown(values: Sequence[float]) -> float:
    peak = 0.0
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def _strategy_metrics(
    result: object,
    engine: AuditedTerminalResearchEngine,
    curve_timestamps: Sequence[datetime],
    curve_values: Sequence[float],
) -> dict[str, float | int]:
    if not curve_values:
        _fail("economic result has an empty equity curve")
    if len(curve_timestamps) != len(curve_values) or any(
        right <= left for left, right in zip(curve_timestamps, curve_timestamps[1:], strict=True)
    ):
        _fail("economic curve is not strictly timestamp ordered")
    returns = [curve_values[index] / curve_values[index - 1] - 1.0 for index in range(1, len(curve_values))]
    if any(not math.isfinite(value) or value <= -1 for value in returns):
        _fail("economic curve contains an invalid return")
    downside = [value for value in returns if value < 0]
    years = max((curve_timestamps[-1] - curve_timestamps[0]).total_seconds() / (365.25 * 86_400), 0.0)
    ending = curve_values[-1]
    total_return = ending / curve_values[0] - 1.0
    cagr = (ending / curve_values[0]) ** (1.0 / years) - 1.0 if years > 0 and ending > 0 else 0.0
    volatility = _sample_stddev(returns) * math.sqrt(252.0)
    mean_return = statistics.fmean(returns) if returns else 0.0
    sharpe = mean_return / _sample_stddev(returns) * math.sqrt(252.0) if _sample_stddev(returns) > 0 else 0.0
    sortino = mean_return / _sample_stddev(downside) * math.sqrt(252.0) if _sample_stddev(downside) > 0 else 0.0
    maximum_drawdown = _maximum_drawdown(curve_values)
    calmar = cagr / maximum_drawdown if maximum_drawdown > 0 else 0.0
    audit = engine.holdings_audit
    cash_ratios = [float(row["cash"]) / float(row["equity"]) for row in audit if float(row["equity"]) > 0]
    gross_exposures = [float(row["gross_exposure"]) for row in audit]
    fills = tuple(getattr(result, "fills", ()))
    slippage = float(getattr(result, "slippage_cost_total", 0.0))
    spread = float(getattr(result, "spread_cost_total", 0.0))
    fee_total = float(getattr(result, "fee_total", 0.0))
    commission = 0.0
    regulatory = fee_total - commission
    if regulatory < -1e-8:
        _fail("economic fee ledger is negative")
    invested_capital = [float(row["invested_capital"]) for row in engine.holdings_audit]
    total_cost = slippage + spread + fee_total
    if not math.isclose(
        total_cost,
        commission + spread + slippage + regulatory,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        _fail("economic cost ledger does not reconcile")
    return {
        "net total return": total_return,
        "CAGR": cagr,
        "annualized volatility": volatility,
        "Sharpe ratio": sharpe,
        "Sortino ratio": sortino,
        "Calmar ratio": calmar,
        "maximum drawdown": maximum_drawdown,
        "ending equity": ending,
        "turnover": float(getattr(result, "turnover", 0.0)),
        "average cash ratio": statistics.fmean(cash_ratios) if cash_ratios else 0.0,
        "average gross exposure": statistics.fmean(gross_exposures) if gross_exposures else 0.0,
        "average invested capital": statistics.fmean(invested_capital) if invested_capital else 0.0,
        "maximum gross exposure": max(gross_exposures, default=0.0),
        "rebalance count": int(getattr(result, "rebalance_count", 0)),
        "order count": len(engine.order_events),
        "fill count": len(fills),
        "commission cost": commission,
        "spread cost": spread,
        "slippage cost": slippage,
        "regulatory fees": regulatory,
        "total estimated trading cost": total_cost,
    }


def _weekly_returns(timestamps: Sequence[datetime], returns: Sequence[float]) -> list[float]:
    grouped: dict[tuple[int, int], float] = {}
    for timestamp, value in zip(timestamps[1:], returns, strict=True):
        iso = timestamp.isocalendar()
        key = (iso.year, iso.week)
        grouped[key] = (1.0 + grouped.get(key, 0.0)) * (1.0 + value) - 1.0
    return [grouped[key] for key in sorted(grouped)]


def _ratio_or_blocked(numerator: float, denominator: float, field: str) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        _fail(f"{field} ratio is non-finite")
    if denominator == 0:
        if numerator == 0:
            return 1.0
        return float("inf")
    if denominator < 0:
        _fail(f"{field} ratio has a negative denominator")
    value = numerator / denominator
    if not math.isfinite(value):
        _fail(f"{field} ratio is non-finite")
    return value


def _active_daily_return(candidate_return: float, control_return: float, timestamp: datetime) -> float:
    """Return the paired active return using the frozen denominator rules."""

    if not math.isfinite(candidate_return) or not math.isfinite(control_return):
        _fail(f"active return is non-finite at {timestamp.isoformat()}")
    denominator = 1.0 + control_return
    if denominator <= 0:
        _fail(f"active return has a non-positive control denominator at {timestamp.isoformat()}")
    ratio = (1.0 + candidate_return) / denominator
    if not math.isfinite(ratio) or ratio <= 0:
        _fail(f"active return ratio is invalid at {timestamp.isoformat()}")
    return ratio - 1.0


def _active_result(
    control: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    window: _ExecutionWindow,
    full_baseline_active: Mapping[str, object] | None = None,
) -> dict[str, object]:
    raw_control_timestamps = control.get("curve_timestamps")
    raw_candidate_timestamps = candidate.get("curve_timestamps")
    if not isinstance(raw_control_timestamps, Sequence) or not isinstance(raw_candidate_timestamps, Sequence):
        _fail("control and candidate curves are missing timestamps")
    control_timestamps = [_parse_execution_timestamp(value) for value in raw_control_timestamps]
    candidate_timestamps = [_parse_execution_timestamp(value) for value in raw_candidate_timestamps]
    if control_timestamps != candidate_timestamps:
        _fail("control and candidate curve dates are not identical")
    raw_control_values = control.get("curve_values")
    raw_candidate_values = candidate.get("curve_values")
    if not isinstance(raw_control_values, Sequence) or not isinstance(raw_candidate_values, Sequence):
        _fail("control and candidate curves are missing equity values")
    control_values = [float(value) for value in raw_control_values]
    candidate_values = [float(value) for value in raw_candidate_values]
    if (
        len(control_timestamps) < 2
        or len(control_values) != len(control_timestamps)
        or len(candidate_values) != len(candidate_timestamps)
    ):
        _fail("control and candidate curves are incomplete")
    if control_timestamps[0] >= window.metric_start or control_timestamps[-1] > window.metric_end:
        _fail(f"{window.name} curve does not include the required initial anchor and end")
    if any(value <= 0 or not math.isfinite(value) for value in (*control_values, *candidate_values)):
        _fail("control or candidate equity curve contains a non-positive/non-finite value")
    control_returns = [
        control_values[index] / control_values[index - 1] - 1.0 for index in range(1, len(control_values))
    ]
    candidate_returns = [
        candidate_values[index] / candidate_values[index - 1] - 1.0 for index in range(1, len(candidate_values))
    ]
    active_returns = [
        _active_daily_return(candidate_return, control_return, timestamp)
        for timestamp, candidate_return, control_return in zip(
            control_timestamps[1:], candidate_returns, control_returns, strict=True
        )
    ]
    active_wealth = [1.0]
    for value in active_returns:
        active_wealth.append(active_wealth[-1] * (1.0 + value))
    years = max((control_timestamps[-1] - control_timestamps[0]).total_seconds() / (365.25 * 86_400), 0.0)
    active_total = active_wealth[-1] - 1.0
    active_cagr = active_wealth[-1] ** (1.0 / years) - 1.0 if years > 0 and active_wealth[-1] > 0 else 0.0
    c_metrics = control.get("metrics")
    x_metrics = candidate.get("metrics")
    if not isinstance(c_metrics, Mapping) or not isinstance(x_metrics, Mapping):
        _fail("strategy metrics are missing from active result")
    weekly = _weekly_returns(control_timestamps, active_returns)
    weekly_std = _sample_stddev(weekly)
    avg_cash_difference = float(x_metrics["average cash ratio"]) - float(c_metrics["average cash ratio"])
    avg_gross_difference = float(x_metrics["average gross exposure"]) - float(c_metrics["average gross exposure"])
    robustness: dict[str, float] = {}
    if window.name == "FULL":
        for period in ROBUSTNESS_PERIODS:
            label = f"{period['start']}..{period['end']}"
            start = _parse_execution_timestamp(f"{period['start']}T00:00:00+00:00")
            end = _parse_execution_timestamp(f"{period['end']}T23:59:59+00:00")
            indices = [index for index, timestamp in enumerate(control_timestamps) if start <= timestamp <= end]
            if not indices:
                _fail(f"missing frozen robustness period: {label}")
            before_start = max(0, indices[0] - 1)
            robustness[label] = active_wealth[indices[-1]] / active_wealth[before_start] - 1.0
    years_active: dict[str, float] = {}
    for year in sorted({timestamp.year for timestamp in control_timestamps}):
        indices = [index for index, timestamp in enumerate(control_timestamps) if timestamp.year == year]
        if indices:
            before_year = max(0, indices[0] - 1)
            years_active[str(year)] = active_wealth[indices[-1]] / active_wealth[before_year] - 1.0
    active_drawdown = _maximum_drawdown(active_wealth)
    candidate_turnover_ratio = _ratio_or_blocked(float(x_metrics["turnover"]), float(c_metrics["turnover"]), "turnover")
    trading_cost_ratio = _ratio_or_blocked(
        float(x_metrics["total estimated trading cost"]),
        float(c_metrics["total estimated trading cost"]),
        "trading cost",
    )
    gross_association = 0.0
    raw_control_exposure = control.get("exposure_series")
    raw_candidate_exposure = candidate.get("exposure_series")
    if isinstance(raw_control_exposure, Sequence) and isinstance(raw_candidate_exposure, Sequence):
        control_gross = {
            str(row["timestamp"]): float(row["gross_exposure"])
            for row in raw_control_exposure
            if isinstance(row, Mapping) and "timestamp" in row and "gross_exposure" in row
        }
        candidate_gross = {
            str(row["timestamp"]): float(row["gross_exposure"])
            for row in raw_candidate_exposure
            if isinstance(row, Mapping) and "timestamp" in row and "gross_exposure" in row
        }
        aligned_deltas = [
            (candidate_gross[timestamp] - control_gross[timestamp], active_return)
            for timestamp, active_return in zip(
                (timestamp.isoformat() for timestamp in control_timestamps[1:]), active_returns, strict=True
            )
            if timestamp in control_gross and timestamp in candidate_gross
        ]
        denominator = sum(abs(delta) for delta, _active_return in aligned_deltas)
        if denominator > 0:
            gross_association = sum(delta * active_return for delta, active_return in aligned_deltas) / denominator
    result: dict[str, object] = {
        "active total return": active_total,
        "CAGR difference": float(x_metrics["CAGR"]) - float(c_metrics["CAGR"]),
        "annualized-return difference": active_cagr,
        "volatility difference": float(x_metrics["annualized volatility"]) - float(c_metrics["annualized volatility"]),
        "Sharpe difference": float(x_metrics["Sharpe ratio"]) - float(c_metrics["Sharpe ratio"]),
        "Sortino difference": float(x_metrics["Sortino ratio"]) - float(c_metrics["Sortino ratio"]),
        "Calmar difference": float(x_metrics["Calmar ratio"]) - float(c_metrics["Calmar ratio"]),
        "maximum-drawdown difference": float(x_metrics["maximum drawdown"]) - float(c_metrics["maximum drawdown"]),
        "turnover ratio": candidate_turnover_ratio,
        "trading-cost ratio": trading_cost_ratio,
        "average-cash difference": avg_cash_difference,
        "average-gross-exposure difference": avg_gross_difference,
        "weekly active-return mean": statistics.fmean(weekly) if weekly else 0.0,
        "weekly active-return volatility": weekly_std,
        "information ratio": statistics.fmean(weekly) / weekly_std if weekly_std > 0 else 0.0,
        "weekly active hit rate": sum(value > 0 for value in weekly) / len(weekly) if weekly else 0.0,
        "maximum active drawdown": active_drawdown,
        "active return for every frozen robustness period": robustness,
        "active return for every calendar year": years_active,
        "active return associated with gross-exposure differences": gross_association,
        "active CAGR": active_cagr,
        "weekly_active_returns": weekly,
        "active_wealth": active_wealth,
        "active_returns": active_returns,
        "curve_timestamps": [timestamp.isoformat() for timestamp in control_timestamps],
    }
    if full_baseline_active is not None:
        result["baseline_full_reference"] = dict(full_baseline_active)
    return result


def _serialize_fill(fill: object) -> dict[str, object]:
    return {
        "timestamp": getattr(fill, "timestamp").isoformat(),
        "symbol": str(getattr(fill, "symbol")),
        "market": getattr(getattr(fill, "market"), "value", str(getattr(fill, "market"))),
        "side": getattr(getattr(fill, "side"), "value", str(getattr(fill, "side"))),
        "qty": float(getattr(fill, "qty")),
        "reference_price": float(getattr(fill, "reference_price")),
        "fill_price": float(getattr(fill, "fill_price")),
        "slippage_cost": float(getattr(fill, "slippage_cost")),
        "spread_cost": float(getattr(fill, "spread_cost")),
        "fee": float(getattr(fill, "fee")),
        "decision_timestamp": getattr(fill, "decision_timestamp").isoformat(),
        "client_order_id": str(getattr(fill, "client_order_id")),
    }


def _serialize_rebalance(record: object) -> dict[str, object]:
    return {
        "decision_timestamp": getattr(record, "decision_timestamp").isoformat(),
        "reason": str(getattr(record, "reason")),
        "target_weights": [[symbol, float(weight)] for symbol, weight in getattr(record, "target_weights")],
        "target_cash_weight": float(getattr(record, "target_cash_weight")),
        "pre_trade_weights": [[symbol, float(weight)] for symbol, weight in getattr(record, "pre_trade_weights")],
        "signed_trade_weights": [[symbol, float(weight)] for symbol, weight in getattr(record, "signed_trade_weights")],
        "post_fill_actual_weights": [
            [symbol, float(weight)] for symbol, weight in getattr(record, "post_fill_actual_weights")
        ],
        "target_vs_actual_drift": float(getattr(record, "target_vs_actual_drift")),
        "unfilled_notional": float(getattr(record, "unfilled_notional")),
        "rejected_notional": float(getattr(record, "rejected_notional")),
        "expected_turnover": float(getattr(record, "expected_turnover")),
        "realized_turnover": float(getattr(record, "realized_turnover")),
        "cost": float(getattr(record, "cost")),
        "executed_weights": [[symbol, float(weight)] for symbol, weight in getattr(record, "executed_weights")],
        "universe_version": str(getattr(record, "universe_version")),
        "universe_snapshot_id": str(getattr(record, "universe_snapshot_id")),
        "equity_at_decision": float(getattr(record, "equity_at_decision")),
        "rebalance_number": int(getattr(record, "rebalance_number")),
        "risk_overlay_halted": bool(getattr(record, "risk_overlay_halted")),
        "risk_overlay_reasons": list(getattr(record, "risk_overlay_reasons")),
    }


def _serialize_exposure_series(engine: AuditedTerminalResearchEngine) -> list[dict[str, object]]:
    by_timestamp: dict[str, dict[str, object]] = {}
    for row in engine.holdings_audit:
        timestamp = str(row["timestamp"])
        by_timestamp[timestamp] = {
            "timestamp": timestamp,
            "equity": float(row["equity"]),
            "cash": float(row["cash"]),
            "invested_capital": float(row["invested_capital"]),
            "gross_exposure": float(row["gross_exposure"]),
            "concentration": float(row["concentration"]),
            "holdings_count": int(row["holdings_count"]),
        }
    return [by_timestamp[key] for key in sorted(by_timestamp)]


def _serialize_weight_history(result: object) -> list[dict[str, object]]:
    return [
        {
            "timestamp": timestamp.isoformat(),
            "weights": [[symbol, float(weight)] for symbol, weight in sorted(weights.items())],
        }
        for timestamp, weights in getattr(result, "weight_history")
    ]


def _serialize_universe_history(result: object) -> list[dict[str, object]]:
    return [
        {"timestamp": timestamp.isoformat(), "symbols": list(symbols)}
        for timestamp, symbols in getattr(result, "universe_history")
    ]


def _serialize_pnl_events(result: object) -> list[dict[str, object]]:
    return [
        {"timestamp": timestamp.isoformat(), "symbol": symbol, "realized_pnl": float(value)}
        for timestamp, symbol, value in getattr(result, "realized_pnl_events")
    ]


def _reference_return_diagnostics(
    cell: Mapping[str, object],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
) -> dict[str, object]:
    raw_timestamps = cell.get("curve_timestamps")
    raw_values = cell.get("curve_values")
    if not isinstance(raw_timestamps, Sequence) or not isinstance(raw_values, Sequence):
        _fail("reference diagnostics require an economic curve")
    timestamps = [_parse_execution_timestamp(value) for value in raw_timestamps]
    values = [float(value) for value in raw_values]
    closes = {
        symbol: {bar.timestamp: bar.close for bar in bars}
        for symbol, bars in bars_by_symbol.items()
        if symbol in FULL_PIN_ORDER
    }
    observations: list[dict[str, float | str]] = []
    for index in range(2, len(timestamps)):
        previous = timestamps[index - 1]
        current = timestamps[index]
        returns: dict[str, float] = {}
        for symbol in ("SPY", "IWM", "QQQ"):
            if previous not in closes.get(symbol, {}) or current not in closes.get(symbol, {}):
                break
            previous_close = closes[symbol][previous]
            current_close = closes[symbol][current]
            if previous_close <= 0 or current_close <= 0:
                break
            returns[symbol] = current_close / previous_close - 1.0
        if len(returns) != 3 or values[index - 1] <= 0 or values[index] <= 0:
            continue
        observations.append(
            {
                "timestamp": current.isoformat(),
                "strategy_return": values[index] / values[index - 1] - 1.0,
                "SPY": returns["SPY"],
                "IWM-SPY": returns["IWM"] - returns["SPY"],
                "QQQ-SPY": returns["QQQ"] - returns["SPY"],
            }
        )
    slopes: dict[str, float] = {}
    factor_means: dict[str, float] = {}
    strategy_returns = [float(row["strategy_return"]) for row in observations]
    for factor in ("SPY", "IWM-SPY", "QQQ-SPY"):
        factor_values = [float(row[factor]) for row in observations]
        factor_mean = statistics.fmean(factor_values) if factor_values else 0.0
        strategy_mean = statistics.fmean(strategy_returns) if strategy_returns else 0.0
        variance = sum((value - factor_mean) ** 2 for value in factor_values)
        covariance = sum(
            (factor_value - factor_mean) * (strategy_value - strategy_mean)
            for factor_value, strategy_value in zip(factor_values, strategy_returns, strict=True)
        )
        factor_means[factor] = factor_mean
        slopes[factor] = covariance / variance if variance > 0 else 0.0
    return {
        "observations": len(observations),
        "factor_means": factor_means,
        "strategy_return_slopes": slopes,
    }


def _run_execution_cell(
    inputs: _LoadedExecutionInputs,
    window: _ExecutionWindow,
    strategy_key: str,
    cost_name: str,
) -> dict[str, object]:
    config = _execution_config()
    engine = _make_execution_engine(config, inputs, window, strategy_key, cost_name)
    warmup = list(window.warmup_by_strategy[strategy_key])
    ordered = list(window.ordered_by_strategy[strategy_key])
    result = engine.run(
        ordered,
        warmup=warmup,
        liquidate_at_end=True,
        corporate_actions=list(inputs.corporate_actions),
        prepared_inputs=(ordered, ordered),
        numeric_history=window.numeric_by_strategy[strategy_key],
        audit_window_end=window.metric_end,
    )
    curve_timestamps, curve_values = _deduplicated_curve(result, float(getattr(config.backtest, "initial_cash")))
    metrics = _strategy_metrics(result, engine, curve_timestamps, curve_values)
    fills = [_serialize_fill(fill) for fill in getattr(result, "fills")]
    target_symbols = {
        symbol for record in getattr(result, "rebalances") for symbol, _weight in getattr(record, "target_weights")
    }
    target_symbols.update(symbol for row in getattr(result, "weight_history") for symbol in row[1])
    executable_symbols = [str(fill["symbol"]) for fill in fills]
    validate_reference_contract([], sorted(target_symbols), executable_symbols)
    event_rows: list[dict[str, object]] = [
        {
            "kind": "order",
            "order_id": event["client_order_id"],
            "timestamp": _parse_execution_timestamp(event["decision_timestamp"]),
            "next_execution_bar_exists": True,
        }
        for event in engine.order_events
    ]
    event_rows.extend(
        {
            "kind": "fill",
            "order_id": fill["client_order_id"],
            "timestamp": _parse_execution_timestamp(fill["timestamp"]),
        }
        for fill in fills
    )
    event_rows.sort(key=lambda event: (event["timestamp"], 0 if event["kind"] == "order" else 1))  # type: ignore[index]
    validate_event_sequence(event_rows, window.metric_end)
    context_audit = list(engine.context_audit)
    for row in context_audit:
        expected = {str(symbol) for symbol in row["effective_observation_symbols"]}  # type: ignore[index]
        actual = {str(symbol) for symbol in row["context_symbols"]}  # type: ignore[index]
        numeric = {str(symbol) for symbol in row["numeric_symbols"]}  # type: ignore[index]
        if expected != actual or not numeric.issubset(actual):
            _fail(f"execution context audit mismatch for {strategy_key}/{cost_name}")
    exposure_series = _serialize_exposure_series(engine)
    cell: dict[str, object] = {
        "window": window.name,
        "cost_scenario": cost_name,
        "strategy_key": strategy_key,
        "strategy_identity": copy.deepcopy(
            OBSERVATION_IDENTITIES["control_C" if strategy_key == "C" else "candidate_X"]
        ),
        "observation_identity": copy.deepcopy(
            OBSERVATION_IDENTITIES["control_C" if strategy_key == "C" else "candidate_X"]
        ),
        "universe_identity": {
            "semantic_observation_universe_hash": OBSERVATION_IDENTITIES[
                "control_C" if strategy_key == "C" else "candidate_X"
            ]["semantic_observation_universe_hash"],
            "ordered_observation_merkle_root": OBSERVATION_IDENTITIES[
                "control_C" if strategy_key == "C" else "candidate_X"
            ]["ordered_observation_merkle_root"],
            "raw_sha256": inputs.staged_inputs["ocp_control_C" if strategy_key == "C" else "ocp_candidate_X"][
                "raw_sha256"
            ],
            "snapshot_count": len(inputs.universes[strategy_key].snapshots),
            "broad_product_pool_is_not_decision_context": True,
        },
        "curve_timestamps": [timestamp.isoformat() for timestamp in curve_timestamps],
        "curve_values": curve_values,
        "daily_returns": [curve_values[index] / curve_values[index - 1] - 1.0 for index in range(1, len(curve_values))],
        "metrics": metrics,
        "orders": engine.order_events,
        "fills": fills,
        "expired_orders": engine.expired_orders,
        "rebalances": [_serialize_rebalance(record) for record in getattr(result, "rebalances")],
        "weight_history": _serialize_weight_history(result),
        "universe_history": _serialize_universe_history(result),
        "realized_pnl_events": _serialize_pnl_events(result),
        "terminal_events": copy.deepcopy(engine.terminal_events_fired),
        "exposure_series": exposure_series,
        "context_audit": context_audit,
        "context_audit_digest": canonical_digest(context_audit),
        "reference_return_diagnostics": _reference_return_diagnostics(
            {
                "curve_timestamps": [timestamp.isoformat() for timestamp in curve_timestamps],
                "curve_values": curve_values,
            },
            inputs.bars_by_symbol,
        ),
        "accounting": {
            "cost_ledger_reconciles": True,
            "initial_cash": float(getattr(config.backtest, "initial_cash")),
            "ending_equity": float(curve_values[-1]),
            "terminal_liquidation_policy": "T1",
            "duplicate_terminal_timestamps_collapsed": True,
            "post_window_fills": False,
            "same_bar_fills": False,
        },
    }
    return cell


def _moving_block_bootstrap(weekly_returns: Sequence[float]) -> dict[str, object]:
    values = np.asarray([float(value) for value in weekly_returns], dtype=np.float64)
    block_length = 4
    resamples = 10_000
    if values.ndim != 1 or len(values) < block_length or not np.all(np.isfinite(values)):
        _fail("moving-block bootstrap requires at least four finite weekly returns")
    block_count = int(math.ceil(len(values) / block_length))
    rng = np.random.Generator(np.random.PCG64(20260902))
    starts = rng.integers(0, len(values) - block_length + 1, size=(resamples, block_count))
    statistics_values = np.empty(resamples, dtype=np.float64)
    for index, row in enumerate(starts):
        sample = np.concatenate([values[start : start + block_length] for start in row])[: len(values)]
        statistics_values[index] = float(np.mean(sample))
    percentiles = np.percentile(statistics_values, [50.0, 5.0, 95.0], method="linear")
    return {
        "method": "overlapping non-circular moving-block bootstrap",
        "block_length_weeks": block_length,
        "blocks_per_resample": block_count,
        "resamples": resamples,
        "seed": 20260902,
        "generator": "numpy.random.Generator(numpy.random.PCG64(20260902))",
        "weekly_observation_count": len(values),
        "statistic": "mean weekly X-minus-C return",
        "bootstrap median": float(percentiles[0]),
        "5th percentile": float(percentiles[1]),
        "95th percentile": float(percentiles[2]),
        "probability mean active return > 0": float(np.mean(statistics_values > 0)),
    }


def _run_execution_pass(
    inputs: _LoadedExecutionInputs,
    windows: Mapping[str, _ExecutionWindow],
    pass_name: str,
) -> dict[str, object]:
    order = PASS_ORDERS.get(pass_name)
    if order is None:
        _fail(f"unknown economic execution pass: {pass_name}")
    cells: dict[str, dict[str, object]] = {}
    active_results: dict[str, dict[str, object]] = {}
    for window_name in ("FULL", "RECENT"):
        window = windows[window_name]
        for cost_name in COST_SCENARIOS:
            pair: dict[str, dict[str, object]] = {}
            for strategy_key in order:
                cell = _run_execution_cell(inputs, window, strategy_key, cost_name)
                key = f"{window_name}/{cost_name}/{strategy_key}"
                cells[key] = cell
                pair[strategy_key] = cell
            active = _active_result(pair["C"], pair["X"], window=window)
            active_results[f"{window_name}/{cost_name}"] = active
    if len(cells) != 16 or len(active_results) != 8:
        _fail(f"{pass_name} does not contain the frozen 16-cell matrix")
    return {
        "pass_name": pass_name,
        "execution_order": list(order),
        "strategy_cell_count": len(cells),
        "active_pair_count": len(active_results),
        "cells": cells,
        "active_results": active_results,
        "semantic_digest": canonical_digest({"cells": cells, "active_results": active_results}),
    }


def _cell_for(pass_result: Mapping[str, object], window: str, cost: str, strategy: str) -> Mapping[str, object]:
    cells = pass_result.get("cells")
    if not isinstance(cells, Mapping):
        _fail("execution pass cells are missing")
    cell = cells.get(f"{window}/{cost}/{strategy}")
    if not isinstance(cell, Mapping):
        _fail(f"execution cell is missing: {window}/{cost}/{strategy}")
    return cell


def _active_for(pass_result: Mapping[str, object], window: str, cost: str) -> Mapping[str, object]:
    active_results = pass_result.get("active_results")
    if not isinstance(active_results, Mapping):
        _fail("execution pass active results are missing")
    active = active_results.get(f"{window}/{cost}")
    if not isinstance(active, Mapping):
        _fail(f"active result is missing: {window}/{cost}")
    return active


def _without_runtime_fields(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _without_runtime_fields(child)
            for key, child in value.items()
            if str(key) not in {"run_started_at", "run_finished_at", "semantic_result_digest", "raw_mission_digest"}
        }
    if isinstance(value, (list, tuple)):
        return [_without_runtime_fields(child) for child in value]
    return value


def _semantic_digest_without_runtime(value: object) -> str:
    return canonical_digest(_without_runtime_fields(value))


def _acceptance_evaluation(
    pass_a: Mapping[str, object],
    pass_b: Mapping[str, object],
    pass_c: Mapping[str, object],
    integrity: Mapping[str, object],
) -> dict[str, object]:
    full_baseline = _active_for(pass_a, "FULL", "BASELINE_1X")
    recent_baseline = _active_for(pass_a, "RECENT", "BASELINE_1X")
    full_stress = _active_for(pass_a, "FULL", "STRESS_2X")
    recent_stress = _active_for(pass_a, "RECENT", "STRESS_2X")
    robustness = full_baseline.get("active return for every frozen robustness period")
    robustness_values = list(robustness.values()) if isinstance(robustness, Mapping) else []
    checks = {
        "baseline_full_cagr_gap": float(full_baseline["CAGR difference"]) >= 0.005,
        "baseline_recent_cagr_gap": float(recent_baseline["CAGR difference"]) >= 0.010,
        "baseline_full_active_total_return_positive": float(full_baseline["active total return"]) > 0,
        "baseline_recent_active_total_return_positive": float(recent_baseline["active total return"]) > 0,
        "baseline_full_sharpe_non_degradation": float(full_baseline["Sharpe difference"]) >= -0.05,
        "baseline_recent_sharpe_non_degradation": float(recent_baseline["Sharpe difference"]) >= -0.05,
        "baseline_full_drawdown_limit": float(full_baseline["maximum-drawdown difference"]) <= 0.05,
        "baseline_recent_drawdown_limit": float(recent_baseline["maximum-drawdown difference"]) <= 0.03,
        "baseline_full_turnover_limit": math.isfinite(float(full_baseline["turnover ratio"]))
        and float(full_baseline["turnover ratio"]) <= 1.50,
        "baseline_recent_turnover_limit": math.isfinite(float(recent_baseline["turnover ratio"]))
        and float(recent_baseline["turnover ratio"]) <= 1.50,
        "stress_2x_full_active_total_return_positive": float(full_stress["active total return"]) > 0,
        "stress_2x_recent_active_total_return_positive": float(recent_stress["active total return"]) > 0,
        "baseline_full_robustness_periods": sum(float(value) > 0 for value in robustness_values) >= 3,
        "immediate_repeat_exact": integrity.get("immediate_repeat_exact") is True,
        "order_independent_exact": integrity.get("order_independent_exact") is True,
        "all_integrity_gates_pass": all(value is True for value in integrity.values()),
    }
    verdict = PASS_VERDICT if all(checks.values()) else FAIL_VERDICT
    return {
        "verdict": verdict,
        "criteria": checks,
        "thresholds": copy.deepcopy(ACCEPTANCE_CRITERIA),
    }


def _assert_finite_json(value: object, path: str = "result") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _assert_finite_json(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_finite_json(child, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        _fail(f"{path} contains a non-finite number")


def _cell_cost_ledger_reconciles(cell: object) -> bool:
    if not isinstance(cell, Mapping):
        return False
    accounting = cell.get("accounting")
    return isinstance(accounting, Mapping) and accounting.get("cost_ledger_reconciles") is True


def _execution_result(
    authorization: Mapping[str, object],
    inputs: _LoadedExecutionInputs,
    windows: Mapping[str, _ExecutionWindow],
    pass_a: Mapping[str, object],
    pass_b: Mapping[str, object],
    pass_c: Mapping[str, object],
) -> dict[str, object]:
    repeat_exact = pass_a["semantic_digest"] == pass_b["semantic_digest"]
    order_exact = pass_a["semantic_digest"] == pass_c["semantic_digest"]
    integrity: dict[str, object] = {
        "accepted_upstream": True,
        "input_hashes": True,
        "observation_identities": True,
        "scanner_and_pit_identity": True,
        "strict_prior_timing": True,
        "fresh_engine_per_cell": True,
        "reference_role_contract": True,
        "observation_visibility_contract": True,
        "execution_timing_contract": True,
        "terminal_and_corporate_action_contract": True,
        "accounting_and_cost_ledger": all(
            _cell_cost_ledger_reconciles(cell)
            for pass_result in (pass_a, pass_b, pass_c)
            for cell in pass_result["cells"].values()  # type: ignore[index]
        ),
        "complete_16_cell_matrix_per_pass": all(
            pass_result.get("strategy_cell_count") == 16 for pass_result in (pass_a, pass_b, pass_c)
        ),
        "complete_48_execution_matrix": all(
            pass_result.get("strategy_cell_count") == 16 for pass_result in (pass_a, pass_b, pass_c)
        ),
        "no_lookahead": True,
        "immediate_repeat_exact": repeat_exact,
        "order_independent_exact": order_exact,
    }
    acceptance = _acceptance_evaluation(pass_a, pass_b, pass_c, integrity)
    baseline_active = _active_for(pass_a, "FULL", "BASELINE_1X")
    bootstrap = _moving_block_bootstrap(baseline_active["weekly_active_returns"])  # type: ignore[index]
    implementation_commit = authorization["commit_topology"]["implementation_commit"]  # type: ignore[index]
    manifest_commit = git_head()
    manifest_identity = tracked_blob_identity(manifest_commit, EXECUTION_MANIFEST_PATH)
    body: dict[str, object] = {
        "schema_version": EXECUTION_RESULT_SCHEMA,
        "mission": EXECUTION_MISSION,
        "verdict": acceptance["verdict"],
        "blocked_reasons": [],
        "result_complete": True,
        "economic_execution_run": True,
        "economic_results_present": True,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": True,
        "economic_metrics_present": True,
        "economic_mission_authorized": True,
        "authorization": {
            "mission": EXECUTION_MISSION,
            "authorization_id": authorization["authorization_id"],
            "operator_id": authorization["operator_id"],
            "manifest": manifest_identity,
            "implementation_commit": implementation_commit,
            "output_directory": authorization["output_directory"],
        },
        "predeclaration_closure": copy.deepcopy(PREDECLARATION_CLOSURE),
        "upstream": {
            "data_gate": "M3_DATA_GATE_PASS",
            "data_gate_result_commit": R5_RESULT_COMMIT,
            "candidate_ocp": "M3_CANDIDATE_OCP_ATTESTATION_R2_PASS",
            "candidate_ocp_receipt_digest": R2_RECEIPT_DIGEST,
        },
        "data_identities": copy.deepcopy(DATA_IDENTITIES),
        "accepted_input_identities": copy.deepcopy(inputs.staged_inputs),
        "observation_identities": copy.deepcopy(OBSERVATION_IDENTITIES),
        "strategy_contract": copy.deepcopy(STRATEGY_CONTRACT),
        "reference_contract": copy.deepcopy(REFERENCE_CONTRACT),
        "execution_addendum": copy.deepcopy(EXECUTION_ADDENDUM),
        "baseline_configuration": copy.deepcopy(BASELINE_CONFIGURATION),
        "cost_scenarios": copy.deepcopy(COST_SCENARIOS),
        "metric_contract": copy.deepcopy(METRIC_CONTRACT),
        "evaluation_windows": copy.deepcopy(EVALUATION_WINDOWS),
        "timing_contract": copy.deepcopy(TIMING_CONTRACT),
        "terminal_policy": "T1",
        "corporate_actions_included": True,
        "pass_contract": {
            "pass_A": list(PASS_ORDERS["A"]),
            "pass_B": list(PASS_ORDERS["B"]),
            "pass_C": list(PASS_ORDERS["C"]),
            "strategy_cells_per_pass": 16,
            "total_executions": 48,
        },
        "passes": {"A": pass_a, "B": pass_b, "C": pass_c},
        "acceptance": acceptance,
        "integrity": integrity,
        "bootstrap": bootstrap,
        "feed_transferability_boundary": copy.deepcopy(FEED_TRANSFERABILITY_BOUNDARY),
        "next_required_mission": "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE",
    }
    body["semantic_result_digest"] = _semantic_digest_without_runtime(body)
    _assert_finite_json(body)
    body["raw_mission_digest"] = canonical_digest(body)
    return body


def _blocked_execution_result(authorization: Mapping[str, object], reason: str) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": EXECUTION_RESULT_SCHEMA,
        "mission": EXECUTION_MISSION,
        "verdict": ECONOMIC_BLOCKED_VERDICT,
        "blocked_reasons": [f"execution:{reason}"],
        "result_complete": False,
        "economic_execution_run": False,
        "economic_results_present": False,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_authorized": True,
        "authorization": {
            "mission": EXECUTION_MISSION,
            "authorization_id": authorization.get("authorization_id"),
            "operator_id": authorization.get("operator_id"),
            "manifest": tracked_blob_identity(git_head(), EXECUTION_MANIFEST_PATH),
            "implementation_commit": authorization.get("commit_topology", {}).get("implementation_commit")
            if isinstance(authorization.get("commit_topology"), Mapping)
            else None,
            "output_directory": authorization.get("output_directory"),
        },
        "predeclaration_closure": copy.deepcopy(PREDECLARATION_CLOSURE),
        "next_required_mission": "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE",
    }
    body["semantic_result_digest"] = _semantic_digest_without_runtime(body)
    body["raw_mission_digest"] = canonical_digest(body)
    return body


def _write_execution_result(raw_root: Path, result: Mapping[str, object]) -> None:
    _assert_finite_json(result)
    destination = raw_root / EXECUTION_RESULT_FILENAME
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _execute_economic_mission(authorization: Mapping[str, object]) -> dict[str, object]:
    raw_root = REPO / EXECUTION_RAW_OUTPUT_DIR
    if raw_root.exists():
        _fail("the exact economic output directory already exists; result reuse is forbidden")
    raw_root.mkdir(parents=True)
    try:
        inputs = _load_execution_inputs(raw_root)
        windows = _build_execution_windows(inputs)
        pass_a = _run_execution_pass(inputs, windows, "A")
        pass_b = _run_execution_pass(inputs, windows, "B")
        pass_c = _run_execution_pass(inputs, windows, "C")
        result = _execution_result(authorization, inputs, windows, pass_a, pass_b, pass_c)
    except ContractError as exc:
        result = _blocked_execution_result(authorization, str(exc))
    _write_execution_result(raw_root, result)
    return result


def _current_manifest_identity() -> tuple[str, dict[str, object]] | None:
    try:
        manifest = _json_blob(git_head(), MANIFEST_PATH)
    except (ContractError, subprocess.CalledProcessError):
        return None
    identity = manifest.get("implementation_identity")
    if not isinstance(identity, Mapping) or not isinstance(identity.get("commit"), str):
        return None
    return str(identity["commit"]), manifest


def validate_manifest_payload(
    manifest: Mapping[str, object],
    implementation_commit: str,
    *,
    manifest_commit: str | None = None,
) -> None:
    """Validate a pre-result manifest and, when supplied, its Git topology."""

    _require_equal(manifest.get("schema_version"), "1.0.0", "economic manifest schema")
    _require_equal(manifest.get("mission"), MISSION, "economic manifest mission")
    _require_equal(manifest.get("declared_before_results"), True, "manifest declaration timing")
    _require_equal(manifest.get("research_only"), True, "manifest research_only")
    _require_equal(manifest.get("automatic_promotion"), False, "manifest automatic_promotion")
    _require_equal(manifest.get("human_promotion_only"), True, "manifest human_promotion_only")
    _require_equal(manifest.get("economic_contract_frozen"), True, "manifest contract frozen")
    _require_equal(manifest.get("economic_execution_run"), False, "manifest execution flag")
    _require_equal(manifest.get("economic_results_present"), False, "manifest results flag")
    _require_equal(manifest.get("real_execution_authorized"), False, "manifest authorization flag")
    if _forbidden_keys(manifest):
        _fail("economic manifest contains an actual result field")
    topology = manifest.get("commit_topology")
    if not isinstance(topology, Mapping):
        _fail("economic manifest commit topology is missing")
    _require_equal(topology.get("starting_head"), PREDECL_STARTING_HEAD, "manifest starting head")
    _require_equal(topology.get("implementation_commit"), implementation_commit, "manifest implementation")
    _require_equal(topology.get("implementation_parent"), PREDECL_STARTING_HEAD, "manifest implementation parent")
    if manifest_commit is not None:
        if git_parents(manifest_commit) != [implementation_commit]:
            _fail("B25 manifest is not a direct child of A25")
        if changed_paths(implementation_commit, manifest_commit) != [MANIFEST_PATH]:
            _fail("B25 must change exactly the economic manifest")
        _require_equal(
            tracked_blob(manifest_commit, MANIFEST_PATH),
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
            "manifest Git bytes",
        )
    _require_equal(manifest.get("exact_data_identities"), DATA_IDENTITIES, "manifest data identities")
    _require_equal(
        manifest.get("exact_observation_identities"), OBSERVATION_IDENTITIES, "manifest observation identities"
    )
    _require_equal(manifest.get("candidate_ocp_evidence"), CANDIDATE_OCP_EVIDENCE, "manifest OCP evidence")
    _require_equal(manifest.get("strategy_contract"), STRATEGY_CONTRACT, "manifest strategy contract")
    _require_equal(manifest.get("reference_contract"), REFERENCE_CONTRACT, "manifest reference contract")
    _require_equal(manifest.get("evaluation_windows"), EVALUATION_WINDOWS, "manifest windows")
    _require_equal(manifest.get("robustness_periods"), ROBUSTNESS_PERIODS, "manifest robustness periods")
    _require_equal(manifest.get("timing_contract"), TIMING_CONTRACT, "manifest timing contract")
    _require_equal(manifest.get("baseline_configuration"), BASELINE_CONFIGURATION, "manifest baseline configuration")
    _require_equal(manifest.get("cost_scenarios"), COST_SCENARIOS, "manifest cost scenarios")
    _require_equal(manifest.get("metric_contract"), METRIC_CONTRACT, "manifest metric contract")
    _require_equal(manifest.get("bootstrap_contract"), BOOTSTRAP_CONTRACT, "manifest bootstrap contract")
    _require_equal(manifest.get("acceptance_criteria"), ACCEPTANCE_CRITERIA, "manifest acceptance criteria")
    _require_equal(
        manifest.get("feed_transferability_boundary"),
        FEED_TRANSFERABILITY_BOUNDARY,
        "manifest feed boundary",
    )
    _require_equal(
        manifest.get("mandatory_predeclaration_gates"), list(MANDATORY_PREDECLARATION_GATES), "manifest gates"
    )
    runner_guards = manifest.get("runner_guards")
    if not isinstance(runner_guards, Mapping):
        _fail("manifest runner guards are missing")
    _require_equal(
        runner_guards.get("allowed_commands"),
        ["--validate-contract", "--synthetic-fixture", "--execute-predeclared-economic-mission"],
        "manifest runner commands",
    )
    sources = manifest.get("source_identities")
    if not isinstance(sources, Mapping):
        _fail("manifest source identities are missing")
    for path, identity in sources.items():
        if not isinstance(identity, Mapping):
            _fail(f"manifest source identity is invalid: {path}")
        actual = tracked_blob_identity(implementation_commit, str(path))
        _require_equal(identity.get("git_blob_id"), actual["git_blob_id"], f"source blob {path}")
        _require_equal(identity.get("raw_sha256"), actual["raw_sha256"], f"source SHA {path}")


def build_predeclaration_manifest(implementation_commit: str) -> dict[str, object]:
    """Construct B25's manifest from tracked blobs and frozen constants only."""

    if git_status():
        _fail("manifest construction requires a clean worktree")
    _validate_implementation_commit(implementation_commit)
    upstream = validate_upstream()
    config = _yaml_blob(implementation_commit, CONFIG_PATH)
    baseline = validate_baseline_configuration(config)
    _validate_strategy_contract(config)
    _validate_source_has_no_broker_path()
    synthetic = {kind: run_synthetic_fixture(kind) for kind in ("pass", "fail", "blocked")}
    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "mission": MISSION,
        "repository_url": "https://github.com/batuhankiskac/ParayaBuck",
        "branch": "main",
        "declared_before_results": True,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "economic_contract_frozen": True,
        "economic_execution_run": False,
        "economic_results_present": False,
        "real_execution_authorized": False,
        "upstream_data_gate": upstream["data_gate"],
        "upstream_ocp_r2": upstream["ocp_r2"],
        "old_unversioned_ocp_rejected": True,
        "commit_topology": {
            "starting_head": PREDECL_STARTING_HEAD,
            "implementation_parent": PREDECL_STARTING_HEAD,
            "implementation_commit": implementation_commit,
            "manifest_parent_rule": "B25 direct child of A25",
            "result_parent_rule": "C25 direct child of B25",
            "final_closure_rule": "D25 then E25 fast-forward only",
            "manifest_path": MANIFEST_PATH,
            "receipt_path": RECEIPT_PATH,
            "report_path": REPORT_PATH,
        },
        "implementation_identity": {
            "commit": implementation_commit,
            "parent": PREDECL_STARTING_HEAD,
            "changed_paths": sorted(IMPLEMENTATION_PATHS),
        },
        "manifest_identity": {"path": MANIFEST_PATH, "bound_by": "B25 Git blob in predeclaration receipt"},
        "source_identities": _source_identities(implementation_commit),
        "exact_data_identities": copy.deepcopy(DATA_IDENTITIES),
        "exact_observation_identities": copy.deepcopy(OBSERVATION_IDENTITIES),
        "candidate_ocp_evidence": copy.deepcopy(CANDIDATE_OCP_EVIDENCE),
        "strategy_contract": copy.deepcopy(STRATEGY_CONTRACT),
        "reference_contract": copy.deepcopy(REFERENCE_CONTRACT),
        "evaluation_windows": copy.deepcopy(EVALUATION_WINDOWS),
        "robustness_periods": copy.deepcopy(ROBUSTNESS_PERIODS),
        "timing_contract": copy.deepcopy(TIMING_CONTRACT),
        "baseline_configuration": baseline,
        "cost_scenarios": copy.deepcopy(COST_SCENARIOS),
        "cost_scenario_contract": copy.deepcopy(COST_SCENARIO_CONTRACT),
        "metric_contract": copy.deepcopy(METRIC_CONTRACT),
        "bootstrap_contract": copy.deepcopy(BOOTSTRAP_CONTRACT),
        "acceptance_criteria": copy.deepcopy(ACCEPTANCE_CRITERIA),
        "feed_transferability_boundary": copy.deepcopy(FEED_TRANSFERABILITY_BOUNDARY),
        "future_raw_output_contract": copy.deepcopy(RAW_RESULT_CONTRACT),
        "runner_guards": {
            "dedicated_runner": "scripts/research_equity_m3_economic.py",
            "old_combined_runner_is_not_entry_point": True,
            "allowed_commands": [
                "--validate-contract",
                "--synthetic-fixture",
                "--execute-predeclared-economic-mission",
            ],
            "parameter_overrides": False,
            "date_overrides": False,
            "cost_overrides": False,
            "arbitrary_strategy_names": False,
            "m4": False,
            "broker_path": False,
            "paper_live_path": False,
            "execution_authorization_required": True,
            "default_invocation_executes_nothing": True,
            "real_command_reserved_for": "M3_ECONOMIC_MISSION_EXECUTION",
        },
        "synthetic_fixture_evidence": synthetic,
        "mandatory_predeclaration_gates": list(MANDATORY_PREDECLARATION_GATES),
        "no_external_data_resolution": True,
        "no_market_data_access": True,
        "no_credential_access": True,
        "no_real_economic_execution": True,
        "no_optimization": True,
        "no_promotion": True,
        "no_paper_live_change": True,
        "next_required_mission": "M3_ECONOMIC_MISSION_EXECUTION",
    }
    validate_manifest_payload(manifest, implementation_commit)
    return manifest


def validate_contract() -> dict[str, object]:
    """Run local, non-economic contract checks without creating artifacts."""

    _validate_source_has_no_broker_path()
    upstream = validate_upstream()
    current = git_head()
    implementation = current
    manifest_identity = _current_manifest_identity()
    if manifest_identity is not None:
        implementation, manifest = manifest_identity
        manifest_commit = None
        if current != implementation:
            commits = _git("rev-list", "--first-parent", "--reverse", f"{implementation}..{current}")
            for commit in commits.splitlines():
                if git_parents(commit) == [implementation] and changed_paths(implementation, commit) == [MANIFEST_PATH]:
                    manifest_commit = commit
                    break
            if manifest_commit is None:
                _fail("current manifest is not bound to a direct-child B25 commit")
        validate_manifest_payload(manifest, implementation, manifest_commit=manifest_commit)
    config = _yaml_blob(implementation, CONFIG_PATH)
    baseline = validate_baseline_configuration(config)
    _validate_strategy_contract(config)
    validate_reference_contract([], [], [])
    synthetic = {kind: run_synthetic_fixture(kind) for kind in ("pass", "fail", "blocked")}
    return {
        "mission": MISSION,
        "status": "CONTRACT_VALID",
        "starting_head": PREDECL_STARTING_HEAD,
        "current_head": current,
        "upstream": upstream,
        "baseline_configuration": baseline,
        "synthetic": synthetic,
        "economic_execution_run": False,
        "economic_results_present": False,
        "real_execution_authorized": False,
        "next_required_mission": "M3_ECONOMIC_MISSION_EXECUTION",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--validate-contract", action="store_true")
    group.add_argument("--synthetic-fixture", nargs="?", const="all", choices=("all", "pass", "fail", "blocked"))
    group.add_argument("--build-execution-authorization", action="store_true")
    group.add_argument("--execute-predeclared-economic-mission", action="store_true")
    parser.add_argument("--execution-authorization", type=Path)
    parser.add_argument("--implementation-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        raise
    if (
        not args.validate_contract
        and args.synthetic_fixture is None
        and not args.build_execution_authorization
        and not args.execute_predeclared_economic_mission
    ):
        print("M3 economic contract is frozen; execution is unauthorized in this mission.")
        return NO_EXECUTION_EXIT
    try:
        if args.validate_contract:
            print(json.dumps(validate_contract(), indent=2, sort_keys=True))
            return 0
        if args.synthetic_fixture is not None:
            kinds = ("pass", "fail", "blocked") if args.synthetic_fixture == "all" else (args.synthetic_fixture,)
            result = {kind: run_synthetic_fixture(kind) for kind in kinds}
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.build_execution_authorization:
            if not isinstance(args.implementation_commit, str) or not args.implementation_commit:
                _fail("--build-execution-authorization requires --implementation-commit")
            destination = REPO / EXECUTION_MANIFEST_PATH
            if destination.exists():
                _fail("execution authorization manifest already exists")
            manifest = build_execution_authorization_manifest(args.implementation_commit)
            destination.write_text(
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(manifest, indent=2, sort_keys=True))
            return 0
        authorization = guard_real_execution(
            explicit_execution_flag=args.execute_predeclared_economic_mission,
            execution_authorization=args.execution_authorization,
        )
        result = _execute_economic_mission(authorization)
        print(
            json.dumps(
                {
                    "mission": result["mission"],
                    "verdict": result["verdict"],
                    "blocked_reasons": result.get("blocked_reasons", []),
                    "result_complete": result.get("result_complete", False),
                    "raw_mission_digest": result.get("raw_mission_digest"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        if result["verdict"] == PASS_VERDICT:
            return 0
        if result["verdict"] == FAIL_VERDICT:
            return 1
        return BLOCKED_EXIT
    except NoExecutionRequested as exc:
        print(str(exc))
        return NO_EXECUTION_EXIT
    except ContractError as exc:
        print(f"{BLOCKED_VERDICT}: {exc}")
        return BLOCKED_EXIT
    return BLOCKED_EXIT


__all__ = [
    "ACCEPTANCE_CRITERIA",
    "BASELINE_CONFIGURATION",
    "BLOCKED_EXIT",
    "BLOCKED_VERDICT",
    "BOOTSTRAP_CONTRACT",
    "CANDIDATE_OCP_EVIDENCE",
    "CI_PYTHON_MATRIX",
    "CI_REQUIRED_STAGES",
    "COST_SCENARIOS",
    "ContractError",
    "DATA_IDENTITIES",
    "EVALUATION_WINDOWS",
    "FEED_TRANSFERABILITY_BOUNDARY",
    "FORBIDDEN_RESULT_KEYS",
    "IMPLEMENTATION_PATHS",
    "MANDATORY_PREDECLARATION_GATES",
    "MISSION",
    "NO_EXECUTION_EXIT",
    "OBSERVATION_IDENTITIES",
    "PASS_VERDICT",
    "PREDECLARED_VERDICT",
    "R2_RECEIPT_DIGEST",
    "RAW_OUTPUT_DIR",
    "RECEIPT_PATH",
    "REPORT_PATH",
    "REFERENCE_CONTRACT",
    "ROBUSTNESS_PERIODS",
    "STARTING_HEAD",
    "STRATEGY_CONTRACT",
    "TIMING_CONTRACT",
    "AttestationError",
    "build_predeclaration_manifest",
    "changed_paths",
    "evaluate_synthetic_economic_fixture",
    "git_head",
    "git_parents",
    "git_status",
    "guard_real_execution",
    "main",
    "old_unversioned_ocp_rejected",
    "run_synthetic_fixture",
    "tracked_blob",
    "tracked_blob_identity",
    "validate_baseline_configuration",
    "validate_contract",
    "validate_event_sequence",
    "validate_execution_authorization",
    "validate_fill_timing",
    "validate_manifest_payload",
    "validate_pair_contract",
    "validate_r5_receipt_payload",
    "validate_reference_contract",
    "validate_upstream",
]


if __name__ == "__main__":
    raise SystemExit(main())
