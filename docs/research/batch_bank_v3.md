# Batch screen of the idea bank, batch 3 (batch_bank_v3, 2026-10-16)

> Evidence: framework `research/batch_screen/` — its pre-registered pipeline is `README.md` plus
> `PREREGISTRATION.md` §§1–6.4 (the framework doc is `batch_screen_v1.md`); the 27 blind-written
> plugins `research/ideas/IC*.py` (committed by the plugin workers before the screen — the runner
> refuses uncommitted code); the option package `research/fast_engine_opts/` (`kernel_opts_v1.md`,
> `README.md`); the ledger `research/batch_screen/REGISTRY.jsonl`; per-arm records
> `research/batch_screen/results/bank_v3/` (+ one `_control_*.json` per window tag, the batch's own
> control re-simulated on each reduced window); the deflation
> `research/batch_screen/results/DEFLATION.json`; the runner's stdout
> (`…/scratchpad/bank_v3_run2.log`). Ideas: `docs/research/idea_bank_v2.md` /
> `idea_bank_v2.json`. The per-study discipline of the common protocol is replaced here by the
> framework's pre-registered pipeline: **no plugin, gate or framework file was touched before or
> after the run**. The two changes made *before* the batch ran are the framework's own dated
> amendments §6.3 and §6.4 (§Amendments below); the IC56 NaN of §Known issue is diagnosed here and
deliberately **not** fixed. Batch 2 (`bank_v2`, 108 arms) is `batch_bank_v2.md`; batch 1
(`bank_v1`), `anchors_v1`, `two_leg_v1` and `ib_fix_v1` are the campaign's earlier batches, all
still inside the same ledger and therefore inside N.

**Verdict: no candidate.** 27 ideas → 80 registered arms (66 simulated, 14 refused); **11 arms
passed Stage 1** — and for six of those the "pass" is nothing: their Δ is 6 × 10⁻⁶ pp, i.e. a
rounding-level equality with K; **all 11 failed Stage 2** (7 `reject_stage2`, 4
`holdout_insufficient`); the deflation rejects **every one of the 53 arms that produced a Δ
vector** (`sr0 = 4.338`, best advantage-Sharpe 4.052 → `dsr = 0.000`) → `declared = []`. Nothing
here is a candidate for an owner decision, and nothing is queued for a weaponisation study except
the one arm that never got a holdout (§`holdout_insufficient`).

| stage | arms |
|---|---|
| registered in this batch (`REGISTRY.jsonl`, `batch = bank_v3`) | 80 (27 ideas) |
| simulated | 66 |
| refused as needing a new kernel | 12 (`IC34/C`, `IC36/A–C`, `IC41/A–C`, `IC45/C`, `IC46/C`, `IC59/A–C`) |
| refused as needing missing data | 2 (`IC54/A`, `IC54/B`) |
| Stage 1 (main window + its block-20 bootstrap): pass | 11 |
| Stage 2 (holdout: 6 OOS windows, 21 ETFs, after-tax 2010/2017, fresh-seed bootstraps, 21 rolling January starts, pooled OOS bootstrap): pass | **0** |
| Stage-2 rejection mode | `reject_stage2` 7, `holdout_insufficient` 4 |
| declared after the multiple-testing deflation | **0** |
| pre-registered in §6.3 but **absent from the run** | 3 (`IC59_K/A_K`, `/B_K`, `/C_K`, §`NEEDS_KERNEL`) |

