# kernel_ext_v1 — the N-leg router, five legs, two books of one instrument; screening the last kernel-blocked arms

> Evidence: tag `archive/research/closeout-2026-10` (not on `main`).

**Verdict: the kernel extension is built and parity-proven; the batch is screened and nothing is
declared.** `research/fast_engine3` now holds **five** instruments in one cash pool and carries
`fe2_opts`'s gain-aware downsize router generalised to N legs; `batch_screen` accepts a `"legs"`
declaration that names one instrument twice (two FIFO books of one instrument). The three
kernel-blocked families — `IC36/B` (a five-instrument off-state blend), `IC41/A–C` (QLD\* + the S&P
500 2x leg with the router) and `IB38/C` (the routed split) — are now expressible and screened as
batch `kernel_ext_v1`. **All five arms fail Stage 1** (the after-tax 2000-window Δ and the B1-recent
median Δ both negative for every arm; `IC36_N/B5_N` also breaks the turnover gate), so no holdout
runs, `declared = []`, and nothing is a candidate for an owner decision.

Evidence: this study's `PREREGISTRATION.md`, `RESULTS.json`, `SUMMARY.txt`, `TIMING.json`,
`DATA_MANIFEST.json`, `run.py`, `run_batch.py`; the kernel
`research/fast_engine3/` (`README.md`, `fe3_sim.py`, `fe3_boot.py`, `bridge3.py`,
`fe3_parity.py`, `PARITY3.json`); the framework `research/batch_screen/` (its
`PREREGISTRATION.md` **§6.7**, `HARNESS.json`, `REGISTRY.jsonl`,
`results/kernel_ext_v1/`, `results/DEFLATION.json`); the new arms
`research/ideas/IC36_N.py` (`B5_N`), `research/ideas/IC41_R.py`, `research/ideas/ib38_C.py`. Ideas:
`docs/research/idea_bank_v1.json` (IB28, IB38) and `idea_bank_v2.json` (IC36, IC41).

## 1. The kernel extension

| change | where | parity |
|---|---|---|
| `N_LEGS` 4 → 5 (`LEG_NAMES` A..E), `api.MAX_LEGS` 4 → 5 | `fe3_sim.py`, `batch_screen/api.py` | a five-books cell (`multi_leg.five_books_trade`) holds five trading books and a sixth key is refused |
| the gain-aware downsize router, N legs: `route_cap`/`route_score` (`usd` \| `indexed`) on `sim3`/`simulate3`, threaded through `fe3_boot` and `bridge3` | `fe3_sim.py`, `fe3_boot.py`, `bridge3.py` | `router.router_indexed_eq_fe2opts_{C0,K}_{pre_tax,progressive}` reproduce `fe2_opts.simulate2(knobs={"route_cap_pp": ...})` **bit for bit** (NAV and both per-leg `_legs` rows); `router_off_is_the_current_kernel` is `fe2_sim.simulate2` exactly; `router_changes_the_split` bounds the move by the cap; `router_usd_vs_indexed` |
| two books of one instrument (a repeated `"legs"` key), declaration + positional `targets` return | `batch_screen/api.py`, `bridge3.py` | `two_books_of_one_instrument` runs two books of one series and the router separates them; `fe3_parity.two_books_of_one_instrument` |
| the router / two-books routing point (`bridge3.needs_kernel3`), the declaration rules, the harness cells and pytest cases | `batch_screen/{api,run,probes/multi_leg,tests}` | `router.a_router_arm_reaches_the_n_leg_kernel`, `two_books_of_one_instrument_run`, `two_books_of_one_instrument_is_a_legal_declaration` |

`research/fast_engine/`, `research/fast_engine2/` and `research/fast_engine_opts/` are **not edited**.
`fe2_opts`'s move is reproduced verbatim: on a session in which at least two legs want to downsize,
the units the most expensive selling leg would realise move to the cheapest selling leg
(`x = min(-du[other], un[cheap] + du[cheap], route_cap * nav_after / oq[cheap, i])`), and a routed
session fills the routed units. With exactly two selling legs the (cheap, other) choice is
`fe2_opts`'s (`fr[A] <= fr[B]` → cheap = A), which is what makes the indexed case bit-for-bit.

**Suites, all green** (`research/kernel_ext_v1/run.py --harness`, `SUMMARY.txt`):

