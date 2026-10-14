# R2 Stage B replay on the production engine (2026-10-14)

> Evidence for this report (`research/r2_stage_b_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/r2-stage-b-v1-2026-10`.

**R2 replicates on the production engine and is a small in-window improvement
over E2: +0.30 pp CAGR at 1x (NAV 1,359,064.18 against 1,329,642.62, +2.21%)
with a max drawdown 1.50 pp *shallower*, and the research's 2023-01-20 early
episode is reproduced almost exactly (+6.61 pp against +6.75 pp).** The whole
gain comes from 2022–2023; 2017–2021 is bit-identical to E2 (no early episode).
This is a replication and measurement task, not a screen: no gates were
re-opened, and R2's research verdict (plateau gate FAIL,
`fast_reentry_v2.md`) is unchanged by this replay.

## Verdict

| Question | Answer |
|---|---|
| Does E2 still reproduce the Stage B baseline on the current `src`? | **Yes**, bit-for-bit (NAV 1,329,642.62 / 1,313,969.74; decision hash `ec634c45…65eb`, fill hash `6aadd358…eaff9`; VT35 1,173,537.61). |
| Does R2 (1.2.0) run on the production engine? | **Yes.** 2,406 decisions, 644 fills, all invariants hold. |
| Is R2 better than E2 inside the Stage B window? | **Yes, slightly**: +0.30 pp CAGR (1x) / +0.29 pp (2x); max DD 1.50 pp shallower (1x). |
| Does the research's 2023-01-20 early episode show up? | **Yes**, +6.61 pp (research: +6.75 pp), the same date and the same 8-session early entry. |
| Does the research's +0.81 pp CAGR edge transfer? | **No** — only +0.30 pp in this window: the research edge is dominated by 2001–2002 episodes that Stage B (2017–2026) does not cover. |

## Setup

- **Window** 2017-01-03 → 2026-07-30, $100k, shipped risk and allocation
  (`maximum_allocation: 0.98`, cap 8, band 0.5 pp), 1x and 2x costs.
- **Engine.** This worktree's `src` (`equity_leveraged_trend_v1` 1.2.0) driven
  by the archived `paper_config_v2` Stage B runner, restored from tag
  `archive/research/e2-integration-v1-2026-10` (itself the runner of
  `archive/research/signal-ensemble-v1-2026-10` with the harness modules of
  `archive/research/sleeve-candidates-v2-v3-2026-09`, patched only in imports
  by `adapt_harness.py`) and copied to `stage_b/`. The runner is the same
  shared-ledger physical replay that produced E2's Stage B row.
- **Arms.**
  - `VT35.yaml` — `trend_rule: sma` (1.0.0).
  - `E2.yaml` — the shipped `config/settings.yaml` **without**
    `early_reentry_weight` (1.1.0). Because R2 shipped, the packaged default
    (`config.py::_default_equity_strategy`) now carries `early_reentry_weight:
    0.5` and a majority-rule YAML block inherits it, so this arm is run with
    the added `--drop-early-reentry` flag, which drops the inherited key exactly
    as `tests/test_vt35_golden.py::test_e2_without_the_early_path` does.
  - `R2.yaml` — `config/settings.yaml` verbatim, `early_reentry_weight: 0.5`
    (1.2.0).
- **Data.** `substitute_v5_{bars,actions}_qqq_spy_qld.csv` (the QQQ/SPY/QLD
  rows of the `profit_max_v1` campaign file, Alpaca SIP split-adjusted) plus
  `equity_entity_lifetimes_full_v5.csv`. No network fetch: every input is
  committed in this repository or restored from an archive tag
  (`DATA_MANIFEST.json`).

### Warm-up extension (data necessity, not a rule change)

R2 declares `minimum_history_bars = 397` (the 126-session replay needs a
252-session vol median defined at index 271), and the engine's LIVE-parity
warm-up gate refuses to decide until QQQ **and** QLD each have 397 completed
closes. The substitute file starts 2016-01-04 and has only **252** QQQ sessions
before 2017-01-03, so on it R2 would start 145 sessions late:

| R2 run | first decision | decisions | ending NAV |
|---|---|---:|---:|
| substitute bars only | 2017-07-31 | 2,262 | 956,118.49 |
| + warm-up extension | 2017-01-03 | 2,406 | **1,359,064.18** |

