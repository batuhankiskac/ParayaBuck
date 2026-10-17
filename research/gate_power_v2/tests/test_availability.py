"""Availability: `NotApplicable` windows, the reduced G7/G8, `holdout_insufficient`.

Synthetic records only (no simulation), reusing `test_gates`' builders -- the availability rules
are arithmetic on a record, so they are testable without the engine. The end-to-end proof that they
fire on real data is `probes/run_probe_batch.py` (documented in the report).
"""

from __future__ import annotations

import json

import numpy as np
from test_gates import control, record

from research.gate_power_v2 import api, run, stages
from research.fast_engine.fe_config import INSTRUMENTS, OOS_WINDOWS

NA = {"applicable": False, "reason": "no data for this window"}


def _na(r, windows=(), etfs=()):
    for k in windows:
        r["oos"][k] = dict(NA)
    for k in etfs:
        r["etf_after_tax"][k] = dict(NA)
    return r


# ----------------------------------------------------------------------------- G7 on the subset
def test_g7_uses_only_the_applicable_windows():
    r = _na(record(), ["M2", "GSPC_1957_1999"])
    d = stages.pre_tax_gates(r, control())["G7_detail"]
    assert set(d["delta_cagr_pp"]) == set(OOS_WINDOWS) - {"M2", "GSPC_1957_1999"}
    assert d["n_applicable"] == 4 and d["n_within_required"] == 3
    assert set(d["not_applicable"]) == {"M2", "GSPC_1957_1999"}
    assert abs(d["median_cagr_pp"] - 0.1) < 1e-9
    assert stages.pre_tax_gates(r, control())["G7"]


def test_full_set_keeps_the_published_five_of_six_rule_and_no_extra_keys():
    d = stages.pre_tax_gates(record(), control())["G7_detail"]
    assert len(d["delta_cagr_pp"]) == 6
    assert "n_applicable" not in d and "n_within_required" not in d   # unchanged for a full run


def test_g7_allows_exactly_one_outlier_at_four_windows():
    r = _na(record(), ["M2", "GSPC_1957_1999"])
    r["oos"]["M1"]["cagr"] = 0.10 - 0.006          # -0.6 pp: outside the -0.5 pp clause
    assert stages.pre_tax_gates(r, control())["G7_detail"]["n_cagr_within"] == 3
    assert stages.pre_tax_gates(r, control())["G7"]
    r["oos"]["M3"]["cagr"] = 0.10 - 0.006          # a second outlier -> fail
    g = stages.pre_tax_gates(r, control())
    assert g["G7_detail"]["n_cagr_within"] == 2 and not g["G7"]


def test_g7_is_not_evaluable_below_three_applicable_windows():
    r = _na(record(), ["M2", "GSPC_1957_1999", "M1", "M3"])
    g = stages.pre_tax_gates(r, control())
    assert not g["G7"] and "not_evaluable" in g["G7_detail"]
    assert g["G7_detail"]["n_applicable"] == 2


# ----------------------------------------------------------------------------- E1 and the verdict
def test_e1_gate_is_computed_on_the_applicable_instruments():
    r = _na(record(), etfs=list(INSTRUMENTS)[:6])
    e1 = stages.e1_gate(r, control())
    assert e1["n_instruments"] == 15 and e1["evaluable"] and e1["pass"]
    assert set(e1["applicable"]) == set(INSTRUMENTS) - set(INSTRUMENTS[:6])


def test_fourteen_applicable_instruments_make_the_holdout_insufficient():
    r = _na(record(), etfs=list(INSTRUMENTS)[:7])
    e1 = stages.e1_gate(r, control())
    assert e1["n_instruments"] == 14 and not e1["evaluable"] and not e1["pass"]
    g = stages.gates(r, control())
    av = g["holdout_insufficient"]
    assert av["n_etf_applicable"] == 14 and av["n_oos_applicable"] == 6 and not av["sufficient"]
    assert not g["pass"] and "HOLDOUT_INSUFFICIENT" in g["failed"]


def test_two_applicable_oos_windows_make_the_holdout_insufficient_even_with_negative_gates():
    r = _na(record(), ["M2", "M3", "M4", "M5"])            # only M1 + GSPC_1957_1999 applicable
    g = stages.gates(r, control())
    assert g["holdout_insufficient"]["n_oos_applicable"] == 2
    assert not g["pass"] and "HOLDOUT_INSUFFICIENT" in g["failed"]
    # the reasons of every unavailable window are carried, so the manual study can start from them
    assert set(g["holdout_insufficient"]["reasons"]) == {"M2", "M3", "M4", "M5"}


def test_a_fully_applicable_record_carries_no_availability_keys():
    g = stages.gates(record(), control())
    assert "holdout_insufficient" not in g and g["pass"] and "HOLDOUT_INSUFFICIENT" not in g["failed"]
    assert "n_applicable" not in g["G7_detail"]


