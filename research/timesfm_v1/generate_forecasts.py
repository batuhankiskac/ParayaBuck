#!/usr/bin/env python3
"""timesfm_v1 forecast generation (see PREREGISTRATION.md). Run ONCE, then screen.py reads the cache.

NOT part of the repo's environment: needs a separate venv with `timesfm[torch]==3.0.2`
(torch 2.14.1, Python 3.13) and the official checkpoint google/timesfm-2.5-200m-pytorch
at revision REVISION (Hugging Face cache via HF_HOME). Offline after the checkpoint is cached
(set HF_HUB_OFFLINE=1). Reads only the committed data/historical/stress_robustness_v1_{daily,dtb3}.csv.

Modes
  (default)  forecast every session t in [FIRST_T, T-1] for both series and write
             forecasts.csv + forecasts.meta.json
  --verify   G9: regenerate VERIFY_N seeded dates in a fresh process with a different batch size
             and compare to the cache at relative tolerance 1e-6; writes g9_verify.json

Series (both strictly causal: the forecast for session t uses data through the close of t only)
  VOL  vol20(t) = annualized sample vol of the 20 QLD* daily returns ending at close t
       (screen.vol_n on screen.synthetic_2x QLD*; identical to VT35's vol20). Context = vol20 over
       the last min(available, 1024) sessions; available starts at index VOL_N (first defined value).
       vol_tfm(t) = median forecast at horizon 20 (= forecast of vol20(t+20), purely future returns).
  DIR  logtr(t) = log QQQ total-return close: tr[0] = close[0], tr[t] = tr[t-1]*(close[t]+div[t])/close[t-1].
       Context = logtr over the last min(t+1, 1024) sessions. Stored: median forecast at h=20 and h=60.
Median = quantile index 5 (q0.5) of the quantile output, which equals the library's point forecast.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True  # leave no __pycache__ in the precedent's directory
sys.path.insert(0, str(ROOT / "research" / "trend_robustness_v2"))
import screen as trv2  # noqa: E402  (load, synthetic_2x, vol_n, EXP_Q, VOL_N; main() is not run)

REPO_ID = "google/timesfm-2.5-200m-pytorch"
REVISION = "1d952420fba87f3c6dee4f240de0f1a0fbc790e3"
WEIGHTS_SHA256 = "2f776efe6245e42b24bc4153ffdf61810140210e4bd3b01fb21f7aa779ab6ce8"
MAX_CONTEXT, MIN_CONTEXT, HORIZON = 1024, 250, 64
VOL_H = 20  # vol_tfm = median forecast at h = VOL_H
DIR_HS = (20, 60)
BATCH, VERIFY_BATCH, THREADS = 32, 8, 4
VERIFY_N, VERIFY_SEED, VERIFY_RTOL = 50, 20261012, 1e-6
MEDIAN_IDX = 5  # quantile output: [mean, q0.1, ..., q0.9]; index 5 = q0.5
FORECAST_FLAGS = dict(  # documented configuration of the model card at REVISION
    normalize_inputs=True,
    use_continuous_quantile_head=True,
    force_flip_invariance=True,
    infer_is_positive=True,
    fix_quantile_crossing=True,
)
OUT = HERE / "forecasts.csv"
META = HERE / "forecasts.meta.json"
VERIFY_OUT = HERE / "g9_verify.json"
COLS = ("date", "t", "vol20", "vol_ctx", "vol_tfm", "logtr", "dir_ctx", "dir_h20", "dir_h60")
VALUE_COLS = ("vol_tfm", "dir_h20", "dir_h60")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_series():
    """Return days, vol20 [T] (NaN for t < VOL_N), logtr [T]; uses trend_robustness_v2 helpers."""
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    if np.isnan(q[:2]).any():
        raise SystemExit("QQQ open/close missing on a common session")
    _, qsyn_c = trv2.synthetic_2x(q[0], q[1], q[2], y, trv2.EXP_Q)
    vol = trv2.vol_n(qsyn_c)[0]
    g = np.ones(len(days))
    g[1:] = (q[1][1:] + q[2][1:]) / q[1][:-1]
    logtr = np.log(q[1][0] * np.cumprod(g))
    return days, vol, logtr


def first_forecast_t() -> int:
    """Earliest t at which BOTH series have >= MIN_CONTEXT values (VOL is the binding one)."""
    return max(trv2.VOL_N + MIN_CONTEXT - 1, MIN_CONTEXT - 1)


def contexts(vol, logtr, ts):
    vin, din = [], []
    for t in ts:
        lo_v = max(trv2.VOL_N, t - MAX_CONTEXT + 1)
        lo_d = max(0, t - MAX_CONTEXT + 1)
        cv, cd = vol[lo_v : t + 1].copy(), logtr[lo_d : t + 1].copy()  # data through close t only
        if len(cv) < MIN_CONTEXT or len(cd) < MIN_CONTEXT:
            raise SystemExit(f"context too short at t={t}")
        if not (np.isfinite(cv).all() and np.isfinite(cd).all()):
            raise SystemExit(f"non-finite context at t={t}")
        vin.append(cv)
        din.append(cd)
    return vin, din


def load_model(batch: int):
    import torch
    import timesfm

    torch.set_num_threads(THREADS)
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(REPO_ID, revision=REVISION, torch_compile=False)
    if str(model.model.device) != "cpu":
        raise SystemExit(f"expected CPU device, got {model.model.device}")
    model.compile(timesfm.ForecastConfig(max_context=MAX_CONTEXT, max_horizon=HORIZON,
                                         per_core_batch_size=batch, **FORECAST_FLAGS))
    return model


def run_model(model, inputs):
    """Median paths [n, HORIZON] as float64 (exact widening of the model's float32 output)."""
    _, qf = model.forecast(horizon=HORIZON, inputs=list(inputs))  # library appends padding to the list
    med = np.asarray(qf[..., MEDIAN_IDX], dtype=np.float64)
    if med.shape != (len(inputs), HORIZON) or not np.isfinite(med).all():
        raise SystemExit(f"bad forecast output {med.shape}")
    return med


def forecast_rows(model, days, vol, logtr, ts, chunk=1024, log=True):
    rows = []
    t0 = time.time()
    for k in range(0, len(ts), chunk):
        part = ts[k : k + chunk]
        vin, din = contexts(vol, logtr, part)
        mv, md = run_model(model, vin), run_model(model, din)
        for j, t in enumerate(part):
            rows.append({
                "date": days[t], "t": int(t), "vol20": float(vol[t]), "vol_ctx": len(vin[j]),
                "vol_tfm": float(mv[j, VOL_H - 1]),
                "logtr": float(logtr[t]), "dir_ctx": len(din[j]),
                "dir_h20": float(md[j, DIR_HS[0] - 1]), "dir_h60": float(md[j, DIR_HS[1] - 1]),
            })
        if log:
            print(f"  {k + len(part)}/{len(ts)} dates, {time.time() - t0:.0f}s", flush=True)
    return rows


def versions():
    import torch
    import timesfm  # noqa: F401
    from importlib.metadata import version

    return {"python": platform.python_version(), "timesfm": version("timesfm"), "torch": torch.__version__,
            "numpy": np.__version__, "huggingface_hub": version("huggingface_hub"),
            "platform": platform.platform(), "torch_threads": THREADS, "device": "cpu"}


def weights_path():
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(REPO_ID, "model.safetensors", revision=REVISION))


