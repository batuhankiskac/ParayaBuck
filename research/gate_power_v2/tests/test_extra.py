"""The extra-data layer: registry, date alignment, the chunk column convention, ffill, downloads.

Every test is offline: the downloaders are tested through an injected `get`/`fetchers`, never the
network, and the committed series files are read, never written.
"""

from __future__ import annotations

import csv
import json

import numpy as np
import pytest

from research.gate_power_v2 import boot as B
from research.gate_power_v2 import extra, windows
from research.fast_engine import fe_boot
from research.fast_engine.fe_config import BLOCK, SEED, TAX_BOOT_EXT, TAX_BOOT_L

WINDOWS = ("main", "boot", "M1", "M2", "M3", "M4", "M5", "GSPC_1957_1999", "XLK")


def _column(series: str) -> dict:
    """One series file parsed independently of the loader: `{date: value}`."""
    meta = extra.series_meta(series)
    out = {}
    with (extra.ROOT / meta["file"]).open() as fh:
        for row in csv.DictReader(fh):
            v = row[meta["column"]]
            out[row["date"]] = np.nan if v == "" else float(v)
    return out


def _aligned(series: str, days) -> np.ndarray:
    col = _column(series)
    return np.array([col.get(d, np.nan) for d in days], dtype=np.float64)


# ----------------------------------------------------------------------------- registry
def test_every_key_resolves_in_every_window_kind():
    names = set(extra.series_names())
    assert extra.keys() and len(names) >= 20
    for key in extra.keys():
        for window in WINDOWS:
            assert extra.resolve_series(key, window) in names


def test_committed_registry_verifies():
    v = extra.verify()
    assert v["ok"], f"bad series: {v['bad']}"
    assert v["n_series"] == len(extra.series_names())


def test_registry_rejects_a_wrong_schema(tmp_path):
    p = tmp_path / "EXTRA_DATA.json"
    p.write_text(json.dumps({"schema": "nope", "series": {"X": {}}, "keys": {"x": "X"}}))
    with pytest.raises(extra.ExtraError):
        extra.registry(p)


def test_unknown_key_and_unmapped_window_raise(tmp_path):
    p = tmp_path / "EXTRA_DATA.json"
    p.write_text(json.dumps({
        "schema": extra.SCHEMA,
        "series": {"^X": {"file": "x.csv", "column": "close"}},
        "keys": {"only_main": {"windows": {"main": "^X"}}},
    }))
    with pytest.raises(extra.ExtraError, match="unknown extra key"):
        extra.resolve_series("nope", "main", p)
    assert extra.resolve_series("only_main", "main", p) == "^X"
    with pytest.raises(extra.ExtraError, match="no series for window"):
        extra.resolve_series("only_main", "M3", p)
    # a bootstrap chunk resolves through the window it replays
    assert extra.resolve_series("only_main", "boot", p) == "^X"


def test_values_on_is_a_by_date_lookup_with_nan_gaps():
    days, panel, _i0, _i1 = windows.main_panel()
    for key, series in (("vix", "^VIX"), ("dgs10", "DGS10"), ("cpi", "CPIAUCSL")):
        want = _aligned(series, days)
        got = extra.values_on(series, days)
        assert np.allclose(got, want, equal_nan=True)
        assert np.allclose(panel.extra(key)[0], want, equal_nan=True)
    # a session the series does not cover is NaN, not a neighbour's value
    assert np.isnan(extra.values_on("^VIX", ["1900-01-02"])[0])
    assert extra.values_on("^VIX", ["1990-01-02"])[0] == _column("^VIX")["1990-01-02"]


def test_extra_is_cached_and_logged_on_every_call():
    _days, panel, _i0, _i1 = windows.main_panel()
    a = panel.extra("vix")
    b = panel.extra("vix")
    assert a is b and len(panel.extra_log) == 2
    assert panel.extra_log[0]["key"] == "vix" and panel.extra_log[0]["nan_share"] == 0.0
    assert panel.extra("vix", ffill=True) is not a
    with pytest.raises(extra.ExtraError):
        panel.extra("not_a_key")


# ----------------------------------------------------------------------------- the chunk convention
def test_bootstrap_chunk_extra_is_the_drawn_source_session_of_each_column():
    win = B.BootWindow("none")
    pos = fe_boot.draw_positions(np.random.default_rng(SEED), BLOCK, 3, TAX_BOOT_L, win.npool,
                                TAX_BOOT_EXT)
    panel = B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]
    vix = panel.extra("vix")
    assert vix.shape == panel.price.shape == (3, TAX_BOOT_L + TAX_BOOT_EXT + 1)
    # market column t is the close of the drawn source session idx[p, t-1]; column 0 is the base
    idx = pos + 1
    pooled = _aligned("^VIX", windows.bars()[0])
    want = pooled[np.concatenate([idx[:, :1], idx], axis=1)]
    assert np.allclose(vix, want, equal_nan=True)
    assert vix.shape == panel.close.shape


