#!/usr/bin/env python3
"""CI gate: verify production cache/runtime files unchanged by tests.

Usage:
  python scripts/check_production_isolation.py --record /tmp/before.json
  pytest
  python scripts/check_production_isolation.py --verify /tmp/before.json

Records mtime_ns and size for protected paths; verifies byte-level identity.
Fails with exit 1 if any protected file was created or mutated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROTECTED = [
    Path("data/cache/warmup.sqlite3"),
    Path("data/cache/warmup.sqlite3-wal"),
    Path("data/cache/warmup.sqlite3-shm"),
    Path("data/runtime/parayabuck.sqlite3"),
    Path("data/runtime/parayabuck.sqlite3-wal"),
    Path("data/runtime/parayabuck.sqlite3-shm"),
    Path("data/runtime/observability.sqlite3"),
    Path("data/runtime/observability.sqlite3-wal"),
    Path("data/runtime/observability.sqlite3-shm"),
    Path("data/runtime/health_snapshot.json"),
]


def _sig(p: Path):
    if not p.exists():
        return None
    st = p.stat()
    # hash first 1MB for byte-level check if size small, otherwise size+mtime
    h = None
    if st.st_size < 50 * 1024 * 1024:
        try:
            hasher = hashlib.sha256()
            with p.open("rb") as f:
                hasher.update(f.read())
            h = hasher.hexdigest()
        except Exception:
            h = None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha256": h}


def record(out: Path):
    data = {str(p): _sig(p) for p in PROTECTED}
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Recorded {len(data)} protected paths to {out}")


def verify(inp: Path):
    before = json.loads(inp.read_text(encoding="utf-8"))
    failures = []
    for p in PROTECTED:
        s = str(p)
        before_sig = before.get(s)
        after_sig = _sig(p)
        if before_sig != after_sig:
            failures.append((s, before_sig, after_sig))
    if failures:
        print("PRODUCTION ISOLATION VIOLATION — protected files mutated by tests:")
        for s, b, a in failures:
            print(f"  {s}: before={b} after={a}")
        sys.exit(1)
    print("Production isolation OK — no protected files mutated")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", type=Path, help="record to file")
    ap.add_argument("--verify", type=Path, help="verify against file")
    args = ap.parse_args()
    if args.record:
        record(args.record)
    elif args.verify:
        verify(args.verify)
    else:
        ap.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