def main_generate():
    wp = weights_path()
    if sha256(wp) != WEIGHTS_SHA256:
        raise SystemExit("checkpoint sha256 mismatch")
    days, vol, logtr = build_series()
    T = len(days)
    ts = list(range(first_forecast_t(), T))
    print(f"forecasting {len(ts)} dates x 2 series: {days[ts[0]]} -> {days[ts[-1]]}")
    model = load_model(BATCH)
    t0 = time.time()
    rows = forecast_rows(model, days, vol, logtr, ts)
    wall = time.time() - t0
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(COLS)
        for r in rows:
            w.writerow([r[c] if isinstance(r[c], (str, int)) else repr(r[c]) for c in COLS])
    meta = {
        "model": {"repo_id": REPO_ID, "revision": REVISION, "weights_sha256": WEIGHTS_SHA256,
                  "torch_compile": False, "max_context": MAX_CONTEXT, "horizon": HORIZON,
                  "effective_forecast_config": {k: getattr(model.forecast_config, k) for k in (
                      "max_context", "max_horizon", "normalize_inputs", "per_core_batch_size",
                      "use_continuous_quantile_head", "force_flip_invariance", "infer_is_positive",
                      "fix_quantile_crossing", "return_backcast", "window_size")},
                  "median": "quantile output index 5 (q0.5)"},
        "versions": versions(),
        "inputs": {str(p.relative_to(ROOT)): sha256(p) for p in (trv2.BARS, trv2.DTB3)},
        "dates": {"first": days[ts[0]], "last": days[ts[-1]], "n": len(ts), "first_t": ts[0]},
        "min_context": MIN_CONTEXT, "vol_h": VOL_H, "dir_h": list(DIR_HS),
        "wall_seconds": round(wall, 1),
        "forecasts_csv_sha256": sha256(OUT),
    }
    META.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.name} ({len(rows)} rows, {wall:.0f}s) and {META.name}")


