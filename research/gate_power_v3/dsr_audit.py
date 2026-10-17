"""gate_power_v3.dsr_audit -- is the screen's deflated Sharpe (DSR) well posed?

The framework's `deflation.deflated_sharpe` builds the expected-maximum-Sharpe threshold

    sr0 = sqrt(var_sr) * E[max of N standard normals]

from `var_sr` = the **cross-sectional variance of the per-arm B1-Δ path-Sharpes across the arms
of the deflated batch** (`deflation.deflate`). In Bailey & Lopez de Prado (2014) the variance in
sr0 is the variance of the *Sharpe estimates across trials under the null* -- the sampling noise
of a Sharpe estimate, which for i.i.d. observations is ~ `1/(n_obs - 1)` at SR = 0 (their own
non-normality factor `(1 - skew·SR + (kurt-1)/4·SR²)/(n_obs-1)` reduces to exactly this when the
null SR is 0). It is **not** the dispersion of genuinely different arms' *true* effects.

The campaign's batch is exactly the case where the two differ: mixed into one pool are real arms
(Sharpe ≈ 0.1–0.7), K-clone arms whose targets *are* the control (Sharpe ≈ 0 by construction) and
deliberately catastrophic idea-bank arms (Sharpe ≈ −1.5 or worse). That cross-section's variance
estimates the *second* thing, not the first, and it inflates sr0 to ≈ 2.4–2.7. No arm whose true
edge yields a Sharpe below ~2.5 can ever clear it, which is why the campaign declared 0 of 303 and
only a deterministic drift (path-Sharpe ≈ 5) could be expected to pass.

This module computes, side by side:

* `sr0_framework` -- sqrt(var of the trial Sharpes) × E[max_N] (what the framework does);
* `var_sr_null`, `sr0_null` -- the null sampling variance `(1 + sr_obs²/2)/(n_obs - 1)` of a Sharpe
  estimate (B&LdP's own quantity), and the resulting threshold;
* the DSR at each, plus the normal-theory one-sided p-value of the Δ mean (what a paired bootstrap
  test actually reports);
* a variance decomposition: `var_total` of the trial Sharpes, the mean per-arm sampling variance
  (the noise floor) and the residual `var_true_effects` = max(0, var_total - noise floor).

Everything is a pure function of the Δ vectors, so it is deterministic and testable.
"""

from __future__ import annotations

import math

import numpy as np

from .deflation import EULER, _N, arm_sharpe  # the framework's own constants and statistic


def expected_max_sharpe(n_trials: int) -> float:
    """`E[max of N standard normal]` as B&LdP approximate it (the bracket sr0 multiplies)."""
    n = max(2, int(n_trials))
    e = EULER
    return (1 - e) * _N.inv_cdf(1 - 1 / n) + e * _N.inv_cdf(1 - 1 / (n * e))


def _dsr(sr_obs: float, sr0: float, n_obs: int, skew: float, kurt: float) -> float:
    """`deflation.deflated_sharpe`'s CDF value for an arbitrary threshold `sr0`."""
    den = 1 - skew * sr_obs + (kurt - 1) / 4 * sr_obs**2
    if den <= 0:
        return 0.0
    z = (sr_obs - sr0) * math.sqrt(max(n_obs - 1, 1)) / math.sqrt(den)
    return float(_N.cdf(z))


def analyse(deltas: dict, n_trials: int) -> dict:
    """The framework's DSR vs the null-variance DSR, over a set of Δ vectors."""
    arms = sorted(deltas)
    sh = {a: arm_sharpe(deltas[a]) for a in arms}
    sr = np.array([sh[a]["sharpe"] for a in arms], dtype=np.float64)
    var_total = float(sr.var(ddof=1)) if sr.size > 1 else 0.0
    n_obs = int(sh[arms[0]]["n"]) if arms else 0
    bracket = expected_max_sharpe(n_trials)
    sr0_framework = math.sqrt(max(var_total, 0.0)) * bracket
    # the null sampling variance of each arm's Sharpe estimate, and its mean over the pool
    var_null = np.array([(1.0 + 0.5 * sh[a]["sharpe"] ** 2) / max(int(sh[a]["n"]) - 1, 1)
                         for a in arms], dtype=np.float64)
    var_sr_null = float(var_null.mean()) if var_null.size else 0.0
    sr0_null = math.sqrt(max(var_sr_null, 0.0)) * bracket
    mono = var_total - var_sr_null  # Hansen-style: total dispersion minus the estimation noise
    out = {
        "n_trials": int(n_trials), "n_obs": n_obs, "n_arms": len(arms),
        "expected_max_normal": bracket,
        "var_total": var_total, "var_sr_null_mean": var_sr_null,
        "var_true_effects": max(0.0, mono),
        "sr0_framework": sr0_framework, "sr0_null": sr0_null,
        "sharpe": {a: sh[a]["sharpe"] for a in arms},
        "p_normal": {a: sh[a]["p_normal"] for a in arms},
    }
    per = {}
    for a in arms:
        s = sh[a]
        per[a] = {
            "sharpe": s["sharpe"], "n_obs": s["n"],
            "dsr_framework": _dsr(s["sharpe"], sr0_framework, int(s["n"]), s["skew"], s["kurt"]),
            "dsr_null": _dsr(s["sharpe"], sr0_null, int(s["n"]), s["skew"], s["kurt"]),
            "p_normal": s["p_normal"],
        }
        per[a]["pass_framework"] = bool(per[a]["dsr_framework"] >= 0.95)
        per[a]["pass_null"] = bool(per[a]["dsr_null"] >= 0.95)
    out["arms"] = per
    out["pass_framework"] = sorted(a for a in arms if per[a]["pass_framework"])
    out["pass_null"] = sorted(a for a in arms if per[a]["pass_null"])
    return out


__all__ = ["analyse", "expected_max_sharpe"]
