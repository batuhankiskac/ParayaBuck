"""Summarize the instrumented Stage B audit dump (execution model audit v1).

python3 analyze_stage_b_audit.py <dir-with-out/VT35_1.0*.json.gz-and-pm/data/historical>/
"""

import csv
import gzip
import json
import math
import sys
from collections import Counter

S = sys.argv[1].rstrip("/") + "/"
a = json.load(gzip.open(S + "out/VT35_1.0_audit.json.gz", "rt"))
p = json.load(gzip.open(S + "out/VT35_1.0.json.gz", "rt"))
print("ending", p["ending_nav"], "counts", p["counts"], "costs", p["costs"])
print("skips", len(a["skips"]), Counter(s[-25:-15] for s in a["skips"]).most_common(3), a["skips"][:3])
eq_skips = [s for s in a["skips"] if "T00:00:00" not in s]
print("non-midnight skips", len(eq_skips), eq_skips[:5])
dl = a["decide_log"]
print("decide calls", len(dl), dl[0]["label"], dl[-1]["label"])
print("trend states", Counter(d["trend_state"] for d in dl))
ev = a["equity_events"]
print("equity event kinds", Counter(e["event"] for e in ev))
fills = [e for e in ev if e["event"] == "fill"]
print("fills", len(fills), Counter((e["symbol"], e["side"]) for e in fills))
# fill timing vs decision label
lag = Counter()
bars = {}
for r in csv.DictReader(open(S + "pm/data/historical/profit_max_v1_daily_split_sip.csv")):
    if r["symbol"] == "QLD":
        bars[r["timestamp"][:10]] = (float(r["open"]), float(r["close"]))
days = sorted(bars)
idx = {d: i for i, d in enumerate(days)}
refdev = []
for f in fills:
    dd = f["decision_timestamp"][:10]
    fd = f["timestamp"][:10]
    lag[idx.get(fd, -99) - idx.get(dd, -99)] += 1
    if fd in bars:
        refdev.append(f["reference_price"] / bars[fd][0] - 1)
    fp = f["fill_price"] / f["reference_price"] - 1
print("fill session lag (sessions after decision)", lag)
print("reference_price == session open max abs dev", max(abs(x) for x in refdev))
print("fill hour UTC", Counter(f["timestamp"][11:16] for f in fills).most_common(4))
print(
    "adverse bps",
    Counter(
        round((f["fill_price"] / f["reference_price"] - 1) * 1e4 * (1 if f["side"] == "buy" else -1), 3) for f in fills
    ),
)
# partial fills: multiple fills per decision
per = Counter((f["decision_timestamp"], f["side"]) for f in fills)
print("decisions with >1 fill same side", sum(1 for v in per.values() if v > 1))
dec = [e for e in ev if e["event"] == "decision"]
print("equity decision events", len(dec), dec[0]["timestamp"], dec[0]["label_time"])
print("decision time-of-day UTC", Counter(e["timestamp"][11:16] for e in dec).most_common(4))
# how many fills where created_at vs decision
print(
    "order_created_at sample", fills[0].get("order_created_at"), fills[0]["timestamp"], fills[0]["decision_timestamp"]
)
# compare decide_log to my own recomputation from campaign/v5 bars (qqq identical)
q = {}
for r in csv.DictReader(open(S + "pm/data/historical/profit_max_v1_daily_split_sip.csv")):
    if r["symbol"] == "QQQ":
        q[r["timestamp"][:10]] = float(r["close"])
qd = sorted(q)
mism = 0
for d in dl:
    day = d["label"][:10]
    if day not in q or d["trend_state"] not in ("uptrend", "downtrend"):
        continue
    i = qd.index(day)
    closes = [q[x] for x in qd[i - 199 : i + 1]]
    up = q[day] > sum(closes) / 200
    qi = days.index(day)
    c = [bars[x][1] for x in days[qi - 20 : qi + 1]]
    r_ = [c[k] / c[k - 1] - 1 for k in range(1, 21)]
    m = sum(r_) / 20
    vol = math.sqrt(sum((x - m) ** 2 for x in r_) / 19 * 252)
    if (d["trend_state"] == "uptrend") != up or (d["vol"] is not None and abs(d["vol"] - vol) > 1e-9):
        mism += 1
print("decide_log vs independent recomputation mismatches", mism)
# unfundable buy deferrals / pending after next decision
print("deferred events", len(a["deferred"]))
# weight vs realized weight after fill: realized QLD weight at open relative to target
