# rw_boot_audit_v1 — the reduced-window two-leg bootstrap Δ was a framework bug (2026-10-16)

> Evidence: tag `archive/research/rw-boot-audit-v1-2026-10` (not on `main`).

> Evidence: the study `research/rw_boot_audit_v1/` (`PREREGISTRATION.md`, `repro.py`, `parity_cells.py`,
> `screen.py`, `repro.json`, `PRE_FIX_BASELINE.npz`, `PRE_FIX_SUMMARY.json`, `PARITY_LEGB.json`,
> `HARNESS.json`, `RESULTS.json`, `SUMMARY.txt`, `DATA_MANIFEST.json`); the fix
> `research/batch_screen/api.py` (`MarketData.leg_series` / `MarketData.chunk_price_path`) and its
> regression tests `research/batch_screen/tests/test_two_leg.py`; the framework amendment
> `research/batch_screen/PREREGISTRATION.md` §6.6; the re-screen batch
> `research/batch_screen/results/rw_fix_v1/` (+ `_control*.json`, `timing.json`), the ledger
> `REGISTRY.jsonl` and `results/DEFLATION.json`; the defect the audit was opened on,
> `docs/research/batch_bank_v3.md` §`Known issue` / `holdout_insufficient` and
> `research/ic37c_confirm_v1` (branch `research/ic37c_confirm_v1`). The engine
> (`research/fast_engine/`), the two-leg kernel (`research/fast_engine2/`), the option package
> (`research/fast_engine_opts/`) and every plugin (`research/ideas/`) are **untouched**.

**Verdict: a bug — a framework defect, not a real edge.** The bootstrap leg-B path of every
two-leg arm whose leg B is a **registered extra-data key** was rebuilt from the drawn source
sessions' *levels* rather than from their *own returns*, so the return the kernel read at each of
the 20-session block jumps spanned the calendar. On the published `IC37/C` draw that is **120 of
2,776** columns with `max |return − 1| = 1.229`, against 1.9 % for the instrument's own largest
daily move. The spurious path inflated the arm's bootstrap CAGR by a median of **+18.5 pp pre-tax**
and **+12.2 pp after tax**; repaired, the same draw gives +0.40 pp and −0.24 pp, in line with the
arm's own fixed-window Δs (+0.59 / +0.18 / +0.64 pp). **`IC37/A`, `IC37/B` and `IC37/C` change
verdict**: from `holdout_insufficient` (a Stage-1 *pass* on the bug) to **`reject_stage1`** on a
real, slightly negative B1 median. Nothing else changes verdict; nothing is declared.

## 1. The symptom, and why it could not be real

