# Batch screen of the idea bank, batch 2 (batch_bank_v2, 2026-10-16)

> Evidence: framework `research/batch_screen/` (rules: its `README.md` + PREREGISTRATION.md, the
> framework doc is `batch_screen_v1.md`); the 36 blind-written plugins `research/ideas/IC*.py`
> (committed by the plugin workers before the screen — the runner refuses uncommitted code); the
> ledger `research/batch_screen/REGISTRY.jsonl`; per-arm records
> `research/batch_screen/results/bank_v2/` (no IC arm declared a second leg, so the two-leg kernel
> `research/fast_engine2/` was not exercised). Ideas:
> `docs/research/idea_bank_v2.md` / `idea_bank_v2.json`. The per-study discipline of the common
> protocol is replaced here by the framework's pre-registered pipeline; **no plugin, gate or
> framework file was touched before or after the run, so there is no Amendment section.** Batch 1
> (`bank_v1`, 89 arms) is in `batch_bank_v1.md`.

**Verdict: no candidate.** 36 ideas → 108 registered arms (105 simulated, 3 refused as needing a
new kernel). Six arms passed Stage 1 (main window + its block bootstrap); **all six failed
Stage 2**; the deflation's deflated-Sharpe gate (≥ 0.95 at N = 215) rejects every arm — the best
arm's advantage-Sharpe is 0.419 against a `sr0` of 0.984 — so nothing is declared.

| stage | arms |
|---|---|
| registered in this batch (incl. `needs_kernel`) | 108 |
| Stage 1 (main window 2000-03-09 → 2026-09-29 + its block bootstrap): pass | 6 |
| Stage 2 (holdout: 6 OOS windows, 21 ETFs, after-tax 2010/2017, fresh-seed bootstraps, 21 rolling starts, pooled OOS bootstrap): pass | 0 |
| declared after the multiple-testing deflation | 0 |
| `not_applicable` | 3 (IC17/A, IC17/B, IC17/C) |
| `holdout_insufficient` | 0 |

