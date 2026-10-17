"""The smoke runner: `targets` only -- no return, no NAV, no registry write."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from research.gate_power_v2 import api, extra, registry, smoke

HERE = Path(smoke.__file__).resolve().parent
TEMPLATE = HERE / "TEMPLATE_plugin.py"
RESULTS = HERE / "results"


def _stage(tmp_path, name: str, body: str):
    (tmp_path / f"{name}.py").write_text(body)
    return tmp_path


def _tree(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_synthetic_panel_has_the_window_shape():
    p = smoke.synthetic_panel()
    assert (p.P, p.T) == (smoke.SYNTH_P, smoke.SYNTH_T) and p.window == "synthetic"
    assert np.isfinite(p.price).all() and p.vol.shape == p.price.shape
    assert p.high is None and p.days[0] == "2000-01-03"


def test_real_panels_cover_every_kind_of_window():
    panels = smoke.real_panels()
    assert set(panels) == {"main", "oos:M2", "etf:XLK", "boot"}
    assert panels["main"].P == 1 and panels["main"].window == "main"
    assert panels["oos:M2"].window == "M2"
    assert panels["etf:XLK"].high is not None
    assert panels["boot"].P == smoke.BOOT_P and panels["boot"].extra_window == "main"


def test_smoke_reports_ok_not_applicable_and_the_keys_touched(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", _stage(tmp_path, "probe_idea", TEMPLATE.read_text()))
    r = smoke.smoke_arm("probe_idea", "demo", smoke.smoke_panels())
    assert r["status"] == "ok" and r["panels"]["synthetic"]["status"] == "ok"
    assert r["panels"]["main"]["leg"] == "2x"
    assert r["panels"]["main"]["wants_max"] <= 0.98
    assert r["panels"]["boot"]["shape"] == [smoke.BOOT_P, r["panels"]["boot"]["shape"][1]]
    assert r["panels"]["oos:M2"]["status"] == "n/a"          # ^VIX has no 1935-1956 values
    assert list(r["extras"]) == ["vix"] and r["extras"]["vix"]["ffill"]
    assert r["extras"]["vix"]["nan_share"]["oos:M2"] == 1.0
    assert r["extras"]["vix"]["nan_share"]["main"] == 0.0


def test_smoke_calls_only_targets_on_an_exception_free_arm(tmp_path, monkeypatch):
    body = "import numpy as np\nARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return np.zeros((data.P, data.T))\n"
    monkeypatch.setattr(api, "IDEAS_DIR", _stage(tmp_path, "zeros", body))
    r = smoke.smoke_arm("zeros", "a", smoke.smoke_panels())
    assert r["status"] == "ok" and r["extras"] == {}
    assert r["panels"]["main"]["wants_nonzero"] == 0.0


def test_smoke_flags_not_applicable_on_the_main_window(tmp_path, monkeypatch):
    body = ("import numpy as np\nfrom research.gate_power_v2.api import NotApplicable\n"
            "ARMS = {'a': {}}\n\n\ndef targets(data, spec):\n"
            "    raise NotApplicable('nothing to see')\n")
    monkeypatch.setattr(api, "IDEAS_DIR", _stage(tmp_path, "never", body))
    r = smoke.smoke_arm("never", "a", smoke.smoke_panels())
    assert r["status"] == "not_applicable_main"
    assert r["panels"]["main"]["reason"] == "nothing to see"


def test_smoke_captures_a_plugin_bug_and_the_cli_exits_nonzero(tmp_path, monkeypatch):
    body = "import numpy as np\nARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return data.price[:, :-1]\n"
    monkeypatch.setattr(api, "IDEAS_DIR", _stage(tmp_path, "short", body))
    r = smoke.smoke_arm("short", "a", smoke.smoke_panels())
    assert r["status"] == "error" and r["panels"]["main"]["status"] == "error"
    assert r["panels"]["main"]["traceback"]
    assert smoke.main(["--ideas", "short"]) == 1


def test_smoke_reports_needs_kernel_without_simulating(tmp_path, monkeypatch):
    body = "ARMS = {'a': {'needs_kernel': 'two legs'}}\n\n\ndef targets(data, spec):\n    return 0\n"
    monkeypatch.setattr(api, "IDEAS_DIR", _stage(tmp_path, "kernel", body))
    r = smoke.smoke_arm("kernel", "a", smoke.smoke_panels())
    assert r["status"] == "needs_kernel" and r["panels"] == {}
    assert smoke.main(["--ideas", "kernel"]) == 1


def test_smoke_writes_nothing():
    lines = registry.n_lines()
    before = _tree(RESULTS)
    for idea in ("k_control", "r2_control", "vol_shape_p15"):
        assert smoke.main(["--ideas", idea]) == 0
    assert registry.n_lines() == lines
    assert _tree(RESULTS) == before


def test_keys_report_covers_every_key():
    rows = smoke.keys_report()
    assert len(rows) == len(extra.keys())
    by_key = {r["key"]: r for r in rows}
    assert by_key["vix"]["main"]["present"] == 1.0
    assert by_key["vix"]["M2"]["present"] == 0.0
    assert by_key["local_index"]["main"]["series"] == "^NDX"
    assert by_key["local_index"]["M2"]["series"] == "^GSPC"
    assert all("error" not in r["main"] for r in rows)
