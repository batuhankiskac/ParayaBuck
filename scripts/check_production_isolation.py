#!/usr/bin/env python3
"""CI gate: prove the test run left every production state directory untouched.

Usage:
  python scripts/check_production_isolation.py --record /tmp/before.json
  pytest
  python scripts/check_production_isolation.py --verify /tmp/before.json

Coverage is by whole directory, recursively, relative to the current working
directory (the repository root in CI): every file the operator owns under
``data/runtime`` (decision DB + WAL, the PAPER/LIVE equity-peak sidecars, the
paper receipt journals, the observability journal, the diagnostic bundles),
``data/cache`` (warmup caches), ``data/backups`` (retention snapshots) and
``logs`` is fingerprinted. A run that mutates a file, deletes one, or creates a
new one under those trees fails the gate, so a test that writes a receipt or the
peak sidecar cannot pass CI just because no individual file was listed by hand.

Fails with exit 1 on any violation, exit 2 when neither mode is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

# Operator-owned state, relative to the repository root. Every entry is walked
# recursively; nothing here is enumerated file by file.
PROTECTED_DIRS = (
    Path("data/runtime"),
    Path("data/cache"),
    Path("data/backups"),
    Path("logs"),
)

_DIR_NAMES = tuple(root.as_posix() for root in PROTECTED_DIRS)

# Beyond this size the fingerprint degrades to (mtime_ns, size): the operator's
# warmup cache can be hundreds of megabytes and hashing it twice per run would
# dominate CI time without adding coverage a write of that magnitude would hide.
_HASH_LIMIT = 50 * 1024 * 1024
_CHUNK = 1024 * 1024


def _iter_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _sig(path: Path) -> dict[str, object] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    sha: str | None = None
    if st.st_size < _HASH_LIMIT:
        try:
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(_CHUNK), b""):
                    hasher.update(chunk)
            sha = hasher.hexdigest()
        except OSError:
            sha = None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha256": sha}


def snapshot() -> dict[str, dict[str, object] | None]:
    """Fingerprint every file under the protected trees, keyed by relative path."""
    files: dict[str, dict[str, object] | None] = {}
    for root in PROTECTED_DIRS:
        for path in _iter_files(root):
            files[path.as_posix()] = _sig(path)
    return files


def record(out: Path) -> None:
    files = snapshot()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dirs": _DIR_NAMES, "files": files}, indent=2), encoding="utf-8")
    print(f"Recorded {len(files)} protected files under {', '.join(_DIR_NAMES)} -> {out}")


def verify(inp: Path) -> None:
    before = json.loads(inp.read_text(encoding="utf-8"))
    before_files = before.get("files") if isinstance(before, dict) else None
    if not isinstance(before_files, dict):
        print(f"PRODUCTION ISOLATION VIOLATION — {inp} is not a snapshot written by --record")
        sys.exit(1)
    after_files = snapshot()

    created = sorted(set(after_files) - set(before_files))
    removed = sorted(set(before_files) - set(after_files))
    mutated = sorted(path for path in set(before_files) & set(after_files) if before_files[path] != after_files[path])

    if created or removed or mutated:
        print("PRODUCTION ISOLATION VIOLATION — production state changed by the test run:")
        for path in created:
            print(f"  CREATED {path}")
        for path in removed:
            print(f"  REMOVED {path}")
        for path in mutated:
            print(f"  MUTATED {path}: before={before_files[path]} after={after_files[path]}")
        sys.exit(1)
    print(f"Production isolation OK — {len(after_files)} protected files unchanged")


def main() -> None:
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
