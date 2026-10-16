# two_leg_kernel_v1 — a two-instrument kernel for the batch-screen framework

**Verdict: built and parity-proven. This is engineering only; no idea's performance was computed.**
The account can now hold two instruments at once: one cash pool, a FIFO lot book per instrument,
one Turkish taxpayer. With leg B at zero it reproduces `research/fast_engine` bit for bit
(pre-tax, after-tax, bootstrap). On a fixed two-instrument case it reproduces
`riskoff_after_tax_v1`'s after-tax engine exactly. The framework sends plugin arms that return
`(wants_a, wants_b, leg_b)` to this kernel. The framework harness still passes its original
59/59 checks, now 73 in total. There are 110 pytest tests and all pass.

## Design

* `research/fast_engine2/fe2_sim.py` (`sim2` / `simulate2`): a copy-and-extend of `fe_sim.sim_one`.
  Each session is decided at the close and filled at the next open. The rule's 0.5 pp band, the
  A1u gain band and the H2 December deferral are applied **per leg on that leg's own lot book**,
  and costs are charged per leg. Financing is only inside the synthetic 2x price. Idle-cash yield
  is optional, as in the engine. Every leg's realisation, indexed by Yİ-ÜFE per lot, goes into
  one annual net TRY gain, and the engine's `_assess`/instalment kernels are reused unchanged.
  Tax-funding sales sell leg A first, then leg B. `research/fast_engine/` itself was not edited.
* `fe2_boot.py`: `_boot_chunk2` (clean bootstrap) and `_pt2` (pooled OOS). Both use the same
  `fe_boot.draw_positions` draws as the engine.
* Framework (`research/batch_screen`):
  * `api.call_targets` returns a `Targets`. A two-leg arm must declare `leg_b` in its spec, so the
    registry pins the instrument. `leg_b` is a panel leg or an extra key, and must differ from
    `leg`.
  * `stages.run_one` sends two-leg arms to `simulate2`.
  * `boot.boot` and `boot.oos_pooled` group arms by `(leg, leg_b)` on shared drawn paths.
  * When leg B's series has no price on a session the engine reads, the window is
    `NotApplicable`; for a bootstrap chunk this means the drawn source sessions.
  * An extra-data series has no OHLC, so its adjusted close serves as both the fill price and the
    close.
* Bug fix found on the way: the plugin bootstrap built its `1x` leg from raw drawn prices. That
  leg was one column short and took returns across block jumps. It is now the chunk's own
  total-return path. No registered arm holds `1x`: IB10 reads `data.leg` only for its gearing.
  So no published batch result changes, and the harness and anchors are unchanged.

## Parity (`python -m research.two_leg_kernel_v1.run --harness`, 134 cells, all PASS)

| check | cells | max abs diff |
|---|---:|---:|
| `research.fast_engine.parity --quick` | 1 | PASS |
| leg B ≡ 0 vs engine: main window, 4 rules × 3 modes × m=1/2/4, base+calibrated (NAV path + every scalar) | 72 | 0 |
| leg B ≡ 0 vs engine: 3 after-tax windows × $10k/$100k/$1M × C0/K × progressive/flat | 36 | 0 |
| leg B ≡ 0 vs engine: idle-cash yield on | 3 | 0 |
| leg B ≡ 0 vs engine: clean bootstrap, every path, pre-tax + after-tax | 8 | 0 |
| published K anchors (band_h2_combo_v1 RESULTS) through the two-leg kernel | 9 | 4.0e-05 pp (6-dp rounding) |
| riskoff_after_tax_v1 engine (verbatim copy) on the fixed QLD* + GLD case, pre-tax | 17 | 0 |
| same, progressive Turkish tax | 17 | 0 |

**Why the published L2 numbers are not the target.** `riskoff_after_tax_v1`'s L2 holds three
legs: QLD\*, BOND10\* and GOLD\*, with a monthly bond/gold choice. That engine's conventions
also differ from the protocol's rule:

1. It assesses each account separately, flooring each leg's annual gain at zero and applying
   the brackets to each leg.
2. It trades non-equity legs on a 10 pp band.
3. It resizes every leg whenever any leg trades.

Parity (b) therefore runs that engine, copied verbatim into `riskoff_ref.py` with its
provenance hash, on a fixed synthetic case. The case is built so that none of these
conventions can bind: each leg realises in only one tax year, and every target change is far
above 10 pp. On that case the match is exact.

**Counter-example (reported, not asserted).** Both legs realise in 2022 (progressive mode, $100k):

* The reference charges 19,115 TRY of tax and the pooled kernel charges 0. Leg A lost about
  222k TRY, and that loss offsets leg B's gain of about 87–94k.
* The kernel's CAGR is 0.61 pp higher.
* Leg B makes 30 trades in the kernel against 2 in the reference, which shows its 10 pp band.

The kernel follows the protocol's single-taxpayer rule.

## Speed (10 threads, warm cache; `TIMING.json`)

| measurement | time |
|---|---:|
| one main-window path, engine | 0.17 ms |
| one main-window path, two legs | 0.39 ms |
| clean bootstrap, 256 paths, one leg | 0.29 s |
| clean bootstrap, 256 paths, two legs | 0.31 s |

`--run` took 72–82 s in total, including the 40 s framework harness. Running it twice gave a
byte-identical `RESULTS.json`.

## What remains NEEDS_KERNEL

* **Now expressible** (a plugin returns `(wants_a, wants_b, leg_b)`):
  * IB25: SHY sleeve, `leg_b="shy"`.
  * IB26: off-state IEF, `leg_b="ief"`. Its T10Y3M input is still not registered.
  * IB27: the beta-matched QQQ + QLD blend, `leg_b="1x"`.

  The idea modules were left untouched because the ledger pins their registered arms. Running
  them needs new registered arms.
* **Still NEEDS_KERNEL:**
  * IB38 A: per-arm tax regime and capital, which are batch-level settings.
  * IB38 B: several accounts or cash pools.
  * IB38 C: a routed split, i.e. two books of the *same* instrument with gain-aware routing and a
    route cap.
  * Any per-session instrument switch within one leg.
  * Three or more legs, such as L2.

## Files

`research/fast_engine2/` (kernel, bootstrap, parity (a)), `research/two_leg_kernel_v1/`
(`riskoff_ref.py`, `parity_b.py`, `run.py`, HARNESS/RESULTS/SUMMARY/TIMING), and
`research/batch_screen/` (`api.py`, `stages.py`, `boot.py`, `run.py`, `probes/two_leg.py`,
`tests/test_two_leg.py`, README §1/§6).