N (the deflation's multiple-testing count) = **215** distinct `(idea, arm, spec_hash)` — 107 from
`anchors_v1` + `bank_v1`, 108 from this batch; `REGISTRY.jsonl` holds 248 lines. Wall clock of the
run: **658.3 s** on 6 processes (`results/bank_v2/timing.json`; the control costs 15.4 s once per
batch). Control `k_control/K` (= production K) reproduces K's published anchors exactly
(`--harness` PASS, 73/73 checks): pre-tax base/calibrated m = 2 CAGR 15.7649 / 15.1785 %, max DD
46.6698 / 46.7948 %, after tax at $100k 10.7533 / 15.2354 / 23.9720 % on the 2000 / 2010 / 2017
windows — the same cells `band_h2_combo_v1` published, to ≤ 4e-05 pp.

The 96 arms rejected at Stage 1 fail on the *after-tax 2000 window* (91 of them), the *B1-recent
median* (89), the drawdown band (14) and/or turnover (6); the table below names the failing
conditions per idea. Nothing came in above +0.71 pp after tax on the main window: only 11 of the
105 simulated arms are positive on the after-tax 2000 window at all (and one of those, `IC26/C`,
by 6e-06 pp — it is the no-op below).

## Per-idea table (Δ = arm − K, pp; the idea's best arm by Stage-1 after-tax 2000-window Δ)

| idea | best arm | Stage-1 AT2000 Δ | B1 med Δ / share > 0 | max DD Δ (base) | turnover / K (base) | Stage 1 | Stage 2 / verdict |
|---|---|---|---|---|---|---|---|
| `IC01` | `IC01/A` | -2.1335 | -0.6901 / 0.379 | +0.5657 | 1.196 | FAIL | reject_stage1 (AT,B1) |
| `IC02` | `IC02/C` | -0.5159 | -0.4133 / 0.344 | +3.3208 | 1.013 | FAIL | reject_stage1 (AT,B1,DD) |
| `IC03` | `IC03/B` | -2.4929 | -0.9507 / 0.347 | +5.2545 | 1.458 | FAIL | reject_stage1 (AT,B1,DD) |
| `IC04` | `IC04/C` | -0.2747 | -0.2056 / 0.382 | -1.2198 | 0.989 | FAIL | reject_stage1 (AT,B1) |
| `IC05` | `IC05/C` | +0.0062 | -0.1583 / 0.415 | -1.9941 | 1.004 | FAIL | reject_stage1 (B1) |
| `IC06` | `IC06/C` | -0.5000 | -0.0000 / 0.484 | +3.8751 | 1.016 | FAIL | reject_stage1 (AT,B1,DD) |
| `IC07` | `IC07/B` | -0.7238 | -0.1431 / 0.488 | -6.4197 | 1.128 | FAIL | reject_stage1 (AT,B1) |
| `IC08` | `IC08/B` | -0.1375 | -0.1880 / 0.423 | -3.3069 | 0.900 | FAIL | reject_stage1 (AT,B1) |
| `IC09` | `IC09/B` | -0.7115 | -0.4163 / 0.303 | -0.7821 | 0.971 | FAIL | reject_stage1 (AT,B1) |
| `IC10` | `IC10/B` | +0.1824 | -0.2117 / 0.435 | +10.5721 | 0.987 | FAIL | reject_stage1 (B1,DD) |
| `IC11` | `IC11/B` | -6.3234 | -1.8059 / 0.365 | -28.0498 | 0.324 | FAIL | reject_stage1 (AT,B1) |
| `IC12` | `IC12/A` | +0.7078 | +1.4136 / 0.633 | -9.9089 | 1.008 | PASS | reject_stage2: AT_2017_01_to_end, E2 |
| `IC13` | `IC13/C` | -0.1639 | -0.0334 / 0.469 | -2.9070 | 1.000 | FAIL | reject_stage1 (AT,B1) |
| `IC14` | `IC14/C` | -0.6313 | +0.2373 / 0.544 | -6.6816 | 1.021 | FAIL | reject_stage1 (AT) |
| `IC15` | `IC15/C` | -1.0278 | -0.8191 / 0.327 | -0.0000 | 1.162 | FAIL | reject_stage1 (AT,B1) |
| `IC16` | `IC16/A` | -0.3216 | -1.5482 / 0.397 | -8.2079 | 1.198 | FAIL | reject_stage1 (AT,B1) |
| `IC17` | — | — | — | — | — | n/a | not_applicable (main window) |
| `IC18` | `IC18/B` | -0.4808 | -0.1754 / 0.458 | -4.7999 | 0.863 | FAIL | reject_stage1 (AT,B1) |
| `IC19` | `IC19/C` | -0.5696 | -0.4273 / 0.301 | -0.0000 | 1.117 | FAIL | reject_stage1 (AT,B1) |
| `IC20` | `IC20/B` | -0.0546 | -0.0000 / 0.453 | -0.0000 | 1.035 | FAIL | reject_stage1 (AT,B1) |
| `IC21` | `IC21/C` | -0.7414 | -0.2001 / 0.450 | -8.0441 | 0.982 | FAIL | reject_stage1 (AT,B1) |
| `IC22` | `IC22/A` | -0.4714 | -0.2629 / 0.402 | -0.0000 | 1.165 | FAIL | reject_stage1 (AT,B1) |
| `IC23` | `IC23/B` | -2.9542 | -1.8389 / 0.008 | -0.6669 | 1.995 | FAIL | reject_stage1 (AT,B1) |
| `IC24` | `IC24/B` | -0.0680 | -0.0333 / 0.388 | +0.0140 | 1.011 | FAIL | reject_stage1 (AT,B1) |
| `IC25` | `IC25/B` | -0.2473 | -0.1450 / 0.417 | -0.7074 | 1.153 | FAIL | reject_stage1 (AT,B1) |
| `IC26` | `IC26/C` | +0.0000 | +0.0000 / 0.510 | -0.0000 | 1.000 | PASS | reject_stage2: G1_base, G5_base, G6_calibrated, AT_2010_01_to_end, AT_2017_01_to_end, B1_recent, B2_flat, E1, FRESH |
| `IC27` | `IC27/C` | +0.0057 | -0.0000 / 0.489 | -0.0000 | 1.018 | FAIL | reject_stage1 (B1) |
| `IC28` | `IC28/C` | -0.0750 | -0.2002 / 0.394 | +1.6111 | 1.009 | FAIL | reject_stage1 (AT,B1) |
| `IC29` | `IC29/C` | +0.0956 | -0.2259 / 0.355 | -0.0000 | 1.009 | FAIL | reject_stage1 (B1) |
| `IC30` | `IC30/B` | -0.0581 | -0.0000 / 0.483 | -0.0000 | 1.003 | FAIL | reject_stage1 (AT,B1) |
| `IC47` | `IC47/C` | +0.1195 | +0.0963 / 0.600 | -0.1758 | 0.996 | PASS | reject_stage2: G7, AT_2017_01_to_end |
| `IC48` | `IC48/C` | -0.2551 | -0.6275 / 0.259 | +6.2894 | 1.162 | FAIL | reject_stage1 (AT,B1,DD) |
| `IC50` | `IC50/C` | +0.0979 | -0.0071 / 0.485 | +0.9967 | 1.015 | FAIL | reject_stage1 (B1) |
| `IC51` | `IC51/B` | -0.9848 | -0.5545 / 0.299 | -1.0486 | 0.955 | FAIL | reject_stage1 (AT,B1) |
| `IC59` | — | — | — | — | — | n/a | needs_kernel (asymmetric resize band) |
| `IC60` | `IC60/A` | +0.1745 | +0.0355 / 0.573 | +1.0498 | 0.991 | PASS | reject_stage2: G2_base, G2_calibrated, G4, AT_2017_01_to_end, E2 |

Stage-1 gates: AT = after-tax $100k 2000-window Δ > 0, B1 = B1-recent median Δ > 0, DD = pre-tax
max DD ≤ K + 2 pp (base *and* calibrated), TO = turnover ≤ 2 × K (base *and* calibrated). All 108
per-arm records (including B2, the OOS/ETF/rolling/fresh cells and the gate detail) are in
`results/bank_v2/`; `SCOREBOARD.md` has one line per arm.

## The six Stage-1 survivors (all rejected at Stage 2)

| arm | AT 2017 Δ | AT 2010 Δ | AT2000 Δ | B1 med Δ / share | B2 med Δ / share | E1 (ETFs) | E2 (starts) | OOS med Δ | G8 | FRESH | failed gates |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `IC12/A` | -1.35 | +0.19 | +0.71 | +1.414 / 0.633 | +2.139 / 0.709 | +2.215 / 0.810 | -0.031 / 0.381 | +1.680 | +1.689 | PASS | AT_2017, E2 |
| `IC12/B` | -0.35 | +0.49 | +0.70 | +0.852 / 0.647 | +1.242 / 0.734 | +1.339 / 0.857 | +0.360 / 0.762 | +0.973 | +1.064 | PASS | AT_2017 |
| `IC26/C` | +0.0000 | +0.0000 | +0.0000 | +0.000 / 0.510 | +0.000 / 0.513 | -0.000 | +0.000 / 0.619 | +0.000 | +0.000 | FAIL | G1_base, G5_base, G6_calibrated, AT_2010, AT_2017, B1, B2, E1, FRESH |
| `IC47/C` | -0.03 | +0.03 | +0.12 | +0.096 / 0.600 | +0.108 / 0.606 | +0.044 / 0.619 | +0.062 / 0.762 | -0.034 | +0.069 | PASS | G7, AT_2017 |
| `IC60/A` | -0.05 | +0.38 | +0.17 | +0.036 / 0.573 | +0.051 / 0.604 | +0.094 / 0.619 | +0.246 / 0.571 | +0.091 | +0.022 | PASS | G2_base, G2_calibrated, G4, AT_2017, E2 |
| `IC60/C` | -0.27 | +0.28 | +0.11 | +0.072 / 0.601 | +0.080 / 0.616 | +0.145 / 0.619 | +0.192 / 0.571 | +0.107 | +0.037 | PASS | G2_base, G2_calibrated, G4, AT_2017, E2 |
| `k_control/K` | 0 (control) | 0 | 0 | 0 / – | 0 / – | 0 | 0 | 0 | 0 | FAIL | — (control) |

**Every survivor fails after-tax 2017.** `IC12/A` and `IC12/B` fail *only* that window (plus the
rolling-start gate E2 for A); both are excluded from a declaration by the after-tax rule itself
(Δ > 0 in **all three** windows) and then by the deflated Sharpe. Both IC12 arms are not
applicable on OOS window M2 (the NFCI state's 156 lagged weeks have no value there), so their G7
ran on 5 windows (M1, M3, M4, M5, GSPC_1957_1999; all within the required band) and G8 on
{M1, M3, M4, M5} — still full passes. `IC26/C` is a **literal no-op equal to K** (its targets are
the production series; Δ = 0.0000 in every cell and it fails the strict gates on rounding noise),
which is the expected control-like behaviour of that arm, not a new result.

## Deflation (over the B1-recent Δ vectors of all 102 arms that produced one)

`N = 215`; 2,000 second-level resamples; seed 20261016, α = 0.05
(`results/DEFLATION.json`).

* `white_rc`: `t_max = 13.259` for the best arm `IC12/B`, `p = 0.0000`.
* `rw_stepdown` rejects 6 arms at 5 %: `IC12/B` (p_adj 0.0000), `IC12/A` (0.0000), `IC25/A`
  (0.0000), `IC47/C` (0.0000), `IC06/A` (0.0095), `IC14/C` (0.0145). Next: `IC60/C` 0.3350,
  `IC14/A` 0.8565.
* `deflated_sharpe`: `sr0 = 0.984` (N = 215, var(SR) = 0.156), best observed advantage-Sharpe
  0.419 (`IC12/B`) → `dsr = 0.000`, `pass_5pct = false` for **every** arm.
* `stage2_pass = []`, `declared = []`.

A candidate needs Stage 2 **and** `rw_stepdown` ≤ 0.05 **and** `dsr` ≥ 0.95; only the second ever
holds here, so the batch declares nothing.

## NotApplicable, NEEDS_KERNEL, holdout_insufficient

* **`not_applicable` — 3 arms, one idea: `IC17/A`, `IC17/B`, `IC17/C`.** Reason (recorded on the
  main window, stage 1): *"the registered BAMLH0A0HYM2 starts 2023-10-16, not 1996-12-31: FRED
  serves only the trailing three years of ICE BofA series, so the bank's full-history precondition
  fails."* IC17 is n/a everywhere — no HY OAS history exists for it. Its ledger line counts
  towards N, as pre-registered.
* **`needs_kernel` — 3 arms, one idea: `IC59/A`, `IC59/B`, `IC59/C`.** Reason: *"an asymmetric
  resize band (up-resize threshold 0.0 / 0.25 pp, A1u's gate on downsizes) changes
  `fe_sim.C0_BAND`, the compiled symmetric 0.5 pp threshold of `sim_one`'s trade test;
  `fe_sim.RULE` / `register_rule` only re-combine the band/usd_rule/defer booleans and cannot set
  a per-direction width — needs a kernel option in an engine copy plus an off/on parity cell."*
  Registered, never simulated, counted in N. A per-direction band is a separate kernel study.
* **`holdout_insufficient` — none.** No arm came through Stage 1 with fewer than 3 applicable OOS
  windows or fewer than 15 applicable instruments, so nothing needs manual confirmation on that
  count. (Window-level N/A inside Stage 2 did occur: `IC12/A`|`IC12/B` on M2, see above; the OOS
  windows M1–M5 also drop some arms' extra series — all visible in each record's `G7_detail`.)

## Near misses worth a dedicated confirmation study

Only arms that passed Stage 1 qualify; of the six, two are worth confirming and three are noise:

* **`IC12/A`, `IC12/B`** — the only arms with a material after-tax edge (+0.71 / +0.70 pp on the
  main window) *and* a lower drawdown (-9.9 / -7.0 pp), positive B1 **and** B2 medians with
  63–73 % share > 0, all 21 ETFs positive (median +2.2 / +1.3 pp, share 0.81 / 0.86), all 21
  rolling starts (E2 pass for B), a positive pooled OOS bootstrap and all four fresh-seed cells.
  They fail exactly one cell — the after-tax **2017** window (−1.35 / −0.35 pp) — and the
  deflated Sharpe. Both are the same idea: a veto that takes the whole position to cash (A) or
  halves it (B) while FRED `NFCI` is above its trailing 156-week median *and* rising, on a causal
  one-week-lagged weekly state. A dedicated confirmation study would have to establish whether
  that single negative window survives a different draw (for `IC12/B` it is the only negative
  cell outside the deflation; `IC12/A` also misses E2) or is the arm's real 2017–2026 behaviour.
* `IC47/C`, `IC60/A`, `IC60/C` (Stage-1 Δ +0.12 / +0.17 / +0.11 pp) also fail 2017 and one to two
  further gates (G7; G2/G4; E2) with a Stage-1 edge an order of magnitude smaller than IC12's —
  no dedicated study is warranted.

## Method, checks, determinism

* Step 1 (before the batch, all green): `bash research/batch_screen/tests/run.sh -q` → **110
  passed** (32 s); `python -m research.fast_engine.parity --quick` → **PASS** (11 s, max |diff|
  5e-07; `PARITY.json` restored to HEAD); `run --harness` → **PASS 73/73** (35 s; `HARNESS.json`,
  whose only diff was wall-clock, restored to HEAD); `run --verify-registry` → **PASS** (140
  lines, 107 arms, N = 107 before this batch); smoke over all 36 ideas → **105 OK, IC59 ×3
  NEEDS_KERNEL** (108 arms).
* Batch: `PYTHONPATH=. $R -m research.batch_screen.run --batch bank_v2 --ideas IC01…IC60
  --workers 6`, then `run --deflate --batch bank_v2`. Every arm went through `fe_sim`/`fe_sim2`/
  `fe_tax`/`fe_boot` — no new per-day Python loop, no engine edit.
* **Determinism.** Re-running Stage 1 for 3 ideas × 3 arms (`IC12`, `IC47`, `IC60`) into a scratch
  batch reproduced their committed Stage-1 records **byte-identically** (9/9 arms: `stage1`,
  `stage1_gates`, `boot_params`, `hashes`, `spec` all equal). The runner always registers, so the
  scratch ledger lines were reverted with `git checkout -- research/batch_screen/REGISTRY.jsonl`
  and `results/scratch_bank_v2/` was deleted — the committed ledger is byte-identical to what the
  batch run wrote (sha256 f40850f9…). The `--deflate` pass itself re-ran byte-identically
  (`DEFLATION.json` sha256 d724e57f… unchanged). A full second batch run was **not** made: it
  would append 108 duplicate ledger lines, and the pre-registered pipeline has no silent mode.
* Wall clock: batch 658.3 s on 6 processes; test suite 32 s; harness 35 s; parity 11 s; deflate
  2.8 s; scratch stage-1 re-run 24 s.

## Caveats

1. The `Δ` in every cell is `arm − K` on the same resampled paths; the stage-1 decision is the
   after-tax 2000-window Δ and the B1 median, and the deflation is applied to that same B1 Δ
   vector (the framework's pre-registered choice, `README.md` §4), not to a daily-return Sharpe.
2. `deflated_sharpe` uses N = 215, i.e. every arm ever registered in this campaign's ledger — the
   correct (conservative) count, and the reason a 0.42 advantage-Sharpe cannot pass.
3. 20-session blocks cannot capture serial dependence beyond 20 sessions; the p-values are
   optimistic under long memory.
4. The arms' `NotApplicable` windows are declared by the plugin, checked by the framework
   (`leg_b_usable`/window maps) and visible in each record; IC12's M2 absence is a data fact
   (NFCI), not a choice made after seeing results.
5. Ideas and their arms were fixed by committed plugin code *before* any result existed; the
   plugin modules (`research/ideas/IC*.py`) and the framework are unchanged by this study. Batch 1
   (`bank_v1`) had already screened a different idea bank; its arms stay in N.
