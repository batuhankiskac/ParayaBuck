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

from .api import idea_path, spec_hash

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "REGISTRY.jsonl"


class RegistryError(Exception):
    """The arm is not screenable (uncommitted code) or the ledger is inconsistent."""


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def rel(path) -> str:
    """The repo-relative path (an out-of-tree path -- a probe staged in a temp dir -- is returned
    absolute: git simply has nothing for it, which is why such a run must skip the commit gate)."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


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


# ----------------------------------------------------------------------------- merge safety
def blob_at(commit: str, path) -> bytes | None:
    """The file's bytes at one commit, or None when it does not exist there."""
    if not commit:
        return None
    p = subprocess.run(["git", "show", f"{commit}:{rel(path)}"], cwd=ROOT, capture_output=True)
    return p.stdout if p.returncode == 0 else None


def _arms_of(blob: bytes, idea_id: str):
    """The `ARMS` of a module's source bytes (executed in a throwaway namespace)."""
    ns = {}
    exec(compile(blob, f"<HEAD:research/ideas/{idea_id}.py>", "exec"), ns)  # noqa: S102
    arms = ns.get("ARMS")
    return arms if isinstance(arms, dict) else None


def verify_ledger(path=None) -> dict:
    """Re-verify every ledger line against git, and every registered arm against HEAD.

    Two independent checks, both needed when several plugin branches merge into one ledger:

    1. **line identity** — the module's bytes *at the line's own recorded commit* must hash to the
       line's `code_hash`. A line therefore describes code that was really committed and is not the
       product of a bad union merge or an edited ledger. Lines whose `commit_gate` was bypassed
       (`--allow-dirty`) cannot be checked this way and are listed under `skipped`.
    2. **arm identity at HEAD** — for every registered `(idea, arm)`, the module at HEAD must exist
       and define the arm, and the arm's spec must hash to the *latest* recorded `spec_hash`. A
       difference means the ledger and the code have drifted: an arm renamed or deleted by a merge,
       or edited after screening without a new ledger line (`stale`, by contrast, only says the
       module has changed since its last screen — expected while a branch grows, so it is reported
       but not an error).

    `RegistryError` is raised by `run.run_batch` when `ok` is false.
    """
    lines = read(path)
    problems, skipped, stale = [], [], []
    by_arm, blobs, arms_cache = {}, {}, {}
    for i, e in enumerate(lines):
        if e.get("git", {}).get("commit_gate") != "pass":
            skipped.append({"line": i, "arm_key": e.get("arm_key"), "why": "commit gate skipped"})
            continue
        issue = e["idea"]
        commit = e["git"].get("idea_commit") or ""
        key = (commit, issue)
        if key not in blobs:
            b = blob_at(commit, idea_path(issue))
            blobs[key] = None if b is None else hashlib.sha256(b).hexdigest()
        if blobs[key] != e["code_hash"]:
            problems.append({"line": i, "arm_key": e.get("arm_key"), "kind": "code_hash",
                             "commit": commit, "recorded": e["code_hash"],
                             "at_commit": blobs[key]})
        by_arm.setdefault((issue, e["arm"]), []).append((i, e))
    for (issue, arm), entries in sorted(by_arm.items()):
        latest = entries[-1][1]
        if issue not in arms_cache:
            b = committed_bytes(idea_path(issue))
            try:
                arms_cache[issue] = (b, None if b is None else _arms_of(b, issue))
            except Exception as exc:  # a module that does not execute at HEAD
                arms_cache[issue] = (b, exc)
        blob, arms = arms_cache[issue]
        if blob is None:
            problems.append({"arm_key": latest.get("arm_key"), "kind": "module_missing_at_head"})
            continue
        if isinstance(arms, Exception):
            problems.append({"arm_key": latest.get("arm_key"), "kind": "module_error_at_head",
                             "error": str(arms)})
            continue
        if not arms or arm not in arms:
            problems.append({"arm_key": latest.get("arm_key"), "kind": "arm_missing_at_head"})
            continue
        spec_hash_at_head = spec_hash(arms[arm])
        if spec_hash_at_head != latest["spec_hash"]:
            problems.append({"arm_key": latest.get("arm_key"), "kind": "spec_drift",
                             "recorded": latest["spec_hash"], "at_head": spec_hash_at_head})
        if hashlib.sha256(blob).hexdigest() != latest["code_hash"]:
            stale.append(latest.get("arm_key"))
    return {"ok": not problems, "head": head_commit(), "lines": len(lines),
            "arms": len(by_arm), "n_trials": n_trials(path), "problems": problems,
            "stale": sorted(stale), "skipped": skipped}


__all__ = [
    "REGISTRY",
    "n_lines",
    "RegistryError",
    "append",
    "blob_at",
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
    "verify_ledger",
    "require_committed",
]