def main_verify():
    with OUT.open() as fh:
        cache = {int(r["t"]): r for r in csv.DictReader(fh)}
    meta = json.loads(META.read_text())
    if sha256(OUT) != meta["forecasts_csv_sha256"]:
        raise SystemExit("forecasts.csv changed since generation")
    days, vol, logtr = build_series()
    keys = sorted(cache)
    rng = np.random.default_rng(VERIFY_SEED)
    ts = sorted(int(keys[i]) for i in rng.choice(len(keys), VERIFY_N, replace=False))
    model = load_model(VERIFY_BATCH)  # different batch size from generation on purpose
    rows = forecast_rows(model, days, vol, logtr, ts, log=False)
    worst, fails = 0.0, []
    for r in rows:
        c = cache[r["t"]]
        if c["date"] != r["date"] or int(c["vol_ctx"]) != r["vol_ctx"] or int(c["dir_ctx"]) != r["dir_ctx"]:
            fails.append({"t": r["t"], "reason": "date/context mismatch"})
        for col in VALUE_COLS:
            a, b = float(c[col]), r[col]
            rel = abs(a - b) / max(abs(a), 1e-12)
            worst = max(worst, rel)
            if not rel <= VERIFY_RTOL:
                fails.append({"t": r["t"], "col": col, "cache": a, "regen": b, "rel": rel})
    out = {"n": len(ts), "seed": VERIFY_SEED, "batch": VERIFY_BATCH, "rtol": VERIFY_RTOL,
           "dates": [days[t] for t in ts], "max_rel_diff": worst, "failures": fails,
           "pass": not fails, "versions": versions(), "forecasts_csv_sha256": meta["forecasts_csv_sha256"]}
    VERIFY_OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"G9: {len(ts)} dates, max rel diff {worst:.3e}, {'PASS' if not fails else 'FAIL'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="G9 reproducibility check against the cache")
    ap.add_argument("--bars", type=Path, help="override input bars (synthetic self-test only)")
    ap.add_argument("--dtb3", type=Path, help="override DTB3 file (synthetic self-test only)")
    ap.add_argument("--outdir", type=Path, help="override output directory (synthetic self-test only)")
    a = ap.parse_args()
    if a.bars or a.dtb3 or a.outdir:
        if not (a.bars and a.dtb3 and a.outdir):
            raise SystemExit("--bars, --dtb3 and --outdir must be given together")
        trv2.BARS, trv2.DTB3 = a.bars.resolve(), a.dtb3.resolve()
        ROOT = Path("/")  # meta paths become absolute
        OUT, META, VERIFY_OUT = (a.outdir / p.name for p in (OUT, META, VERIFY_OUT))
    main_verify() if a.verify else main_generate()
