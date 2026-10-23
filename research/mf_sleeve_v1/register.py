"""Register `data/historical/mf_sleeve_v1_proxy.csv` as the hand-made extra key `mf_proxy`."""

from __future__ import annotations

import json

from research.batch_screen import extra

NAME = "MF_PROXY"
FILE = "data/historical/mf_sleeve_v1_proxy.csv"


def main():
    reg = json.loads(extra.REGISTRY_PATH.read_text())
    f = extra._file(FILE)
    path = extra.ROOT / FILE
    meta = {"range": [str(f["dates"][0]), str(f["dates"][-1])], "rows": int(f["dates"].size),
            "bytes": path.stat().st_size, "sha256": extra.sha256(path)}
    reg["series"][NAME] = {
        "file": FILE, "column": "close", "price": "close", "source": "hand-made",
        "url": "research/mf_sleeve_v1/build_proxy.py", "fetched": "2026-10-23",
        "range": meta["range"], "rows": meta["rows"], "bytes": meta["bytes"],
        "sha256": meta["sha256"],
    }
    reg["keys"]["mf_proxy"] = {"windows": {"main": NAME}}
    extra.REGISTRY_PATH.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(reg["series"][NAME], indent=1))


if __name__ == "__main__":
    main()
