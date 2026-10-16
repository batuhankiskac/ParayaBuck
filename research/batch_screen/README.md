# research/batch_screen — batch screening for ParayaBuck ideas

Screening hundreds of ideas needs two things the per-study harnesses do not give: a cheap way to
describe an idea (a plugin), and a way to stop the false positives that hundreds of trials
produce. This framework is built on the mandatory Numba engine `research/fast_engine` and adds

* a **plugin API** — an idea is a Python module with `ARMS = {arm_id: spec}` and a vectorised
  `targets(data, spec)`;
* **extra data** — `data.extra(key)` for series beyond the idea's own instrument (`^VIX`, FRED
  yields, sector ETFs, GLD/TLT/IEF, the other markets' indices), declared with file, URL, range and
  SHA-256 in `EXTRA_DATA.json` and downloadable from Yahoo/FRED by the same module;
* a **trial ledger** (`REGISTRY.jsonl`) that refuses to screen an arm whose code is not committed
  *before* the run, whose line count N feeds the multiple-testing correction, and which
  re-verifies every line against git before a batch (several plugin branches merge into it);
* a two-stage protocol — **stage 1** screens on the main window and its block bootstrap only,
  **stage 2** applies the full holdout gate set to the survivors, and both skip the windows an
  arm declares `NotApplicable` (see §2);
* a **smoke runner** (`smoke.py`) that calls `targets` on synthetic and real panels without
  computing any performance number;