Missing the 2017 rally (E2 is in market at that time) costs 30% of the ending
NAV, so the extension is mandatory for an apples-to-apples replay. The
extension is `warmup_yahoo_2014_2015_bars_qqq_qld_spy.csv`: 1,512 rows
(2014-01-02 … 2015-12-31, XNYS sessions) built by `build_warmup_extension.py`
from the repository's committed `data/historical/stress_robustness_v1_daily.csv`
(Yahoo daily OHLC — the file the `fast_reentry` research itself used as its
production price basis). The extension is *not* silently mixed in:

- On the 2,659 overlapping sessions Yahoo's `close` equals the Alpaca SIP
  split-adjusted `close` with a **median relative difference of 2.2e-08** (worst
  9.3e-04); both are price (splits-only) closes, and no pre-2016 dividends are
  added, so the ledger's action set is unchanged.
- E2 and VT35 reproduce **bit-for-bit** on the extended bars (identical NAV,
  decision and fill hashes).
- R2's decisions on the 2,262 sessions both runs can decide are **identical**
  (state, weights and `early_reentry` metadata, 0 differences): the extension
  only opens the evaluation window; it changes no decision.
- Residual exposure: the pre-2016 closes feed R2's 252-session vol medians for
  decisions up to ~2018-01 (no early episode occurs before 2022-07 anywhere).

## Reproduction

| Arm | Costs | Ending NAV | Expected | Fills | Decision hash | Fill hash |
|---|---|---:|---:|---:|---|---|
| VT35 (1.0.0) | 1x | 1,173,537.61 | 1,173,537.61 | 615 | `c9adec34…4829` | `8c00d14f…a9f0` |
| VT35 (1.0.0) | 2x | 1,156,890.18 | 1,156,890.18 | 616 | `c9adec34…4829` | — |
| E2 (1.1.0) | 1x | 1,329,642.62 | 1,329,642.62 | 617 | `ec634c45…65eb` | `6aadd358…eaff9` |
| E2 (1.1.0) | 2x | 1,313,969.74 | 1,313,969.74 | 618 | `ec634c45…65eb` | — |

Fees match too (VT35 193.53, E2 186.46 at 1x). Every published
`e2_integration_v1` Stage B number reproduces.

## Results

| Arm | Costs | Ending NAV | CAGR | Max DD | Sharpe | Turnover/yr | Fills | 2017–21 CAGR | 2022–26 CAGR |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| VT35 (1.0.0) | 1x | 1,173,537.61 | 29.35% | 27.70% | 1.054 | 4.24 | 615 | 37.24% | 21.31% |
| VT35 (1.0.0) | 2x | 1,156,890.18 | 29.16% | 27.84% | 1.049 | 4.24 | 616 | 37.03% | 21.13% |
| **E2 (1.1.0)** | 1x | **1,329,642.62** | **31.05%** | **32.19%** | **1.098** | 3.84 | 617 | 41.05% | 20.99% |
| E2 (1.1.0) | 2x | 1,313,969.74 | 30.89% | 32.33% | 1.093 | 3.84 | 618 | 40.89% | 20.83% |
| **R2 (1.2.0)** | 1x | **1,359,064.18** | **31.35%** | **30.69%** | **1.102** | 4.06 | 644 | 41.05% | 21.57% |
| R2 (1.2.0) | 2x | 1,342,001.29 | 31.18% | 30.89% | 1.097 | 4.06 | 645 | 40.89% | 21.39% |

R2 − E2:

| Costs | Δ NAV | Δ NAV% | Δ CAGR | Δ max DD | Δ Sharpe | Δ turnover/yr | Δ fills | Δ costs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1x | +29,421.56 | +2.21% | +0.30 pp | **−1.50 pp** | +0.004 | +0.22 | +27 | +421.22 |
| 2x | +28,031.55 | +2.13% | +0.29 pp | −1.44 pp | +0.004 | +0.22 | +27 | +830.04 |

- **Max DD episodes.** VT35 peak 2020-02-19 → trough 2020-04-09 (27.70%); E2
  and R2 share the episode peak 2021-11-19 → trough 2023-03-13 (32.19% against
  30.69%). R2's shallower DD is carried entirely by its +2.21% NAV ratio versus
  E2 built before the trough, not by an earlier exit.
