"""Unit tests for gates_v1: PSR / MinTRL / DSR, PBO (CSCV) and provenance.

Run from the worktree root:
    PYTHONPATH=. /Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python -m pytest research/gates_v1/tests_gates.py -q
(research venv: /Users/batuhankiskac/.venvs/pb-research/bin/python -m pytest ... also works; no network, no data.)
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from research.gates_v1 import provenance, pbo, stats


# ----------------------------------------------------------------------------- PSR / DSR
def test_dsr_matches_bailey_lopez_de_prado_numerical_example():
    # Bailey & Lopez de Prado (2014), "A numerical example": annualised SR 2.5 over 5 years of
    # daily data (T = 1250, non-annualised SR = 2.5 / sqrt(250)), skewness -3, kurtosis 10,
    # N = 46 independent trials, trial-SR variance 0.5 per year (= 0.5/250 per period). Their
    # text reports DSR = 0.9505 at N = 46 (the first N at which the 95 % bar is cleared).
    sr = 2.5 / math.sqrt(250)
    var_trials = 0.5 / 250
    out = stats.dsr(sr, t=1250, skew=-3.0, kurt=10.0, n_trials=46, var_trials=var_trials)
    assert out["dsr"] == pytest.approx(0.9505, abs=5e-4)


def test_expected_max_sr_single_trial_is_zero_and_grows_with_n():
    assert stats.expected_max_sr(1, 0.01) == 0.0
    vals = [stats.expected_max_sr(n, 1.0) for n in (2, 10, 100, 1000)]
    assert vals == sorted(vals)
    # The Bailey & Lopez de Prado closed form (Gumbel approximation) gives 2.5306 at N = 100;
    # the exact expected maximum of 100 standard normals is ~2.508, so the formula overstates by ~0.02.
    assert stats.expected_max_sr(100, 1.0) == pytest.approx(2.5306, abs=1e-3)


def test_psr_is_half_at_benchmark_equal_to_sr_and_monotone():
    assert stats.psr(0.1, 0.1, t=500, skew=0.0, kurt=3.0) == pytest.approx(0.5)
    assert stats.psr(0.2, 0.1, 500) > stats.psr(0.15, 0.1, 500) > 0.5


def test_psr_normal_closed_form():
    # normal kurtosis 3, zero skew: z = SR * sqrt(T-1) / sqrt(1 + SR^2 / 2)
    t, sr = 253, 0.05
    expected = stats.cdf(sr * math.sqrt(t - 1) / math.sqrt(1.0 + sr * sr / 2.0))
    assert stats.psr(sr, 0.0, t, 0.0, 3.0) == pytest.approx(expected)


def test_min_trl_inverts_psr():
    # PSR evaluated at MinTRL must equal 1 - alpha (the defining property)
    sr, sr_star, skew, kurt = 0.08, 0.0, -0.5, 6.0
    n = stats.min_trl(sr, sr_star, skew, kurt, alpha=0.05)
    assert stats.psr(sr, sr_star, int(round(n)), skew, kurt) == pytest.approx(0.95, abs=0.01)
    assert stats.min_trl(0.01, 0.02) == math.inf


def test_sample_moments_recovers_normal_and_skewed_series():
    rng = np.random.default_rng(1)
    m = stats.sample_moments(rng.normal(0.001, 0.01, 200000))
    assert m["skew"] == pytest.approx(0.0, abs=0.03)
    assert m["kurt"] == pytest.approx(3.0, abs=0.05)
    assert m["sr"] == pytest.approx(0.1, abs=0.01)


def test_meff_identity_and_bounds():
    assert stats.n_eff_meff(np.eye(5))["meff"] == pytest.approx(5.0)
    c = np.full((4, 4), 0.99)
    np.fill_diagonal(c, 1.0)
    meff = stats.n_eff_meff(c)["meff"]
    assert 1.0 <= meff < 1.1


# ----------------------------------------------------------------------------- PBO
def _noise(seed, t=9600, n=10):
    # 16 blocks of 600 sessions: long enough that the 252-session purge leaves in-sample rows
    return np.random.default_rng(seed).normal(0.0, 0.01, size=(t, n))


def test_pbo_pure_noise_is_near_half():
    # With 16 blocks every combination shares the same few blocks, so a single seed's PBO
    # swings widely (0.07 to 0.75 across seeds). The estimator's expectation is 0.5: average 8 seeds.
    vals = []
    for seed in range(8):
        res = pbo.pbo_cscv(_noise(seed), n_blocks=16, purge=252)
        assert res["n_combinations"] == math.comb(16, 8)
        vals.append(res["pbo"])
    assert 0.35 <= float(np.mean(vals)) <= 0.65


def test_pbo_true_edge_is_low():
    r = _noise(11)
    r[:, 0] += 0.002  # arm 0 has a real positive mean of 20 bp / day against 1 % daily noise
    res = pbo.pbo_cscv(r, n_blocks=16, purge=252)
    assert res["pbo"] < 0.10


def test_pbo_purge_removes_rows_and_rejects_bad_input():
    res = pbo.pbo_cscv(_noise(3, t=9600, n=4), n_blocks=16, purge=252)
    assert res["mean_purged_is_rows"] > 0
    with pytest.raises(ValueError):
        pbo.pbo_cscv(_noise(3, t=200, n=4), n_blocks=7)
    with pytest.raises(ValueError):  # purge leaves no in-sample rows in short samples
        pbo.pbo_cscv(_noise(3, t=2000, n=4), n_blocks=8, purge=252)


# ----------------------------------------------------------------------------- provenance
def test_compare_results_accepts_same_and_refuses_changed_data(tmp_path):
    f1 = tmp_path / "a.csv"
    f2 = tmp_path / "b.csv"
    f1.write_text("x\n1\n")
    f2.write_text("x\n2\n")
    s1 = provenance.stamp({"a": f1}, cwd=tmp_path)
    s2 = provenance.stamp({"a": f1}, cwd=tmp_path)
    assert provenance.compare_results({"provenance": s1}, {"provenance": s2}) is True
    s3 = provenance.stamp({"a": f2}, cwd=tmp_path)
    with pytest.raises(provenance.ProvenanceMismatch):
        provenance.compare_results({"provenance": s1}, {"provenance": s3})


def test_compare_results_refuses_changed_code():
    base = {"data_sha256": {"a": "x"}, "alfred_vintage": None, "git_sha": "aaa",
            "versions": {"numba": "1"}}
    other = dict(base, git_sha="bbb")
    with pytest.raises(provenance.ProvenanceMismatch):
        provenance.compare_results(base, other)