| run | result |
|---|---|
| `python -m research.fast_engine.parity --quick` | **PASS** (`research/fast_engine/PARITY.json` unchanged) |
| `python -m research.fast_engine3.fe3_parity --write` | **PASS, 215 cells, 0 failed** (was 206) |
| `python -m research.batch_screen.run --harness` | **PASS, 143 checks, 0 failed** (was 139) |
| `python -m research.fast_engine_opts.harness` | **PASS** (14 anchors + 13 mechanisms, unchanged) |
| `bash research/batch_screen/tests/run.sh -q` | **154 passed** (was 152; one pre-existing red test reconciled) |
| `bash research/fast_engine_opts/tests/run.sh -q` | **36 passed** |
| `python -m research.batch_screen.run --verify-registry` | **PASS** (544 lines, 317 arms, N = 317) |

The K control's published anchors are reproduced through the engine (framework harness):
pre-tax m = 2 CAGR **15.7649 / 15.1785 %** (base / calibrated), max DD **46.6698 / 46.7948 %**,
after tax at $100k **10.7533 / 15.2354 / 23.9720 %** on the 2000 / 2010 / 2017 windows — every cell
within 4 × 10⁻⁵ pp. The **R2** reference row is 15.48 / 14.90 % (m = 2), 15.75 % (m = 1), max DD
46.76 % — the published values, through `r2_control/R2`.

## 2. The arms

| arm | idea / bank arm | declaration |
|---|---|---|
| `IC36_N/B5_N` | IC36/B | `legs ["2x","gld","ief","shy","sec_xlu"]`, `start "auto"`; while the E2 vote is off, `0.98 × (1/vol20_i)/Σ_j(1/vol20_j)` over the four sleeves, monthly |
| `IC41_R/A_N_R` | IC41/A | `legs ["2x","2x_spy"]`, each leg 0.5 of production's target; `route_cap_pp 10`, `route_score "usd"`, `up_resize "furthest_below_share"` |
| `IC41_R/B_N_R` | IC41/B | as A but leg weights ∝ 1/vol20 of each index |
| `IC41_R/C_N_R` | IC41/C | as A but the early half-weight path is held only in the Nasdaq leg |
| `ib38_C/C` | IB38/C | `legs ["2x","2x"]` — two FIFO books of one instrument, 0.5 of the target each, `route_cap_pp 10`, `route_score "usd"` |

The pinned twins (`IC36_N/B_N`, `IC41/A_N–C_N`, `ib38/C`) keep their `needs_kernel` ledger lines and
their byte-identical modules; every old `spec_hash` re-computes unchanged and
`git diff -U0 fc5dcfa3 -- research/ideas/ | grep '^-[^-]'` is empty.

## 3. Per-arm results (Δ = arm − K in pp, on the arm's own declared window)

Stage-1 gates read from the records: `AT` = after-tax $100k 2000-window Δ > 0, `B1` = B1-recent
median Δ > 0, `DD` = pre-tax max DD ≤ K + 2 pp (both financing models, m = 2), `TO` = turnover ≤
2 × K (both models).