- **Time in market** (share of decisions with a QLD target): R2 85.37%, E2
  83.79%, VT35 82.83%; in-market episodes 16 / 13 / 18.
- **Calendar years that differ (1x).** 2022: VT35 −19.71%, E2 −20.34%, **R2
  −22.14%**; 2023: VT35 +62.47%, E2 +53.43%, **R2 +60.45%**. Every other year
  is identical to the cent (2019: E2 and R2 both +44.72%, VT35 +26.20%; 2025:
  both +25.98%, VT35 +19.47%).
- **Costs** (gross of financing, as defined by the runner): 1x R2 6,158.68,
  E2 5,737.46, VT35 5,739.04; 2x R2 12,216.17, E2 11,386.13.

## R2 against E2: differing decisions and episodes

R2's own decisions are identical to E2's in every one of the 2,406 sessions;
the arms differ only where R2 holds half the production weight on the early
path: **38 sessions, all E2 `downtrend` → R2 `early_reentry`**, forming four
episodes (the full 38-row table with `sma50`, `vol20` vs its 252-session
median, `momentum20` and the R2 target weight is in
`STAGE_B_RESULTS.json` under `r2_vs_e2.differing_decisions`). Every entry
condition was satisfied on every one of those sessions (close > SMA50, vol20 <
median, close > close 20 sessions earlier), and E2's vote never turned on
during them.

Early episodes and their P&L (span = the first early decision's close to the
close of the fill session that rebalances the early position, i.e. the session
after the state leaves `early_reentry`; `Δ` = R2's return minus E2's over the
same span):

| Episode (early decisions) | Sessions | Exit session | Fill session | R2 span | E2 span | Δ | E2's next entry |
|---|---:|---|---|---:|---:|---:|---|
| 2022-07-25 → 2022-08-29 | 26 | 2022-08-30 | 2022-08-31 | +0.77% | 0.00% | **+0.77 pp** | 2023-02-01 |
| 2022-12-13 → 2022-12-14 | 2 | 2022-12-15 | 2022-12-16 | −3.01% | 0.00% | **−3.01 pp** | 2023-02-01 |
| 2023-01-17 → 2023-01-18 | 2 | 2023-01-19 | 2023-01-20 | −1.84% | 0.00% | **−1.84 pp** | 2023-02-01 |
| 2023-01-20 → 2023-01-31 | 8 | 2023-02-01 | 2023-02-02 | +8.76% | +2.15% | **+6.61 pp** | 2023-02-01 |

- The four episodes compound to ×1.02149 against the terminal R2/E2 ratio of
  ×1.02213: a residual of **+0.06 pp** (resize-band effects on the position
  built inside the last episode). The whole Stage B gain is these four
  episodes.
- **E2 was in cash for the entire stretch 2022-07-25 → 2023-02-01** (its NAV is
  flat), so the two losing episodes are R2 buying a rally that failed, at half
  weight, and the two winning ones are R2 catching the January 2023 turn
  before E2 did.
- **Against the research.** The research's in-window episodes and Stage B's
  agree closely: 2023-01-17 → −1.84 pp here against −1.85 pp in
  `fast_reentry_v2.md`; 2022-12-13 → −3.01 pp here against −6.16 pp for the
  full-weight R1 arm there (R2 is half R1 by construction); **2023-01-20 →
  +6.61 pp here against +6.75 pp there** (R2's own span return +8.76% here
  against +8.74% there). The research found 32 early episodes over 2000–2026
  and 2.46% of sessions active; Stage B's 2017–2026 slice contains 4 episodes
  and 38/2,406 = **1.58%** of sessions, and the two largest research episodes
  (2001-10-31, 2002-11-04 — 61% of the research gain) are outside the window.
  That, not a data or engine difference, is why the replay delivers +0.30 pp of
  CAGR rather than the research's +0.81 pp.

## Checks

- **Accounting invariants** (`cash_conserved`, `nav_matches_ledger`,
  `position_quantities_reconciled`, `duplicate_fill_count == 0`,
  `unowned_fill_count == 0`, `no_future_data_access`) hold in all six runs, as
  the archived `evaluate.py::_accounting` computes them. 2,406 causality checks
  per run; `final_position_count == 0` (liquidated at the end).
