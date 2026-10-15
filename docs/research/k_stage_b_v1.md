# K on the production engine: Stage B replay of A1u + H2 (k_stage_b_v1, 2026-10-15)

> Evidence: `research/k_stage_b_v1/` (harness copies, run dumps, `STAGE_B_RESULTS.json`,
> `SUMMARY.txt`, `DATA_MANIFEST.json`) are not on `main`; they are preserved in tag `archive/research/k-stage-b-v1-2026-10`.

**K reproduces on the production engine, in the research's direction and (roughly) its size.**
K = the shipped `execution.downsize_gain_band` (A1u) + `execution.december_downsize_deferral_sessions: 10`
(H2) run on 2017-01-03 → 2026-07-30 with `equity_leveraged_trend_v1` 1.2.0 and the shared-ledger
Stage B replay: **ending NAV 1,405,902.45 against R2's 1,359,064.18, +0.47 pp CAGR at 1x
(31.82 % against 31.35 %) with max drawdown 0.24 pp *shallower* (30.93 % against 30.69 %) and
turnover 0.86×/yr lower**; after Turkish tax at $100k K is **+1.275 pp CAGR against R2** (25.341 %
against 24.067 %), of which the band alone is +0.40 pp pre-tax / +0.882 pp after tax. The research's
2017-window expectation is A1u +0.82 pp and K +1.09 pp after tax (`gain_band_v3.md`,
`band_h2_combo_v1.md`), so **the after-tax size reproduces slightly larger, and the sign, ordering
and mechanism do**. R2 and E2 reproduce the `r2_stage_b_v1` payloads **bit-for-bit**, so the
harness check is exact. This is a replication and measurement task, not a screen: no gate was
opened, no arm or parameter was chosen after seeing data, and nothing is deployed
(`live.enabled: false`).

## Verdict

| Question | Answer |
|---|---|
| Does R2 with A1u and H2 explicitly off reproduce the r2_stage_b_v1 R2 row? | **Yes**, bit-for-bit: NAV 1,359,064.18 / 1,342,001.29 at 1x/2x, 644 / 645 fills, fees 205.08163691187292 / 406.98344013840074, decision hash `d69d8557…bb914`, fill hashes `4f365dab…34c5` / `697fb961…cfb63`. |
| Does E2 (1.1.0) still reproduce? | **Yes**: 1,329,642.62 / 1,313,969.74, 617 / 618 fills, decision hash `ec634c45…65eb`. |
| Is the shipped policy the arm that was run? | **Yes**: `config/settings.yaml` verbatim gives the same NAV (1,405,902.45) and the same decision and fill hashes as `configs/K.yaml`. |
| Does A1u improve the production engine in-window? | **Yes**: +0.40 pp CAGR (1x) / +0.43 pp (2x), max DD +0.42 pp / +0.41 pp worse, Sharpe +0.003, turnover −0.85×/yr, 341 fewer fills. |
| Does K improve on A1u? | **Yes, additively**: +0.07 pp CAGR and −0.18 pp max DD on top of A1u at 1x (+0.06 / −0.19 at 2x). |
| Does the after-tax edge reproduce? | **Yes**: A1u +0.88 pp, K +1.27 pp CAGR vs R2 (research: +0.82 / +1.09 pp), 2x +0.90 / +1.30 pp; the tax paid falls 222,197 → 212,690 (A1u) → 202,879 (K) USD. |
| Are the rules' fail-safes exercised? | **No**: zero lot-book fallback log lines in every run, so the band ran on the engine's own FIFO lot book throughout. |
| Accounting, determinism | Invariants hold in all six runs; every arm run twice, payloads identical up to `elapsed_seconds`, audits byte-identical; `STAGE_B_RESULTS.json` byte-identical across two evaluations (`baa87011…3d2484`). |

## Setup

