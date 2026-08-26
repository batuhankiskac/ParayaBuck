#!/usr/bin/env python3
"""Canonical research entry point for reproducing the final historical validation.

This is a thin shim that documents and delegates to the frozen canonical runner:

    scripts/run_symmetric_v5_validation.py  (K_FINAL_PARITY_FREEZE / L_FINAL_PARITY_RESULTS)

Historical V3 research is FROZEN. Do not create V4, sweeps, or parameter changes here.
This entry exists only to satisfy the post-research cleanup contract that there be
exactly one clearly documented entry point under `scripts/research/` while avoiding
unnecessary churn of the already-frozen path. The actual implementation and frozen
FROZEN_V3_PARAMS live in the target file; this shim simply re-exports its `main()`.

Usage:
    python scripts/research/run_v3_canonical_validation.py
    # equivalent to:
    python scripts/run_symmetric_v5_validation.py
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANONICAL = REPO / "scripts" / "run_symmetric_v5_validation.py"


def main() -> None:
    if not CANONICAL.exists():
        print(f"canonical runner missing: {CANONICAL}", file=sys.stderr)
        raise SystemExit(1)
    # Delegate to the frozen canonical module; preserves argv and exit semantics.
    runpy.run_path(str(CANONICAL), run_name="__main__")


if __name__ == "__main__":
    main()
