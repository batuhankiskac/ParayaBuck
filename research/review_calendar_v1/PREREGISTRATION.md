# PREREGISTRATION: review_calendar_v1 (audit item 56)

Worker `review_calendar_v1`, branch `research/review_calendar_v1`, dated 2026-10-27. Written and committed
before any non-control arm is simulated on real data. Driver `screen.py`; engine `engine/` (a copy of
`research/fast_engine` with one added kernel option, see "Engine option"); manifest `DATA_MANIFEST.json`.
Control anchors: `research/band_h2_combo_v1/RESULTS.json`.

## Question

K resizes its vol-target weight daily, inside the A1u gain band and the H2 December deferral. Does reviewing
K's **sizing only on a fixed calendar**, while entries from zero and exits to zero stay daily, improve K
**after Turkish tax** without a materially worse drawdown?

Hypothesis: about 91 % of R2's realised Turkish taxable gain came from vol-target trims. Trimming and
re-levering on a few fixed review sessions per year should cut the taxable sells further, at a pre-tax cost
that must be small. The test is the after-tax result, not the sell count.

## Control and reference (unchanged)

- **K** (control) = the production rule: E2 vote + R2 early re-entry, target `on x factor x min(1, 0.35/vol20)`,
  cap 0.98, decided at the close and filled at the next open, 0.5 pp band; resizes fill under the A1u gain-scaled
  band (0.5 / 3 / 8 pp at a USD FIFO gain fraction of <= 1 % / <= 5 % / > 5 %) and the H2 December deferral (a
  downward resize whose fill session is one of the last 10 NYSE sessions of December is postponed).
- **R2ref** (reference row) = the same target series with the C0 fill rule (0.5 pp band, no A1u, no H2).

## Arms (every parameter fixed here)

All arms use K's target series bit for bit. Only the fill decision of a resize changes.

Definitions. A **resize** is a fill session i at which the held weight w_i (units x open / NAV, before the fill)
and the target t_i (decided at the previous close) satisfy w_i > 0 and t_i > 0 and |t_i - w_i| > 0.5 pp, i.e. the
trade the K band would otherwise make between two positive weights (up or down). An **entry** is w_i = 0 < t_i,
an **exit** is t_i = 0 < w_i; both stay daily and are never gated.

A **review session** is a fill session i that is:
- for **R1**: the first NYSE session of each calendar month (the first session of the month in the session list);
- for **R2Q**: the first NYSE session of January, April, July or October;
- for **R3**: the same as R2Q.

Gate rule (R1, R2Q, R3): a resize on a non-review session is **suppressed** (not forced), its weight drifts with
price, and it is re-evaluated at every later session, as H2 and the monthly deferral do. A resize on a review
session goes through K's fill rule unchanged (A1u band, then H2, then fill).

| arm | review sessions | escape | fill rule for gated resizes |
|---|---|---|---|
| **R1** | first session of each calendar month | none | K (A1u + H2) on review sessions |
| **R2Q** | first session of Jan/Apr/Jul/Oct | none | K (A1u + H2) on review sessions |
| **R3** | first session of Jan/Apr/Jul/Oct | a resize with \|t - w\| > 15 pp executes at once (any session) | K (A1u + H2) on every executing resize |

Arms: R1, R2Q, R3 (three new arms, within the limit of four), plus controls K and R2ref.

### How R1 differs from the failed monthly deferral (`monthly_defer_v1`, `docs/research/monthly_defer_v1.md`)

`monthly_defer_v1` suppressed **only downward** resizes during the **last 10 (or 21)** sessions of each calendar
month, with up-resizes unconstrained and a daily window of about half of each month. It failed the pre-tax bootstrap
because it held above-target weight, i.e. extra exposure, on those days. R1 differs in four ways:
1. **Both directions** are gated: up-resizes and down-resizes between positive weights, so the held weight is
   not biased upward. Its exposure error is symmetric in sign, not one-sided. Pre-tax exposure is still measured
   and reported (see Gates).
