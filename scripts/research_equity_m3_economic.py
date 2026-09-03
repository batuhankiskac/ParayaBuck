#!/usr/bin/env python3
"""Fail-closed contract runner for the predeclared M3 economic experiment.

This module freezes the future experiment and validates only tracked
provenance plus synthetic fixtures.  It deliberately does not load the
canonical market-data CSV, ignored research outputs, broker clients, or
credentials.  The real execution command is a guarded placeholder for the
later execution mission; it cannot produce an economic result in this
predeclaration mission.

Allowed commands::

    python scripts/research_equity_m3_economic.py --validate-contract
    python scripts/research_equity_m3_economic.py --synthetic-fixture

The real command is intentionally unavailable until a separate execution
authorization artifact and the later mission are supplied.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, NoReturn

import yaml

_REPO_BOOTSTRAP = Path(__file__).resolve().parents[1]
for _path in (_REPO_BOOTSTRAP / "src", _REPO_BOOTSTRAP / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from compact_equity_m3_candidate_ocp_attestation_r2 import (  # noqa: E402
    AttestationError,
    validate_r2_receipt,
)
from m3_data_gate_helpers import canonical_digest  # noqa: E402
from research_equity_m3_m4 import ProxyMultifactorResidualMomentum  # noqa: E402

from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

MISSION = "M3_ECONOMIC_MISSION_PREDECLARATION_R1"
PREDECLARED_VERDICT = "M3_ECONOMIC_MISSION_PREDECLARED"
BLOCKED_VERDICT = "M3_ECONOMIC_PREDECLARATION_BLOCKED"
PASS_VERDICT = "M3_ECONOMIC_GATE_PASS"
FAIL_VERDICT = "M3_ECONOMIC_GATE_FAIL"
ECONOMIC_BLOCKED_VERDICT = "M3_ECONOMIC_GATE_BLOCKED"

STARTING_HEAD = "06aaf78a7ea75aa3d97404916d5107194534fe6c"
R5_RESULT_COMMIT = "3c2f67d31772f2fba118137ad262bd8ffdb737d8"
R5_RECEIPT_DIGEST = "41afb58aca2fb38f4f0d301fc169938809b1cb9afcfa5c9855d97e292916abeb"
R5_MANIFEST_COMMIT = "2f6ce2aaf347b9e0257d4ae31e9fdcc90cdcafe4"
R2_RECEIPT_DIGEST = "8a576e650060c27e7f4a0c6f0fd8a532bc2509df26fd58c0587656ad47f766b6"
R2_CORRECTIVE_CI_RUN = "33678608824"
R2_CORRECTIVE_CI_HEAD = "154ffd6925327ea6283903f3f1c1098992efef3e"
R2_IMPLEMENTATION_COMMIT = "6e8067dfa816d2de138004babf5d5f47ab327642"
R2_MANIFEST_COMMIT = "4ebb5fa2353eba43794b48fc5b00a6cfe0cd902"
R2_RESULT_COMMIT = "154ffd6925327ea6283903f3f1c1098992efef3e"
E24_COMMIT = STARTING_HEAD
E24_PARENT = "f05056198f2519cfd396b1b05d3779bd7e7c2ace"

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
    if parents != [STARTING_HEAD]:
        _fail("A25 implementation must be a direct child of E24")
    if sorted(changed_paths(STARTING_HEAD, implementation_commit)) != sorted(IMPLEMENTATION_PATHS):
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
    references = set(REFERENCE_CONTRACT["full_pin_order"])
    for label, values in (
        ("candidate membership", candidate_membership),
        ("target symbols", target_symbols),
        ("executable symbols", executable_symbols),
    ):
        overlap = references.intersection(values)
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


def validate_execution_authorization(path: Path) -> dict[str, object]:
    if not path.is_file():
        _fail("execution authorization artifact is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("execution authorization artifact is invalid") from exc
    if not isinstance(value, dict):
        _fail("execution authorization artifact must be an object")
    _require_equal(value.get("mission"), "M3_ECONOMIC_MISSION_EXECUTION", "authorization mission")
    _require_equal(value.get("real_execution_authorized"), True, "authorization flag")
    _require_equal(value.get("human_approval"), True, "human authorization")
    return value


def guard_real_execution(
    *,
    explicit_execution_flag: bool,
    execution_authorization: Path | None,
) -> NoReturn:
    """Validate the future command boundary, then stop in this mission."""

    if not explicit_execution_flag:
        _fail("real execution requires the explicit execution flag")
    if execution_authorization is None:
        _fail("real execution requires --execution-authorization")
    validate_execution_authorization(execution_authorization)
    _fail("real economic execution is reserved for M3_ECONOMIC_MISSION_EXECUTION")


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
    _require_equal(topology.get("starting_head"), STARTING_HEAD, "manifest starting head")
    _require_equal(topology.get("implementation_commit"), implementation_commit, "manifest implementation")
    _require_equal(topology.get("implementation_parent"), STARTING_HEAD, "manifest implementation parent")
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
            "starting_head": STARTING_HEAD,
            "implementation_parent": STARTING_HEAD,
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
            "parent": STARTING_HEAD,
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
        "starting_head": STARTING_HEAD,
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
    group.add_argument("--execute-predeclared-economic-mission", action="store_true")
    parser.add_argument("--execution-authorization", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        raise
    if not args.validate_contract and args.synthetic_fixture is None and not args.execute_predeclared_economic_mission:
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
        guard_real_execution(
            explicit_execution_flag=args.execute_predeclared_economic_mission,
            execution_authorization=args.execution_authorization,
        )
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
