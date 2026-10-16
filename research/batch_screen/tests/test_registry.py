"""The trial ledger: append-only bookkeeping, the commit gate, and the planner's refusal."""

from __future__ import annotations

import json

import pytest

from research.batch_screen import api, registry, run


def test_append_is_append_only(tmp_path):
    p = tmp_path / "REGISTRY.jsonl"
    for i in range(3):
        registry.append({"arm": f"a{i}", "batch": "b"}, path=p)
    lines = registry.read(p)
    assert [e["arm"] for e in lines] == ["a0", "a1", "a2"]
    registry.append({"arm": "a3", "batch": "b"}, path=p)
    assert registry.read(p)[:3] == lines
    assert registry.n_trials(p) == 4
    # the previous bytes are untouched
    assert json.loads(p.read_text().splitlines()[0])["arm"] == "a0"


def test_ledger_queries(tmp_path):
    p = tmp_path / "REGISTRY.jsonl"
    registry.append(registry.entry_for("idea", "arm", {"p": 1}, api.spec_hash({"p": 1}), "deadbeef",
                                       "b1", "screened", {"head": "x"}), path=p)
    assert registry.is_rerun("idea", "arm", api.spec_hash({"p": 1}), "deadbeef", p)
    assert not registry.is_rerun("idea", "arm", api.spec_hash({"p": 2}), "deadbeef", p)
    assert not registry.is_rerun("idea", "other", api.spec_hash({"p": 1}), "deadbeef", p)
    assert len(registry.history("idea", "arm", p)) == 1
    assert registry.ledger_arms(p)[0]["arm_key"] == "idea/arm"


def test_require_committed_compares_head_bytes(tmp_path, monkeypatch):
    """The gate is byte equality between the working tree and HEAD."""
    p = api.idea_path("k_control")
    real = p.read_bytes()
    monkeypatch.setattr(registry, "committed_bytes", lambda path: real)
    assert registry.require_committed("k_control", p)["head"]
    monkeypatch.setattr(registry, "committed_bytes", lambda path: real + b"# edited\n")
    with pytest.raises(registry.RegistryError, match="differs from its committed version"):
        registry.require_committed("k_control", p)
    monkeypatch.setattr(registry, "committed_bytes", lambda path: None)
    with pytest.raises(registry.RegistryError, match="not committed at HEAD"):
        registry.require_committed("k_control", p)


def test_require_committed_rejects_a_real_untracked_file():
    """Against real git: a module that is not in HEAD is refused."""
    idea = api.IDEAS_DIR / "_tmp_gate_untracked.py"
    idea.write_text("ARMS = {}\n\n\ndef targets(data, spec):\n    return 0\n")
    try:
        assert registry.committed_bytes(idea) is None
        with pytest.raises(registry.RegistryError, match="not committed at HEAD"):
            registry.require_committed("_tmp_gate_untracked", idea)
    finally:
        idea.unlink()


def test_plan_refuses_an_uncommitted_idea(tmp_path, monkeypatch):
    """The runner refuses to screen an arm whose module is not committed (pre-registration)."""
    monkeypatch.setattr(registry, "REGISTRY", tmp_path / "REGISTRY.jsonl")
    idea = api.IDEAS_DIR / "_tmp_gate_probe.py"
    idea.write_text("ARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return 0\n")
    try:
        with pytest.raises(registry.RegistryError):
            run.plan(["_tmp_gate_probe"], "batch_gate_probe")
        # with the gate explicitly skipped (a smoke path) the arm plans fine
        arms, skipped = run.plan(["_tmp_gate_probe"], "batch_gate_probe", allow_dirty=True)
        assert [a["id"] for a in arms] == ["_tmp_gate_probe/a"] and not skipped
    finally:
        idea.unlink()
    lines = registry.read()
    assert [e["arm_key"] for e in lines] == ["_tmp_gate_probe/a"]
    assert lines[0]["git"]["commit_gate"] == "skipped"


def test_plan_registers_arms_and_needs_kernel(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY", tmp_path / "REGISTRY.jsonl")
    idea = api.IDEAS_DIR / "_tmp_plan_probe.py"
    idea.write_text(
        "ARMS = {'ok': {'p': 1}, 'later': {'needs_kernel': 'a second leg'}}\n\n\n"
        "def targets(data, spec):\n    return 0\n"
    )
    try:
        arms, skipped = run.plan(["_tmp_plan_probe"], "batch_plan_probe", allow_dirty=True)
        code = api.code_hash("_tmp_plan_probe")
    finally:
        idea.unlink()
    assert [a["id"] for a in arms] == ["_tmp_plan_probe/ok"]
    assert skipped and skipped[0][0] == "_tmp_plan_probe/later" and skipped[0][1] == "needs_kernel"
    lines = registry.read()
    assert len(lines) == 2
    by_arm = {e["arm"]: e for e in lines}
    assert by_arm["ok"]["status"] == "screened" and by_arm["later"]["status"] == "needs_kernel"
    assert by_arm["ok"]["spec_hash"] == api.spec_hash({"p": 1})
    assert by_arm["ok"]["code_hash"] == code
    assert registry.n_trials() == 2  # needs_kernel still counts as a declared trial


def test_plan_refuses_a_cash_yield_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY", tmp_path / "REGISTRY.jsonl")
    idea = api.IDEAS_DIR / "_tmp_cash_probe.py"
    idea.write_text("ARMS = {'a': {'cash_yield': 'tbill'}}\n\n\ndef targets(data, spec):\n    return 0\n")
    try:
        with pytest.raises(SystemExit, match="cash_yield"):
            run.plan(["_tmp_cash_probe"], "batch_cash_probe", cash_yield="none", allow_dirty=True)
    finally:
        idea.unlink()


def test_rnd_and_dump_are_deterministic(tmp_path):
    payload = {"b": [1.0000004999, 2.5, float("nan")], "a": {"x": 1}}
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    run.dump(one, payload)
    run.dump(two, payload)
    assert one.read_bytes() == two.read_bytes()
    assert list(json.loads(one.read_text())) == ["a", "b"]  # sorted keys
    assert json.loads(one.read_text())["b"][0] == 1.0      # rounded to 6 dp