- **Window** 2017-01-03 → 2026-07-30 (the substitute bars' own range), $100k, shipped risk and
  allocation (`maximum_allocation: 0.98`, cap 8, band 0.5 pp), 1x and 2x costs.
- **Engine.** This worktree's `src` (`equity_leveraged_trend_v1` 1.2.0) driven by the archived
  `paper_config_v2` Stage B runner, restored from tag `archive/research/r2-stage-b-v1-2026-10` and
  copied to `stage_b/` (harness modules from `archive/research/sleeve-candidates-v2-v3-2026-09`,
  adapted by `adapt_harness.py` exactly as `r2_stage_b_v1` did). Two changes to that copy, both
  under `research/k_stage_b_v1/`:
  1. **The one that makes the task possible.** The archived factory built the engine's policy as
     `PortfolioExecutionPolicy(rebalance_band_pct=…, minimum_order_notional=…)` — literally, so the
     A1u/H2 fields added afterwards stayed at their code defaults (`None` / `0`) and the new rules
     could never fire. It now uses the single production mapping
     `portfolio_execution_policy_from_config(config.execution)`, i.e. the same config → policy call
     `runtime.py`, `cli.py`, `live_authorization.py`, `backtest/product.py` and the web engine use.
     With the arms' configs setting the keys explicitly (below) this reproduces R2 and E2 exactly,
     which is the check that the swap changed nothing else.
  2. **Audit only.** The audit dump gains `rebalances` (per-decision target/pre-trade weights and
     the engine's own `gain_band_holds` / `december_deferral_holds` records) and `band_fallbacks`
     (the `Downsize gain band unavailable` INFO lines). The payload is untouched, so its decision
     and fill hashes stay comparable with the r2_stage_b_v1 reproduction.
- **Arms** (`stage_b/configs/`, all four built from the archived R2 arm config by
  `make_configs.py`; the only edit is the `execution` block):
  - `R2off.yaml` — `downsize_gain_band: null`, `december_downsize_deferral_sessions: 0` (control:
    production R2, whose 1.2.0 behaviour predates both rules);
  - `E2.yaml` — the archived 1.1.0 arm config, likewise explicitly off, needs
    `--drop-early-reentry`;
  - `A1u.yaml` — the shipped band schedule (`thresholds: [0.01, 0.05]`, `bands: [0.005, 0.03, 0.08]`),
    deferral 0;
  - `K.yaml` — the same schedule + deferral 10 = the shipped policy (`shipped.yaml` is
    `config/settings.yaml` verbatim and is run as an identity check).
- **Data.** `substitute_v5_plus_warmup_bars_qqq_spy_qld.csv` (the Yahoo 2014-2015 warm-up extension
  plus the QQQ/SPY/QLD rows of the `profit_max_v1` Alpaca SIP campaign file), the matching actions
  file and `equity_entity_lifetimes_full_v5.csv` — all restored from the r2 tag, SHA-256 in
  `DATA_MANIFEST.json`. No network fetch; no input is regenerated.

## Reproduction

R2off and E2 against the r2_stage_b_v1 payloads, read field by field:

| Arm | Costs | Ending NAV | Reference | Fills | Fees | Decision hash | Fill hash |
|---|---|---:|---:|---:|---:|---|---|
| R2 (band/deferral off) | 1x | 1,359,064.18 | 1,359,064.18 | 644 | 205.08163691 | `d69d8557…bb914` ✓ | `4f365dab…34c5` ✓ |
| R2 (band/deferral off) | 2x | 1,342,001.29 | 1,342,001.29 | 645 | 406.98344014 | `d69d8557…bb914` ✓ | `697fb961…cfb63` ✓ |
| E2 1.1.0 | 1x | 1,329,642.62 | 1,329,642.62 | 617 | 186.45543252 | `ec634c45…65eb` ✓ | `6aadd358…eaff9` ✓ |
| E2 1.1.0 | 2x | 1,313,969.74 | 1,313,969.74 | 618 | 370.21152010 | `ec634c45…65eb` ✓ | `9618f2fb…32a0e` ✓ |

Every 1x/2x NAV, fill count, fee total and both hashes match. `config/settings.yaml` verbatim
produces K's NAV, decision hash and fill hash exactly. Because A1u and H2 only decide whether an
order exists, **the decisions of all three arms are identical** (the target series is untouched):
the in-market share is 85.37 % with the same 16 episodes in every arm, and the max drawdown episode
is the same for all three (peak 2021-11-19 → trough 2023-03-13).

## Pre-tax results

| Arm | Costs | Ending NAV | CAGR | Max DD | Sharpe | Turnover/yr | Fills | Sells | Sells/yr | 2017-21 CAGR | 2022-26 CAGR |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **R2** | 1x | 1,359,064.18 | 31.35 % | 30.69 % | 1.102 | 4.06 | 644 | 316 | 33.0 | 41.05 % | 21.57 % |
| R2 | 2x | 1,342,001.29 | 31.18 % | 30.89 % | 1.097 | 4.06 | 645 | 317 | 33.1 | 40.89 % | 21.39 % |
| **A1u** | 1x | 1,399,349.50 | 31.75 % | 31.11 % | 1.105 | 3.21 | 303 | 84 | 8.8 | 41.73 % | 21.71 % |
| A1u | 2x | 1,384,605.37 | 31.61 % | 31.30 % | 1.101 | 3.21 | 302 | 84 | 8.8 | 41.59 % | 21.56 % |
| **K** | 1x | **1,405,902.45** | **31.82 %** | **30.93 %** | **1.106** | 3.20 | 299 | 83 | 8.7 | 41.81 % | 21.77 % |
| K | 2x | 1,391,144.33 | 31.67 % | 31.11 % | 1.103 | 3.20 | 298 | 83 | 8.7 | 41.67 % | 21.62 % |

Deltas against R2 (1x): A1u **+0.40 pp CAGR, +0.42 pp max DD, +0.003 Sharpe, −0.85 turnover/yr,
341 fewer fills**; K **+0.47 pp, +0.24 pp, +0.004, −0.86, 345 fewer fills**. At 2x the same
ordering holds (+0.43 / +0.49 pp).

- **The tax-timing mechanism is visible pre-tax.** Sells fall from 33.0/yr to 8.8/yr (A1u) and
  8.7/yr (K) — the research engine's ~31/yr → ~6-11/yr — and the cost lines fall accordingly
  (slippage −753, spread −377, fees −39 dollars at 1x). The exposure is unchanged: turnover
  measures traded notional, and the 0.85×/yr drop is exactly the suppressed small trims.
- **Calendar years that move (1x, pp vs R2).** 2018 +1.07, 2019 +0.96 (both arms), 2021 +1.28
  (A1u) / **+1.66** (K, the December 2021 deferral), 2023 +1.11 (both), 2024 +1.10 / +0.88;
  negatives: 2020 −0.24, 2022 −0.48 / −0.47, 2026 −0.33. 2017 is identical (the first band hold is
  2018-02-05). No year moves by more than 1.7 pp.
- **Drawdown.** All three arms share the 2021-11-19 → 2023-03-13 episode; K's depth (30.93 %) sits
  between R2's (30.69 %) and A1u's (31.11 %) — the band gives back 0.42 pp of DD, H2 returns
  0.18 pp of it.

## Where the pre-tax gain comes from

The rules hold **415 band holds in 133 runs** (A1u) and **401 band holds in 129 runs plus 18
deferral holds in 3 runs** (K); all 415 A1u records are gain-positive (`g_frac` > 0, tiers: 127/133
runs at the 8 pp band, 6 at 3 pp). No lot-book fail-safe ever fired.

| Arm | Extra exposure P&L | Saved costs | Extra dividends | Intraday marking | = NAV difference vs R2 |
|---|---:|---:|---:|---:|---:|
| A1u 1x | $34,151.67 | $1,168.22 | $222.49 | $4,742.94 | $40,285.32 |
| K 1x | $33,500.19 | $1,169.29 | $242.30 | $11,926.49 | $46,838.27 |
| A1u 2x | $35,560.31 | $2,304.64 | $228.49 | $4,510.64 | $42,604.09 |
| K 2x | $34,950.82 | $2,306.62 | $248.38 | $11,637.22 | $49,143.04 |

The first column is exact: `Σ_t (units_arm(t) − units_R2(t)) × (close(t+1) − close(t))` over the
whole window, i.e. the P&L of the units the rule kept in the market that the control sold. The
"intraday marking" residual is the decomposition's own `Δunits × (close − fill price)` term — where
a sale moved from one open to a later one — and is the channel the December deferral works through
(K's residual is 2.5× A1u's while its price exposure is slightly lower).

**Per-episode, indicative** (retained notional at the first hold × QLD open-to-open return from the
intended fill session to the executing sell; overlapping runs share spans, so the column does not
sum to the exact total):

| Rule | First → last decision | Decisions | Sell executed | Retained | Span | P&L |
|---|---|---|---:|---:|---:|---:|
| K, H2 December | 2021-12-16 → 2021-12-29 | 9 | 2022-01-21 | $21,300 | −12.32 % | −$2,624 |
| K, H2 December | 2024-12-18 → 2024-12-30 | 8 | 2025-01-02 | $27,196 | −2.67 % | −$725 |
| K, H2 December | 2025-12-18 | 1 | 2026-03-23 | $28,680 | −8.28 % | −$2,375 |
| K, band | 2023-03-16 → 2023-03-20 | 3 | 2023-07-13 | $33,775 | +46.82 % | +$15,814 |
| K, band | 2025-06-05 → 2025-06-09 | 3 | 2025-10-31 | $23,746 | +39.25 % | +$9,321 |
| K, band | 2026-04-14 → 2026-04-17 | 4 | 2026-06-08 | $31,954 | +28.34 % | +$9,054 |
| K, band | 2025-11-04 → 2025-11-06 | 3 | 2025-11-21 | $64,775 | −10.26 % | −$6,643 |
| K, band | 2023-03-22 → 2023-03-23 | 2 | 2023-07-13 | $13,446 | +43.65 % | +$5,869 |

The three December episodes are all negative pre-tax — each defers a trim into a January that falls
— and K still beats A1u by +0.07 pp CAGR because the deferral also moves the band's later
re-planning (K has 14 fewer band-hold decisions). The band's own largest
holds are the 2023-03 and 2026-04 rallies it stayed in and the 2025-11 drawdown it did not avoid.
The whole pre-tax effect is a handful of episodes, the same concentration the earlier studies found:
the five largest of A1u's 133 runs are +$33.3k of the ~$34.2k of extra-exposure P&L.

## After tax (the decisive number)

The tax block feeds **each arm's own production fills** (session, side, quantity, fill price —
which already carries slippage and spread — and fee, plus the run's QLD dividends and its final
liquidation) through the corrected Turkish engine restored from tag
`archive/research/gain-band-v3-2026-10` (`research/gain_band_v3/tax_engine.py`: FIFO lots with the
GVK mük. 81 Yİ-ÜFE indexation, 10 % floor, progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE, annual assessment at the year boundary, the two instalments paid the following
31 March / 31 July out of the account — selling QLD to fund them when cash is short — and a final
liquidation). Engine unchanged; only the trade source differs from the research's
target-series/synthetic-QLD* replay. Inputs (CBRT USD/TRY buying rate, TÜİK Yİ-ÜFE) are the tag's,
SHA-256 matched against its manifest.

**Scale: the 1x run already starts at $100k (`backtest.initial_cash: 100000`), so no quantity
scaling was needed — the arms' own fills at their own $100k.** One construction choice is
unavoidable and is stated: production quantities are absolute, but this account pays tax, so with
`scaling=True` (headline) each fill's quantity is multiplied by
`min(my NAV at the decision close, my NAV now) / production NAV at the decision close` — the
after-tax account then holds the **production weights** while its NAV shrinks with the tax paid,
which is what a weight-targeting engine does. A production buy that would still overdraw the
post-instalment cash is capped at available cash (2-4 buys per run; the production engine caps its
own buys the same way). `scaling=False` (literal quantities, effectively free leverage on the tax
paid) is reported as a sensitivity.

| Arm | Costs | After-tax CAGR | Δ vs R2 | Δ unscaled | Tax paid | Terminal | Funding sales / capped buys |
|---|---|---:|---:|---:|---:|---:|---:|
| **R2** | 1x | 24.067 % | — | — | $222,197 | $787,346 | 4 / 4 |
| **A1u** | 1x | 24.949 % | **+0.882 pp** | +1.070 pp | $212,690 | $842,590 | 4 / 2 |
| **K** | 1x | **25.341 %** | **+1.275 pp** | +1.499 pp | $202,879 | $868,246 | 4 / 2 |
| R2 | 2x | 23.921 % | — | — | $220,078 | $778,553 | 4 / 4 |
| A1u | 2x | 24.825 % | +0.904 pp | +1.103 pp | $210,968 | $834,635 | 4 / 2 |
| K | 2x | 25.218 % | +1.296 pp | +1.533 pp | $201,231 | $860,075 | 4 / 2 |

- **Harness check.** With the tax switched off (`pre_tax` regime) the replay reproduces every
  production NAV path: ending NAV equal to the last cent in all six runs, max path difference
  ≤ 9.3e-10. The progressive rows are that same accounting with the tax on.
- **Against the research.** The research's 2017 window gives A1u **+0.815 pp** and (A1u + H2)
  **+1.09 pp** after tax at $100k with its own cost model; this production-fill replay gives
  **+0.88 pp and +1.27 pp**. Direction, ordering and mechanism reproduce; the size is ~0.1-0.2 pp
  larger, consistent with the different trade stream (production fills and real SEC/TAF/CAT fees
  against the research's 4 bps × m and synthetic QLD* leg) and the six weeks shorter window
  (Stage B ends 2026-07-30 against the research's 2026-09-29).
- **Cross-check on the tax model.** The research's after-tax max drawdown for the control on this
  window is 36.243 %; this replay's R2 is **36.19 %**, and the ordering A1u (36.14 %) → K (35.30 %)
  says the rules do not buy their CAGR with a deeper after-tax drawdown.
- **The channel.** Cumulative tax paid falls from $222,197 (R2) to $212,690 (A1u) and $202,879 (K),
  and Yİ-ÜFE indexation relief rises from 10.44 M to 11.07 M / 11.34 M TRY: fewer, later
  realisations of the same embedded gain.
- **Not modelled, in every arm equally:** QLD's dividend cash is credited (the production ledger
  credited it) but not assessed as dividend income, exactly as the research engine never did; and
  the March/July instalments are funded at the session open with the engine's own 8 bps cost.

## Checks

- **Accounting invariants** (`cash_conserved`, `nav_matches_ledger`,
  `position_quantities_reconciled`, `duplicate_fill_count == 0`, `unowned_fill_count == 0`,
  `no_future_data_access`) hold in all six runs, as the archived `evaluate.py::_accounting` computes
  them; `final_position_count == 0` (liquidated at the end) and `cash_reconciliation_error == 0.0`
  in every run. The same payloads the r2_stage_b_v1 report used for its invariants.
- **Causality.** The runner's physical replay asserts no future-dated history and no decision
  before physical availability, as before; nothing about the two rules touches that path.
- **Determinism.** Every arm was run twice: payloads identical except the wall-clock
  `elapsed_seconds`, audits byte-identical (R2 1x `a4ca30325d33…`, A1u 1x `97a4468590e2…`,
  K 1x `5bf3d427c7ea…`). `STAGE_B_RESULTS.json` is byte-identical across two evaluations
  (`baa87011…3d2484`).
- **Structural.** 2,406 decisions in every arm, 16 in-market episodes, no slot-claim violations,
  observation stream sizes `[3]`, sleeve fills 0, same pinned universe as the r2 replay.
- **Fail-safe.** Zero `Downsize gain band unavailable` lines: the band read a valid lot book at
  every one of the 1,000+ band-eligible decisions, so the shipped fallback path was not what
  produced these numbers.

## Amendments

None: no gate, arm or parameter was changed after data was seen. The two harness edits above were
made (and are the reproduction's own check) before any non-control arm was run; the configs were
generated by `make_configs.py` from the tagged R2 arm config and are pinned by SHA-256 in
`DATA_MANIFEST.json`.

## Caveats

- **The edge is episode-concentrated, again.** 133 band runs of 415 decisions: the five largest are
  +$33.5k of the ~$34.2k of A1u's extra price exposure, and the single 2023-03 run is +$15.8k. The
  three December deferrals are all negative pre-tax; K's extra comes from the tax channel and from
  re-planned band holds, not from those episodes.
- **The attribution is a decomposition, not a closure.** The exact column is the units-held
  difference marked at the session closes; the residual (12 % of the effect for A1u, 25 % for K)
  is the intraday marking of trades that moved between opens. Per-episode numbers overlap by
  construction and are indicative.
- **After-tax scaling choice.** The headline keeps production *weights* and caps 2-4 buys at
  available cash; the literal-quantity variant is 0.19-0.22 pp higher for every arm (free leverage
  on the tax paid) and preserves the Δ ordering (+1.070 / +1.499 pp for A1u / K). Both are reported.
- **Window end.** Stage B stops 2026-07-30 (the substitute file's last close), six weeks before the
  research sample end; the research's own 2000-2026 row and the gain_band_v3 FAIL on the E3
  block-10 flat-tax cell are unaffected by this replay.
- **Warm-up seam.** 2017's decisions rest on Yahoo pre-2016 closes (matching SIP to 2.2e-08
  median) — inherited unchanged from the r2_stage_b_v1 replay, which verified that R2's decisions
  are identical wherever both runs can decide.
- **Not a selection.** A1u's own campaign failed its pre-registered E3 gate and nothing was selected
  by it; both rules are already shipped on the owner's instruction. This replay measures them on the
  production engine and does not re-open that verdict.
- PAPER-only; nothing here touches `live.enabled: false`.

## Reproduce

```
D=research/k_stage_b_v1
# 1. tagged harness bytes (archive/ is gitignored)
mkdir -p $D/archive
git archive archive/research/sleeve-candidates-v2-v3-2026-09 research/physical_availability_clock_v2 \
    research/sleeve_candidates_v2/stage_b | tar -x -C $D/archive
git archive archive/research/signal-ensemble-v1-2026-10 research/paper_config_v2 | tar -x -C $D/archive
git archive archive/research/gain-band-v3-2026-10 research/gain_band_v3 \
    data/historical/after_tax_tr_v1_usdtry_cbrt.csv data/historical/after_tax_tr_v1_yiufe.csv \
    | tar -x -C $D/taxref
ln -sfn ../../../trend_robustness_v2 $D/taxref/research/trend_robustness_v2
# 2. the harness copies the runner needs (adapt_harness.py patches them in place)
mkdir -p $D/stage_b/research/physical_availability_clock_v2 $D/stage_b/research/sleeve_candidates_v2/stage_b
cp $D/archive/research/physical_availability_clock_v2/*.py $D/stage_b/research/physical_availability_clock_v2/
cp $D/archive/research/sleeve_candidates_v2/stage_b/*.py   $D/stage_b/research/sleeve_candidates_v2/stage_b/
cp $D/archive/research/paper_config_v2/evaluate.py          $D/pc2_evaluate.py
cp $D/archive/research/paper_config_v2/run_stage_b.py       $D/stage_b/research/paper_config_v2/run_stage_b.py
python $D/adapt_harness.py
# 3. the archived bars, actions, lifetimes, configs and the k_stage_b_v1 harness edits
git archive archive/research/r2-stage-b-v1-2026-10 research/r2_stage_b_v1 | tar -x -C /tmp
cp /tmp/research/r2_stage_b_v1/stage_b/data/historical/*.csv $D/stage_b/data/historical/
cp /tmp/research/r2_stage_b_v1/stage_b/configs/R2.yaml $D/stage_b/configs/R2_reference.yaml
cp /tmp/research/r2_stage_b_v1/stage_b/configs/E2.yaml $D/stage_b/configs/E2_reference.yaml
# ... then re-apply the two documented edits to run_stage_b.py / the factory, or reuse the
#     committed copies (stage_b/research/** is committed here; only archive/ is not)
python $D/make_configs.py
# 4. the arms (each twice), from $D/stage_b
EXT=$PWD/data/historical/substitute_v5_plus_warmup_bars_qqq_spy_qld.csv
PY=/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python
for arm in R2off E2 A1u K; do for rep in "" rep; do for cost in 1.0 2.0; do
  flag=""; [ $arm = E2 ] && flag=--drop-early-reentry
  $PY research/paper_config_v2/run_stage_b.py --config $PWD/configs/$arm.yaml --cost $cost \
      --bars $EXT --out $PWD/runs/${arm}${rep}_${cost}.json.gz $flag
done; done; done
$PY research/paper_config_v2/run_stage_b.py --config $PWD/configs/shipped.yaml --cost 1.0 \
    --bars $EXT --out $PWD/runs/Kshipped_1.0.json.gz
# 5. metrics and tax
$PY $D/evaluate_k_stage_b.py && $PY $D/make_summary.py && $PY $D/make_manifest.py
```

Each arm takes ~22 s (A1u/K a little more: their decisions replay the 126-session window), the
evaluation ~25 s, well under 1 GB.

## Evidence

- `research/k_stage_b_v1/STAGE_B_RESULTS.json` — every number above: reproduction rows, the results
  tables (both costs), drawdown episodes, calendar years, the full band/deferral run lists with the
  reconciliation, determinism hashes, invariants and the whole after-tax block.
- `research/k_stage_b_v1/stage_b/runs/` — 34 gzipped payload + audit dumps (R2off/E2/A1u/K at 1x
  and 2x, each twice, plus the shipped-config identity).
- `research/k_stage_b_v1/{make_configs.py, tax_replay.py, evaluate_k_stage_b.py, make_summary.py,
  make_manifest.py, adapt_harness.py}` and the configs under `stage_b/configs/`.
- `research/k_stage_b_v1/DATA_MANIFEST.json` — SHA-256, ranges and provenance of every input, the
  code copies and the reference tags.
