"""Create the committed, non-economic pre-run validation receipt."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.config import load_config

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
EXPECTED_MAIN = "839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de"
EXPECTED_DESIGN = "a822083eef4123d960ee7886d52951bfb20b29bd"
EXPECTED_DATA = {
    "bars": (
        ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
        "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    ),
    "lifetimes": (
        ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv",
        "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    ),
    "actions": (
        ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
        "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
    ),
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    config = load_config()
    branch = git("branch", "--show-current")
    head = git("rev-parse", "HEAD")
    main_sha = git("rev-parse", "main")
    design_sha = git("rev-parse", "research/information-discreteness-design-v1")
    status = git("status", "--porcelain")
    if branch != "research/information-discreteness-economic-v1":
        raise RuntimeError(branch)
    if status:
        raise RuntimeError("Preflight must begin clean: " + status)
    if main_sha != EXPECTED_MAIN or design_sha != EXPECTED_DESIGN:
        raise RuntimeError(f"Unexpected refs main={main_sha} design={design_sha}")
    data = {}
    for name, (path, expected) in EXPECTED_DATA.items():
        actual = sha(path)
        if actual != expected:
            raise RuntimeError(f"{name} hash mismatch: {actual}")
        data[name] = {"path": str(path), "sha256": actual, "expected_sha256": expected}
    artifact_found = any(
        item.name
        not in {
            "FROZEN_INPUT_RECEIPT.json",
            "PRE_RUN_VALIDATION.json",
            "candidate_strategy.py",
            "economic_runner.py",
            "independent_verifier.py",
            "preflight.py",
            "__pycache__",
        }
        for item in OUTPUT.iterdir()
    )
    payload = {
        "artifact": "information_discreteness_economic_v1_pre_run_validation",
        "validated_at": datetime.now(UTC).isoformat(),
        "branch": branch,
        "head": head,
        "main_sha": main_sha,
        "design_branch_sha": design_sha,
        "worktree_clean_before_economic_run": True,
        "paper_identity": config.strategy.equity.name,
        "runtime_mode": config.runtime.mode.value,
        "live_enabled": config.live.enabled,
        "live_hard_dollar_cap": config.live.hard_dollar_cap,
        "candidate": "equity_information_discreteness_continuous_v1",
        "prior_candidate_b_economic_artifact_found": artifact_found,
        "data": data,
        "endpoint": "2026-07-30T04:00:00+00:00",
        "development": ["2017-01-03T04:00:00+00:00", "2021-12-31T04:00:00+00:00"],
        "validation": ["2022-01-03T04:00:00+00:00", "2026-07-30T04:00:00+00:00"],
        "economic_output_started": False,
        "production_or_paper_change_authorized": False,
    }
    if artifact_found:
        raise RuntimeError("Prior Candidate B economic artifact found")
    (OUTPUT / "PRE_RUN_VALIDATION.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
