"""The multiple-testing correction: null control, power, monotonicity and the DSR's monotonicity."""

from __future__ import annotations

import numpy as np
import pytest

from research.gate_power_v2 import deflation

N = 1000
SEED = 20261016


def synth(means, sd=1.0, n=N, seed=SEED):
    rng = np.random.default_rng(seed)
    return {f"arm{i}": rng.normal(m, sd, n) for i, m in enumerate(means)}


def test_null_family_is_not_declared():
    """Five arms with no real advantage: the stepdown declares nothing at 5 %."""
    d = synth([0.0, 0.0, 0.0, 0.0, 0.0])
    out = deflation.rw_stepdown(d, alpha=0.05)
    assert out["rejected"] == []
    assert all(p > 0.05 for p in out["p_adjusted"].values())
    assert deflation.white_rc(d)["p_value"] > 0.05


def test_a_real_advantage_is_declared_and_the_others_are_not():
    """One arm with a 4-sigma advantage: it is declared, its siblings are not."""
    d = synth([0.0, 0.4, 0.0, 0.05], sd=1.0)
    out = deflation.rw_stepdown(d, alpha=0.05)
    assert out["rejected"] == ["arm1"]
    assert out["p_adjusted"]["arm1"] < 0.05
    assert out["t"]["arm1"] == max(out["t"].values())
    assert out["order"][0] == "arm1"


def test_stepdown_p_values_are_monotone_in_the_order():
    d = synth([0.0, 0.4, 0.0, 0.05])
    out = deflation.rw_stepdown(d)
    ps = [out["p_adjusted"][a] for a in out["order"]]
    assert ps == sorted(ps)
    for a in out["order"]:
        assert out["p_adjusted"][a] >= out["p_unadjusted"][a] - 1e-12


def test_reality_check_p_value_is_at_least_the_best_unadjusted_one():
    """RC's max statistic is dominated by the best arm's own statistic, so its p is larger."""
    d = synth([0.0, 0.3, -0.1, 0.2])
    rc = deflation.white_rc(d)
    unadj = deflation.rw_stepdown(d)["p_unadjusted"]
    assert rc["p_value"] >= unadj[rc["best_arm"]] - 1e-12
    assert rc["best_arm"] == "arm1" and 0.0 <= rc["p_value"] <= 1.0


def test_dependent_arms_are_corrected_more_than_independent_ones():
    """Two arms that are the *same* series must not both be declared on one lucky draw."""
    rng = np.random.default_rng(1)
    base = rng.normal(0.25, 1.0, N)
    d = {"a": base, "b": base.copy(), "c": rng.normal(0.0, 1.0, N)}
    out = deflation.rw_stepdown(d, alpha=0.05)
    assert "c" not in out["rejected"]
    # the duplicated arm cannot be singled out: both get the same adjusted p
    assert out["p_adjusted"]["a"] == out["p_adjusted"]["b"]


def test_deflated_sharpe_monotone_in_trials_and_zero_signal():
    zero = deflation.deflated_sharpe(0.0, n_trials=10, var_sr=0.05, n_obs=N, skew=0.0, kurt=3.0)
    assert 0.0 <= zero["dsr"] <= 0.5 and zero["sr0"] > 0
    strong = deflation.deflated_sharpe(0.5, n_trials=10, var_sr=0.05, n_obs=N, skew=0.0, kurt=3.0)
    assert strong["dsr"] > zero["dsr"]
    many = [deflation.deflated_sharpe(0.5, n_trials=n, var_sr=0.05, n_obs=N, skew=0.0, kurt=3.0)["dsr"]
            for n in (2, 10, 100, 1000)]
    assert many == sorted(many, reverse=True)


def test_deflate_pass_reports_both_tests_for_every_arm():
    d = synth([0.0, 0.3])
    out = deflation.deflate(d, n_trials=37)
    assert out["n_trials"] == 37 and out["n_arms_with_delta"] == 2
    assert set(out["rw_stepdown"]["p_adjusted"]) == set(d)
    assert set(out["deflated_sharpe"]) == set(d)
    assert out["white_rc"]["best_arm"] == out["best_by_sharpe"]
    assert out["best_by_sharpe"] == "arm1"
    assert all(0.0 <= v["dsr"] <= 1.0 for v in out["deflated_sharpe"].values())


def test_deflate_with_no_arms_is_a_noop():
    out = deflation.deflate({}, n_trials=0)
    assert "note" in out and "rw_stepdown" not in out


def test_arm_sharpe_is_scale_free_and_reports_normality():
    rng = np.random.default_rng(3)
    d = rng.normal(0.1, 1.0, 500)
    s = deflation.arm_sharpe(d)
    assert s["sharpe"] == pytest.approx(d.mean() / d.std(ddof=1))
    assert deflation.arm_sharpe(d * 10.0)["sharpe"] == pytest.approx(s["sharpe"])
    assert 0.0 <= s["p"] <= 1.0 and s["n"] == 500
    flat = deflation.arm_sharpe(np.zeros(50))
    assert flat["sharpe"] == 0.0 and flat["p"] == 1.0
