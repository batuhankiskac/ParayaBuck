"""batch_screen.registry -- the multiple-testing ledger and the pre-registration-by-commit gate.

`REGISTRY.jsonl` has one line per arm ever screened (append-only, never rewritten, never
deleted):

    {"idea": ..., "arm": ..., "arm_key": ..., "spec": {...}, "spec_hash": ..., "code_hash": ...,
     "batch": ..., "date": "YYYY-MM-DD", "status": "screened"|"needs_kernel", "note": ...,
     "git": {"head": ..., "idea_commit": ...}}

The runner refuses to screen an arm whose idea module is not committed in git *before* the run:
the module's working-tree bytes must equal the bytes at HEAD (a tracked, unmodified file), so the
`code_hash` recorded in the ledger is the committed one. The total line count N is the number of
trials the deflation corrects for.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "REGISTRY.jsonl"


class RegistryError(Exception):
    """The arm is not screenable (uncommitted code) or the ledger is inconsistent."""


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def rel(path) -> str:
    return str(Path(path).resolve().relative_to(ROOT))


def head_commit() -> str:
    return _git("rev-parse", "HEAD")


def idea_commit(path) -> str:
    return _git("log", "-1", "--format=%H", "--", rel(path))


def committed_bytes(path) -> bytes | None:
    """The module's bytes at HEAD, or None when it is not committed there."""
    p = subprocess.run(["git", "show", f"HEAD:{rel(path)}"], cwd=ROOT, capture_output=True)
    return p.stdout if p.returncode == 0 else None


def require_committed(idea_id: str, path) -> dict:
    """Raise unless the idea module's current bytes are exactly its bytes at HEAD."""
    data = committed_bytes(path)
    if data is None:
        raise RegistryError(
            f"{rel(path)} is not committed at HEAD: pre-register the idea (commit it) before screening"
        )
    disk = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != hashlib.sha256(disk).hexdigest():
        raise RegistryError(
            f"{rel(path)} differs from its committed version: commit it before screening "
            "(the registry pins the committed spec and code)"
        )
    return {"head": head_commit(), "idea_commit": idea_commit(path)}


def read(path=None) -> list:
    """Every ledger line, in file order."""
    p = Path(path) if path is not None else REGISTRY
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def append(entry: dict, path=None) -> dict:
    """Append one line (create the file if needed). Never rewrites existing lines."""
    p = Path(path) if path is not None else REGISTRY
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
        fh.flush()
    return entry


def entry_for(idea_id: str, arm_id: str, spec: dict, spec_hash: str, code_hash: str, batch: str,
              status: str, git: dict, note: str = "") -> dict:
    return {
        "idea": idea_id,
        "arm": arm_id,
        "arm_key": f"{idea_id}/{arm_id}",
        "spec": spec,
        "spec_hash": spec_hash,
        "code_hash": code_hash,
        "batch": batch,
        "date": date.today().isoformat(),
        "status": status,
        "note": note,
        "git": git,
    }


def history(idea_id: str, arm_id: str, path=None) -> list:
    """Every ledger line of one arm, in file order."""
    return [e for e in read(path) if e["idea"] == idea_id and e["arm"] == arm_id]


def is_rerun(idea_id: str, arm_id: str, spec_hash: str, code_hash: str, path=None) -> bool:
    return any(e["spec_hash"] == spec_hash and e["code_hash"] == code_hash
               for e in history(idea_id, arm_id, path))


def n_lines(path=None) -> int:
    """The number of ledger lines (one per declaration/screening run, never rewritable)."""
    return len(read(path))


def n_trials(path=None) -> int:
    """N, the multiple-testing count: the distinct `(idea, arm, spec_hash)` ever registered.

    A re-run of the same arm with the same spec/code is the *same* hypothesis (the ledger still
    gets its line), so it is not a second trial; an arm re-specified into a new `spec_hash` is.
    """
    return len({(e["idea"], e["arm"], e["spec_hash"]) for e in read(path)})


def ledger_arms(path=None) -> list:
    """The distinct (idea, arm) pairs ever registered, with their most recent line."""
    seen = {}
    for e in read(path):
        seen[e["arm_key"]] = e
    return [seen[k] for k in sorted(seen)]


__all__ = [
    "REGISTRY",
    "n_lines",
    "RegistryError",
    "append",
    "committed_bytes",
    "entry_for",
    "head_commit",
    "history",
    "idea_commit",
    "is_rerun",
    "ledger_arms",
    "n_trials",
    "read",
    "rel",
    "require_committed",
]