def test_ffill_carries_the_last_value_forward_but_not_before_the_first():
    a = np.array([[np.nan, np.nan, 1.0, np.nan, np.nan, 2.0, np.nan]])
    out = extra.forward_fill(a)
    assert np.isnan(out[0, :2]).all()
    assert np.array_equal(out[0, 2:], [1.0, 1.0, 1.0, 2.0, 2.0])
    days, panel, _i0, _i1 = windows.main_panel()
    raw = panel.extra("cpi")
    filled = panel.extra("cpi", ffill=True)
    lead = int(np.argmax(~np.isnan(raw[0])))     # the first session the monthly series reaches
    assert lead > 0 and np.isnan(filled[0, :lead]).all()
    assert np.isfinite(filled[0, lead:]).all()   # everything after it is carried forward
    assert np.isnan(raw).sum() > np.isnan(filled).sum()


# ----------------------------------------------------------------------------- downloads (offline)
def _yahoo_payload():
    return json.dumps({"chart": {"result": [{
        "timestamp": [0, 86400, 172800],
        "indicators": {"quote": [{"close": [1.0, None, 2.0]}],
                       "adjclose": [{"adjclose": [0.9, None, 1.8]}]},
    }]}}).encode()


def test_parse_yahoo_keeps_the_api_shape_and_marks_nulls():
    dates, close, adj = extra.parse_yahoo(_yahoo_payload(), "X")
    assert dates == ["1970-01-01", "1970-01-02", "1970-01-03"]
    assert close[0] == 1.0 and close[2] == 2.0 and np.isnan(close[1])
    assert adj[0] == 0.9 and adj[2] == 1.8 and np.isnan(adj[1])


def test_parse_yahoo_raises_on_an_api_error():
    with pytest.raises(extra.ExtraError):
        extra.parse_yahoo(json.dumps({"chart": {"error": {"code": "Not Found"}}}).encode(), "X")


def test_fetch_yahoo_quotes_the_symbol_drops_null_rows_and_builds_an_audit_url():
    dates, cols, url = extra.fetch_yahoo("^VIX", period2=0, get=lambda u, t: _yahoo_payload())
    assert dates == ["1970-01-01", "1970-01-03"]           # the null row is dropped
    assert cols["close"] == [1.0, 2.0] and cols["adjclose"] == [0.9, 1.8]
    assert "%5EVIX" in url and "period1=0" in url and "period2=0" in url


def test_parse_and_fetch_fred_skip_missing_observations():
    dates, vals = extra.parse_fred(b"observation_date,T10YIE\n2020-01-02,1.64\n2020-01-03,.\n")
    assert dates == ["2020-01-02"] and vals == [1.64]
    dates, cols, url = extra.fetch_fred("T10YIE", get=lambda u, t: b"observation_date,T10YIE\n2020-01-02,1.5\n")
    assert cols == {"value": [1.5]} and url.endswith("id=T10YIE")


def test_build_writes_a_series_file_and_redeclares_it(tmp_path):
    reg = tmp_path / "EXTRA_DATA.json"
    reg.write_text(json.dumps({"schema": extra.SCHEMA, "series": {},
                               "keys": {"hand": "^HAND"}}))
    dates = ["2020-01-02", "2020-01-03", "2020-01-06"]
    cols = {"close": [10.0, 11.0, np.nan], "adjclose": [10.0, 10.5, np.nan]}
    done = extra.build(only=["SMH"], path=reg, root=tmp_path, verbose=False,
                       fetchers={"SMH": (dates, cols, "https://example.invalid/smh")})
    entry = done["SMH"]
    assert entry["file"] == "data/historical/batch_screen_v1_smh.csv"
    assert entry["column"] == "adjclose" and entry["price"] == "adjclose"
    assert entry["range"] == ["2020-01-02", "2020-01-03"] and entry["rows"] == 2
    assert entry["url"] == "https://example.invalid/smh"
    body = (tmp_path / entry["file"]).read_text().splitlines()
    assert body[0] == "date,adjclose,close" and body[1] == "2020-01-02,10,10"
    # the hand-written key survives, the fetched series is declared, and both files hash
    reg_doc = json.loads(reg.read_text())
    assert reg_doc["keys"]["hand"] == "^HAND" and "SMH" in reg_doc["series"]
    extra.reset()
    v = extra.verify(path=reg, root=tmp_path)
    assert v["ok"] and v["n_series"] == 1


def test_verify_catches_a_refreshed_file_that_was_not_redeclared(tmp_path):
    reg = tmp_path / "EXTRA_DATA.json"
    reg.write_text(json.dumps({"schema": extra.SCHEMA, "series": {},
                               "keys": {"hand": "^HAND"}}))
    cols = {"close": [1.0, 2.0], "adjclose": [1.0, 2.0]}
    extra.build(only=["SMH"], path=reg, root=tmp_path, verbose=False,
                fetchers={"SMH": (["2020-01-02", "2020-01-03"], cols, "u")})
    extra.reset()
    f = tmp_path / "data/historical/batch_screen_v1_smh.csv"
    f.write_text(f.read_text() + "2020-01-06,3,3\n")
    extra.reset()
    v = extra.verify(path=reg, root=tmp_path)
    assert not v["ok"] and v["bad"] == ["SMH"] and not v["series"]["SMH"]["sha256_ok"]


def test_build_refuses_a_series_it_cannot_download(tmp_path):
    reg = tmp_path / "EXTRA_DATA.json"
    reg.write_text(json.dumps({"schema": extra.SCHEMA, "series": {}, "keys": {}}))
    with pytest.raises(extra.ExtraError, match="not a downloadable series"):
        extra.build(only=["FOMC_DATES"], path=reg, root=tmp_path, verbose=False)