- **Determinism.** Every R2 arm was run twice: payloads are identical except
  the wall-clock `elapsed_seconds`, audits are byte-identical (R2 1x audit
  `ec27218d…4148`). `STAGE_B_RESULTS.json` is byte-identical across two
  evaluations (`88f7bc9b…bc04`).
- **Structural.** No slot-claim violations, sleeve fills 0, observation stream
  sizes `[3]`, same pinned universe as E2.

## Caveats

- **The pass is one episode again, and then some.** Compound the four episodes
  without 2023-01-20 and R2's ratio versus E2 is **0.9600 (−4.0%, ≈ −0.42 pp
  CAGR)**: the largest episode is more than the whole in-window gain and the
  other three episodes net negative. The research's own three-episode
  concentration (95.3% of its gain) is the same property.
- **The warm-up seam.** R2's first-year decisions rest on Yahoo pre-2016 closes
  (matching SIP to 2.2e-08 median). No early episode occurs before 2022-07, and
  R2's decisions are unchanged wherever both runs can decide, but the median
  windows in 2016–2017 are not pure SIP data.
- **Window end.** Stage B stops 2026-07-30 (the substitute file's last close),
  six weeks before the research sample end; the research's own 2000–2026 row
  and its failed plateau gate are unaffected by this replay.
- **The E2 arm needs a flag.** `--drop-early-reentry` exists only because the
  packaged default now carries the R2 key; it is the documented
  (`tests/test_vt35_golden.py`) way to express 1.1.0, and the arm reproduces
  E2's published hashes.
- PAPER-only; nothing here touches `live.enabled: false`.

## Reproduce

```
D=research/r2_stage_b_v1
git checkout archive/research/e2-integration-v1-2026-10 -- research/e2_integration_v1
git archive archive/research/sleeve-candidates-v2-v3-2026-09 \
    research/physical_availability_clock_v2 research/sleeve_candidates_v2/stage_b/{composite,run_stage_b}.py | tar -x -C $D/archive
git archive archive/research/signal-ensemble-v1-2026-10 research/paper_config_v2 | tar -x -C $D/archive
cp $D/archive/research/physical_availability_clock_v2/*.py $D/stage_b/research/physical_availability_clock_v2/
cp $D/archive/research/sleeve_candidates_v2/stage_b/*.py   $D/stage_b/research/sleeve_candidates_v2/stage_b/
cp research/e2_integration_v1/stage_b/research/paper_config_v2/run_stage_b.py $D/stage_b/research/paper_config_v2/
cp research/e2_integration_v1/stage_b/data/historical/substitute_v5_*.csv    $D/stage_b/data/historical/
cp data/historical/equity_entity_lifetimes_full_v5.csv                       $D/stage_b/data/historical/
ln -sfn ../../../src $D/stage_b/src
python $D/adapt_harness.py && python $D/build_warmup_extension.py
cd $D/stage_b
EXT=$PWD/data/historical/substitute_v5_plus_warmup_bars_qqq_spy_qld.csv
for arm in VT35 E2 R2; do for cost in 1.0 2.0; do
  flag=""; [ $arm = E2 ] && flag=--drop-early-reentry
  python research/paper_config_v2/run_stage_b.py --config $PWD/configs/$arm.yaml \
      --cost $cost --bars $EXT --out $PWD/runs/${arm}_$cost.json.gz $flag
done; done
cd .. && python evaluate_r2_stage_b.py
```

Each arm takes ~1 s (R2 ~22 s: its `decide()` replays 126 sessions per call)
and well under 1 GB.

## Evidence

- `research/r2_stage_b_v1/STAGE_B_RESULTS.json` — every number above, the 38
  differing decisions with their `early_reentry` metadata, the four episodes,
  the reproduction rows, the determinism block and the invariant dumps.
- `research/r2_stage_b_v1/stage_b/runs/` — 26 gzipped payload + audit dumps
  (E2/VT35 reproduction, E2/VT35/R2 on the extended bars, R2 twice for
  determinism, R2 without the extension).
- `research/r2_stage_b_v1/DATA_MANIFEST.json` — SHA-256, ranges and provenance
  of every input and derived file.
- `research/r2_stage_b_v1/{adapt_harness.py, build_warmup_extension.py,
  evaluate_r2_stage_b.py}` and the patched runner/configs under `stage_b/`.