`IC37/C` ("while the trend vote is off, hold the residual `0.98 − w` in IEF when the 10-year real
yield > +0.25 %", window `w20030103`, pool `(959, 6930)`) reported, in `bank_v3`:

| cell | value |
|---|---|
| pre-tax bootstrap B1 median Δ (base) | **+18.4999 pp** (n = 5000, share > 0 = 0.922) |
| after-tax B1 (progressive) median Δ | **+12.2296 pp** (n = 1000, share > 0 = 0.878) |
| after-tax B2 (flat) median Δ | +14.5108 pp |
| after-tax window Δ, $100 k, 2000 / 2010 / 2017 | **+0.5879 / +0.1764 / +0.6431 pp** |
| pre-tax main-window CAGR Δ (m = 2) | +1.04 pp |

The Δ was already huge **pre-tax** and ≈ +35.6 pp in *level*, so it was never a tax effect: the
arm's bootstrap market path itself had to be different from its window path. A full-window two-leg
arm with a **panel** leg B (`ib27_2L`, `leg_b = "1x"`) reported a sane B1 (−1.72 pp) — the first
hint that the defect was specific to extra-key legs.

## 2. Root cause

`MarketData.leg_series(key)` returned an extra-data leg B as the per-column value of
`extra(key)`: inside a bootstrap chunk that is "the drawn source session's own value at column
t". That is the right convention for a **state condition**, and `extra()` still does exactly that.
But a leg B is a **tradable price**, and the kernel reads the return between two consecutive
columns. `fe_boot.draw_positions` stitches a chunk from 20-session blocks with a jump between
blocks, so at every jump columns `t−1` and `t` are two unrelated sessions and the ratio spans the
calendar:

```
level used:  … IEF[s₉] , IEF[s₂₀] , IEF[s₂₁] …        the drawn sessions' absolute levels
kernel sees: IEF[s₂₀]/IEF[s₉]                        a jump's return = a ratio of two
                                                     unrelated sessions (up to ±120 %)
```

The framework's own market leg never does this — `boot.boot_panels` steps each drawn session from
its **own** previous close (`prev = win.q[1][idx − 1]`) — and `opts_nan_fix_v1` established the same
convention for `chunk_triple` after the identical defect killed the option legs. Panel legs
(`1x`/`2x`) were always built correctly, which is exactly why only `leg_b ∈ {ief, lqd, tip, hyg,
shy, gld, gdx, sec_xlu, sec_xlp}` arms were affected.

The audit's other three hypotheses are **not** the cause, and were checked:

* **(a) pairing — fine.** `windows.boot_pool`, `_control_<tag>.json` and `BootWindow` put the arm
  and K on the same pool `(959, 6930)`, the same `i0 = 960`, the same session count, the same
  $100 k capital and the same TRY calendar; the one-path repro reads the cached
  `_control_w20030103.json` and finds `record.win.start == 960 == arm i0` and 1000 after-tax
  control paths. The framework's own `reduced_window.*` harness cells pass.
* **(c) units / annualisation — fine.** Both sides are per-path CAGRs from the same kernel formula,
  `(nav[k]/nav[0])**(252/k) − 1` pre-tax and `(scal[SC_LIQ]/cap)**(1/yrs) − 1` after tax, with the
  same `k` and `yrs`; weights are fractions, all Δs are pp. The window cells are bit-identical
  old→new for every arm, which would not hold under a unit or annualisation change.
* **(d) tax funding — not the cause.** On the reproduced path the arm's NAV never reaches ≤ 0
  (min 0.809 pre-tax, 80,871 USD after tax); the ≥ 0-taxes-cash branch never fires. The follow-up
  study's "a path whose after-tax NAV went ≤ 0" is a *consequence* of the spurious leg-B gains
  (its tax was 80.4 M TRY against the control's 5.65 M), not an independent defect.

## 3. The fix

`research/batch_screen/api.py`, one accessor (framework code; no kernel, no gate, no arm, no spec):

```python
def leg_series(self, key):
    if key in self.legs:
        return self.legs[key]
    v = self.chunk_price_path(key) if self.extra_index is not None else self.extra(key)
    return v, v

def chunk_price_path(self, key):                      # new
    idx  = self.extra_index[:, 1:]                    # the drawn source sessions
    pool = _ext.aligned(key, window, self.extra_days)[0]
    own  = pool[idx] / pool[idx - 1]                  # each drawn session's OWN return
    return np.concatenate([np.ones((idx.shape[0], 1)), np.cumprod(own, axis=1)], axis=1)
```

Column `t`'s return is therefore drawn session `t−1`'s own close-to-close return — `boot_panels`'
convention for the market leg — and a block jump cannot span the calendar. On a **contiguous** draw
the two conventions agree on every return but the artificial base column, so the fix is a no-op
there. `extra()` is **unchanged**; `leg_b_usable` follows `leg_series`, so the availability check is
now about the price path the kernel reads (for every registered gap-free leg-B ETF the verdict is
identical; a mid-chunk gap in a gapped series like `dgs10` is refused at the first poisoned column).

If the arm needs a new execution mechanism, add it as a numba kernel option in a copy of the engine —
not needed here. The fix is numpy panel construction in the framework, where the defect lived.

## 4. The one-path decomposition (item 1 of the task)

`python -m research.rw_boot_audit_v1.repro` → `repro.json`. The published draw's **path 0**
(seed 20261011, block 20), rebuilt from the same rng calls, with the arm and the framework control
on the same chunk:

| quantity | value |
|---|---|
| leg-B columns / drawn sessions in the chunk | 2,776 / 2,776 |
| columns whose return ≠ the drawn session's own return | **120** (as read) vs **0** (own-built) |
| `max abs(return − 1)`: leg B as read / IEF's own / the market leg | 1.229 / 0.019 / 0.243 |
| leg-B level at the last column: as read / own-built | 76.3 / 1.193 |
| leg-A target panel identical to the control's | **yes** |
| zeroed leg B reproduces K's pre-tax NAV | **bit for bit** |
| pre-tax CAGR: K / arm (as read) / arm (`leg_series`) | 0.2002 / 0.5969 / 0.2038 |
| pre-tax Δ: as read / repaired | **+39.67 pp** / **+0.36 pp** |
| after-tax (progressive $100 k) Δ: as read / repaired | **+28.3583 pp** / −1.1066 pp |
| the recorded `IC37/C` `b1_delta_pp[0]` | **+28.358243 pp** ← reproduced exactly |
| tax paid (TRY): control / arm as read / arm repaired | 5.65 M / 80.4 M / 5.43 M |
| NAV minimum, after tax (USD): as read / repaired | 80,871 / 71,321 (no path ≤ 0) |

The arm's leg-A market path and targets are identical to the control's, and a zeroed leg B
reproduces K bit for bit — so **the whole Δ is leg B**, and the leg-B path is the corrupted object.

## 5. Proof nothing else moved (`PARITY_LEGB.json`)

`parity_cells.py` archives per-path bootstrap CAGRs (pre-tax base/calibrated, after-tax
progressive/flat at $100 k, plus one pooled-OOS cell) for 8 arms over 5 draws — the published
main-window draw, two fresh seeds/blocks, and the `IC37/C` reduced pool twice — **76 cells**,
dumped on the pre-fix tree (`PRE_FIX_BASELINE.npz`) and diffed against the post-fix archive:

| class | arms | result |
|---|---|---|
| single-leg | `k_control/K`, `r2_control/R2` | **bit-identical**, NaN pattern included |
| panel-leg two-leg | `IC38/A`, `ib27_2L/A_2L` | **bit-identical** |
| extra-key two-leg | `IC37/C`, `IC31/A`, `ib25_RW/A_RW`, `IB26_2L/A_2L` | 128 of 128 cells changed (the repair); no cell gained a non-finite entry; nothing went finite → non-finite |

`hard_failures = []`, `unaffected_arms_moved = []`.

## 6. The re-screen (`rw_fix_v1`) — old vs new Stage-1 cells

Batch `rw_fix_v1`: 49 registered arms (17 ideas), 46 simulated, 3 still `needs_kernel`
(`IC34/C`, `IC45/C`, `IC46/C`); every line a `rerun` with its recorded `spec_hash`, **N = 303
unchanged** (ledger 383 → 530 lines). Wall clock: run 1 **242.7 s**, run 2 **138.7 s**, deflation
2 s on 6 workers; `RESULTS.json`, `SCOREBOARD.md` and all 46 arm records **byte-identical** across
the two runs (`shasum -a 256`).

`b`/`c` = base/calibrated financing; `pre base Δ`/`pre cal Δ` are the main-window m = 2 CAGR Δs vs
K, `pre-boot base Δ` the pre-tax B1 bootstrap median Δ, `AT` the after-tax $100 k window Δs.

| arm | window | leg B | pre base Δ | pre cal Δ | pre-boot base Δ | max DD Δ (b/c) | turnover ×K | AT 2000/2010/2017 Δ | B1 med (share) | B2 med (share) | non-finite | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `IB26_2L/A_2L` | default | two_leg_v1 | - → - | - → - | - → - | - / - | nan / nan | - / - / - | - (-) → - (-) | - (-) → - (-) | 0 → 0 | not_applicable → not_applicable |
| `IB26_2L/B_2L` | default | two_leg_v1 | - → - | - → - | - → - | - / - | nan / nan | - / - / - | - (-) → - (-) | - (-) → - (-) | 0 → 0 | not_applicable → not_applicable |
| `IB26_2L/C_2L` | default | two_leg_v1 | - → - | - → - | - → - | - / - | nan / nan | - / - / - | - (-) → - (-) | - (-) → - (-) | 0 → 0 | not_applicable → not_applicable |
| `IB26_RW/A_RW` | w20020731 | bank_v3 | +0.2224 → +0.2224 | +0.2212 → +0.2212 | +12.1632 → -0.1153 | +0.0865 / +0.0861 | 1.803 / 1.803 | -0.1121 / -0.8124 / -0.6344 | +7.0624 (+0.717) → -0.5282 (+0.258) | +9.1426 (+0.774) → -0.6066 (+0.242) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IB26_RW/B_RW` | w20020731 | bank_v3 | -0.0465 → -0.0465 | -0.0462 → -0.0462 | +17.4554 → -0.0072 | +0.3189 / +0.3176 | 1.818 / 1.818 | -0.1438 / -0.7979 / -0.9736 | +11.7805 (+0.870) → -0.2822 (+0.334) | +13.9263 (+0.891) → -0.2856 (+0.315) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IB26_RW/C_RW` | w20020731 | bank_v3 | +0.2265 → +0.2265 | +0.2253 → +0.2253 | -5.3392 → -0.0985 | +0.0000 / -0.0000 | 1.263 / 1.263 | +0.1876 / +0.0405 / +0.0088 | nan (+0.229) → -0.3101 (+0.288) | -5.6211 (+0.264) → -0.3249 (+0.276) | 1 → 0 | reject_stage1 → reject_stage1 |
| `IC31/A` | w20020731 | bank_v3 | +0.0805 → +0.0805 | +0.0853 → +0.0853 | +6.0311 → -0.7047 | +5.0967 / +4.9817 | 2.000 / 2.000 | -0.2287 / -0.1859 / -0.2298 | +0.9594 (+0.537) → -0.9868 (+0.261) | +2.8532 (+0.594) → -1.3527 (+0.192) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC31/B` | w20020731 | bank_v3 | +0.7230 → +0.7230 | +0.7190 → +0.7190 | +9.2531 → -0.2348 | +6.2685 / +6.1542 | 1.991 / 1.991 | +0.2809 / +0.0166 / +0.0346 | nan (+0.618) → -0.8362 (+0.245) | +5.3494 (+0.678) → -1.0762 (+0.195) | 1 → 0 | reject_stage1 → reject_stage1 |
| `IC31/C` | w20020731 | bank_v3 | -0.6934 → -0.6934 | -0.6844 → -0.6844 | -35.5411 → -1.0352 | +0.9496 / +0.9441 | 1.007 / 1.007 | -0.5326 / -0.2053 / -0.2019 | nan (+0.004) → -1.0460 (+0.220) | -32.1796 (+0.004) → -1.0913 (+0.199) | 2 → 0 | reject_stage1 → reject_stage1 |
| `IC32/A` | w20070412 | bank_v3 | +0.8517 → +0.8517 | +0.8510 → +0.8510 | +8.4668 → -0.2998 | +11.5120 / +11.3834 | 1.969 / 1.969 | +0.4941 / +1.3932 / +0.2189 | +3.6801 (+0.619) → -0.8314 (+0.343) | +5.6555 (+0.668) → -1.1956 (+0.275) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC32/B` | w20070412 | bank_v3 | -0.4223 → -0.4223 | -0.4159 → -0.4159 | -30.1989 → -0.8334 | +0.9496 / +0.9441 | 1.003 / 1.003 | -0.3123 / -0.2053 / -0.2019 | -28.2172 (+0.007) → -0.9125 (+0.264) | -27.3146 (+0.007) → -0.9724 (+0.237) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC32/C` | w20070412 | bank_v3 | +0.1657 → +0.1657 | +0.1688 → +0.1688 | +2.6919 → -0.9590 | +3.2197 / +3.2056 | 2.023 / 2.023 | -0.0024 / +0.1917 / -0.6491 | +0.1347 (+0.506) → -1.0930 (+0.259) | +1.0609 (+0.543) → -1.0984 (+0.256) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC33/A` | w20031208 | bank_v3 | -0.9270 → -0.9270 | -0.9176 → -0.9176 | +2.5516 → -0.5231 | +0.8228 / +0.8164 | 1.972 / 1.972 | -0.8846 / -0.7922 / -0.6683 | -0.2204 (+0.487) → -0.9629 (+0.249) | +0.7507 (+0.535) → -1.4010 (+0.148) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC33/B` | w20031208 | bank_v3 | -0.2452 → -0.2452 | -0.2439 → -0.2439 | +3.9761 → +0.0159 | +1.6959 / +1.5916 | 1.970 / 1.970 | -0.3693 / -0.5626 / -0.4433 | +0.4844 (+0.530) → -0.7253 (+0.230) | +1.6876 (+0.576) → -1.0022 (+0.153) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC33/C` | w20031208 | bank_v3 | -1.0931 → -1.0931 | -1.0827 → -1.0827 | +4.0289 → -0.9295 | +1.8867 / +1.8777 | 1.634 / 1.634 | -1.0972 / -1.3103 / -1.6311 | +1.3196 (+0.561) → -1.0918 (+0.170) | +2.4557 (+0.600) → -1.1500 (+0.151) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC34/A` | default | bank_v3 | -1.0889 → -1.0889 | -1.0770 → -1.0770 | +21.1734 → +0.7583 | +20.9004 / +20.8192 | 2.062 / 2.062 | -1.2542 / +2.8139 / +1.2158 | nan (+0.557) → +0.0976 (+0.507) | nan (+0.694) → -0.3723 (+0.456) | 104 → 0 | reject_stage1 → reject_stage1 |
| `IC34/B` | default | bank_v3 | -0.4487 → -0.4487 | -0.4400 → -0.4400 | +21.7965 → +0.8788 | +15.8005 / +15.7321 | 2.062 / 2.062 | -0.6369 / +2.2007 / -0.0933 | nan (+0.564) → +0.3141 (+0.540) | nan (+0.695) → -0.2330 (+0.463) | 94 → 0 | reject_stage1 → reject_stage1 |
| `IC35/A` | w20060523 | bank_v3 | +3.0110 → +3.0110 | +2.9982 → +2.9982 | +8.8907 → +3.8942 | +39.9374 / +39.7485 | 1.984 / 1.984 | +1.8302 / +5.8478 / +5.7468 | nan (+0.543) → +1.0421 (+0.582) | +4.6480 (+0.596) → +1.4072 (+0.607) | 8 → 0 | reject_stage1 → reject_stage1 |
| `IC35/B` | w20060523 | bank_v3 | +2.1508 → +2.1508 | +2.1428 → +2.1428 | +20.8817 → +2.2369 | +21.8165 / +21.6660 | 1.523 / 1.523 | +1.5329 / +3.4492 / +3.4262 | +13.8156 (+0.933) → +1.1354 (+0.642) | +15.3250 (+0.945) → +1.1365 (+0.650) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC35/C` | w20060523 | bank_v3 | -0.4018 → -0.4018 | -0.3957 → -0.3957 | -38.2499 → +0.0678 | +0.9496 / +0.9441 | 1.003 / 1.003 | -0.2802 / -0.2053 / -0.2019 | nan (+0.006) → -0.5203 (+0.439) | -34.4287 (+0.006) → -0.3056 (+0.464) | 10 → 0 | reject_stage1 → reject_stage1 |
| **`IC37/A`** | w20030103 | bank_v3 | +0.3281 → +0.3281 | +0.3306 → +0.3306 | +19.5926 → -0.0623 | +1.6113 / +1.6033 | 1.831 / 1.831 | +0.0158 / -0.0853 / +0.3180 | +11.3523 (+0.857) → **-0.6379** (+0.333) | +13.5821 (+0.883) → -0.8195 (+0.274) | 0 → 0 | **holdout_insufficient → reject_stage1** |
| **`IC37/B`** | w20030103 | bank_v3 | +0.2135 → +0.2135 | +0.2166 → +0.2166 | +29.8926 → -0.5212 | +0.9496 / +0.9441 | 1.625 / 1.625 | +0.0599 / +0.1259 / +0.4797 | +19.8896 (+0.946) → **-0.7971** (+0.267) | +22.5386 (+0.954) → -0.8907 (+0.228) | 0 → 0 | **holdout_insufficient → reject_stage1** |
| **`IC37/C`** | w20030103 | bank_v3 | +1.0367 → +1.0367 | +1.0309 → +1.0309 | +20.2993 → **+0.4553** | +0.8082 / +0.8050 | 1.810 / 1.810 | +0.5879 / +0.1764 / +0.6431 | **+12.2296** (+0.878) → **-0.2363** (+0.385) | +14.5108 (+0.907) → -0.3366 (+0.357) | 0 → 0 | **holdout_insufficient → reject_stage1** |
| `IC38/A` | default | 1x (panel) | -2.0528 → -2.0528 | -2.0361 → -2.0361 | +3.9023 → +3.9023 | +34.1801 / +34.0809 | 2.062 / 2.062 | -2.1957 / +3.1607 / +0.0403 | +2.1019 (+0.681) → +2.1019 (+0.681) | +1.8223 (+0.660) → +1.8223 (+0.660) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC38/B` | default | 1x (panel) | -0.8543 → -0.8543 | -0.8436 → -0.8436 | +2.0761 → +2.0761 | +17.3490 / +17.2726 | 1.577 / 1.577 | -0.9860 / +1.6672 / +0.2055 | +1.3638 (+0.736) → +1.3638 (+0.736) | +1.1995 (+0.691) → +1.1995 (+0.691) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC38/C` | default | 1x (panel) | -1.6181 → -1.6181 | -1.6036 → -1.6036 | +3.8724 → +3.8724 | +31.0825 / +30.9875 | 2.132 / 2.132 | -1.8280 / +2.5742 / -0.8167 | +2.1975 (+0.717) → +2.1975 (+0.717) | +1.9886 (+0.684) → +1.9886 (+0.684) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC42/A` | default | 1x (panel) | -0.4778 → -0.4778 | -0.4168 → -0.4168 | -0.5152 → -0.5152 | -0.1923 / -0.3066 | 2.262 / 2.261 | -1.3399 / -1.7736 / -2.9107 | -1.9798 (+0.001) → -1.9798 (+0.001) | -1.1163 (+0.010) → -1.1163 (+0.010) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC42/B` | default | 1x (panel) | -0.4778 → -0.4778 | -0.4168 → -0.4168 | -0.5152 → -0.5152 | -0.1923 / -0.3066 | 2.262 / 2.261 | -1.3229 / -1.7517 / -2.8749 | -1.9785 (+0.001) → -1.9785 (+0.001) | -1.1147 (+0.009) → -1.1147 (+0.009) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC42/C` | default | 1x (panel) | -0.4778 → -0.4778 | -0.4168 → -0.4168 | -0.5152 → -0.5152 | -0.1923 / -0.3066 | 2.262 / 2.261 | -1.3652 / -1.7764 / -2.8820 | -1.9813 (+0.001) → -1.9813 (+0.001) | -1.1218 (+0.009) → -1.1218 (+0.009) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC43/A` | default | 1x (panel) | -0.4778 → -0.4778 | -0.4168 → -0.4168 | -0.5152 → -0.5152 | -0.1923 / -0.3066 | 2.262 / 2.261 | -1.4665 / -2.0070 / -3.2949 | -2.1200 (+0.002) → -2.1200 (+0.002) | -1.2371 (+0.007) → -1.2371 (+0.007) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC43/B` | default | 1x (panel) | -0.4778 → -0.4778 | -0.4168 → -0.4168 | -0.5152 → -0.5152 | -0.1923 / -0.3066 | 2.262 / 2.261 | -1.6569 / -2.3326 / -3.8915 | -2.2045 (+0.002) → -2.2045 (+0.002) | -1.2935 (+0.007) → -1.2935 (+0.007) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC43/C` | default | 1x (panel) | -0.4778 → -0.4778 | -0.4168 → -0.4168 | -0.5152 → -0.5152 | -0.1923 / -0.3066 | 2.262 / 2.261 | -1.4395 / -1.9563 / -3.2344 | -2.0878 (+0.001) → -2.0878 (+0.001) | -1.2057 (+0.009) → -1.2057 (+0.009) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC44/A` | default | 1x (panel) | -0.5264 → -0.5264 | -0.4589 → -0.4589 | -0.4995 → -0.4995 | +0.0542 / -0.0600 | 2.259 / 2.260 | -1.3417 / -1.7384 / -2.8364 | -1.9674 (+0.001) → -1.9674 (+0.001) | -1.1092 (+0.009) → -1.1092 (+0.009) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC44/B` | default | 1x (panel) | -0.5027 → -0.5027 | -0.4360 → -0.4360 | -0.5053 → -0.5053 | +0.0542 / -0.0600 | 2.245 / 2.246 | -1.3410 / -1.7625 / -2.8454 | -1.9177 (+0.002) → -1.9177 (+0.002) | -1.0990 (+0.014) → -1.0990 (+0.014) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC44/C` | default | 1x (panel) | -0.4832 → -0.4832 | -0.4254 → -0.4254 | -0.5084 → -0.5084 | +0.0881 / -0.0262 | 2.246 / 2.246 | -1.3132 / -1.7255 / -2.8225 | -1.9588 (+0.001) → -1.9588 (+0.001) | -1.0977 (+0.008) → -1.0977 (+0.008) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC45/A` | w20041119 | bank_v3 | -0.3433 → -0.3433 | -0.2690 → -0.2690 | +28.2621 → +0.2353 | -4.6258 / -4.5714 | 0.901 / 0.901 | +0.0070 / -0.3443 / -0.5342 | +21.4065 (+1.000) → +0.4654 (+0.689) | +23.5147 (+1.000) → +0.2739 (+0.597) | 0 → 0 | holdout_insufficient → holdout_insufficient |
| `IC45/B` | w20041119 | bank_v3 | -0.8743 → -0.8743 | -0.7599 → -0.7599 | +59.6639 → +0.3703 | -8.3040 / -8.4175 | 0.811 / 0.808 | -0.1446 / -0.7997 / -1.4110 | +44.3916 (+1.000) → +0.8626 (+0.678) | +49.2696 (+1.000) → +0.4228 (+0.569) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC46/A` | w20020731 | bank_v3 | -1.4895 → -1.4895 | -1.4043 → -1.4043 | +5.3666 → -0.8306 | -4.5580 / -4.5992 | 0.880 / 0.880 | -0.6593 / -1.0196 / -2.2799 | +4.4507 (+0.995) → -0.2901 (+0.410) | +4.4541 (+0.995) → -0.6203 (+0.305) | 0 → 0 | reject_stage1 → reject_stage1 |
| `IC46/B` | w20020731 | bank_v3 | -2.9480 → -2.9480 | -2.7898 → -2.7898 | +12.8326 → -1.5859 | -8.3543 / -8.3935 | 0.783 / 0.782 | -1.6845 / -2.2886 / -4.5488 | +10.1364 (+0.999) → -0.6088 (+0.376) | +10.5014 (+0.999) → -1.3518 (+0.280) | 0 → 0 | reject_stage1 → reject_stage1 |
| `ib25_2L/A_2L` | default | two_leg_v1 | - → - | - → - | - → - | - / - | nan / nan | - / - / - | - (-) → - (-) | - (-) → - (-) | 0 → 0 | not_applicable → not_applicable |
| `ib25_2L/B_2L` | default | two_leg_v1 | - → - | - → - | - → - | - / - | nan / nan | - / - / - | - (-) → - (-) | - (-) → - (-) | 0 → 0 | not_applicable → not_applicable |
| `ib25_RW/A_RW` | w20020731 | bank_v3 | -0.0159 → -0.0159 | -0.0158 → -0.0158 | +1.5048 → -0.3153 | +0.8200 / +0.8168 | 1.978 / 1.978 | -0.2669 / -0.4705 / -0.3996 | -0.1384 (+0.483) → -0.8328 (+0.055) | +0.2138 (+0.519) → -1.0530 (+0.017) | 0 → 0 | reject_stage1 → reject_stage1 |
| `ib25_RW/B_RW` | w20020731 | bank_v3 | +0.0871 → +0.0871 | +0.0866 → +0.0866 | +3.1456 → -0.1205 | +0.7096 / +0.7068 | 2.001 / 2.001 | -0.2760 / -0.6152 / -0.7071 | +0.7035 (+0.567) → -1.0808 (+0.020) | +1.3453 (+0.603) → -1.1328 (+0.016) | 0 → 0 | reject_stage1 → reject_stage1 |
| `ib27_2L/A_2L` | default | 1x (panel) | -0.4746 → -0.4746 | -0.4198 → -0.4198 | -0.3727 → -0.3727 | +0.7433 / +0.6608 | 2.136 / 2.136 | -1.3164 / -1.7284 / -2.9088 | -1.7169 (+0.005) → -1.7169 (+0.005) | -0.8650 (+0.030) → -0.8650 (+0.030) | 0 → 0 | reject_stage1 → reject_stage1 |
| `ib27_2L/B_2L` | default | 1x (panel) | -0.4778 → -0.4778 | -0.4168 → -0.4168 | -0.5152 → -0.5152 | -0.1923 / -0.3066 | 2.262 / 2.261 | -1.3305 / -1.7954 / -2.9618 | -1.9682 (+0.001) → -1.9682 (+0.001) | -1.1056 (+0.010) → -1.1056 (+0.010) | 0 → 0 | reject_stage1 → reject_stage1 |
| `ib27_2L/C_2L` | default | 1x (panel) | -0.6270 → -0.6270 | -0.5879 → -0.5879 | -0.7373 → -0.7373 | -0.0419 / -0.0477 | 2.329 / 2.329 | -1.3833 / -1.8373 / -2.9749 | -1.7873 (+0.002) → -1.7873 (+0.002) | -1.0467 (+0.007) → -1.0467 (+0.007) | 0 → 0 | reject_stage1 → reject_stage1 |

**Every window cell is bit-identical old→new** (pre-tax Δs, after-tax Δs, DD Δs, turnover ratios) —
only bootstrap cells moved, and only for extra-key legs. The panel-leg arms (`IC38`, `IC42`–`IC44`,
`ib27_2L`) are *cell for cell identical*, including their nonzero Δs: the check that the fix is
confined to the extra-key path. Every extra-key B1 median collapses into **`[−1.09, +1.14] pp`**
(and every pre-tax B1 median into `[−1.59, +3.89] pp`), consistent with the arm's own window Δs.

### Verdicts that changed

| arm | old | new | why |
|---|---|---|---|
| `IC37/A` | holdout_insufficient | **reject_stage1** | B1 median +11.35 → **−0.64** pp (fails the Stage-1 B1 half) |
| `IC37/B` | holdout_insufficient | **reject_stage1** | B1 median +19.89 → **−0.80** pp |
| `IC37/C` | holdout_insufficient | **reject_stage1** | B1 median +12.23 → **−0.24** pp |

Everything else keeps its verdict. In particular `IC45/A` still passes Stage 1 on a genuine
(now +0.47 pp, was +21.4) B1 median and remains `holdout_insufficient`.

### The one consequence for the campaign

`IC37/C` was the single arm `docs/research/batch_bank_v3.md` flagged as *"the one arm worth a
dedicated confirmation study"*, and `research/ic37c_confirm_v1` (branch
`research/ic37c_confirm_v1`) was opened on B1/B2 medians of +25/+28 pp. Those numbers were the
defect: with the repaired leg B, `IC37/C`'s bootstrap advantage is **−0.24 pp** after tax
(−0.34 pp flat) and it is **rejected at Stage 1** on its own B1 gate. The confirmation study's
premise is void; nothing about the IC37 idea (or any other) survives this audit as a candidate.

## 7. Definition of done — what was run

| check | result |
|---|---|
| `python -m research.fast_engine.parity --quick` | **PASS** (11.5 s; `PARITY.json` restored byte for byte) |
| `python -m research.batch_screen.run --harness` | **PASS**, **116/116** checks |
| `bash research/batch_screen/tests/run.sh -q` | **136 passed** (4 new: the own-return convention, the contiguous-draw isolation, the synthetic/constant series, the availability rule) |
| `python -m research.fast_engine_opts.harness` | **PASS**, 30/30 (option-off side-by-side + bootstrap, 14 anchor cells, 13 mechanism checks) |
| `bash research/fast_engine_opts/tests/run.sh -q` | **36 passed** |
| `python -m research.rw_boot_audit_v1.screen --harness` | **PASS** (engine parity, K's and R2's 14 anchor cells ≤ 0.01 pp, the leg-B convention cells, the pairing cells, the one-path repro, the pre/post parity diff) |
| `run --verify-registry` | **PASS** (530 lines, N = 303) |
| `rw_fix_v1` run twice | `RESULTS.json`, `SCOREBOARD.md`, 46 arm records **byte-identical** (`shasum -a 256`) |
| `--deflate --batch rw_fix_v1` | N = 303; 41 arms with a finite Δ vector; `excluded_non_finite = {}`; `white_rc` best `IC38/B` (`t_max = 17.37`, p = 0.0000); `stage2_pass = []`, `declared = []` |

The `excluded_non_finite = {}` is itself evidence: the seven arms whose `bank_v3` B1 vectors carried
non-finite entries (`IC34/A` 104, `IC34/B` 94, `IC35/C` 10, `IC35/A` 8, `IC31/C` 2, `IC31/B` 1,
`IB26_RW/C_RW` 1 — left un-investigated by `opts_nan_fix_v1` as "a separate effect") are now
entirely finite. They were the same defect: the spurious leg-B gains produced an advance tax the
account could not fund, `(NAV < 0) ** (1/years)` went NaN, and the arm dropped out of the
deflation. With realistic leg-B returns no such path exists.

## 8. Determinism, cost, artifacts

* Re-screen wall clock (`RUN_TIMING.json`): `--run` **383.3 s** in total — batch pass 1 **242.7 s**,
  batch pass 2 **138.7 s**, deflation **1.9 s** (6 workers, 49 arms); a further reference pass
  (**94.1 s**) reproduced `RESULTS.json`, `SCOREBOARD.md` and all 46 arm records byte for byte.
* The ledger is append-only: 383 → 530 lines, all `rw_fix_v1` additions `rerun` (or
  `needs_kernel` for `IC34/C`, `IC45/C`, `IC46/C`), **N = 303 unchanged**.
* `research/rw_boot_audit_v1/`: `PREREGISTRATION.md`, `DATA_MANIFEST.json`, `repro.py`/`repro.json`,
  `parity_cells.py`, `PRE_FIX_BASELINE.npz`, `PRE_FIX_SUMMARY.json`, `PARITY_LEGB.json`,
  `screen.py`, `HARNESS.json`, `RUN_TIMING.json`, `RESULTS.json`, `SUMMARY.txt`.
* `research/batch_screen/results/rw_fix_v1/` (+ `_control*.json`, `timing.json`, `SCOREBOARD.md`).
* No data was downloaded; every input the study reads is pinned in `DATA_MANIFEST.json` (SHA-256)
  and the framework's own `research/batch_screen/DATA_MANIFEST.json` / `extra --verify`.

## 9. Caveats

1. **The fix is stricter than the defect was wrong.** `chunk_price_path` is a cumulative product, so
   a missing value at *any* drawn session poisons every later column — hence a window whose leg-B
   series has an interior gap is now refused at the first gap instead of only where the gap is
   read. For every registered leg-B instrument (contiguous ETF histories) the verdict is identical,
   before and after; the change affects only gapped series, which no leg-B arm uses. The quoted
   coverage in the refusal message therefore ends at the gap (the harness cell
   `chunk_leg_b_gap_names_the_failing_column` still names the failing column and still passes).
2. **A jump-free draw is not affected at all** — the two conventions agree on every return but the
   artificial base column — so this is a bootstrap/pooled-OOS-path repair only; every *window*
   number in the campaign stands.
3. `IB26_2L/A–C_2L` and `ib25_2L/A–B_2L` were and remain `not_applicable` (their leg B has no price
   at the main window's 2000-03-08 session); they carry no evidence either way.
4. Arms with `leg_b = "1x"` are in the re-screen only as the collateral check; they are bit-identical
   and add no new information.
5. 20-session blocks cannot capture serial dependence beyond 20 sessions (the campaign's standing
   caveat); N = 303 is the whole campaign ledger.
6. The `rw_fix_v1` deflation is a *batch* deflation over its 41 arms with finite Δ vectors, as the
   framework's `--deflate --batch` is defined; it does not re-declare anything from earlier batches.
