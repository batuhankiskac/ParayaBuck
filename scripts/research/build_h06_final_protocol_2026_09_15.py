#!/usr/bin/env python3
"""Create the outcome-free frozen H06 protocol and sealed one-shot base."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "data/research/local/h06_population_2026_09_15/h06_population.sqlite3"
STRUCTURAL = REPO / "data/research_manifests/h06_full_structural_admission_2026_09_14.json"
PROTOCOL = REPO / "data/research_manifests/h06_final_economic_protocol_2026_09_15.json"
STATE = REPO / "data/research_manifests/h06_final_one_shot_state_2026_09_15.json"
DOC = REPO / "docs/research/h06_final_economic_protocol_2026_09_15.md"
RUNNER = REPO / "scripts/research/h06_final_economic_2026_09_15.py"
VERIFIER = REPO / "scripts/research/verify_h06_final_economic_2026_09_15.py"
CALENDAR = REPO / "data/research/r46_treasury_auction_unwind_tlt_v1_2026_09_10/nyse_calendar.jsonl"
BASE = "b9a136cb9d5bce725ba1e7b416653794c30be3c6"
C_ENRICH = "890f79281188ca225acc04220d249cbf90ed1341"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def self_hash(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.setdefault("integrity", {}).pop("self_sha256", None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value.setdefault("integrity", {})["self_sha256"] = self_hash(value)
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decisions() -> list[dict[str, Any]]:
    connection = sqlite3.connect(DB)
    rows = []
    for month, session, timestamp, prior in connection.execute(
        "SELECT calendar_month,decision_session,decision_timestamp,prior_21_sessions FROM monthly_decisions ORDER BY decision_session"
    ):
        rows.append({"calendar_month": month, "decision_session": session, "decision_timestamp": timestamp,
                     "prior_21_sessions": json.loads(prior)})
    if len(rows) != 55:
        raise ValueError("monthly_decision_count_mismatch")
    return rows


def build() -> None:
    structural = json.loads(STRUCTURAL.read_text(encoding="utf-8"))
    if structural["status"] != "H06_FULL_STRUCTURAL_ADMISSION_READY":
        raise ValueError("structural_not_ready")
    protocol = seal({
        "schema": "h06-final-economic-protocol-v1", "status": "H06_FINAL_ECONOMIC_PROTOCOL_SEALED",
        "candidate": "H06", "source_commit": BASE, "structural_commit": C_ENRICH,
        "structural_manifest_sha256": sha256(STRUCTURAL),
        "continuous_path": ["2022-01-01", "2026-07-31"],
        "windows": {"full": ["2022-01-01", "2025-12-31"], "recent": ["2024-01-01", "2025-12-31"],
                    "true_holdout": ["2026-01-01", "2026-07-31"]},
        "score": "sum unique admitted open-market non-derivative direct purchase shares*price in prior 21 sessions / exact raw market cap",
        "economic_transaction_identity": "sha256(accession|'|'|source_transaction_id), owner CIK excluded, counted once per decision",
        "ranking": {"direction": "highest positive decile", "k": "max(1,ceil(N/10)) when N>0",
                    "tie_break": ["score descending", "issuer CIK ascending", "symbol ascending"], "ties_expand": False},
        "holding": {"entry": "next supported regular-session open", "completed_sessions": 63,
                    "exit": "next open after session 63", "reselection": "extend to later expiry", "duplicate_leverage": False},
        "standalone": {"account_equity_budget": 0.55, "equal_weight_active_unique": True, "per_symbol_account_cap": 0.10,
                       "unused_budget": "cash", "starting_nav": 100000},
        "shared_overlay": {"v3_fraction": 0.95, "candidate_fraction": 0.05, "equity_sleeve": 0.55,
                           "candidate_account_budget": 0.0275, "max_positions": 6, "max_symbol_weight": 0.10,
                           "max_equity": 0.55, "max_gross": 0.60, "min_cash": 0.40,
                           "overlap_consumes_slot": False, "blocked_or_excess": "cash",
                           "accounting": "two immutable subledgers: 95% accepted V3 NAV plus 5% capacity-filtered H06 sleeve NAV",
                           "v3_forward_fill_after_coverage": False},
        "execution": {"bars": "canonical V5 split-adjusted", "fills": "next eligible regular-session open",
                      "same_bar_fill": False, "slippage_bps": 2.0, "spread_bps": 2.0, "commission": 0.0,
                      "cost_multipliers": [1, 2, 3], "multiplied_components": ["slippage", "spread"],
                      "regulatory_fees": "repository effective-dated TAF/CAT/SEC schedule, not multiplier-scaled",
                      "cash_dividends": "canonical action rate on ex-date", "final_cutoff": "mark, do not liquidate"},
        "statistics": {"moving_block_bootstrap": {"draws": 10000, "block_sessions": 21, "seed": 20260909,
                                                    "interval": [0.025, 0.975]},
                       "paired_sign_permutation": {"draws": 10000, "seed": 20260910, "sides": ["two-sided", "positive"]},
                       "placebo": {"information_session_shift": 7, "unit": "supported sessions", "diagnostic_only": True}},
        "verdicts": {"insufficient": "no full-window selected position or no true-holdout selected position",
                     "directional_gate_count": 10, "strong": "all directional plus bootstrap lower>0 plus positive p<=0.05",
                     "provisional": "all directional without strong", "reject": "any directional failure"},
        "monthly_decisions": decisions(),
        "input_pins": {"calendar_sha256": sha256(CALENDAR), "runner_sha256": sha256(RUNNER),
                       "verifier_sha256": sha256(VERIFIER)},
        "economic_run": False, "outcomes_accessed": False, "tuning_allowed": False, "rerun_allowed": False,
        "promotion_authorized": False, "paper_live_registry_changes": False, "integrity": {},
    })
    PROTOCOL.write_text(json.dumps(protocol, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    state = seal({
        "schema": "h06-final-one-shot-state-v1", "candidate": "H06", "status": "SEALED_UNUSED",
        "source_commit": BASE, "structural_commit": C_ENRICH, "protocol_sha256": sha256(PROTOCOL),
        "run_count": 0, "rerun_allowed": False, "tuning_allowed": False, "outcomes_accessed": False,
        "reservation_state": "UNRESERVED", "economic_run": False, "integrity": {},
    })
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    DOC.write_text("\n".join([
        "# H06 final economic protocol — 2026-09-15", "", "Status: `H06_FINAL_ECONOMIC_PROTOCOL_SEALED`.", "",
        "This protocol was frozen before any post-decision H06 return was read or calculated.", "",
        "- Signal: unique eligible transaction notional over exact raw market cap, prior 21 NYSE sessions.",
        "- Selection: highest positive decile, deterministic CIK/symbol tie break.",
        "- Holding: next-open entry, 63 completed sessions, next-open exit; reselection only extends expiry.",
        "- Standalone: at most 55% equity and 10% per name; unused budget is cash.",
        "- Shared: 95% unchanged accepted V3 plus 5% H06 relative to the equity sleeve; six positions and account caps remain binding.",
        "- Costs: 1x/2x/3x slippage and spread; effective-dated regulatory fees unchanged.",
        "- Statistics: 10,000 moving-block bootstrap draws (21 sessions, seed 20260909), 10,000 paired sign draws (seed 20260910), fixed +7-session placebo.",
        "- One shot: no tuning, no rerun, no automatic promotion, no PAPER/LIVE/registry change.", "",
        f"Protocol self-hash: `{protocol['integrity']['self_sha256']}`.", "",
    ]), encoding="utf-8")


if __name__ == "__main__":
    build()
