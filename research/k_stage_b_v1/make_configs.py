#!/usr/bin/env python3
"""Build the k_stage_b_v1 arm configs from the archived R2 arm config.

Base: ``stage_b/configs/R2_reference.yaml`` — the archived r2_stage_b_v1 R2 arm
config, i.e. the shipped ``config/settings.yaml`` of 2026-10-14 before A1u/H2
were added to the ``execution`` block, restored byte-for-byte from tag
``archive/research/r2-stage-b-v1-2026-10``. The only edit is the ``execution``
block, so every arm shares the shipped strategy, risk, cost and universe
settings and differs only in the two order-existence rules under test:

* ``R2off.yaml`` -- ``downsize_gain_band: null`` + deferral 0 = production R2.
* ``A1u.yaml``   -- the shipped band schedule, deferral 0.
* ``K.yaml``     -- the shipped band schedule + deferral 10 (= shipped config).

The E2 arm is built from ``stage_b/configs/E2_reference.yaml`` (the same tag's
E2 arm config) and needs ``--drop-early-reentry``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = HERE / "stage_b/configs/R2_reference.yaml"
E2_SRC = HERE / "stage_b/configs/E2_reference.yaml"
OUT = HERE / "stage_b/configs"

ANCHOR = "  minimum_order_notional: 10\n"

BAND_ON = """\
  # 2026-10-15 (gain_band_v3, A1u): a downward resize between two positive
  # weights (never an entry, an exit to zero or an up-resize) fills only when
  # the drift exceeds the band selected by the USD FIFO gain fraction of the
  # units the sell would consume, over the bot's own lots (0.5pp / 3pp / 8pp
  # at g_frac <= 1% / <= 5% / > 5%). Per-lot cost basis includes buy fees; a
  # missing or inconsistent lot book falls back to rebalance_band_pct exactly.
  # Set the block to null to restore the constant band.
  downsize_gain_band:
    thresholds: [0.01, 0.05]
    bands: [0.005, 0.03, 0.08]
"""

BAND_OFF = """\
  # A1u explicitly OFF for the reproduction and control arms.
  downsize_gain_band: null
"""

DEFERRAL_ON = """\
  # 2026-10-15 (band_h2_combo_v1, H2): a downward resize between two positive
  # weights whose FILL session is one of the last 10 NYSE sessions of December
  # is postponed to the first January decision. 0 disables it.
  december_downsize_deferral_sessions: 10
"""

DEFERRAL_OFF = """\
  # H2 explicitly OFF: no December deferral window.
  december_downsize_deferral_sessions: 0
"""

HEADER = "# k_stage_b_v1 arm {arm}: {note}\n"


def build(arm: str, note: str, band: str, deferral: str, source: Path | None = None) -> None:
    text = (source or SRC).read_text()
    assert text.count(ANCHOR) == 1, "execution anchor not unique"
    text = text.replace(ANCHOR, ANCHOR + band + deferral)
    (OUT / f"{arm}.yaml").write_text(HEADER.format(arm=arm, note=note) + text)
    print("wrote", OUT / f"{arm}.yaml")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    build("R2off", "production R2 with A1u and H2 explicitly off", BAND_OFF, DEFERRAL_OFF)
    build("A1u", "production R2 + the A1u gain-scaled downsize band", BAND_ON, DEFERRAL_OFF)
    build("K", "production R2 + A1u + H2 = the shipped policy", BAND_ON, DEFERRAL_ON)
    # The reproduction arm: the r2_stage_b_v1 E2 arm config carries no execution
    # block keys, so on current src it would inherit the shipped A1u+H2 rules.
    # E2 (1.1.0) predates both, so they are set explicitly off, exactly as the
    # band-off golden does.
    build(
        "E2",
        "1.1.0 reproduction arm (needs --drop-early-reentry), A1u and H2 off",
        BAND_OFF,
        DEFERRAL_OFF,
        source=E2_SRC,
    )
    # The shipped configuration, verbatim, as a third-party check on K.yaml.
    shutil.copyfile(ROOT / "config/settings.yaml", OUT / "shipped.yaml")
    print("wrote", OUT / "shipped.yaml")


if __name__ == "__main__":
    main()
