"""Merge safety: the union attribute on the ledger and its re-verification against git.

A union merge can put lines from several branches side by side; these tests pin the two invariants
the runner relies on -- every line describes code that really was committed at the commit it names,
and every registered arm still exists at HEAD with its latest recorded spec.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from research.batch_screen import api, registry
from research.batch_screen import run as R


def _line(idea: str, arm: str, spec: dict, *, commit: str | None = None, gate: str = "pass",
          code_hash: str | None = None) -> dict:
    path = api.idea_path(idea)
    commit = commit or registry.idea_commit(path)
    return registry.entry_for(idea, arm, spec, api.spec_hash(spec),
                              code_hash or api.code_hash(idea), "merge_test", "screened",
                              {"head": commit, "idea_commit": commit, "commit_gate": gate})


def _spec(arm: str = "K") -> dict:
    return dict(api.load_idea("k_control").ARMS[arm])


def _ledger(tmp_path, *lines) -> str:
    p = tmp_path / "REGISTRY.jsonl"
    p.write_text("".join(json.dumps(ln, sort_keys=True) + "\n" for ln in lines))
    return str(p)


def test_git_is_configured_to_union_merge_the_ledger():
    out = subprocess.run(["git", "check-attr", "merge", "--", registry.rel(registry.REGISTRY)],
                         cwd=registry.ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip().endswith("merge: union")


def test_the_committed_ledger_verifies():
    v = registry.verify_ledger()
    assert v["ok"], v["problems"]
    assert v["lines"] == registry.n_lines() and v["arms"] >= 3 and v["n_trials"] >= 3
    assert v["stale"] == []            # every module is still the one that was screened


def test_a_faithful_line_verifies(tmp_path):
    v = registry.verify_ledger(_ledger(tmp_path, _line("k_control", "K", _spec())))
    assert v["ok"] and v["problems"] == [] and v["skipped"] == []
    assert v["arms"] == 1 and v["n_trials"] == 1


def test_a_tampered_code_hash_is_caught(tmp_path):
    ln = _line("k_control", "K", _spec(), code_hash="0" * 64)
    v = registry.verify_ledger(_ledger(tmp_path, ln))
    assert not v["ok"] and [p["kind"] for p in v["problems"]] == ["code_hash"]
    assert v["problems"][0]["at_commit"] == api.code_hash("k_control")


def test_an_arm_that_is_not_at_head_is_caught(tmp_path):
    v = registry.verify_ledger(_ledger(tmp_path, _line("k_control", "K9", _spec())))
    assert not v["ok"] and [p["kind"] for p in v["problems"]] == ["arm_missing_at_head"]


def test_a_spec_that_drifted_from_the_code_is_caught(tmp_path):
    drifted = dict(_spec())
    drifted["cash_yield"] = "tbill"                      # the module's arm says "none"
    v = registry.verify_ledger(_ledger(tmp_path, _line("k_control", "K", drifted)))
    assert not v["ok"] and [p["kind"] for p in v["problems"]] == ["spec_drift"]
    assert v["problems"][0]["at_head"] == api.spec_hash(_spec())


def test_a_module_that_changed_since_its_screen_is_reported_stale_not_broken(tmp_path, monkeypatch):
    real = registry.committed_bytes
    monkeypatch.setattr(registry, "committed_bytes", lambda path: real(path) + b"\n# later edit\n")
    v = registry.verify_ledger(_ledger(tmp_path, _line("k_control", "K", _spec())))
    assert v["ok"] and v["stale"] == ["k_control/K"]


def test_lines_whose_commit_gate_was_bypassed_are_skipped(tmp_path):
    v = registry.verify_ledger(_ledger(tmp_path, _line("k_control", "K", _spec(), gate="skipped")))
    assert v["ok"] and v["skipped"] == [{"line": 0, "arm_key": "k_control/K",
                                         "why": "commit gate skipped"}]


def test_a_malformed_line_is_reported_not_a_crash(tmp_path):
    # a hand-resolved or duplicated merge block: the runner must say what is wrong, naming the line
    v = registry.verify_ledger(_ledger(tmp_path, {"arm": "A/1", "arm_key": "A/1"},
                                       _line("k_control", "K", _spec())))
    assert not v["ok"]
    assert v["problems"] == [{"line": 0, "arm_key": "A/1", "kind": "malformed_line",
                              "missing": ["idea", "spec_hash", "code_hash", "git"]}]
    assert v["arms"] == 1


def test_run_batch_refuses_to_screen_a_ledger_that_does_not_verify(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY", tmp_path / "REGISTRY.jsonl")
    monkeypatch.setattr(R, "RESULTS", tmp_path / "results")
    registry.append(_line("k_control", "K9", _spec()), path=registry.REGISTRY)
    with pytest.raises(registry.RegistryError, match="does not verify"):
        R.run_batch("merge_test", ["k_control"], allow_dirty=True, stage2=False)
