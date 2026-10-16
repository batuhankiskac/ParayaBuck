"""The plugin contract: loading, validation, hashing, the targets call and the leg plumbing."""

from __future__ import annotations

import numpy as np
import pytest

from research.batch_screen import api

TINY = 40


def tiny_panel(leg="2x", div_scale=0.0, rate_=0.04):
    n = TINY
    rng = np.random.default_rng(7)
    price = 100.0 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    div = np.zeros(n)
    div[1:] = price[:-1] * div_scale
    rate = np.full(n, rate_)
    return api.panel(range_days(n), price=price, div=div, rate=rate, o2=price * 0.999, c2=price,
                     d2=div, window="tiny", leg=leg)


def range_days(n, y0=2000):
    from datetime import date, timedelta

    d0 = date(y0, 1, 3)
    out, d = [], d0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def write_idea(tmp_path, name, body):
    p = tmp_path / f"{name}.py"
    p.write_text(body)
    return p


def test_load_idea_validates_module(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    write_idea(tmp_path, "no_arms", "def targets(data, spec):\n    return 0\n")
    with pytest.raises(api.PluginError):
        api.load_idea("no_arms")
    write_idea(tmp_path, "no_targets", "ARMS = {'a': {}}\n")
    with pytest.raises(api.PluginError):
        api.load_idea("no_targets")
    write_idea(tmp_path, "bad_spec", "ARMS = {'a': {'x': object()}}\n\n\ndef targets(data, spec):\n    return 0\n")
    with pytest.raises(api.PluginError):
        api.load_idea("bad_spec")
    with pytest.raises(api.PluginError):
        api.load_idea("missing")


def test_resolve_arm_validates_options(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    write_idea(tmp_path, "opt", "ARMS = {'a': {'rule': 'nope'}}\n\n\ndef targets(data, spec):\n    return 0\n")
    mod = api.load_idea("opt")
    with pytest.raises(api.PluginError):
        api.resolve_arm(mod, "a")
    write_idea(tmp_path, "opt2", "ARMS = {'a': {'leg': '3x'}}\n\n\ndef targets(data, spec):\n    return 0\n")
    with pytest.raises(api.PluginError):
        api.resolve_arm(api.load_idea("opt2"), "a")
    write_idea(tmp_path, "opt3", "ARMS = {'a': {'cash_yield': 'gold'}}\n\n\ndef targets(data, spec):\n    return 0\n")
    with pytest.raises(api.PluginError):
        api.resolve_arm(api.load_idea("opt3"), "a")
    write_idea(tmp_path, "ok", "ARMS = {'a': {'rule': 'C0', 'leg': '1x', 'p': 2}}\n\n\ndef targets(data, spec):\n    return 0\n")
    arm = api.resolve_arm(api.load_idea("ok"), "a")
    assert arm["rule"] == "C0" and arm["leg"] == "1x" and arm["params"] == {"p": 2}


def test_needs_kernel_is_declared_not_screened(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    write_idea(tmp_path, "nk", "ARMS = {'a': {'needs_kernel': 'two legs'}}\n\n\ndef targets(data, spec):\n    return 0\n")
    with pytest.raises(api.NeedsKernel):
        api.resolve_arm(api.load_idea("nk"), "a")
    write_idea(tmp_path, "nkm", "NEEDS_KERNEL = 'portfolio layer'\nARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return 0\n")
    with pytest.raises(api.NeedsKernel):
        api.resolve_arm(api.load_idea("nkm"), "a")


def test_call_targets_normalises_and_rejects(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    p = tiny_panel()
    write_idea(tmp_path, "shape", "import numpy as np\nARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return np.zeros((data.P, data.T - 1))\n")
    with pytest.raises(api.PluginError):
        api.call_targets(api.load_idea("shape"), p, {"params": {}, "leg": "2x"})
    write_idea(tmp_path, "nan", "import numpy as np\nARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return np.full((data.P, data.T), np.nan)\n")
    with pytest.raises(api.PluginError):
        api.call_targets(api.load_idea("nan"), p, {"params": {}, "leg": "2x"})
    write_idea(tmp_path, "leg", "import numpy as np\nARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return np.zeros((data.P, data.T)), np.array([['2x', '1x'] * (data.T // 2)] * data.P)\n")
    with pytest.raises(api.NeedsKernel):
        api.call_targets(api.load_idea("leg"), p, {"params": {}, "leg": "2x"})
    write_idea(tmp_path, "consleg", "import numpy as np\nARMS = {'a': {}}\n\n\ndef targets(data, spec):\n    return np.zeros((data.P, data.T)), '1x'\n")
    wants, leg = api.call_targets(api.load_idea("consleg"), p, {"params": {}, "leg": "2x"})
    assert leg == "1x" and wants.shape == (p.P, p.T)


def test_hashes_are_canonical(tmp_path, monkeypatch):
    assert api.spec_hash({"b": 1, "a": [2, 3]}) == api.spec_hash({"a": [2, 3], "b": 1})
    assert api.spec_hash({"a": 1}) != api.spec_hash({"a": 1.0 + 1e-9})
    monkeypatch.setattr(api, "IDEAS_DIR", tmp_path)
    write_idea(tmp_path, "h", "ARMS = {}\n")
    h1 = api.code_hash("h")
    write_idea(tmp_path, "h", "ARMS = {}\n# a comment\n")
    assert api.code_hash("h") != h1


def test_leg_1x_is_the_total_return_recurrence():
    """The 1x leg folds dividends in once; with zero dividends and no financing it is the price
    path (scaled), and the 2x leg's daily excess over 1 is twice the 1x's."""
    p = tiny_panel(div_scale=0.0, rate_=0.0)
    o1, c1 = p.legs["1x"]
    o2, c2 = p.legs["2x"]
    assert np.allclose(c1[0], p.price[0] / p.price[0, 0], rtol=1e-12)
    r1 = c1[0, 1:] / c1[0, :-1] - 1
    r2 = c2[0, 1:] / c2[0, :-1] - 1
    from research.fast_engine.fe_config import EXP_Q

    assert np.allclose(r2 - r1, r1 - EXP_Q / 252, rtol=1e-9, atol=1e-12)
    assert np.allclose(o1[0, 0], 1.0) and np.allclose(o2[0, 0], 1.0)


def test_leg_choice_moves_the_panel():
    p2 = tiny_panel(leg="2x")
    p1 = tiny_panel(leg="1x")
    assert p2.leg == "2x" and p1.leg == "1x"
    assert not np.array_equal(p2.close, p1.close)
    assert np.all(p1.vol[0, -1] < p2.vol[0, -1])
    assert np.array_equal(p2.price, p1.price)  # the underlying is the same either way


def test_market_data_shape_helpers():
    p = tiny_panel()
    assert p.P == 1 and p.T == TINY
    assert p.vol.shape == (1, TINY)
    assert p.year.shape == (TINY,) and p.month.shape == (TINY,)
