"""Provenance stamp for RESULTS.json and a comparison that refuses mismatched stamps.

A stamp records: SHA-256 of every input file, the ALFRED vintage if one was used, the git commit
of the code, and the Python / numba / numpy / pandas versions. `compare_results` raises
ProvenanceMismatch unless two stamps agree on data (every input hash, the vintage) and on code
(git commit and the package versions).
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ProvenanceMismatch(Exception):
    """Two results were produced from different data or code."""


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha(cwd=None) -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd or ROOT), capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else "unknown"


def package_versions() -> dict:
    out = {"python": platform.python_version()}
    for name in ("numba", "numpy", "pandas"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", "unknown")
        except Exception:  # noqa: BLE001 - a missing package is recorded, not fatal
            out[name] = "missing"
    return out


def stamp(input_paths: dict, alfred_vintage=None, cwd=None) -> dict:
    """Build the provenance dict. `input_paths` maps a logical name to a file path."""
    data = {name: sha256_file(p) for name, p in sorted(input_paths.items())}
    return {
        "data_sha256": data,
        "alfred_vintage": alfred_vintage,
        "git_sha": git_sha(cwd),
        "versions": package_versions(),
    }


def _data_view(s: dict) -> dict:
    return {"data_sha256": s.get("data_sha256"), "alfred_vintage": s.get("alfred_vintage")}


def _code_view(s: dict) -> dict:
    return {"git_sha": s.get("git_sha"), "versions": s.get("versions")}


def compare_results(a: dict, b: dict) -> bool:
    """True when both results carry the same data and code stamps; raises otherwise."""
    sa = a.get("provenance") if isinstance(a, dict) and "provenance" in a else a
    sb = b.get("provenance") if isinstance(b, dict) and "provenance" in b else b
    if _data_view(sa) != _data_view(sb):
        raise ProvenanceMismatch("data stamps disagree: " + json.dumps(
            {"a": _data_view(sa), "b": _data_view(sb)}, sort_keys=True)[:2000])
    if _code_view(sa) != _code_view(sb):
        raise ProvenanceMismatch("code stamps disagree: " + json.dumps(
            {"a": _code_view(sa), "b": _code_view(sb)}, sort_keys=True)[:2000])
    return True