N (the deflation's multiple-testing count) = **300** distinct `(idea, arm, spec_hash)` over the
whole campaign ledger (356 lines; `bank_v3` contributes 80 of the 300 distinct triples).
`white_rc`'s `n_arms` is 53 — the arms with a finite B1 Δ vector (§Deflation).
Wall clock of the batch: **399.2 s** on 6 workers (`results/bank_v3/timing.json`,
`total_wall_s = 399.153353`; per arm 15.0–65.1 s). The control `k_control/K` (= production K)
reproduces K's published anchors through the framework harness — 116/116 checks, engine parity
`--quick` PASS, control anchors to ≤ 5e-05 pp: pre-tax m = 2 CAGR 15.7649 / 15.1785 % base /
calibrated, max DD 46.6698 / 46.7948 %, after tax at $100k 10.7533 / 15.2354 / 23.9720 % on the
2000 / 2010 / 2017 windows (`research/batch_screen/HARNESS.json`).

## Deflation (`results/DEFLATION.json`)

N = 300; 2,000 second-level resamples; seed 20261016, α = 0.05; Δ vectors = each arm's B1-recent
after-tax progressive Δ in pp (the pre-registered deflation input, `README.md` §4).

* **53 of the 66 simulated arms have a finite Δ vector**; 13 are excluded as non-finite
  (`excluded_non_finite`): `IC56/A`, `IC56/B`, `IC56/C` (988/1000 each — §Known issue), `IC40/A`,
  `IC40/B`, `IC40/C` (981/981/992 — the same defect, §Known issue), `IC34/A` 104, `IC34/B` 94,
  `IC35/C` 10, `IC35/A` 8, `IC31/C` 2, `IC31/B` 1, `IB26_RW/C_RW` 1.
* `white_rc`: `t_max = 128.131` for the best arm `IC45/A`, `p = 0.0000`.
* `rw_stepdown` alone rejects **24 of the 53** arms at 5 % (`p_adj = 0.0000` for the top twelve);
  the un-rejected ones are the trivial-Δ arms and the negative ones.
* `deflated_sharpe`: `var(SR) = 2.7736`, `sr0 = 4.3382` (N = 300); the best observed
  advantage-Sharpe is **4.052** (`IC45/A`, mean Δ 2,197.8 pp, sd 542.4 — the vector's fat tail, not
  an edge: its median Δ is +21.4 pp) → `dsr = 0.000647` < 0.95; for every other arm `dsr = 0.000`.
  `pass_5pct = false` for all 53.
* `stage2_pass = []`, `declared = []`, `deflation_rejected = []`. A candidate would need Stage 2
  **and** `rw_stepdown` ≤ 0.05 **and** `dsr` ≥ 0.95; the first two can hold (24 / 11 arms), the
  third never does.

## Per-idea table (best arm by Stage-1 after-tax 2000-window Δ; tags = the Stage-1 gates it fails)

Stage-1 gates: `AT` = after-tax $100k 2000-window Δ > 0, `B1` = B1-recent median Δ > 0 (a NaN
counts as a failure), `DD` = pre-tax max DD ≤ K + 2 pp in *both* financing models, `TO` = turnover
≤ 2 × K in both.

| idea | best arm | AT2000 Δ | B1 med Δ | Stage 1 | Stage 2 / verdict |
|---|---|---|---|---|---|
| `IB26_RW` | `IB26_RW/C_RW` | +0.1876 | nan | reject_stage1 (B1) | — |
| `IC31` | `IC31/B` | +0.2809 | nan | reject_stage1 (B1,DD) | — |
| `IC32` | `IC32/A` | +0.4941 | +3.6801 | reject_stage1 (DD) | — |
| `IC33` | `IC33/B` | -0.3693 | +0.4844 | reject_stage1 (AT) | — |
| `IC34` | `IC34/B` | -0.6369 | nan | reject_stage1 (AT,B1,DD,TO) | — |
| `IC35` | `IC35/A` | +1.8302 | nan | reject_stage1 (B1,DD) | — |
| `IC36` | — | — | — | n/a | needs_kernel (all three arms) |
| `IC37` | `IC37/C` | +0.5879 | +12.2296 | **PASS** | holdout_insufficient (G7, G8, E1) |
| `IC38` | `IC38/B` | -0.9860 | +1.3638 | reject_stage1 (AT,DD) | — |
| `IC39` | `IC39/B` | -2.6895 | -2.2343 | reject_stage1 (AT,B1) | — |
| `IC40` | `IC40/A` | -1.3388 | nan | reject_stage1 (AT,B1) | — (NaN, §Known issue) |
| `IC41` | — | — | — | n/a | needs_kernel (all three arms) |
| `IC42` | `IC42/B` | -1.3229 | -1.9785 | reject_stage1 (AT,B1,TO) | — |
| `IC43` | `IC43/C` | -1.4395 | -2.0878 | reject_stage1 (AT,B1,TO) | — |
| `IC44` | `IC44/C` | -1.3132 | -1.9588 | reject_stage1 (AT,B1,TO) | — |
| `IC45` | `IC45/A` | +0.0070 | +21.4065 | **PASS** | holdout_insufficient (10 gates) |
| `IC46` | `IC46/A` | -0.6593 | +4.4507 | reject_stage1 (AT) | — |
| `IC49` | `IC49/C` | -0.5561 | +0.1087 | reject_stage1 (AT,DD) | — |
| `IC52` | `IC52/B` | +0.0122 | -0.0000 | reject_stage1 (B1) | (`IC52/A`, `IC52/C` passed on a 6e-06 Δ) |
| `IC53` | `IC53/C` | -0.2594 | +0.6639 | reject_stage1 (AT) | — |
| `IC54` | `IC54/C` | -0.2857 | +0.0936 | reject_stage1 (AT) | (A/B refused: no data) |
| `IC55` | `IC55/A` | -0.0266 | -0.0202 | reject_stage1 (AT,B1) | — |
| `IC56` | `IC56/B` | -0.1400 | nan | reject_stage1 (AT,B1) | — (NaN, §Known issue) |
| `IC57` | `IC57/A` | +0.0068 | +0.0025 | **PASS** | reject_stage2 (G8, AT2010, AT2017, B1, E1, E2, FRESH) |
| `IC58` | `IC58/A` | +0.0000 | +0.0000 | **PASS** (trivial) | reject_stage2 (9 gates) |
| `IC59` | — | — | — | n/a | needs_kernel (3 pinned arms); `IC59_K` ×3 never ran |
| `ib25_RW` | `ib25_RW/A_RW` | -0.2669 | -0.1384 | reject_stage1 (AT,B1) | — |

Nothing in the batch came in above **+1.83 pp** after tax on the main window, and the two arms
above +1 pp (`IC35/A` +1.83, `IC35/B` +1.53) buy it with max-DD increases of +39.9 and +21.8 pp
(base) — a different strategy, not K plus a tweak. The window each arm is measured on is its own
declared window (`start = auto` over its inputs), with K re-simulated on that same window as the
control: `w20020731` (shy), `w20030103` (dgs10/t10yie/ief), `w20031208` (t10yie/tip), `w20041119`
(gld), `w20060523` (gdx), `w20070412` (hyg), else the default 2000-03-09 window.
`RESULTS.json` carries all 66 arm rows with their full `b1_delta_pp` vectors; `SCOREBOARD.md` has
one line per arm.

## The 11 Stage-1 survivors (all rejected at Stage 2)

| arm | AT 2000 / 2010 / 2017 Δ | B1 med / share | B2 med / share | E1 (ETFs) med / n | E2 (21 starts) med / n / share ≥ 0 | OOS med Δ | fresh | failed gates |
|---|---|---|---|---|---|---|---|---|
| `IC37/A` | +0.0158 / −0.0853 / +0.3180 | +11.352 / 0.857 | +13.582 / 0.883 | −0.110 / 2 / 0.50 | +0.011 / 18 / 0.556 | n/a | PASS | G2_base, G2_calibrated, G7, G8, AT_2010, E1, E2, HOLDOUT_INSUFFICIENT |
| `IC37/B` | +0.0599 / +0.1259 / +0.4797 | +19.890 / 0.946 | +22.539 / 0.954 | +0.128 / 2 / 0.50 | +0.127 / 18 / 0.778 | n/a | PASS | G7, G8, E1, HOLDOUT_INSUFFICIENT |
| `IC37/C` | **+0.5879** / +0.1764 / +0.6431 | +12.230 / 0.878 | +14.511 / 0.907 | +0.147 / 2 / 1.00 | +0.433 / 18 / 0.833 | n/a | PASS | G7, G8, E1, HOLDOUT_INSUFFICIENT |
| `IC45/A` | +0.0070 / −0.3443 / −0.5342 | +21.407 / 1.000 | +23.515 / 1.000 | +0.000 / 0 / 0.00 | −0.350 / 17 / 0.059 | n/a | PASS | G3_base, G3_calibrated, G4, G7, G8, AT_2010, AT_2017, E1, E2, HOLDOUT_INSUFFICIENT |
| `IC52/A` | +0.0000 / −0.0000 / −0.0000 | +0.000 / 0.505 | +0.000 / 0.509 | −0.000 / 21 / 0.238 | +0.000 / 21 / 0.619 | +0.271 | FAIL | G1_base, G5_base, G6_calibrated, AT_2010, AT_2017, B1, B2, E1, FRESH |
| `IC52/C` | +0.0000 / −0.0000 / −0.0000 | +0.000 / 0.505 | +0.000 / 0.509 | −0.000 / 21 / 0.238 | +0.000 / 21 / 0.619 | +0.271 | FAIL | G1_base, G5_base, G6_calibrated, AT_2010, AT_2017, B1, B2, E1, FRESH |
| `IC57/A` | +0.0068 / −0.0045 / −0.0083 | +0.003 / 0.548 | +0.004 / 0.556 | −0.006 / 21 / 0.381 | −0.005 / 21 / 0.143 | +0.006 | FAIL | G8, AT_2010, AT_2017, B1, E1, E2, FRESH |
| `IC57/B` | +0.0000 / −0.0000 / −0.0000 | +0.000 / 0.510 | +0.000 / 0.513 | −0.000 / 21 / 0.190 | +0.000 / 21 / 0.619 | +0.000 | FAIL | G1_base, G5_base, G6_calibrated, AT_2010, AT_2017, B1, B2, E1, FRESH |
| `IC58/A` | +0.0000 / −0.0000 / −0.0000 | +0.000 / 0.510 | +0.000 / 0.513 | −0.000 / 21 / 0.190 | +0.000 / 21 / 0.619 | +0.000 | FAIL | G1_base, G5_base, G6_calibrated, AT_2010, AT_2017, B1, B2, E1, FRESH |
| `IC58/B` | +0.0000 / −0.0000 / −0.0000 | +0.000 / 0.510 | +0.000 / 0.513 | −0.000 / 21 / 0.190 | +0.000 / 21 / 0.619 | +0.000 | FAIL | G1_base, G5_base, G6_calibrated, AT_2010, AT_2017, B1, B2, E1, FRESH |
| `IC58/C` | +0.0000 / −0.0000 / −0.0000 | +0.000 / 0.510 | +0.000 / 0.513 | −0.000 / 21 / 0.190 | +0.000 / 21 / 0.619 | +0.000 | FAIL | G1_base, G5_base, G6_calibrated, AT_2010, AT_2017, B1, B2, E1, FRESH |
| `k_control/K` | 0 (control) | 0 | 0 | 0 | 0 | 0 | — | — |

Only four of the eleven are arms that are not numerically K — `IC37/A` (+0.016 pp), `IC37/B`
(+0.060 pp), `IC37/C` (+0.588 pp at $100k on the main window, +0.176 / +0.643 on 2010 / 2017, B1
+12.2 pp at 88 % share, DD +0.8 pp, turnover 1.81 × K, 18 rolling starts with a +0.43 pp median at
83 % share) and, an order of magnitude below, `IC57/A` (+0.0068 pp) — and **every** arm in the
table fails the after-tax 2010 and/or 2017 window (except `IC37` arms B/C, whose holdout is not
evaluable at all, §`holdout_insufficient`).
`IC45/A`'s impressive B1 (+21.4 pp, 100 % share) is a reduced-window artefact: on the same arm the
2010 and 2017 windows are **negative** (−0.34 / −0.53 pp), its rolling starts are 94 % negative
and it fails 10 gates. The `IC52`/`IC57`/`IC58` rows are the trivial-Δ family (§Identical by
construction): a 6e-06 pp "pass" that Stage 2 then kills on the strict `Δ > 0` gates.

## Identical by construction (and why a "PASS" there means nothing)

Four arm pairs are the same simulation by construction. For the first three the records agree to the
last digit (same `hashes.spec`, same `stage1`, same `gates`, `np.array_equal(..., equal_nan=True)`
on the `b1_delta_pp` vectors); `IC57/B` carries K's own spec and its Δ vector is a rounding residue
of ≤ 5e-05 pp:

| pair | spec hash | why |
|---|---|---|
| `IC56/C` ≡ `IC56/A` | `9421bce8ad5a` | arm C's foreign-tax credit is report-only in the bank's own text: both register `opts={"wht": 0.20}` (the plugin says so in `IC56.py`) |
| `IC58/C` ≡ `IC58/A` | `1c89d7b46f3a` | only a same-session sale's proceeds can fund a same-session buy, so `settle_floor = 0` and arm C's variant coincide single-leg |
| `IC40/A` ≡ `IC40/B` | `3e2b7f04506d` | both run `leg = "2x_spy"` with the vote on the QQQ path; arm B's "also for the early leg" is the same instrument |
| `IC57/B` ≡ **K** | `0b64700f53f2` = the control's spec | arm B asks for what the engine already does (decide on the previous close, fill at the early close's open); its spec is literally `{"cash_yield": "none", "leg": "2x", "rule": "K"}` |