2. **One session per month** (the first) is the only resize opportunity, not a window of ten or twenty-one.
   Drift accumulates for up to 21 sessions instead of at most 10, so R1 carries more tracking error; the pre-tax
   bootstrap and the 2x turnover gate test whether that error costs return.
3. **The review session is the first session of the month**, not the last. Consequence (stated in advance): H2's
   window (last 10 NYSE sessions of December) can never contain an R1 or R2Q review session, so **H2 never binds on
   those arms**. A December trim fills on the first December session instead of being postponed to January. H2 can
   bind only on an R3 escape. This is a feature of the calendar design, not a bug; it is reported, not patched.
4. **No drift-free exits or entries are slowed**: entries from zero and exits to zero stay daily, as in K.

The mechanism tested is therefore "one resize opportunity per period on both sides", not "defer downsizes by a window".

## Escape (R3)

A resize with drift |t - w| > 15 pp on a non-review session executes at once. The escape bypasses only the review
gate. K's A1u band and H2 still apply to it, as to any executing resize (a 15 pp drift always clears A1u's 8 pp
band ceiling, so only H2 can postpone it).

## Engine option (added before any real-data run)

`engine/fe_sim.py` (copy of `research/fast_engine/fe_sim.py`) adds `gate` (int8 per session, empty = off) and
`gate_esc` to `sim_one` and `simulate`. Inside the decision, after `blocked = 0` and before H2/A1u:
`if trade and gate.shape[0] > 0 and tgt > 0 and w > 0 and gate[i] == 0 and drift <= gate_esc: trade = False; blocked = 3`.
`engine/fe_boot.py` passes the gate per arm to `_boot_chunk` (anchor call off). `engine/fe_config.py` only changes
the repo-root depth (`parents[3]`). Nothing in `research/fast_engine/` is edited.

Parity cells (all in `--harness`, all control weights): gate off equals `research.fast_engine` bit for bit for K and
R2ref in pre-tax, progressive and flat modes (6 cells); an all-ones gate with escape 0 equals K bit for bit (pre-tax
and progressive, 2 cells); the published `python -m research.fast_engine.parity --quick` passes; K and R2ref reproduce
band_h2_combo_v1's anchors (pre-tax base and calibrated CAGR and max DD, after tax at $100k on the three windows)
within 0.01 pp; the bootstrap kernel with the gate plumbing runs (smoke). Result on 2026-10-27: HARNESS PASS (the
identical numbers are in the harness output recorded in the study report).

## Data