def test_applicability_counts_and_reasons():
    r = _na(record(), ["M2"], etfs=["XLK"])
    av = stages.applicability(r)
    assert av["n_oos_applicable"] == 5 and av["n_etf_applicable"] == len(INSTRUMENTS) - 1
    assert av["sufficient"] and av["reasons"] == {"M2": NA["reason"], "XLK": NA["reason"]}


# ----------------------------------------------------------------------------- pooled OOS pairing
def test_oos_pooled_delta_pairs_the_control_inside_a_reduced_draw(monkeypatch):
    calls = []

    def fake_oos_pooled(arms, markets=None, **kw):
        calls.append(([a["id"] for a in arms], markets))
        ids = [a["id"] for a in arms]
        return {"pooled": {a: np.full(50, 0.10 if a == ids[0] else 0.08) for a in ids}}

    monkeypatch.setattr(stages.B, "oos_pooled", fake_oos_pooled)
    arm, ctrl_arm = {"id": "cand"}, {"id": "k_control/K"}
    d, note = stages.oos_pooled_delta(arm, None, ctrl_arm, "none", ["M1", "M3"])
    assert calls == [(["cand", "k_control/K"], ("M1", "M3"))]
    assert np.allclose(d, 0.02) and note["markets"] == ["M1", "M3"]
    assert note["control"] == "re-drawn in the same draw"


def test_oos_pooled_delta_uses_the_cached_control_vector_on_the_full_set(monkeypatch):
    calls = []

    def fake_oos_pooled(arms, markets=None, **kw):
        calls.append(([a["id"] for a in arms], markets))
        return {"pooled": {a["id"]: np.full(4, 0.05) for a in arms}}

    monkeypatch.setattr(stages.B, "oos_pooled", fake_oos_pooled)
    d, note = stages.oos_pooled_delta({"id": "cand"}, np.full(4, 0.01), None, "none", None)
    assert calls[0][0] == ["cand"] and calls[0][1] == stages.OOS_POOLED_MARKETS
    assert np.allclose(d, 0.04) and note["control"] == "cached six-market vector"


def test_oos_pooled_markets_probes_one_path_per_market(monkeypatch):
    seen = []

    def fake_oos_pooled(arms, markets=None, per_market=None, **kw):
        seen.append((markets[0], per_market))
        if markets[0] == "M2":
            raise api.NotApplicable("M2 has no chunk")
        return {"pooled": {}}

    monkeypatch.setattr(stages.B, "oos_pooled", fake_oos_pooled)
    ok, bad = stages.oos_pooled_markets({"id": "cand"})
    assert ok == ["M1", "M3", "M4", "M5"] and bad == {"M2": "M2 has no chunk"}
    assert all(per_market == 1 for _m, per_market in seen)


# ----------------------------------------------------------------------------- not applicable arms
def test_na_record_writes_a_verdict_and_no_vector(tmp_path):
    out = {"arm": "x/y", "idea": "x", "arm_id": "y", "b1_delta_pp": [1.0]}
    res = run._na_record(out, tmp_path, "main window (stage 1)", "no inputs", 0.0)
    rec = json.loads((tmp_path / "x__y.json").read_text())
    assert rec["verdict"] == "not_applicable" and rec["b1_delta_pp"] == []
    assert rec["not_applicable"] == {"where": "main window (stage 1)", "reason": "no inputs"}
    assert res["verdict"] == "not_applicable" and res["stage1_pass"] is False


def _write(tmp_path, name, payload):
    (tmp_path / f"{name}.json").write_text(json.dumps(payload))


def test_scoreboard_marks_unavailable_arms_and_lists_them_for_manual_confirmation(tmp_path):
    ctrl = {"record": {"window": {"start": "2000-03-09", "end": "2026-09-29"}}}
    arms = [{"idea": "probe", "arm_id": "na"}, {"idea": "probe", "arm_id": "hold"},
            {"idea": "probe", "arm_id": "plain"}]
    _write(tmp_path, "probe__na", {"arm": "probe/na", "idea": "probe", "arm_id": "na",
                                  "verdict": "not_applicable", "b1_delta_pp": [],
                                  "not_applicable": {"where": "main window (stage 1)",
                                                     "reason": "no inputs"}})
    _write(tmp_path, "probe__hold", {"arm": "probe/hold", "idea": "probe", "arm_id": "hold",
                                    "verdict": "holdout_insufficient", "stage1": None,
                                    "stage1_pass": True, "b1_delta_pp": [],
                                    "availability": {"n_oos_applicable": 1, "n_etf_applicable": 21,
                                                     "reasons": {"M2": "no data"}}})
    _write(tmp_path, "probe__plain", {"arm": "probe/plain", "idea": "probe", "arm_id": "plain",
                                     "verdict": "reject_stage1", "stage1": None,
                                     "stage1_pass": False, "b1_delta_pp": []})
    md = run.scoreboard("probe", tmp_path, ctrl, arms)
    table, manual = md.split("## manual confirmation")
    assert "not applicable (main window (stage 1))" in table
    assert "holdout insufficient" in table
    assert "`probe/hold`" in manual and "`probe/na`" not in manual and "`probe/plain`" not in manual
    assert table.count("| `probe/") == 3
