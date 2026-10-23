"""Write research/put_hedge_v1/DATA_MANIFEST.json (SHA-256 of every input)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NEW = {
    "data/historical/put_hedge_v1_vxn.csv": (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EVXN",
        "Yahoo ^VXN daily closes; QQQ/NDX 30-day implied vol, 2001-01-23 -> 2026-10-22",
    ),
    "data/historical/put_hedge_v1_vix.csv": (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
        "Yahoo ^VIX daily closes; the pre-registered fallback and the PPUT sanity input",
    ),
    "data/historical/put_hedge_v1_spx.csv": (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ESPX",
        "Yahoo ^SPX daily OHLC; the PPUT sanity check and the 1990-1999 OOS underlying",
    ),
    "data/historical/put_hedge_v1_sp500tr.csv": (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ESP500TR",
        "Yahoo ^SP500TR daily closes; S&P 500 total return for the PPUT dividend leg",
    ),
    "data/historical/put_hedge_v1_ndx.csv": (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX",
        "Yahoo ^NDX daily OHLC; provenance/report only (not an engine input)",
    ),
    "data/historical/put_hedge_v1_pput.csv": (
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/PPUT_History.csv",
        "Cboe PPUT index history (daily closes); the pricing-model sanity target",
    ),
}

ENGINE = {
    "data/historical/stress_robustness_v1_daily.csv":
        "QQQ/SPY/QLD bars (the fast engine's main-window input)",
    "data/historical/stress_robustness_v1_dtb3.csv": "DTB3 rate (financing and option discounting)",
    "data/historical/after_tax_tr_v1_usdtry_cbrt.csv": "CBRT USD/TRY buying rate (Turkish tax)",
    "data/historical/after_tax_tr_v1_yiufe.csv": "TUIK YI-UFE monthly index (equity indexation)",
    "data/historical/ewma_band_v1_tb3ms.csv": "TB3MS (pre-1954 rate fallback; load_rates)",
    "research/band_h2_combo_v1/RESULTS.json": "published K anchors (pre-tax and after-tax cells)",
}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    files = {}
    for rel, (url, use) in NEW.items():
        p = ROOT / rel
        files[rel] = {"sha256": sha(p), "bytes": p.stat().st_size, "source_url": url, "used_by": use}
    for rel, use in ENGINE.items():
        p = ROOT / rel
        files[rel] = {"sha256": sha(p), "bytes": p.stat().st_size, "used_by": use}
    out = {
        "campaign": "put_hedge_v1",
        "files": files,
        "note": (
            "The six put_hedge_v1_* CSVs were converted from the raw public downloads kept in "
            "data/historical/raw_put_hedge_v1/ (Yahoo chart JSON, Cboe PPUT CSV); the conversion "
            "is research/put_hedge_v1/prep_data.py. No other network access. The engine inputs are "
            "the fast_engine_v1 archive data, unchanged. band_h2_combo_v1/RESULTS.json is published "
            "evidence, not an input: it supplies the K anchors the harness asserts."
        ),
        "verified_on": "2026-10-22",
    }
    p = ROOT / "research/put_hedge_v1/DATA_MANIFEST.json"
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {p} ({len(files)} files)")


if __name__ == "__main__":
    main()