| arm | window | AT2000 Δ | AT2010 Δ | AT2017 Δ | B1 med / share | B2 med / share | pre-tax m2 CAGR b/c | maxDD Δ b/c (pp) | TO ×K b/c | failed gates | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC36_N/B5_N` | **w20041119** | **−0.5584** | −0.3439 | −0.3464 | **−0.6726** / 0.278 | −1.0793 / 0.159 | 19.23 / 18.56 % | +0.88 / +0.88 | **2.006** / 2.006 | AT, B1, TO | reject_stage1 |
| `IC41_R/A_N_R` | default | **−0.9891** | −2.7127 | −4.3665 | **−0.0334** / 0.490 | −0.2893 / 0.442 | 14.67 / 14.08 % | −10.93 / −10.88 | 1.327 / 1.329 | AT, B1 | reject_stage1 |
| `IC41_R/B_N_R` | default | **−1.2076** | −3.2089 | −5.1543 | **−0.1049** / 0.481 | −0.2058 / 0.459 | 14.88 / 14.28 % | −10.71 / −10.66 | 1.649 / 1.649 | AT, B1 | reject_stage1 |
| `IC41_R/C_N_R` | default | **−0.8848** | −2.5832 | −4.1710 | **−0.0237** / 0.494 | −0.2380 / 0.454 | 14.76 / 14.17 % | −10.93 / −10.88 | 1.326 / 1.328 | AT, B1 | reject_stage1 |
| `ib38_C/C` | default | **−0.3345** | −0.5191 | −0.8814 | **−0.1217** / 0.312 | −0.0870 / 0.371 | 15.72 / 15.13 % | +0.30 / +0.30 | 1.103 / 1.103 | AT, B1 | reject_stage1 |
| `k_control/K` | — | 0 (control) | 0 | 0 | 0 / — | 0 / — | 15.76 / 15.18 % | — | 1.000 | — | control |

Bold = a failing cell. Every one of the five `b1_delta_pp` vectors is **1000/1000 finite** and
`DEFLATION.json`'s `excluded_non_finite` is `{}` — **no NaN appears in this batch**.

Read plainly: **the router is a tax-side mechanism and this batch is a tax-side loss without a
pre-tax gain to offset.** The two IC41 families split the leveraged Nasdaq exposure into a Nasdaq
book and an S&P book; that genuinely halves the concentration (max DD −10.9 pp — the largest DD
improvement in the campaign) but the S&P leg's lower return costs more pre-tax than the routing
saves after tax (AT2000 −0.89 to −1.21 pp, worsening to −4.4/−5.2 pp by 2017 because the split
persistently underperforms QLD\* in a Nasdaq-led market). The USD router's own effect is visible
but small: `A_N_R` and `C_N_R` are within 0.1 pp of each other, and the B1 median Δ is only −0.02
to −0.10 pp — the routing does not rescue the level. `IC36_N/B5_N` repeats the `IC36_N/A_N`/`C_N`
result (the bank_v4b blend) with one more sleeve and a turnover ratio exactly at the gate (2.006×K:
the fifth book trades). `ib38_C/C` — two books of the *same* instrument — is the mildest failure
(−0.33 pp AT2000, +0.30 pp DD, 1.10×K turnover): splitting one book into two halves only re-times
the band, and it slightly *raises* turnover and drawdown without any tax saving, because the two
books see the same prices and the same gain fractions.

**Plausibility check (the `rw_boot_audit_v1` signature).** No bootstrap Δ here is an order of
magnitude off its fixed-window Δ — the largest |B1 median| is 0.67 pp (`B5_N`) against a −0.56 pp
2000-window Δ, the same order. The DDs are negative (impossible as a *bug* signature) and the
turnover ratios are 1.1–2.0×K. Nothing here looks like the pre-fix leg-B defect.

## 4. Deflation and N

* **N = 317** distinct `(idea, arm, spec_hash)` over the whole campaign ledger (was 312; this batch
  adds 5 arms → 544 ledger lines, `--verify-registry` PASS; the `stale` set is the same `_N`/`_K`
  twin modules of bank_v3/v4b, information per `verify_ledger`).
* `white_rc`: best arm `IC41_R/C_N_R`, `t_max` = −2.0193, p = 0.9995; `rw_stepdown` **rejected = []**
  (p_adjusted 0.9995–1.0000); `deflated_sharpe`: `var_sr` = 0.056993, `sr0` = 0.626364, and every
  arm's `sr_obs` is **negative** (−0.06 to −0.55) → `dsr = 0.0`, `pass_5pct = false`.
* `stage2_pass = []`, `declared = []`.

## 5. Still blocked, and why

* **`IB38/A` (the regime × capital × window switch matrix) and `IB38/B` (two/three independent
  accounts summed)** stay `NEEDS_KERNEL` in `research/ideas/ib38.py`: A varies *batch-level* settings
  (`targets` returns weights only); B is several cash pools summed into a household series. Neither
  is a target-weight rule and neither is this kernel's job.
* **The pinned twins** (`IC36_N/B_N`, `IC41/A_N–C_N`, `ib38/C`, and the older `IC36/A–C`,
  `IC41/A–C` from bank_v3) keep their `needs_kernel` lines and specs; the runnable arms are the new
  names. `IC36_N/B_N` is *now* expressible (capacity 5) — its twin `B5_N` is this batch's arm — but
  the pinned line cannot be re-specified without a `spec_drift`.
* **Still outside the kernel** (named, not approximated): a per-session instrument *switch* inside
  one leg; more than five instruments; several cash pools or a per-arm tax regime/capital; and the
  single-leg execution options (`band_up`/`band_down`, `min_notional`, slicing, the drawdown
  governor, `early_mode`, `settle_floor`, `fx_cost`, `fill_mode`, `wht`) on a multi-leg arm — only
  the margin loan and the router reach the N-leg kernel.

## 6. Method, determinism and amendments

* **The batch screens only the new arms.** `run.plan` iterates every arm of a listed module, so
  `run_batch.py` rebinds the runner's `plan` global in its own process with a restricted arm set and
  otherwise calls the framework's own `plan`/control/worker/scoreboard/deflation code; **no framework
  file is edited by the driver** and no pinned arm is re-run or re-registered.
* **Determinism.** The five per-arm records were re-simulated through the framework's own
  `run._arm_task` and are **byte-identical** (`cmp`, 5/5); `RESULTS.json` and `SUMMARY.txt` are
  byte-identical over two `--report` runs (and `RESULTS.json` over two `--harness` runs). The parity
  suite ran twice (`--write`, then via the harness) with identical 215/215.
* **Add-only.** `git diff -U0 fc5dcfa3 -- research/ideas/ | grep '^-[^-]'` prints nothing; every old
  arm's `spec_hash` re-computes unchanged. The two new modules carry their parents' refusal reasons
  verbatim; `IC36_N.py` gains one new arm.
* **Amendments.**
  1. `research/batch_screen/PREREGISTRATION.md` **§6.7** (dated 2026-10-17), committed **before** any
     arm of batch `kernel_ext_v1` was screened.
  2. **Bug fixed before any gate number was read:** `IC41_R/B_N_R`'s `targets` fed the *already
     computed* `data.vol` back through `fe_signals.vol_n` (a vol-of-vol whose leading NaN poisoned
     the `cumsum`), so leg A's weight was 0 on every session and the arm ran as a single book. Found
     by this study's own `run_batch --check`; the fix inverts `data.vol` directly for leg A and takes
     `vol_n` only of a *price* series for leg B. The arm's frozen spec is unchanged; no ledger line
     for `IC41_R` existed at the time. (An earlier draft of the `fe3_sim` router also omitted the
     `if routed: d = du[l]` fill override in the execute block; the `fe3_parity` router cells caught
     it before the first commit, so no published number or arm was affected.)
  3. **Reconciled framework test:** `test_ledger_merge.py`'s `stale == []` assertion was **already
     red at this branch's base commit** — bank_v3/v4b added `_N` arms to existing modules without
     re-registering the pinned old ones, and `verify_ledger`'s own docstring (and `batch_bank_v4b.md`)
     report `stale` as information. It is replaced by "every stale name is a committed ledger arm".
* **Wall clock.** `--run` (harness + batch + deflation) **129.7 s**; the batch itself
  **69.2 s** on 6 workers (`results/kernel_ext_v1/timing.json`; per arm 14.8–30.3 s), the three
  paired controls computed once each; `--report` **≈ 54 s**.

## 7. Caveats

1. Δ is `arm − K` on the same resampled paths and on the arm's **own declared window**
   (`B5_N` on `w20041119`, the rest on the default window); a Δ across two windows is not a
   comparison, and `DEFLATION.json`'s `window_tags` names them.
2. `IC41_R/*`'s `route_score = "usd"` is the bank/IB28 wording (the lower USD FIFO gain fraction);
   the kernel's `indexed` score is the one proven bit-for-bit against `fe2_opts`, so the arms'
   *own* setting is the `usd` variant — the parity covers the mechanism, not the arm's score choice.
3. `ib38_C/C`'s "two accounts" is read as **two books of one instrument in one cash pool** (the
   frozen reading), each carrying half the target: the bank's "two unrouted identical accounts
   reproduce K's NAV exactly" belongs to a two-independent-accounts construction, and this one-pool
   split is *not* a relabelling of one full-weight book (the 0.5 pp band acts on each half-weight).
4. The `IC41_R/*` arms lose pre-tax as well as after tax (the S&P leg's return deficit), so the
   router's tax saving is never the binding effect on these specs; a router arm whose *pre-tax*
   level matches K would be a different spec, which the bank does not freeze.
5. One reduced-window control (`_control_w20041119.json`) is computed because `B5_N` declares
   `start = "auto"`; its Δ is paired against K on the same window.