* a **deflation** pass (White's Reality Check, Romano–Wolf stepdown, deflated Sharpe) over every
  trial ever registered.

Everything the framework simulates goes through `fe_sim.sim_one`; the only new kernels are two
numba drivers over that same function (a per-arm target panel instead of production R2's single
series, and a per-arm fill rule), and `tests/test_parity.py` asserts both reproduce the engine's
`fe_boot.boot` and the old pure-Python `boot_oos_market` **bit for bit** (max |diff| = 0, not a
tolerance). Extra series are plain numpy evaluated inside `targets` — no kernel, no engine change.

Run it from the worktree root with the research venv:

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.batch_screen.run --harness
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas my_idea          # no metric, no ledger write
PYTHONPATH=. $R -m research.batch_screen.smoke --keys                   # the extra-data keys
PYTHONPATH=. $R -m research.batch_screen.run --verify-registry          # the ledger vs git
PYTHONPATH=. $R -m research.batch_screen.run --batch my_batch --ideas my_idea
PYTHONPATH=. $R -m research.batch_screen.run --deflate --batch my_batch
PYTHONPATH=. $R -m research.batch_screen.extra --keys                   # same table, longer form
research/batch_screen/tests/run.sh            # pytest
```

## 0. Writing a plugin (the short version)

```sh
cp research/batch_screen/TEMPLATE_plugin.py research/ideas/my_idea.py
$EDITOR research/ideas/my_idea.py                 # ARMS = {...} and targets(data, spec)
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas my_idea
git add research/ideas/my_idea.py && git commit   # the runner refuses uncommitted code
PYTHONPATH=. $R -m research.batch_screen.run --batch my_batch --ideas my_idea
```

`TEMPLATE_plugin.py` is a working example of both of the v2 mechanisms (it reads `data.extra("vix")`
and raises `NotApplicable` for a window whose `^VIX` series is empty) and of the production target
series; it is illustration only, not a claimed improvement. The smoke runs the arm on a synthetic
panel, the real main window, the `M2` OOS window, the `XLK` instrument window and a real
block-bootstrap chunk, and reports — without any return, NAV or gate — the shapes, the leg, the
extra keys the arm touched and how much of each series is missing:

```
  my_idea/demo     OK    synthetic ok[3x800] | main ok[1x6932] | oos:M2 n/a: the ^VIX series has no value in this window | etf:XLK ok[1x6994] | boot ok[4x2777]
                         extras: vix (ffill=True; NaN synthetic 0%, main 0%, oos:M2 100%, etf:XLK 0%, boot 0%)
```

## 1. The plugin API

```python
# research/ideas/<idea_id>.py
ARMS = {"arm_id": {"rule": "K", "leg": "2x", "cash_yield": "none", ...fixed params...}}

def targets(data, spec):
    """(P, T) target weights of the held leg, or (wants, leg), or (wants_a, wants_b, leg_b)."""
```

`targets` gets a `MarketData` panel whose arrays are all `(P, T)` — `P = 1` for a real window, a
bootstrap chunk otherwise — and must be written in vectorised numpy:

| field | meaning |
|---|---|
| `days` (T,), `year`, `month` (T,) | sessions of the window / chunk |
| `price` (P,T) | the underlying's price close: QQQ without dividends on main, the reconstructed price path on a bootstrap chunk, the index/ETF close on the OOS and instrument windows (the series the production vote runs on) |
| `legs` `{key: (open, close)}`, `leg` | every leg the window offers and the one this arm holds |
| `open`, `close`, `vol` (P,T) | the held leg's open/close (`vol = vol_n(close)`), the sizing input |
| `div`, `rate` (P,T) | per-session dividend and DTB3 rate of the window's construction |
| `high`, `low` (P,T or None) | underlying high/low where the data has them (ETFs) |
| `meta` | the window's own constants (`fin`, `exp_q`, `window`, …) |
| `extra(key, ffill=False)` | a registered extra series, `(P,T)`, `NaN` where the series has no value on that session |

**Extra data** (`extra.py`, registry `EXTRA_DATA.json`, 32 keys / 31 series committed): `^VIX`,
`^VIX3M`, `^VXN`, FRED `DGS10`/`T10YIE`/`DTWEXBGS`/`BAA10Y`/`CPIAUCSL`, `SMH`, `SOXX`, `HG=F`,
`SHY`/`GLD`/`TLT`/`IEF`, the nine sector ETFs, `^STOXX50E`/`^GDAXI`/`^N225`/`^FTSE`/`^IXIC`/`^GSPC`/
`^NDX` and `local_index` (the window's own index: `^NDX` on main, the market's index on each OOS
window). `python -m research.batch_screen.smoke --keys` prints the key table, its coverage on the
main and `M2` windows and the first/last session each series reaches; `data.extra` raises
`extra.ExtraError` for an unknown key, and a key whose window map has no entry for the window.

* alignment is **by date**: a session gets the series' value on that session's own date (`NaN`
  where the series has none — an FRED series is missing on non-publication days, a monthly series
  only exists on the first of the month, so `ffill=True` carries the last value forward, leaving
  leading `NaN`s in place). Inside a bootstrap chunk the value is the *drawn source session's* of
  that column, i.e. the chunk replays the extra series together with its returns.
* a key is a series name (one string: the same series in every window) or a `{"windows": {…}}` map
  resolved as: the window's own entry, else `main` (a clean/fresh bootstrap chunk replays the main
  window's pool), else `*`.
* sources are the Yahoo chart API and FRED CSV only. `python -m research.batch_screen.extra
  --fetch-all` (or `--fetch SYM,SYM` / `--fetch-fred ID`) refetches and rewrites the file, the
  registry's `sha256`/`range`/`rows`/`url` and — before any batch runs — `--verify` re-hashes every
  file and re-reads every declared range. Add a hand-made series (a public calendar, say) by
  writing its `date,value` CSV and its registry entry, then `--verify`.

Two legs are supported: `2x` is the campaign's published synthetic daily-reset 2x (this is what
every published number used) and `1x` is the same underlying at gearing 1 with no cost. The leg is
constant per arm; `targets` may *return* a per-session leg selection, but a varying one raises
`NeedsKernel` — an arm's instruments are fixed, only their weights vary.

**Two-leg arms** (`research/fast_engine2`, task `two_leg_kernel_v1`). An arm whose spec declares
`"leg_b": key` — a panel leg (`2x`/`1x`) or a registered extra key (`shy`, `ief`, `gld`, `tlt`, …,
`api.leg_b_keys()`) other than its own `leg` — returns `(wants_a, wants_b, leg_b)` (both `(P,T)`,
`leg_b` equal to the declaration). The runner routes it to `fe2_sim.simulate2` (one window) and
`fe2_boot._boot_chunk2` / `_pt2` (bootstraps): one cash pool, next-open fills, each leg with its
own units and FIFO lot book, the rule's 0.5 pp band, A1u gain band and H2 deferral applied **per
leg on that leg's own book**, per-leg costs, financing only inside the synthetic 2x leg's price,
optional idle-cash yield as in the engine, and one taxpayer: both legs' realisations (Yİ-ÜFE
indexed per lot) add into one annual net TRY gain. A tax-funding sale sells leg A first, then B.
An extra series has one column (its adjusted close), used as both the leg's fill and close price
(`MarketData.leg_series`); a leg-B series without a price on every session the engine reads makes
the window `NotApplicable` (`leg_b_usable`; for a bootstrap chunk, on the drawn source sessions),
so for example a GLD leg is not applicable on the main window (GLD starts 2004-11-18). Bootstrap
groups are `(leg, leg_b)` and share each chunk's drawn paths, so Δ vs the control stays paired.
With leg B identically zero the kernel is bit-identical to the engine (`fe2_parity`, in the
harness). Not expressible yet (still `NEEDS_KERNEL`): a per-session instrument switch, two books
of the *same* instrument, several cash pools/accounts, per-arm tax regime or capital, and gain-
aware routing between books (IB38 A/B/C).

**Availability.** `targets` may raise `api.NotApplicable(reason)` for a window whose inputs do not
exist (`^VIX` before 1990 for `M2`, a European index for `^N225`, a series that starts after the
window). Stage 1 requires the **main** window to be applicable; in stage 2 the six OOS windows and
the 21 ETFs are evaluated on the applicable ones only (§3, §4).

Engine RULE options come from the spec and are the engine's own three booleans:

* `rule` — `K` (band + USD gain fraction + December deferral), `C0`/`R2` (plain 0.5 pp band),
  `A1u` (band only), `H2` (deferral only), or a name a plugin registers with
  `research.batch_screen.rules.register_rule(name, band, usd_rule, defer)` at import time
  (`fe_sim.RULE` is the engine's extension point, so no engine file is edited). A genuinely new
  mechanism outside the option set below — a bracket budget, a third leg — is **not** expressible:
  declare it `NEEDS_KERNEL = "why"` at module level (or `"needs_kernel": "why"` in the arm's spec),
  and the runner registers the trial and refuses to simulate it. That is the honest boundary: a new
  mechanism is a kernel change in a copy of the engine plus a parity cell, i.e. a separate study.
* `leg` — `2x` (default), `1x`, or `2x_spy` (the S&P 500 daily-reset 2x leg of
  `research/fast_engine_opts`, IC40; on a window without an SPY series it resolves to that window's
  own `2x` leg); `leg_b` — the second instrument of a two-leg arm (above).

**Kernel options (`register_rule(..., opts={...})`).** The single-leg execution mechanisms the
engine does not implement live in `research/fast_engine_opts` — a *copy* of `fe_sim.sim_one` with
every option inert by default, so that options-off is the engine bit for bit (1260 cells and the
bootstrap driver, max |diff| = 0; read that package's README). A plugin asks for one through the
rule it registers:

```python
from research.batch_screen import rules
rules.register_rule("kopt_band_up0", band=True, usd_rule=True, defer=True,
                    opts={"band_up": 0.0})      # IC59-A: immediate upsizes, K's downsize gate
ARMS = {"A": {"rule": "kopt_band_up0", "leg": "2x", "cash_yield": "none"}}
```

`opts` holds the values of any `fe_opts.Opts` field (a per-direction band width, a dollar floor on
a permitted downsize, order slicing, the NAV-drawdown governor, margin interest on a negative
balance, the early-close calendar, T+1 funding, the TRY conversion cost and pre-funding, the fill
reference) plus two things that are properties of the *window* rather than of the plugin:
`margin_spread` (the loan rate over the window's own DTB3 column) and `wht` (dividend withholding
in the leg's construction). `resolve_arm` returns the dict as `arm["opts"]`, and `stages.run_one`,
`boot.boot` and `boot.oos_pooled` route a non-empty one to `research.fast_engine_opts.bridge`;
with `{}` every call site is the engine's own path, unchanged. An arm cannot combine `opts` with
`leg_b` (options are single-leg).
* `cash_yield` — `"none"` (default) or `"tbill"` (the session-folded DTB3 schedule of
  `engine README`). It changes every published cell, so it is a batch-level flag: an arm whose
  declaration disagrees with `--cash-yield` is refused.

Three example plugins reproduce published results exactly (`--harness`, and the `anchors_v1`
batch): `k_control/K` (production K — the framework's control), `r2_control/R2` (the same target
series under the unbanded rule) and `vol_shape_p15/p15` (`research/vol_shape_v1`'s I2 arm at
p = 1.5, c = 0.24593188954326456). All three match their published pre-tax cells, after-tax
windows and bootstrap medians to ≤ 0.0001 pp.

## 2. Registry and pre-registration by commit

`REGISTRY.jsonl` gets one line per arm ever screened, append-only:

```json
{"idea": "vol_shape_p15", "arm": "p15", "spec": {...}, "spec_hash": "sha256…",
 "code_hash": "sha256…", "batch": "anchors_v1", "date": "2026-10-16", "status": "screened",
 "git": {"head": "...", "idea_commit": "...", "commit_gate": "pass"}}
```

The runner **refuses to screen an arm whose module's working-tree bytes differ from its bytes at
HEAD** (`RegistryError`, see `registry.require_committed`) — the `code_hash` in the ledger is
therefore the committed one, and a spec cannot be edited after seeing results without a new
ledger line (`status: "rerun"` when the same spec/code has been screened before). Lines are never
removed. N is the number of distinct `(idea, arm, spec_hash)` ever registered — the
multiple-testing count, including arms that were declared but refused (`needs_kernel`).

**Merging several plugin branches.** `.gitattributes` marks `REGISTRY.jsonl` `merge=union`, so
branches that each *append* lines merge without conflict. A union merge cannot corrupt the ledger's
meaning because the runner re-verifies it before every batch (`--verify-registry` prints the
report) and **aborts** on any inconsistency:

* every line's `code_hash` must be the SHA-256 of that module *at the commit the line names*
  (`git show <idea_commit>:research/ideas/<idea>.py`) — a line therefore describes code that was
  really committed, not a merge artefact or an edited ledger (lines whose commit gate was bypassed
  with `--allow-dirty` cannot be checked and are reported as `skipped`);
* every registered `(idea, arm)` must still exist at HEAD and its spec must hash to the *latest*
  recorded `spec_hash` — catching an arm renamed, deleted or re-specified by a merge without a new
  ledger line (`spec_drift`);
* a line missing its fields (`malformed_line`, e.g. a hand-resolved merge block) is reported and
  refused rather than crashing the counter.

A module that changed *after* its last screen (expected while a branch grows: a later commit adds a
second arm to the same file) is reported as `stale`, which is information, not an error.

## 3. Stages

**Stage 1 — screen.** Main window only (QQQ/QLD* from
`data/historical/stress_robustness_v1_daily.csv` and `…_dtb3.csv` as `fe_data` loads them,
2000-03-09 → 2026-09-29, base and calibrated financing, `m = 2`), the 2000 after-tax window at
$100k and the clean block bootstrap (seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax
paths). Pass rule (fixed, pre-registered):

```
after-tax 2000-window Δ > 0  AND  B1-recent median Δ > 0
AND pre-tax max DD ≤ K + 2 pp (base and calibrated, m = 2)
AND turnover ≤ 2 × K (base and calibrated, m = 2)
```

**Stage 2 — holdout**, for stage-1 survivors only: G1–G9 vs K (as `ewma_band_v1` defines them,
G9 = ≤ 2 × K), after tax at $100k on all three windows, B1/B2 median Δ ≥ 0 with share > 0 ≥ 55 %,
the 21 ETFs after tax (median > 0, share ≥ 60 %), the 21 January rolling starts (median ≥ 0,
share ≥ 60 %), the six OOS windows (median Δ ≥ 0, ≥ 5 of 6 within −0.5 pp CAGR / +3 pp DD, max DD
Δ ≤ +8 pp), the pooled OOS bootstrap (median ≥ 0) and the fresh-seed bootstraps (seed 20261016,
blocks 10/40, median ≥ 0 and share ≥ 55 % in all four cells). A stage-2 failure still writes its
record, so the registry counts it.

**Availability (`NotApplicable`).** Stage 1 requires the *main* window to be applicable — an arm
that declares N/A on the main window or inside the stage-1 bootstrap is recorded with
`verdict = not_applicable` (the reason and the stage are in the record) and is not screened further.
In stage 2 the two multi-window stages drop the windows the arm refuses, and the record shows which
ran and why the others did not:

* **G7** is evaluated on the applicable OOS windows `k` (the record's `G7_detail` then carries
  `n_applicable`, the reasons and `n_within_required`). At `k = 6` it is the published rule
  unchanged; on a reduced set the same "one outlier allowed" rule is applied as `n_c ≥ k − 1` and
  `n_d ≥ k − 1`, and `k ≥ 3` is required.
* **G8** (pooled OOS bootstrap) runs on the applicable market subset with the control re-drawn
  **inside the same draw** — the rng sequence depends only on the market list, so the two arms
  share their paths and the Δ stays paired (`G8_detail` names the subset). A market whose *chunk*
  the arm refuses is dropped after a one-path probe per market.
* **E1** is computed on the applicable instruments only, and is not evaluable below 15 of them.
* **`holdout_insufficient`** — an arm that comes through stage 1 with fewer than 3 applicable OOS
  windows or fewer than 15 applicable instruments is **not a pass**: `verdict =
  holdout_insufficient`, never declared, and listed in the scoreboard's "manual confirmation"
  section with the counts and the reasons, for its own pre-registered confirmation study. The
  registry and N are unchanged — every such arm keeps its ledger line.

An arm that is applicable everywhere takes exactly the v1 code path: same record, same scoreboard,
same numbers (`--harness` and the committed results files are unchanged by all of this).

The Δ in every cell is `arm − K` in percentage points, on the *same* resampled paths (the
bootstrap draw is a deterministic function of `(seed, block, path index)`), which is what makes
the Δ vectors paired across arms.

## 4. Deflation

Three tests, all on the **B1-recent Δ vector** of every screened arm (seed 20261011, block 20,
1,000 paths, after-tax $100k progressive) — the statistic the screening decision is built on:

* `white_rc` — White (2000): `p = P*(max_a t*_a ≥ max_a t_a)`;
* `rw_stepdown` — Romano–Wolf (2005) stepM: studentized max statistic, arms ordered by the observed
  t, at step k the reference set is the arms still under test, p-values made monotone along that
  order; a rejection is `p_adjusted ≤ 0.05`;
* `deflated_sharpe` — Bailey & López de Prado (2014): the probability that the selected arm's
  advantage-Sharpe (mean/std of its Δ vector) exceeds `E[max SR]` over N = the ledger count trials,
  using the cross-sectional variance of the trial Sharpes and the Δ sample's skew/kurtosis.

A candidate is declared only if it passes stage 2 **and** `rw_stepdown` rejects at 5 % **and** the
deflated Sharpe is ≥ 0.95.

**Method.** The null distribution of the max statistic is built by a *second-level* bootstrap:
2,000 resamples of the B per-path Δ values (the same resampled path indices for every arm, which
preserves their cross-arm dependence), recentered per arm to its own mean, studentized by the
resample's own standard error.

**Assumptions, stated plainly.**

1. The first-level block bootstrap is taken as an adequate approximation of the sampling
   distribution of the Δ estimator (the campaign's pre-existing choice, not this framework's).
2. Arms are *paired*: every arm screened in a batch shares the control's paths for the same
   (seed, block, path count). Arms screened under a different draw are not poolable.
3. The second-level resampling adds no information beyond that distribution; it is a way of
   reading a p-value for the mean off a finite panel of path draws.
4. 20-session blocks cannot capture serial dependence longer than 20 sessions, so the p-values are
   optimistic under long memory — the same caveat the campaign's own bootstraps carry.
5. The deflated Sharpe here is the *advantage*-Sharpe of the bootstrap Δ distribution, not the
   daily-return Sharpe of the strategy (the screening decision is made on the after-tax CAGR
   advantage, and the deflation is applied to the same statistic).

## 5. Outputs, cost, and byte-identical reruns

```
results/<batch>/_control.json            the control's record + its raw per-path vectors
results/<batch>/<idea>__<arm>.json       one arm: stage 1, stage 2, gates, the B1 Δ vector
results/<batch>/RESULTS.json             the aggregate (the byte-identity check)
results/<batch>/SCOREBOARD.md            one line per arm
results/<batch>/timing.json              wall clock per arm (the only non-deterministic file)
results/DEFLATION.json                   the last deflation pass
HARNESS.json                             the --harness checks
```

Every number is rounded to 6 dp and every container sorted before serialising, so re-running a
batch reproduces `RESULTS.json` and the per-arm files byte for byte. Arms run in separate
processes (`--workers`, default = the CPU count); each arm is independent because it re-runs the
same deterministic path draw, and the control is computed once per batch and shared. One arm costs
about 1 s of stage 1 plus (if it survives) 5–10 s of stage 2 on this machine — the synthetic
scale factor is the full holdout set, not the arm.

**The availability probes.** `probes/` holds two engineering fixtures (never registered as trials —
they are outside `research/ideas/`) that exercise the `NotApplicable` paths on real data:
`yield_veto.py` (a `dgs10` veto whose extra series feeds the weights) and `coverage_gate.py`
(`dgs10_gate` applicable on four OOS windows, so G7 runs on four and G8 on `{M1, M3, M4, M5}`;
`gld_gate` applicable on one, so it must come out `holdout_insufficient`).

```sh
PYTHONPATH=. $R -m research.batch_screen.probes.run_probe_batch      # ~50 s, isolated ledger
```

## 6. Tests

`research/batch_screen/tests/run.sh` (the research venv's numba + the project venv's pytest, see
the header of that script; it finds the project venv whether or not the worktree sits next to the
main checkout). 110 tests: the plugin contract and its validation, the leg constructions, the
registry's append-only semantics and commit gate, every stage-1/stage-2 gate branch on synthetic
records, the deflation's null control/power/monotonicity/DSR properties, the extra-data layer
(registry resolution, date alignment, the bootstrap chunk convention, ffill, downloads and hashing
— all offline), the availability rules, the ledger's re-verification against git, and
the parity cells (engine `fe_boot.boot`, old `boot_oos_market`, the production R2 target series,
`vol_shape_v1`'s sizing, and the control's published K cells stage by stage), and the two-leg
contract and routing (`tests/test_two_leg.py`, probe `probes/two_leg.py`). The harness is 73
checks: the original 59 plus the two-leg routing checks and the two-leg kernel's parity groups.