Because Stage 1's pass rule is `AT2000 Δ > 0 and B1 median Δ > 0`, an arm equal to K passes it
**trivially** whenever the residual rounding of the two runs lands on the positive side. Measured
on this batch: `IC57/B` `AT2000 = 6e-06 pp`, B1 median `1e-06 pp`, `max|Δ| = 5e-05 pp` over the
1000 paths (989 non-zero) — that is the entire "edge". `IC58/A`, `IC58/B`, `IC58/C` are the same
number (their `settle_floor` option is inert on the production target series, `kernel_opts_v1.md`
§"Semantics"), and `IC52/A`/`IC52/C` join them on the main window because a $250 notional floor is
inert at $100k (their ETF cell does move: `E1` share 0.238 against K's 0.190, and their pooled OOS
median is +0.271 pp — that is the arm's only real content, and it fails E1 and FRESH anyway). The
six `6e-06 pp` "PASS" rows in the tables above are therefore not evidence of anything.

## NEEDS_KERNEL / NEEDS_DATA (14 registered arms, plus 3 that never ran)

Declared by the plugins and recorded in the batch's ledger; none was simulated, and all count in N.

* **`NEEDS_KERNEL` (12): `IC34/C`, `IC36/A`, `IC36/B`, `IC36/C`, `IC41/A`, `IC41/B`, `IC41/C`,
  `IC45/C`, `IC46/C`, `IC59/A`, `IC59/B`, `IC59/C`.**
  * `IC34/C` — holds XLU *and* XLP *and* leg A's QLD\* in one account at once (three instruments);
    the two-leg kernel holds the arm's own leg plus exactly one other book. A/B screened normally.
  * `IC36/A–C` — a three- (arm B: four-) instrument inverse-volatility blend in one account,
    rescaled monthly; the two-leg kernel holds at most two books.
  * `IC41/A–C` — leg B must be a 2x S&P-500 book (synthetic daily-reset 2x from SPY, or the real
    SSO): the panel builds the 2x leg only from the window's own underlying, and an extra-data leg
    is held at gearing 1 with no financing or expense plumbing — a second leg *family* (data triple
    + levered recurrence in the kernel), i.e. the IC40 boundary, not a plugin.
  * `IC45/C` / `IC46/C` — the frozen sleeve formula on top of production holds more than the 0.98
    cap (44.5 % of sessions, mean excess 2.6 pp of NAV for IC45; 58.6 % and 7.4 pp for IC46); the
    kernel funds the excess with negative cash and no interest, i.e. a cash loan (the IC39 boundary).
  * `IC59/A–C` — an asymmetric resize band changes `fe_sim.C0_BAND`; `register_rule` only
    re-combines `band`/`usd_rule`/`defer` and cannot set a per-direction width. The mechanism now
    *is* expressible (`band_up`/`band_down` in `research/fast_engine_opts`), but the three original
    arms are pinned in the ledger (`resolve_arm` applies IC59's module-level `NEEDS_KERNEL` to every
    arm of the module, and editing them would re-hash their specs and trip `verify_ledger`), so
    their `needs_kernel` lines stand.
* **`NEEDS_DATA` (2): `IC54/A`, `IC54/B`.** The arm needs the fill session's first-30-minute price
  (close for A, VWAP for B) on every decision session of a 2000–2026 window: the Yahoo chart API
  serves `interval=30m` for roughly the last 730 days only and the Alpaca SIP bars API has no
  offline access. The *mechanism* is implemented (`fill_mode = 2` + a `fill` array) and the bridge
  now refuses it explicitly (it has no call site carrying a fill series), so this is a data gap, not
  a kernel one. (The ledger's `status` field says `needs_kernel` for these two as well — the
  framework has one refusal column; the reason text is the data gap, and `kernel_opts_v1.md`
  classifies them as `NEEDS_DATA`.)
* **Declared in §6.3 but absent from the run (3): `IC59_K/A_K`, `IC59_K/B_K`, `IC59_K/C_K`.**
  §6.3 fixes the batch's composition as *"…and `IC59_K` — the runnable twins of `IC59`'s three arms
  under the `band_up` kernel option"*; `research/ideas/IC59_K.py` is committed and its three
  `up_band_pp` / `down_band` / `gain_fraction` settings are the bank's, but **no `IC59_K` line
  exists in `REGISTRY.jsonl` and no record in `results/bank_v3/`**: those three arms were never
  registered, never simulated and never counted in N = 300. This is the one deviation from the
  pre-registered batch composition that this report can see (it also explains why the batch brief
  expected 83 arms where the ledger holds 80). No other arm's number is affected. A follow-up would
  have to run those three arms as their own pre-registered batch/trial set — they are the *only*
  untested thing left of the IC59 idea, and they change the execution band rather than the target,
  which is the class of change K itself came from.

## `holdout_insufficient` (4 arms) and what a confirmation study would need

`IC37/A`, `IC37/B`, `IC37/C` and `IC45/A` came through Stage 1 but their holdout is not evaluable —
**not a pass**. The framework's rule: ≥ 3 applicable OOS windows of the six, ≥ 15 applicable
instruments of the 21. Measured: `IC37/A–C` have **0 applicable OOS windows and 2 applicable
instruments** (`EEM`, `EFA`), `IC45/A` has **0 and 0**. The reason is always leg B's data start: the
campaign's six OOS windows all begin before 2002-07-30 (`ief`) / 2004-11-18 (`gld`), and each ETF's
window begins at that instrument's own history start, which for 19 of the 21 predates it too. So
`G7` (0 of 6 windows → "not evaluable"), `G8` and `E1` (2 instruments, or none) fail by construction,
and the arms' `OOS med Δ` is blank.

**`IC37/C` is the one arm worth a dedicated confirmation study** (+0.588 pp after tax at $100k on its
2003-01-03 → 2026-09-29 window; +0.176 / +0.643 pp on the 2010 / 2017 windows; B1 +12.23 pp at
87.8 % share, B2 +14.51 pp at 90.7 %; rolling starts median +0.433 pp with 83 % ≥ 0 of 18 applicable;
max DD +0.81 pp; turnover 1.81 × K). What it is: *"while off, hold the residual (0.98 − w) in IEF
under the condition that the 10-year real yield `DGS10 − T10YIE` > +0.25 %"*, i.e. an off-state
Treasury sleeve keyed on the real yield, with production K on both books.

* **Its inputs, and whether any is a revised macro series** (the question `ic12_confirm_v1.md`
  raises): `DGS10` (FRED CSV, 1962-01-02 →) is the 10-year constant-maturity Treasury yield,
  `T10YIE` (FRED CSV, **2003-01-02** →) the 10-year breakeven, and `IEF` (Yahoo adjusted close,
  **2002-07-30** →) the leg-B instrument; the window start is `auto` = 2003-01-03, the breakeven's
  first publication being the binding input. **None of the three is a revised statistical series**:
  DGS10 and T10YIE are market-derived yields published the same day and never restated, and IEF is
  an ETF print — the NFCI vintage trap that turned `IC12`'s +0.71 pp into −0.56 pp on a point-in-time
  signal does **not** apply here. What does apply is the same *structural* weakness the IC12 lesson
  teaches: the whole state (and its 0.25 pp threshold) rests on one macro series, over a short
  common history.
* **What the study would need** (the framework has no machinery for it, so it must be pre-registered
  from scratch): (a) **new OOS windows**, because the six campaign windows are all unusable — the
  IC37 rule is `NotApplicable` before 2003-01-03 by the framework's missing-input convention. Any
  set of ≥ 3 windows starting at/after 2003-01-03 (e.g. 2003 → 2009/2016/2026 splits, or post-2013
  sub-periods) qualifies as long as it is fixed before simulation. (b) **an instrument set whose
  windows start after 2002-07-30**, i.e. re-based ETF windows (start every ETF at 2003-01-03 and
  discard the pre-2003 history) rather than the campaign's per-instrument history start — only
  `EEM`/`EFA` qualify under the current convention, and 2 < 15. (c) a **pooled-OOS market set**
  reaching 2003, or the reduced gate pre-registered in advance (the protocol explicitly allows a
  pre-registered reduced gate when a window/OOS set is not usable).
* `IC37/A` (−0.09 pp on 2010) and `IC37/B` (weaker, and both fail `G2`/`E1`) do not warrant their own
  study; `IC45/A`'s edge is +0.007 pp with two negative after-tax windows and fails 10 gates — the
  reported B1 median is a reduced-window artefact (§survivors table), so nothing about it needs
  confirming either.
* For the record, the deflation also excludes all four: `IC37/C` `sr_obs = 1.087` / `sr0 = 4.338` →
  `dsr = 0.000`, and `rw_stepdown` alone would have "rejected" it at `p_adj = 0.0000` — the stepdown
  rejects an arm when its *Δ vector is positive*, which is not a confirmation, only the price of
  N = 300.

## Known issue: the IC56 bootstrap Δ vectors are NaN (988/1000), and IC40's (981–992/1000)

`IC56/A`, `IC56/B`, `IC56/C` have 988 of their 1000 B1 (and B2) Δ entries NaN, so their Stage-1 B1
median is NaN, their share > 0 is 0.007 and the deflation excludes them. `IC40/A`, `IC40/B` (981)
and `IC40/C` (992) show the same signature. Both ideas reach the engine through
`research/fast_engine_opts`' chunk path — `wht` (`IC56`) and `leg = "2x_spy"` (`IC40`) — and the
cause is a **defect in that path, not a property of withholding or of a second leg**.

* **Mechanism.** On a window, `repanel` rebuilds the leg from the panel's *raw* triple; inside a
  bootstrap chunk, `bridge.prepare_panel` calls `fe_opts_legs.chunk_raw_panel` → `chunk_triple`,
  which takes the drawn sessions' raw prices as `[close(idx₀−1), close(idxₜ)…]` and hands them to
  `fe_signals.synthetic_2x`. `synthetic_2x` reads the return between *consecutive columns*. A
  chunk, however, is stitched from ~20-session blocks with a jump between blocks
  (`fe_boot.draw_positions`, and a pool wrap), so at every jump column *t* and *t−1* are two
  unrelated sessions and the raw-price reconstruction silently spans the calendar gap — while the
  framework's own chunk panel (`batch_screen.boot.boot_panels`) uses each drawn session's *own*
  return (`prev = win.q[1][idx−1]`) and skips the gap. For a 2x leg one spanning return below −50 %
  makes the gross factor `1 + 2r − cost` ≤ 0, the cumprod path flips sign, the leg's price goes
  ≤ 0, the NAV with it, and the per-path CAGR `(nav[k]/nav[0])^(1/yrs) − 1` is NaN. The README's
  claim that the rebuilt triple is *"the same series up to floating-point rounding"* holds only on
  a contiguous draw.
* **Minimal repro** (committed, read-only, ~1.4 s):
  `research/batch_bank_v3/ic56_nan_repro.py`, run as
  `/Users/batuhankiskac/.venvs/pb-research/bin/python -m research.batch_bank_v3.ic56_nan_repro`.
  It prints, on the published draw (seed 20261011, block 20, 2 paths × 2776 sessions):
  118 / 126 jumps per path, of which 37 / 39 have a 2x gross factor ≤ 0; the rebuilt leg's first
  ≤ 0 column is *exactly* the first such column (52 on path 0, 11 on path 1) and 1541 / 1267 of
  the 2777 columns are ≤ 0, against the framework's own chunk leg which stays positive
  (min 0.205 / 0.583, 0 columns ≤ 0); the isolation case — the same rebuild on a jump-free
  contiguous 2776-session draw — has 0 jumps, 0 non-positive columns and agrees with the
  framework's leg to 3.6e-04 relative (2.3e-03 absolute); the `2x_spy` path (`IC40`, same repro
  part) does the same to the SPY option leg — **2,835 of 5,554 columns ≤ 0, min −60.9**, against 0
  for the framework's own leg; and the end-to-end case — `stages.
  stage1_vectors` for `IC56/A` with 16 paths — reproduces 16/16 NaN after-tax Δ in both modes and
  both pre-tax cells while the control's medians stay finite (−0.0041 / −0.0090).
* **No check catches it.** `chunk_raw_panel` is called only from production `prepare_panel` — no
  test or parity cell compares the rebuilt chunk leg with the framework's (`off_boot_parity` runs an
  all-off bundle; `check_withholding_panel` uses a small contiguous fixture; `check_options_through_
  the_framework` only asserts that an option *moves* the CAGRs and that a rerun is identical).
* **Could it have changed IC56's verdict? No.** All three IC56 arms already fail the after-tax
  2000-window gate (−0.1825 / −0.1400 / −0.1825 pp) on the *window* path, which does not touch
  `chunk_triple` (`repanel` on a contiguous window is correct); a repaired B1 cell could therefore
  only replace a NaN "fail" with a numeric one at best, and cannot make Stage 1 pass. Same for
  IC40: all three arms fail `AT` (−1.34 / −1.34 / −2.81 pp) whatever the bootstrap says. What the
  defect *does* invalidate is every bootstrap-based inference about the `wht` / `2x_spy` arms — none
  of which was ever used, since those arms died at Stage 1. The same defect would silently corrupt
  a *passing* arm with those options, which is the reason to record it.
* **Not fixed here** (out of this report's scope; framework code is off-limits to it): the fix is to
  rebuild the chunk leg from the drawn sessions' own returns as `boot_panels` does — a change to
  `research/fast_engine_opts` plus an off/on parity cell, i.e. a kernel-option change in an engine
  copy, not a plugin edit. The seven other arms with a few non-finite B1 entries (`IC34/A` 104,
  `IC34/B` 94, `IC35/C` 10, `IC35/A` 8, `IC31/C` 2, `IC31/B` 1, `IB26_RW/C_RW` 1) were **not**
  investigated: they are a much smaller effect on two-leg extra-series/leg paths, and every one of
  those arms was rejected at Stage 1 on `AT` and/or `DD` regardless.

## Amendments and bugs

The batch's plugins were written blind by three workers; the framework bugs they found (and the one
the orchestrator hit) are the framework's own, dated, and committed **before any `bank_v3` number
existed** (`PREREGISTRATION.md` §6, which is where the framework files its amendments — this batch's
verdict required no amendment after the fact, and no gate, arm, seed or sample size was changed at
any point). Two of them changed code this batch runs on:

* **Step 0 — bug B1, `59f13666` (§6.3): the `2x_spy` panel construction.** `api.panel` builds only
  the window's own `2x`/`1x` legs, and every non-bootstrap call site built its panel with the arm's
  own leg, so a `leg = "2x_spy"` arm raised `KeyError: '2x_spy'` while the panel was being built —
  `bridge.prepare_panel` (which adds the SPY leg) was never reached. `stages.panel_leg(arm)` now
  uses `bridge.base_leg(arm["leg"])` at all six `windows.*_panel` call sites and `smoke` prepares
  the panel per arm; the regression test is `tests/test_spy_leg.py`. Effect on the batch: IC40's
  three arms became runnable (they had never been registered, so no `spec_hash` was superseded) and
  **no other arm's panel, gate or number changed** — an arm that does not hold `2x_spy` takes the
  identical code path.
* **`ad5ed2df` (§6.4): the pooled-OOS option-bundle width (the crash).** A first attempt at this
  batch crashed in Stage 2 of the option arms with
  `ValueError: could not broadcast input array from shape (1,7304) into shape (1,2773)`:
  `bridge.bundle_for` sized the per-session option arrays by the panel's *columns* but filled them
  from `panel.days`, the window's own session list — equal on a plain window, one off on a main
  bootstrap chunk and wildly different on a pooled-OOS chunk (7,304 sessions on M1 against 2,773
  columns). Fixed by clamping the arrays to `W = min(T, panel.days.size)` with the window's own
  labels (which is what `windows.usd_env`'s rails are indexed by), plus an explicit refusal of
  `fill_mode = 2` (no call site carries a fill series). Regression tests in
  `research/fast_engine_opts/tests/test_opts.py`. **The crashed attempt's records were set aside
  outside the repository and never read or used**; its ledger lines were reverted, and the run
  reported here is the clean one. Every previously working path is bit-identical.
* **Two bookkeeping nits this report could not resolve from the artifacts** (no numbers depend on
  them): (i) §6.4's "Unchanged" note counts *11* declared `NEEDS_KERNEL` refusals and lists them
  without IC59's three arms, while the batch's own ledger carries IC59/A–C as `needs_kernel` in
  `bank_v3` — the refusal count of this batch is 14 (12 kernel + 2 data); (ii) `IC59_K`'s three arms,
  declared in §6.3's batch composition, are in neither the ledger nor the results
  (§`NEEDS_KERNEL`), which is the only deviation from the pre-registered composition.

## Method, checks, determinism

* Before the batch (all green, recorded in `HARNESS.json`): the framework harness 116/116 checks,
  the engine's `parity --quick` PASS, K's published anchors reproduced to ≤ 5e-05 pp — the cells
  quoted at the top, including after tax 10.7533 / 15.2354 / 23.9720 % at $100k on the three
  windows. `research/fast_engine_opts`' own harness (32 tests, K's 14 anchor cells) and the parity
  cells passed with the two fixes of §Amendments.
* The batch: 66 arms through `fe_sim` / `fe2_sim` / `fe_tax` / `fe_boot` / `fe_opts_*` — no new
  per-day Python loop, no edit to `research/fast_engine/`. Every arm's Stage 1 ran on its own
  declared window with the control re-simulated on it (`_control_w*.json`), Stage 2 on the holdout
  set defined in `README.md` §3.
* **Determinism.** The ledger is the record: 356 lines, 300 distinct `(idea, arm, spec_hash)`, of
  which `bank_v3` is exactly the 80 lines this batch wrote (66 `screened`, 14 `needs_kernel`), and
  each per-arm record carries the `code` and `spec` hashes the runner read. The deflation pass is
  seeded (`seed = 20261016`, 2,000 resamples) and its output is committed
  (`results/DEFLATION.json`). A second full batch run was **not** made: the pre-registered pipeline
  has no silent mode, so it would append 80 duplicate ledger lines; the framework's own anchors
  (above) are the reproducibility evidence for the engine, and the only new code this report ran is
  the IC56 repro (~1.4 s), which is deterministic and read-only.
* Wall clock: batch 399.2 s on 6 workers; the per-arm times are in
  `results/bank_v3/timing.json` (15.0 s for `ib25_RW/A_RW` up to 65.1 s for `IC52/A`).

## Caveats

1. Δ is always `arm − K` on the same resampled paths; the Stage-1 decision is the after-tax
   2000-window Δ plus the B1 median, and the deflation is applied to that same B1 Δ vector — the
   framework's pre-registered choice, not a daily-return Sharpe.
2. Stage 1's pass rule is a strict `> 0`, so an arm *equal* to K passes on rounding noise
   (6e-06 pp). Read every `IC57/B` and `IC58/A–C` "PASS" as "identical to K", and `IC52/A` /
   `IC52/C`'s as "identical to K on the Stage-1 window" (their ETF and pooled-OOS cells do differ —
   that is the arm's only content, and it fails E1 and FRESH).
3. `deflated_sharpe` uses N = 300, every distinct trial ever registered in this campaign's ledger —
   the conservative count, and the reason no arm here can pass: `sr0 = 4.338` against a best of
   4.052, and the two largest advantage-Sharpes come from reduced-window arms whose Δ vectors are
   dominated by a few extreme bootstrap paths (`IC45/A` mean 2,197.8 pp, sd 542.4, median 21.4 pp).
   `var(SR) = 2.7736` is set by those tails.
4. 20-session blocks cannot capture serial dependence beyond 20 sessions; the p-values are
   optimistic under long memory. 13 of the 66 arms have non-finite entries in their B1 Δ vector
   (and therefore a NaN B1 median), so those arms' bootstrap cells are unusable and the deflation's
   Δ set is the 53 arms that remain (§Deflation, §Known issue).
5. The arms' windows differ by design (the plugin's own `start = auto` over its inputs), and the
   control is re-simulated on each; a Δ between two arms on different windows is not a comparison.
6. Ideas and their arms were fixed by committed plugin code *before* any result existed; the
   plugin modules (`research/ideas/IC*.py`) and the framework are unchanged by this study. Batches
   1 and 2 stay in N.
7. The IC56/IC40 bootstrap defect (§Known issue) is documented, not fixed; anything that needs a
   bootstrap Δ for the `wht` or `2x_spy` options must be re-run after that fix.
