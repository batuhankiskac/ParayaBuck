# two_leg_arms_v1 — IB25 / IB26 / IB27 on the two-leg kernel (batch `two_leg_v1`)

**Verdict: no candidate. IB27 is screened and fails stage 1 on three of its four conditions; IB25
and IB26 are expressible on the new kernel but still cannot be screened, because their leg-B
instruments start after the framework's fixed main-window start — all five of those arms are
recorded `not_applicable`, with no claim made about them.** Nothing was declared; the deflation
rejects nothing (`rw_stepdown` p = 1.0, deflated Sharpe 0.0). N moved 107 → 115.

| question | answer |
|---|---|
| IB27 (beta-matched QQQ + QLD blend), 3 arms | screened, all `reject_stage1`: after-tax Δ(2000) −1.32 … −1.38 pp, B1 median Δ −1.72 … −1.97 pp (share > 0 ≤ 0.5 %), turnover 2.14 … 2.33 × K (> the 2 × gate) |
| IB25 (off-state SHY sleeve), 2 arms | `not_applicable` (stage 1): leg B = SHY starts 2002-07-30 |
| IB26 (curve-conditioned off-state IEF leg), 3 arms | `not_applicable` (stage 1): leg B = IEF starts 2002-07-30 |

The idea bank's own expectation for IB27 was "high: a deterministic cost saving with no signal
content". The cost saving is real — but small: on the same weight paths, with no costs and no tax,
the blend earns +0.12 … +0.21 pp/yr more than K (close-to-close cross-check below). Holding a
second leg doubles the traded weight, which costs ≈0.5 pp/yr, and the second FIFO lot book keeps
realising taxable TRY gains (39.3 sells/yr against K's 10.7), which costs a further ≈0.85 pp/yr
after tax. Net: −1.32 pp/yr. Pre-tax (main window) the three arms are −0.47, −0.48 and −0.63 pp; on
the block bootstrap −0.35 … −0.74 pp median.

## What was added

* `research/ideas/ib25_2L.py` (`A_2L`, `B_2L`), `research/ideas/IB26_2L.py` (`A_2L`, `B_2L`,
  `C_2L`) and `research/ideas/ib27_2L.py` (`A_2L`, `B_2L`, `C_2L`) — the bank's arms with `leg_b`
  declared (`shy`, `ief`, `1x`), the same frozen parameters, no tuning, no extra arms. The old
  modules (`research/ideas/ib25.py`, `IB26.py`, `ib27.py`) and their `needs_kernel` ledger lines
  are untouched: they declare `NEEDS_KERNEL` at module level, which `api.resolve_arm` applies to
  every arm of the module, and adding a `needs_kernel` key to their existing specs would change
  their `spec_hash` and make `--verify-registry` report `spec_drift` (the runner then refuses the
  batch). New modules were the only way to add runnable arms without touching registered ones.
* FRED **T10Y3M** (10y − 3m constant-maturity spread, 1982-01-04 →, `value`, level) registered
  through the framework's own extra-data mechanism, by hand because `extra.py`'s `--fetch-fred`
  only accepts the ids in its module-level `FRED` tuple:
  `data/historical/two_leg_arms_v1_T10Y3M.csv`,
  `research/two_leg_arms_v1/register_t10y3m.py`, `…/DATA_MANIFEST.json`
  (sha256 `a464c1dc…75060`, 11 199 rows). `python -m research.batch_screen.extra --verify` PASS.
  The registry diff is 9 added lines (`research/batch_screen/EXTRA_DATA.json`).
* The trial ledger: 8 new arms (`140 → 156` lines, `107 → 115` distinct trials); the second run
  appended 8 `rerun` lines for the determinism check.

## Method

Control `k_control/K` (production K), reference `r2_control/R2`. All arms run `rule = "K"`,
`leg = "2x"`, `cash_yield = "none"` through `research/fast_engine2.fe2_sim.simulate2` — one cash
pool, a FIFO lot book per leg, one taxpayer, K's 0.5 pp band, A1u gain band and H2 deferral applied
per leg on that leg's own book. The two-stage protocol, its gates, the bootstrap seed/block/path
counts and the deflation are the framework's, unchanged
(`research/batch_screen/PREREGISTRATION.md` §§ 3–4). The arms, their readings and the two
transcriptions below were committed before the batch ran
(`research/two_leg_arms_v1/PREREGISTRATION.md`).

The control reproduced its published cells (pre-tax 15.7649 % base / 15.1785 % calibrated, max DD
46.6698 % / 46.7948 %, after tax at $100k 10.7533 % on the 2000 window), and the framework harness
— 73 checks, including `research.fast_engine.parity --quick` and the two-leg kernel's parity
groups — PASSed.

**IB25-B is S2_10's shape, transcribed.** `tbill_sweep_v1`'s S2_10 (see its `screen.py` on
`archive/research/tbill-sweep-v1-2026-10`) keeps the sleeve in `[hi − X, hi]`, `hi = 0.98 − w*`,
and resets it to `max(0, hi − X/2)` on a breach, `X = resize_band = 0.10`. That decision is taken
on the *held* sleeve weight, which a target-weight plugin cannot see, so it is taken on the accepted
target: hold the accepted target while it lies inside `[hi − X, hi]`, else reset to
`max(0, hi − X/2)`; `0.98` while the production path is off. This reproduces S2_10's resizes and
never lets the sleeve exceed the current residual, i.e. `wants_a + wants_b ≤ 0.98` (asserted in
`check_plugins.py`). No new parameter: `X` is the spec's `resize_band`.

**IB27 is the bank's formula plus one pre-registered long-only tie-break.** `beta = 2 ×
min(0.98, f·vt)`, `cash = min(0.98, beta)`, `w_QLD = beta − cash` (leg A), `w_QQQ = cash − w_QLD`
(leg B), `vt = min(1, 0.35/vol20)` on leg A's own vol. This is exactly the bank's construction while
`f·vt ≤ 0.98` (at the bank's own example, `vt = 0.8397`, it gives `w_QLD = 0.70`, `w_QQQ = 0.28`);
beyond it the literal formula needs `w_QQQ = 1.96 − beta < 0`, a QQQ short (≤ 0.04) that the
long-only single-cash-pool account does not model, so the delivered beta is capped at production's
own (max long-only beta at the 0.98 cap) and the blend degenerates to production's 0.98 QLD. That
region is large — 46.2 % of main-window sessions have `vt > 0.98` — so the tie-break matters: the
arm is "beta-matched to K, at the lowest long-only carry" rather than "beta `2·f·vt` at any cost".
30.3 % of sessions are pure QQQ (`beta ≤ 0.98`), 2.4 % are early-leg sessions.

## Stage 1 (main window 2000-03-09 → 2026-09-29, m = 2, Δ = arm − K in pp)

| arm | CAGR base | CAGR cal | max DD base / cal | turnover | × K | AT2000 Δ | AT2000 % | B1 med / share | B2 med / share | pre-tax boot med / share | sells/yr | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `k_control/K` | 15.7649 | 15.1785 | 46.6698 / 46.7948 | 5.456 | 1.00 | — | 10.7533 | — | — | — | 10.7 | control |
| `ib27_2L/A_2L` | 15.2903 | 14.7587 | 47.4131 / 47.4556 | 11.656 | 2.136 | −1.3164 | 9.4369 | −1.7169 / 0.005 | −0.8650 / 0.030 | −0.3494 / 0.176 | 39.3 | reject_stage1 |
| `ib27_2L/B_2L` | 15.2871 | 14.7617 | 46.4775 / 46.4882 | 12.340 | 2.262 | −1.3305 | 9.4228 | −1.9682 / 0.001 | −1.1056 / 0.010 | −0.5202 / 0.102 | 39.6 | reject_stage1 |
| `ib27_2L/C_2L` | 15.1379 | 14.5906 | 46.6279 / 46.7471 | 12.710 | 2.329 | −1.3833 | 9.3700 | −1.7873 / 0.002 | −1.0467 / 0.007 | −0.7410 / 0.022 | 37.8 | reject_stage1 |
| `ib25_2L/A_2L`, `ib25_2L/B_2L`, `IB26_2L/A_2L`, `IB26_2L/B_2L`, `IB26_2L/C_2L` | — | — | — | — | — | — | — | — | — | — | — | not_applicable |

Failed stage-1 conditions, all three arms: after-tax $100k Δ(2000) > 0, B1-recent median Δ > 0,
turnover ≤ 2 × K. Passed: pre-tax max DD ≤ K + 2 pp (+0.74 / −0.19 / −0.04 pp base, calibrated
+0.66 / −0.31 / −0.05). Stage 2, the 2010/2017 after-tax windows, the rolling starts and the fresh
seeds were therefore not run. Deflation: `white_rc` p = 1.0, `rw_stepdown` p_adjusted = 1.0 for all
three (studentized t ≈ −65 … −76), deflated Sharpe 0.0, `stage2_pass = []`, N = 115.

## Why IB27 loses

Cost-free, tax-free, close-to-close cross-check of the same weight paths on the main window
(`research/two_leg_arms_v1/check_plugins.py::gross_check`; an illustration, not a gate):

| construction | gross CAGR |
|---|---|
| production (one leg) | 15.2432 % |
| `ib27_2L/A_2L` | 15.4277 % (+0.184 pp) |
| `ib27_2L/B_2L` | 15.4522 % (+0.209 pp) |
| `ib27_2L/C_2L` | 15.3615 % (+0.118 pp) |

So the construction does buy beta more cheaply — by ≈0.2 pp/yr, the order the bank estimated (it
quoted ≈45 bp at the mean `vt`, before the 46 % of sessions in which the cap region removes the
difference). What the bank's single-fund sketch could not show is the price of *running* it:

* **Turnover.** Production rebalances one weight by `|Δw|` per session; the blend rebalances two by
  `2|Δvt|` each, i.e. four times the traded weight. Measured 11.66 … 12.71 units/yr against K's
  5.46, and 2.7 … 2.8 × K's trade count (2 366–2 491 vs 877 over the window). At 8 bp per unit
  (4 bp × m = 2) that is ≈+0.5 pp/yr of cost, which is the whole realised pre-tax difference
  (−0.47 pp for A_2L).
* **Tax.** The QQQ leg appreciates less than the levered leg, so its downsizes have a *low* USD
  gain fraction and K's A1u band lets them through (0.5 pp rather than 3 / 8 pp): the arm's
  strategy sells rise from 10.7 to 39.3 per year, each realising a TRY gain and restarting a lot.
  Its total tax is *lower* ($765 k vs K's $831 k, because the terminal wealth is lower) but it is
  paid much earlier, and the after-tax CAGR falls 0.85 pp further than the pre-tax one.

## Availability: IB25 and IB26 cannot be screened yet

`MarketData.leg_b_usable` refuses a leg-B instrument that does not reach every session the engine
reads, and stage 1 requires the **main** window (and therefore the stage-1 bootstrap) to be
applicable. SHY and IEF both start **2002-07-30**, so the five arms are recorded
`verdict = not_applicable`, `where = main window (stage 1)`, with the framework's own reason
("the leg-B instrument 'shy' has no price on 2000-03-08 (the series covers 2002-07-30..2026-09-29
of this window)"). This is the same path the framework's own probe `probes/two_leg.py::gld_sleeve`
exercises. The arms are nevertheless registered and screened, so the outcome is recorded rather
than assumed; **no claim is made about IB25 or IB26.**

The bank anticipated this: its `data.main_window` for IB25 is a reduced start (2002-08) and for
IB26 2003-07, and its `evaluation_contract.reduced_gate_convention` requires a pre-registered
reduced gate where a window is not evaluable. The framework cannot express it: stage 1 hardcodes
`windows.main_panel()` (i0 = 253 = 2000-03-09) and the bootstrap's pool is the main window's own
1999-2026 history, so a reduced-window screen needs two framework changes — a per-arm window start
(the spec already flows into `targets` as `params`, so a `"start"` key would reach it, but
`stages.stage1` and `boot.boot` would have to honour it) and a matching reduced bootstrap pool.
That is a framework change, which this study is not allowed to make; it is the concrete follow-up.
Pre-registered here for that follow-up: main window from the leg's own start
(IB25 2002-08-01, IB26 2003-07-01), the same four stage-1 conditions, the same control recomputed
on the reduced window, and the reduced-gate disclosure the bank asks for.

## Framework bug (reported, not patched)

`MarketData.leg_b_usable` builds its failure reason as `self.days[finite[-1]]`, but inside a
bootstrap chunk the leg series is one column wider than the chunk's `days` (the documented leading
base column), so when an extra-key leg B has a `NaN` inside the chunk's read window and a finite
last column, the index overflows:

```
python -m research.batch_screen.smoke --ideas ib25_2L,IB26_2L
  File "research/batch_screen/api.py", line 234, in leg_b_usable
    span = (f"{self.days[finite[0]]}..{self.days[finite[-1]]}" if finite.size
IndexError: index 2776 is out of bounds for axis 0 with size 2776
```

It is reproducible with the framework's own probe (`probes/two_leg.py::gld_sleeve`, leg B = GLD, an
extra key) on the boot panel `smoke` builds — no arm of this study is involved — and it affects
every two-leg arm whose leg B is an extra series with an interior `NaN` in a drawn chunk, in
`smoke`, in the clean bootstrap (`boot.two_leg_series`) and in the pooled OOS bootstrap. It does
**not** affect this batch's screened arms: `ib27_2L/*` holds the panel's `1x` leg, whose prices are
finite everywhere (`leg_b_usable` returns `True` before the reason is built), and the IB25/IB26 arms
are refused on the main window before any chunk exists. `smoke --ideas ib27_2L` is OK; the
plugin-side checks the smoke performs, plus the weight invariants, are in
`research/two_leg_arms_v1/check_plugins.py`. The framework was left unedited.

## Caveats

* The `1x` leg carries the campaign's synthetic conventions — no fund expense, no financing — so
  IB27's arms are not charged the bank's "QQQ ≈ 20 bp/yr". That bias is in the arm's favour and it
  still loses by 1.3 pp; a real QQQ would widen the loss.
* The 46.2 %-of-sessions tie-break is the only place where the arm deviates from the bank's literal
  weights, and the deviation is in the direction the bank's prose argues for (minimum carry). A
  kernel that could hold the small QQQ short would answer the literal question; the bank's own
  numbers (`mean(vt) = 0.8397`) are in the region where the two agree.
* Both legs are real kernel legs, so the after-tax cells are exact for the construction; only the
  bank's *pre-tax* single-fund sketch is not used.
* The block-20 bootstrap cannot represent serial dependence longer than 20 sessions, and the
  deflated Sharpe is the advantage-Sharpe of the Δ vector — the campaign's standing caveats.
* IB25/IB26 remain untested, not disproved.

## Reproduction

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.two_leg_arms_v1.register_t10y3m     # data/historical + EXTRA_DATA.json
PYTHONPATH=. $R -m research.two_leg_arms_v1.check_plugins      # plugin-side checks + invariants
PYTHONPATH=. $R -m research.batch_screen.run --verify-registry
PYTHONPATH=. $R -m research.batch_screen.run --harness         # 73 checks, 78.4 s
PYTHONPATH=. $R -m research.batch_screen.run --batch two_leg_v1 \
    --ideas ib25_2L,IB26_2L,ib27_2L --workers 4                # 29.1 s (control included)
PYTHONPATH=. $R -m research.batch_screen.run --deflate --batch two_leg_v1
```

Two runs gave a byte-identical `RESULTS.json`
(`sha256 08b7d3386358ab41e0670e4876e99ab2a707a3fe8e5d6ba47dd0e3f002704a28`). Per-arm records,
`SCOREBOARD.md`, `RESULTS.json` and `timing.json` are in
`research/batch_screen/results/two_leg_v1/`; the deflation is
`research/batch_screen/results/DEFLATION.json`; this study's own summary and manifest are
`research/two_leg_arms_v1/SUMMARY.txt` and `…/DATA_MANIFEST.json`.
