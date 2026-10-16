# plugins_d1 — the off-state idea-bank plugins (IC31–IC38)

`research/ideas/IC31.py` … `IC38.py`: the `idea_bank_v2` off-state family as batch-screen plugins
(plugin API, two-leg kernel and reduced windows per `research/batch_screen/README.md`,
`docs/research/framework_v2.md`). **No arm of this branch has been screened**: no stage 1, no stage
2, no metric, no ledger line. The plugins + data + `EXTRA_DATA.json` entries are the deliverable.

| idea | off-state asset | arms | window | N/A windows | kernel |
|---|---|---|---|---|---|
| IC31 | LQD (investment-grade credit) | A replace 0.98, B residual, C 0.98 gated LQD > SMA200 | `w20020731` | pre-2002 (M1/M2) | two-leg, `leg_b=lqd` |
| IC32 | HYG (high yield) | A replace 0.98, B gated HYG > SMA200, C gated TSM252(price) > 0 | `w20070412` | pre-2007 (M1/M2) | two-leg, `leg_b=hyg` |
| IC33 | TIP (TIPS) | A replace 0.98, B residual, C 0.98 gated 126-session T10YIE change > 0 | `w20031208` | pre-2003 (M1/M2) | two-leg, `leg_b=tip` |
| IC34 | XLU / XLP (defensive sectors) | A 0.98 XLU, B 0.98 XLP, **C needs_kernel** (0.49 XLU + 0.49 XLP, three instruments) | default | M1/M2, `GSPC_1957_1999` | two-leg, `leg_b=sec_xlu`/`sec_xlp` |
| IC35 | GDX (gold miners) | A replace 0.98, B 0.49, C gated GDX > SMA200 | `w20060523` | pre-2006 (M1/M2) | two-leg, `leg_b=gdx` |
| IC36 | GLD/IEF/SHY (risk-parity blend) | A/B/C — **all needs_kernel** (3–4 instruments, monthly rescale) | — | — | none |
| IC37 | IEF (Treasuries) | A 0.98 if real10y > 0.25, B > 1.00, C residual under 0.25 | `w20030103` | pre-2003 (M1/M2) | two-leg, `leg_b=ief` |
| IC38 | 1x index (never fully out) | A 0.98, B 0.49, C `min(0.98, 0.35/vol20_1x)` | default | none (all six OOS windows) | two-leg, `leg_b=1x` |

`research/plugins_d1/build_extra.py` downloads and registers LQD, TIP and GDX (Yahoo chart API
adjusted closes, url/range/sha256 in `DATA_MANIFEST.json`, additive entries in
`research/batch_screen/EXTRA_DATA.json`); HYG, XLU/XLP, IEF and DGS10/T10YIE were already
registered. `research/plugins_d1/check_plugins.py` re-checks the 20 screenable arms' target panels
(caps, the sleeve only while the vote is off, leg A = production's target while on);
`python -m research.batch_screen.smoke --ideas IC31,IC32,IC33,IC34,IC35,IC36,IC37,IC38` proves every
arm runs on every panel kind (the four `needs_kernel` arms report `NEEDS_KERNEL` by design).

## Frozen readings (each module's docstring is the authority)

The bank's arm texts are ambiguous in four places; the reading frozen in the code, with the reason:

1. **"the vote" = the E2 2-of-3 vote** (`fe_signals.e2_up` on the window's own price close), i.e.
   the framework's own off-state recipe (`README.md` §1b, `probes/two_leg.py::gld_offstate`), not
   the production path state `on_eff` (E2 *or* the early re-entry). Reading `off` as `~on_eff`
   would make IC31/IC33/IC37's *residual* arms identical to their "0.98 while off" arms (the
   residual `0.98 - w` would always be 0.98), so the arm text decides it.
2. **"hold 0.98 in X while off; production target otherwise" = a substitution**: leg A holds
   production's target only while the vote is on and is zero while off; the sleeve takes the whole
   off-state slot. Consequence, stated in every module: in the states where E2 is off but the early
   re-entry is active, production's half-weight *2x* path is replaced by the sleeve rather than kept
   alongside it (the alternative reading would put the account above the 0.98 total cap).
3. **"the residual (0.98 - w)" = leg B = max(0, 0.98 - w) while off**, `w` being production's own
   target on that session (its early-path weight or zero); leg A keeps production's target
   unchanged, and no resize band is applied (the arm text names none — unlike IB25's SHY sleeve).
4. **IC38 arm C's outer cap**: the bank writes `min(1, 0.35/vol20_of_the_1x_leg)`; the frozen code
   uses `min(0.98, 0.35/vol20_1x)` — the campaign's published total cap (`fe_config.CAP`, obeyed by
   every other arm and by `sized_wants`) instead of `1`, because a weight of exactly 1.0 leaves the
   account no cash for costs and for the advance-tax funding sale the after-tax stages model, and
   the framework's own plugin checks treat a weight above `CAP` as a bug. The `vol20` is the 1x
   leg's own (`data.legs["1x"][1]`), as the arm text says, not the 2x leg's `data.vol`. Effect on
   the main window: off-state weight mean 0.87, minimum 0.34, 44 % of off-state sessions below
   0.98 — materially different from arm A.
5. **IC32 arm C's "the arm's own close"** is the panel's **price** close (`data.price`), the series
   production's own `TSM252` vote is computed on — the bank lists "the arm's own instrument close"
   separately from the HYG download, and the gate is described as "the momentum leg".
6. **IC37's real yield** is `DGS10 - T10YIE` in the percentage points FRED publishes, so the bank's
   `+0.25 %` / `+1.00 %` are `> 0.25` / `> 1.00`; both series are read `ffill=True` (FRED has no
   value on non-publication sessions) and a window where either is empty is `NotApplicable`.

`needs_kernel`: **IC36** (all three arms: a 3–4-instrument blend in one account, rescaled monthly)
and **IC34 arm C** (XLU + XLP + leg A = three instruments) — registered, never simulated, per
`README.md`'s "not expressible yet" list. No other arm needed a kernel change.