Inputs and hashes in `DATA_MANIFEST.json` (the same seven market and rate files as slow_vol_blend_v1, verified by
SHA-256 against that study's manifest, plus the range-volatility ETF bars that the published parity needs).
No new market data is downloaded. Main window 2000-03-09 to 2026-09-29 (QQQ calendar from 1999-03-10).
Base and calibrated financing (+0.75 %), m = 2 (and m = 1 for G6). Idle cash earns zero. Turkish tax: progressive
2026 tariff, YI-UFE indexation, instalments, $100k liquidated (after-tax windows 2000-03-09 to 2026-09-29, 2010-01-01
to end, 2017-01-03 to end); flat 20 % on the annual net USD gain (B2). Bootstraps: block 20, seed 20261011,
5,000 pre-tax paths; 1,000 after-tax paths on the 2015-09 to 2026-09 TRY calendar (B1-recent, progressive) and the
same paths under B2 (flat 20 %). OOS: six windows (^GSPC 1957-1999 and M1-M5), calibrated, m = 2.

## Gates (pre-registered; all must pass for an arm to be a candidate)

Same definitions as `slow_vol_blend_v1` (which follows `vol_regime_leverage_v1` and `ewma_band_v1`), with K as C0.

- **G1 (both financings):** CAGR >= K, or Calmar >= 1.1 x K's Calmar with CAGR >= K - 0.5 pp.
- **G2 (both):** max DD <= K + 1 pp.
- **G3 (both):** 2017-end CAGR >= K - 1 pp.
- **G4 (base):** CAGR above K in >= 2 of 3 sub-periods (P1 2000-2008, P2 2009-2016, P3 2017-end).
- **G5 (both):** pre-tax bootstrap median (arm - K) >= 0.
- **G6 (both):** G1 at m = 1.
- **G7 (calibrated, m = 2):** six OOS windows. Median Delta CAGR >= 0; Delta CAGR >= -0.5 pp in >= 5 of 6;
  Delta DD <= +3 pp in >= 5 of 6 and never > +8 pp.
- **G8 (reduced, as pre-registered in vol_regime_leverage_v1):** block-20 circular resampling of each M1-M5 daily
  Delta vs K, 2,000 replications, seed 20261011; statistic = mean over M1-M5 of 252 x mean daily Delta. Median >= 0.
  Labelled as a deviation from the pooled ten-year path bootstrap.
- **G9:** annual turnover <= 2 x K's (base, m = 2).
- **A1 (after tax):** Delta vs K > 0 at $100k in all three windows.
- **A2 (B1-recent):** median Delta >= 0 and share Delta > 0 >= 55 %.
- **A3 (B2, flat 20 %):** median Delta >= 0 and share Delta > 0 >= 55 %.
- **A4 (pre/post 2020):** after-tax Delta vs K > 0 on both 2000-03-09 to 2019-12-31 and 2020-01-01 to end.

Reported, not gated: sells per year, max DD (after tax and pre-tax), mean QQQ-equivalent exposure on vote-on days
(for every arm, to show the drift cost), pre-tax CAGR by half, the OOS table, bootstrap medians of pre-tax and
after-tax CAGR, and the number of gated resize sessions.

**DSR.** Bailey and Lopez de Prado deflated Sharpe (`research/gates_v1/stats.py`) on the pre-tax daily base m = 2
returns and the after-tax (progressive) daily series of each arm, with **N = 375 + 3 = 378** trials. The 375 is the
registry count given in the task; the three new arms are added. (slow_vol_blend_v1 had counted 369 + 3 = 372; that
figure is not used here because the task set 375.) Bar 0.95, reported only. Sanity check: K at N = 365 reproduces
gates_v1's 0.711 (harness check).

**PBO.** Not computed. This study has three fixed arms plus two controls, with no parameter grid, so
`research/gates_v1/pbo.py` is not applicable under the protocol's "grid of more than four arms" rule. Stated as a caveat.

**Provenance.** RESULTS.json carries `research/gates_v1/provenance.stamp` over the seven inputs, the git sha, package
versions, and the SHA-256 of `screen.py`, this file and the engine modules.

**Selection.** An arm replaces K only if every G and A gate passes. Otherwise K stays. No production configuration
changes in any case. The study is a candidate test, not a promotion.

## Known limits (stated before running)

- **H2 is inert on R1 and R2Q** (see point 3 above). Their December trims fill at the first December session.
- **Drift on non-review sessions.** Held weights drift with QLD's price between reviews; the target's 0.98 cap binds
  on the target, not on the drifting held weight. Exposure is reported; the max held weight is not gated.
- **Tax timing.** Fewer, larger resizes can realise more gain per sale; this is what the after-tax gates measure.
- **One price path.** The after-tax windows overlap heavily (2000 and 2010 share history). The bootstraps and OOS
  windows are the robustness checks. The bootstraps resample a fixed 2015-09 to 2026-09 calendar, so month and
  quarter starts are the window's own calendar in every path.
- **The slow leg of slow_vol is not involved.** This study uses K's target unchanged.

## Amendments

None. Any bug fix found after results is recorded here with the reason; the gates and arms stay as written.
