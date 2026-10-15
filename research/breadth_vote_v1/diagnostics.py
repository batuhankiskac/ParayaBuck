#!/usr/bin/env python3
"""Report-only mechanism diagnostics for breadth_vote_v1 (committed after the pre-registration).

Computes, on the main window, how far each arm's main vote moves away from production E2's, how
much of the position's time is spent in the early leg, and how often the arms' exit timing differs.
No simulation, no gate: signals only. Run from the worktree root.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bvs", HERE / "screen.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

days, o, c, price, spy_m, iwm_m, vol_instr = m.main_inputs(0.0)
comps = m.early_components(price)
ip, sp, wp = m.vote_parts(price), m.vote_parts(spy_m), m.vote_parts(iwm_m)
sess = np.arange(m.START, len(days))  # measured sessions (decision at the close of session i-1)

maj = {"C0": ip[0][0], "B1": m.main_on("B1", ip, sp, wp)[0], "B2": m.main_on("B2", ip, sp, wp)[0],
       "B3": m.main_on("B3", ip, sp, wp)[0], "B4": m.main_on("B4", ip, sp, wp)[0]}
early = {}
for arm in m.ARMS:
    _, factor = m.early_state(price, m.main_on(arm, ip, sp, wp) if arm != "C0" else ip[0], comps)
    early[arm] = factor[0]

T = len(days) - 1
print(f"main window {days[m.START]} -> {days[-1]}, {T - m.START + 1} sessions measured")
print(f"panel majority-hold fractions: instrument {ip[0][0][m.START - 1:T].mean():.4f}, "
      f"SPY member {sp[0][0][m.START - 1:T].mean():.4f}, IWM member {wp[0][0][m.START - 1:T].mean():.4f}")
print(f"vote agreement with the instrument's own majority: SPY {np.mean(sp[0][0][m.START-1:T] == ip[0][0][m.START-1:T]):.4f}, "
      f"IWM {np.mean(wp[0][0][m.START-1:T] == ip[0][0][m.START-1:T]):.4f}")
print()
print("arm | main-vote ON share | differs from C0's ON state (sessions, share) | "
      "flat->long / long->flat transitions vs C0 | early-leg share of sessions | full-weight share")
for arm in m.ARMS:
    v = maj[arm][m.START - 1:T]
    v0 = maj["C0"][m.START - 1:T]
    d = v != v0
    trans = int(np.sum(np.diff(v.astype(np.int8)) == 1)), int(np.sum(np.diff(v.astype(np.int8)) == -1))
    t0 = int(np.sum(np.diff(v0.astype(np.int8)) == 1)), int(np.sum(np.diff(v0.astype(np.int8)) == -1))
    e = early[arm][m.START - 1:T]
    print(f"{arm:3s} {v.mean():.4f} {int(d.sum()):5d} {d.mean():.4f} | {trans} vs C0 {t0} | "
          f"{np.mean(e == 0.5):.4f} | {np.mean(e == 1.0):.4f}")
print()
print("B2 latch: sessions where C0's main vote is OFF but B2's is ON (delayed exits):",
      int(np.sum((~maj["C0"][m.START - 1:T]) & maj["B2"][m.START - 1:T])))
print("B3/B4: sessions where C0's main vote is ON but the arm's is OFF (extra exits): "
      f"B3 {int(np.sum(maj['C0'][m.START-1:T] & ~maj['B3'][m.START-1:T]))}, "
      f"B4 {int(np.sum(maj['C0'][m.START-1:T] & ~maj['B4'][m.START-1:T]))}")


def oos_episode(key):
    d, oo, cc, y, start = m.load_market(key)
    spy2, iwm2 = m.panel_members(d)
    _, c_sc = m.trv2.synthetic_2x(oo, cc, np.zeros_like(cc), y + m.EXTRA_FIN, m.EXP_Q)
    ip2, sp2, wp2 = m.vote_parts(cc), m.vote_parts(spy2), m.vote_parts(iwm2)
    v0 = ip2[0][0][start - 1:]
    v1 = m.main_on("B1", ip2, sp2, wp2)[0][start - 1:]
    print(f"{key} {m.OOS_MARKETS[key][0]}: measured sessions {len(v0)}, "
          f"instrument-own majority ON {v0.mean():.4f}, B1 (SPY vote) ON {v1.mean():.4f}, "
          f"disagreement {np.mean(v0 != v1):.4f}")


print()
print("reduced OOS windows (instrument time-in-market):")
for key in m.OOS_WINDOWS:
    oos_episode(key)
