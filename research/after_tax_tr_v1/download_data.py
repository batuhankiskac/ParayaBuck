#!/usr/bin/env python3
"""after_tax_tr_v1 data download (network; run once). Writes:

  data/historical/after_tax_tr_v1_usdtry_cbrt.csv   daily CBRT indicative USD rates
  data/historical/after_tax_tr_v1_yiufe.csv         monthly Yi-UFE index (2003=100), spliced
  data/historical/after_tax_tr_v1_manifest.json     source, retrieval time, sha256

Sources
  * CBRT daily indicative exchange-rate bulletins, https://www.tcmb.gov.tr/kurlar/YYYYMM/DDMMYYYY.xml
    (public, no login). USD ForexBuying / ForexSelling. Pre-2005 values are old lira
    (TRL); divided by 1,000,000 to TRY (5083 sayili Kanun redenomination 2005-01-01).
    Days without a bulletin (weekends, holidays) are absent.
  * Yi-UFE monthly % changes from the CBRT "Uretici Fiyatlari" page (TUIK series,
    2003=100), 2003-01 .. latest. Chained from 100 at 2002-12.
  * 2000-01 .. 2002-11 splice: FRED TURPPDMMINMEI (OECD MEI, Turkey domestic
    producer prices, manufacturing), ratio-linked at 2002-12. Pre-2003 indexation
    in the model therefore uses a proxy (documented in PREREGISTRATION.md).

Raw responses are cached under --cache (a scratch dir), never committed.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_FX = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
OUT_UFE = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
MANIFEST = ROOT / "data/historical/after_tax_tr_v1_manifest.json"
UFE_URL = "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Istatistikler/Enflasyon+Verileri/Uretici+Fiyatlari"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=TURPPDMMINMEI"
FIRST, LAST = dt.date(2000, 1, 3), dt.date(2026, 10, 9)
UA = {"User-Agent": "Mozilla/5.0 (research; after_tax_tr_v1)"}


def fetch(url: str, ua: bool = True, tries: int = 4) -> bytes | None:
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA if ua else {})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (k + 1))
        except Exception:
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"failed: {url}")


def cbrt_day(d: dt.date, cache: Path):
    f = cache / f"{d:%Y%m%d}.xml"
    if f.exists():
        body = f.read_bytes()
    else:
        body = fetch(f"https://www.tcmb.gov.tr/kurlar/{d:%Y%m}/{d:%d%m%Y}.xml")
        if body is None:
            body = b""
        f.write_bytes(body)
    if not body:
        return None
    txt = body.decode("iso-8859-9" if b"ISO-8859-9" in body[:100] else "utf-8", errors="replace")
    m = re.search(r'<Currency[^>]*Kod="USD".*?</Currency>', txt, flags=re.S)
    if not m:
        return None
    blk = m.group(0)
    fb = re.search(r"<ForexBuying>([^<]+)</ForexBuying>", blk)
    fs = re.search(r"<ForexSelling>([^<]+)</ForexSelling>", blk)
    if not fb or not fs:
        return None
    scale = 1e6 if d.year < 2005 else 1.0
    return d.isoformat(), float(fb.group(1)) / scale, float(fs.group(1)) / scale


def ufe_changes(raw: str):
    t = re.sub(r"<script.*?</script>", "", raw, flags=re.S)
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))
    s = s[s.find("Ay-Yıl"):]
    out = {}
    for m in re.finditer(r"(\d{2})-(\d{4}) ((?:-?\d+[.,]\d+ ?){1,2})", s):
        vals = [float(v.replace(",", ".")) for v in m.group(3).split()]
        out[f"{m.group(2)}-{m.group(1)}"] = vals[-1]  # monthly change is the last column
    return dict(sorted(out.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    a = ap.parse_args()
    cache = Path(a.cache)
    (cache / "cbrt").mkdir(parents=True, exist_ok=True)
    days = [FIRST + dt.timedelta(k) for k in range((LAST - FIRST).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    with cf.ThreadPoolExecutor(6) as ex:
        rows = [r for r in ex.map(lambda d: cbrt_day(d, cache / "cbrt"), days) if r]
    with OUT_FX.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "usdtry_forex_buying", "usdtry_forex_selling"])
        for r in rows:
            w.writerow([r[0], repr(r[1]), repr(r[2])])

    ufe_raw = fetch(UFE_URL)
    (cache / "ufe.html").write_bytes(ufe_raw)
    ch = ufe_changes(ufe_raw.decode("utf-8", errors="replace"))
    fred_raw = fetch(FRED_URL, ua=False)
    (cache / "fred.csv").write_bytes(fred_raw)
    fred = {r["observation_date"][:7]: float(r["TURPPDMMINMEI"]) for r in csv.DictReader(fred_raw.decode().splitlines())}
    idx = {"2002-12": 100.0}
    lvl = 100.0
    for ym, pct in ch.items():
        if ym < "2003-01":
            continue
        lvl *= 1 + pct / 100
        idx[ym] = lvl
    base = fred["2002-12"]
    for ym, v in fred.items():
        if "1999-01" <= ym < "2002-12":
            idx[ym] = 100.0 * v / base
    with OUT_UFE.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["month", "yiufe_index", "source"])
        for ym in sorted(idx):
            src = "TCMB/TUIK Yi-UFE monthly % chained" if ym > "2002-12" else ("anchor" if ym == "2002-12" else "FRED TURPPDMMINMEI ratio-linked")
            w.writerow([ym, f"{idx[ym]:.6f}", src])

    def sha(p) -> str:
        return hashlib.sha256(p.read_bytes() if isinstance(p, Path) else p).hexdigest()

    man = {
        "retrieved_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "files": {
            str(OUT_FX.relative_to(ROOT)): {
                "source": "CBRT (TCMB) daily indicative exchange-rate bulletins (gosterge niteligindeki kurlar)",
                "url_pattern": "https://www.tcmb.gov.tr/kurlar/YYYYMM/DDMMYYYY.xml",
                "fields": "USD ForexBuying (doviz alis) and ForexSelling; pre-2005 TRL / 1e6",
                "requested": [FIRST.isoformat(), LAST.isoformat()],
                "rows": len(rows), "first": rows[0][0], "last": rows[-1][0],
                "sha256": sha(OUT_FX),
            },
            str(OUT_UFE.relative_to(ROOT)): {
                "source": "Yi-UFE (2003=100) monthly % changes from the CBRT 'Uretici Fiyatlari' page; 2000-01..2002-11 FRED TURPPDMMINMEI ratio-linked at 2002-12",
                "urls": [UFE_URL, FRED_URL],
                "rows": len(idx), "first": min(idx), "last": max(idx),
                "raw_ufe_page_sha256": sha(ufe_raw), "raw_fred_sha256": sha(fred_raw),
                "sha256": sha(OUT_UFE),
            },
        },
    }
    MANIFEST.write_text(json.dumps(man, indent=2) + "\n")
    print(json.dumps(man, indent=2))


if __name__ == "__main__":
    main()
