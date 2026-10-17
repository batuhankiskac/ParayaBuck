"""IC34 -- off-state in defensive sectors (XLU / XLP). idea_bank_v2, arms A/B/C.

    A  while off, hold 0.98 XLU
    B  while off, hold 0.98 XLP
    C  while off, hold 0.49 XLU + 0.49 XLP (a fixed equal split, no selection)  -> NEEDS_KERNEL

Frozen readings (parameters fixed in `ARMS`; nothing tuned):

* **"the vote"** is production's E2 trend vote on the window's own price close (`fe_signals.e2_up`);
  `off` is that vote being off -- the framework's off-state recipe (`README.md` section 1b).
* **"hold 0.98 XLU/XLP while off"** is read as a *substitution*: leg A holds production's own target
  only while the vote is on and is exactly zero while it is off, leg B holds the one fixed
  defensive sector at 0.98, so the total never exceeds 0.98. Stated consequence: while E2 is off but
  the early re-entry is active, production's half-weight QLD path is *replaced* by the sector.
  No selection and no rotation: the sector is fixed per arm and never switched.
* **arm C is not expressible on the current kernel** and is registered as `needs_kernel`: it needs
  XLU *and* XLP *and* leg A's QLD* in one account at once -- three instruments, while the two-leg
  kernel holds the arm's own leg plus exactly one other book. The 0.49/0.49 split is frozen in the
  spec; a copy of the kernel with a third book (and its own parity cell) is a separate study.
* **window**: the SPDR sector ETFs start 1998-12-22, *before* the published main window, so
  `start = "auto"` resolves to the published window (the declaration is kept for the record of what
  the arm reads). Windows before 1998-12-22 (the `^GSPC` 1957-1999 and `M1`/`M2` OOS windows) are
  `NotApplicable` through the framework's `leg_b_usable` rule -- the bank's "SPDRs absent on
  GSPC_1957_1999/M1/M2 -> 3 applicable windows; reduced 3-window G7".
* **execution** is production K on both books (0.5 pp band, A1u gain band, H2 December deferral, per
  leg on its own FIFO lot book, next-open fills, per-leg costs).
* **leg-B prices** are the sector ETF's adjusted close (`sec_xlu`/`sec_xlp`, registered by the
  batch_screen_v1 build) used for both that leg's fill and its close -- already on file, no new
  download, and the bank states the same `data/in_repo` files.
"""

from __future__ import annotations

from research.fast_engine import fe_signals

#: arm C's own declaration -- a per-arm `spec["needs_kernel"]`, *not* the module-level
#: `NEEDS_KERNEL` (which would refuse arms A and B as well)
_ARM_C_NEEDS_KERNEL = ("IC34 arm C holds XLU and XLP and leg A's QLD* in one account at once: "
                       "three instruments, while the two-leg kernel holds the arm's own leg plus "
                       "exactly one other book. Arms A and B are screened normally.")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "sec_xlu", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98},
    "B": {"rule": "K", "leg": "2x", "leg_b": "sec_xlp", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98},
    "C": {"rule": "K", "leg": "2x", "leg_b": "sec_xlu", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "split", "weight": 0.49, "leg_b2": "sec_xlp",
          "needs_kernel": _ARM_C_NEEDS_KERNEL},
    # --- batch_bank_v4b: the *same* IC34-C arm on the N-leg kernel (`research/fast_engine3`),
    #     which holds leg A's QLD* and the two defensive sectors in one cash pool. A new arm name
    #     (`C_N`) keeps the pinned `C` spec byte-identical (the old arm's spec_hash must not move).
    #     `targets` returns the `{leg: wants}` form only for a spec that declares `"legs"`, so arms
    #     A/B/C take the unchanged two-leg code path.
    "C_N": {"rule": "K", "legs": ["2x", "sec_xlu", "sec_xlp"], "cash_yield": "none",
            "start": "auto", "inputs": ["sec_xlu", "sec_xlp"], "form": "split", "weight": 0.49},
}

def production_wants(data):
    """Production's target series: E2's vote plus the half-weight early re-entry, vol-sized."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)

def off_vote(data):
    """`(P, T)` bool: the E2 trend vote is off on this session."""
    return ~fe_signals.e2_up(data.price)

def targets(data, spec):
    if spec.get("legs"):
        return _three_leg_targets(data, spec)
    wants = production_wants(data)
    off = off_vote(data)
    return wants * (~off), float(spec["weight"]) * off, spec["leg_b"]

def _three_leg_targets(data, spec):
    """`C_N`: the same substitution on the N-leg kernel, as `{leg: wants}`.

    While E2 is off, leg A's QLD* is zero and the one fixed defensive split holds `0.49` XLU and
    `0.49` XLP (the bank's "fixed equal split, no selection"), so the account never exceeds 0.98.
    """
    wants = production_wants(data)
    off = off_vote(data)
    w = float(spec["weight"])
    return {"2x": wants * (~off), "sec_xlu": w * off, "sec_xlp": w * off}

__all__ = ["ARMS", "off_vote", "production_wants", "targets"]
