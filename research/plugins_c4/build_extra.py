"""plugins_c4 -- download, derive and register the extra series of IC09/IC14/IC19/IC29/IC51.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_c4.build_extra

Raw inputs (new downloads, public sources only):

* FRED CSV `ICSA` (weekly initial claims, week-ending Saturday)       -> IC14
* FRED CSV `DCOILWTICO` (Cushing WTI spot, daily)                     -> IC19
* Yahoo chart API `EEM`, `SPY` (adjusted = total-return closes)       -> IC29

and the nine sector SPDRs already registered by the framework (`XLB` ... `XLY`, adjclose) for
IC09/IC51. The derived state series (`research/plugins_c4/signals.py` explains why they are
precomputed on the native calendar) are written with the framework's own writer/hasher and
declared in the framework registry `research/batch_screen/EXTRA_DATA.json` (data only -- no
framework code is edited), with `derived_from` naming each input file and its SHA-256. This
branch's `DATA_MANIFEST.json` repeats every entry.

Nothing here simulates, computes a strategy return, or reads a result file.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np

from research.batch_screen import extra
from research.plugins_c4 import signals as S

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REGISTRY = ROOT / "research/batch_screen/EXTRA_DATA.json"
LOCAL_MANIFEST = HERE / "DATA_MANIFEST.json"
PFX = "data/historical/plugins_c4_"

#: plugin-facing key -> series name (each series is one column of a derived file)
KEYS = {
    "icsa_shock": "ICSA-SHOCK",
    "oil_r126": "WTI-R126",
    "oil_med_gap": "WTI-MEDGAP",
    "eem_spy_rs126": "EEM-SPY-RS126",
    "sec_corr": "SEC-CORR",
    "sec_corr_rel": "SEC-CORR-REL",
    "sec_disp": "SEC-DISP",
    "sec_disp_rel": "SEC-DISP-REL",
    # the raw downloads, registered for provenance (no plugin reads them directly)
    "icsa": "ICSA",
    "oil": "DCOILWTICO",
    "eem": "EEM",
    "spy": "SPY",
}


def _rng(dates, vals):
    v = np.asarray(vals, dtype=float)
    have = np.isfinite(v)
    if not have.any():
        return [None, None], 0
    return [str(dates[int(np.argmax(have))]),
            str(dates[len(dates) - 1 - int(np.argmax(have[::-1]))])], int(have.sum())


def _entry(rel, col, dates, vals, info, *, price, source, url, derived_from=None, note=None):
    rng, n = _rng(dates, vals)
    e = {"file": rel, "column": col, "price": price, "source": source, "url": url,
         "fetched": date.today().isoformat(), "range": rng, "rows": n,
         "bytes": info["bytes"], "sha256": info["sha256"]}
    if derived_from:
        e["derived_from"] = derived_from
    if note:
        e["note"] = note
    return e


def fetch_raw():
    """`{series: (dates, values, url, column, source)}` of the four new downloads."""
    out = {}
    for sid in ("ICSA", "DCOILWTICO"):
        d, cols, url = extra.fetch_fred(sid)
        out[sid] = (d, cols["value"], url, "value", "FRED CSV", cols)
    for sym in ("EEM", "SPY"):
        d, cols, url = extra.fetch_yahoo(sym)
        out[sym] = (d, cols["adjclose"], url, "adjclose", "Yahoo chart API", cols)
    return out


def write_raw(raw) -> dict:
    ser = {}
    for name, (d, v, url, col, src, cols) in raw.items():
        rel = f"{PFX}{name.lower()}.csv"
        info = extra.write_series(rel, d, cols)
        ser[name] = _entry(rel, col, d, v, info,
                           price="level" if col == "value" else "adjclose", source=src, url=url)
    return ser


def _sector_panel():
    """The nine registered SPDR adjcloses on their common sessions: `(dates, (N, 9))`."""
    per = {}
    for sym in S.SECTORS:
        meta = extra.series_meta(sym)
        f = extra._file(meta["file"])
        v = f["cols"][meta["column"]]
        per[sym] = dict(zip(f["dates"].tolist(), v.tolist()))
    common = sorted(set.intersection(*(
        {d for d, x in m.items() if np.isfinite(x)} for m in per.values())))
    return common, np.array([[per[s][d] for s in S.SECTORS] for d in common])


def derive(raw, raw_ser) -> dict:
    ser = {}
    today = date.today().isoformat()

    def src(*names):
        files = [raw_ser[n]["file"] if n in raw_ser else extra.series_meta(n)["file"] for n in names]
        return {"files": {f: extra.sha256(ROOT / f) for f in files}}

    # IC14 -- weekly claims shock, usable one week after the week-end date
    d, v = raw["ICSA"][0], raw["ICSA"][1]
    days, cols = S.to_calendar(d, {"icsa_shock": S.icsa_shock(v)}, lag_days=S.ICSA_LAG_DAYS)
    rel = f"{PFX}icsa_shock.csv"
    info = extra.write_series(rel, days, cols)
    ser["ICSA-SHOCK"] = _entry(
        rel, "icsa_shock", days, cols["icsa_shock"], info, price="ratio",
        source="derived (research/plugins_c4/signals.py icsa_shock)", url=raw_ser["ICSA"]["url"],
        derived_from=src("ICSA"),
        note="mean4/min26(mean4)-1 on weekly ICSA; dated week-end + 7 days; calendar-day ffill")

    # IC19 -- WTI 126-observation return and gap to its 252-observation median (same-day)
    d, v = raw["DCOILWTICO"][0], raw["DCOILWTICO"][1]
    r126, gap = S.oil_state(v)
    days, cols = S.to_calendar(d, {"r126": r126, "med_gap": gap})
    rel = f"{PFX}wti_state.csv"
    info = extra.write_series(rel, days, cols)
    for name, col in (("WTI-R126", "r126"), ("WTI-MEDGAP", "med_gap")):
        ser[name] = _entry(rel, col, days, cols[col], info, price="ratio" if col == "r126" else "level",
                           source="derived (research/plugins_c4/signals.py oil_state)",
                           url=raw_ser["DCOILWTICO"]["url"], derived_from=src("DCOILWTICO"),
                           note="native FRED observations; same-day; calendar-day ffill")

    # IC29 -- EEM vs SPY 126-session total-return relative strength (common NYSE sessions)
    e = dict(zip(raw["EEM"][0], raw["EEM"][1]))
    s = dict(zip(raw["SPY"][0], raw["SPY"][1]))
    common = sorted(x for x in set(e) & set(s) if np.isfinite(e[x]) and np.isfinite(s[x]))
    rs = S.rel_strength(np.array([e[x] for x in common]), np.array([s[x] for x in common]))
    days, cols = S.to_calendar(common, {"rs126": rs})
    rel = f"{PFX}eem_spy_rs126.csv"
    info = extra.write_series(rel, days, cols)
    ser["EEM-SPY-RS126"] = _entry(
        rel, "rs126", days, cols["rs126"], info, price="ratio",
        source="derived (research/plugins_c4/signals.py rel_strength)",
        url=raw_ser["EEM"]["url"] + " ; " + raw_ser["SPY"]["url"], derived_from=src("EEM", "SPY"),
        note="adjclose 126-session returns on common sessions; same-day; calendar-day ffill")

    # IC09 / IC51 -- sector correlation and dispersion
    common, panel = _sector_panel()
    st = S.sector_stats(panel)
    days, cols = S.to_calendar(common, st)
    rel = f"{PFX}sector_stats.csv"
    info = extra.write_series(rel, days, cols)
    for name, col in (("SEC-CORR", "corr"), ("SEC-CORR-REL", "corr_rel"),
                      ("SEC-DISP", "disp"), ("SEC-DISP-REL", "disp_rel")):
        ser[name] = _entry(rel, col, days, cols[col], info, price="ratio",
                           source="derived (research/plugins_c4/signals.py sector_stats)",
                           url="registered batch_screen sector SPDR series (Yahoo chart API)",
                           derived_from=src(*S.SECTORS),
                           note="nine SPDR adjcloses on common sessions; same-day; calendar-day ffill")
    del today
    return ser


def register(ser: dict) -> dict:
    reg = json.loads(REGISTRY.read_text())
    clash = [k for k in KEYS if k in reg["keys"] and reg["keys"][k] != KEYS[k]]
    if clash:
        raise SystemExit(f"key clash with the registry: {clash}")
    # appended in place (the registry's existing order and 2-space format are kept, so the diff
    # is additive and several plugin branches merge with the least conflict)
    reg["series"].update(ser)
    reg["keys"].update(KEYS)
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")
    doc = {"campaign": "plugins_c4",
           "note": ("Extra series for the plugins_c4 IC09/IC14/IC19/IC29/IC51 plugins: four new "
                    "public downloads (FRED ICSA, FRED DCOILWTICO, Yahoo EEM, Yahoo SPY) and the "
                    "state statistics derived from them and from the registered sector SPDRs "
                    "(research/plugins_c4/signals.py). The framework registry "
                    "research/batch_screen/EXTRA_DATA.json carries the series and the keys."),
           "keys": KEYS,
           "extra_data": ser,
           "files": {m["file"]: {"bytes": m["bytes"], "sha256": m["sha256"]} for m in ser.values()}}
    LOCAL_MANIFEST.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return reg


def main(argv=None) -> int:
    raw = fetch_raw()
    raw_ser = write_raw(raw)
    for n, m in raw_ser.items():
        print(f"  {n:12s} {m['file']}  {m['range'][0]} -> {m['range'][1]}  {m['rows']} rows")
    der = derive(raw, raw_ser)
    for n, m in der.items():
        print(f"  {n:14s} {m['file']}:{m['column']}  {m['range'][0]} -> {m['range'][1]}  {m['rows']} rows")
    reg = register({**raw_ser, **der})
    extra.reset()
    bad = extra.verify()["bad"]
    print(f"registry: {len(reg['series'])} series, {len(reg['keys'])} keys; verify bad={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
