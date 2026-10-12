# E2 integration v1 (2026-10-12)

**E2 ships in the PAPER config as `equity_leveraged_trend_v1` 1.1.0. The owner
approved it as a documented exception to the failed bootstrap gates of
`signal_ensemble_v1` (G5) and `e2_confirmation_v1` (K5).**

E2 holds QLD while at least two of three QQQ trend votes are true. Sizing is
VT35's `min(1, 0.35/vol20)`, capped at 0.98. The production rule differs from
research E2 in two inputs, because production has only split-adjusted price
closes and no T-bill series:

- TSM is the 252-session **price** return against a **zero** hurdle.
- DON uses the **price** close.

Over 2000–2026 this costs 0.15 pp CAGR against research E2. Max DD is the same.

- **Stage B on the production engine (2017-01-03 → 2026-07-30, $100k).**
  - VT35 at 1x reproduces the S1 baseline exactly: NAV 1,173,537.61, 615 fills.
  - E2 at 1x ends at NAV 1,329,642.62. CAGR is 31.05% against 29.35%.
  - E2's max DD is **32.19% against 27.70%**. The research screen shows the
    same rise over this window for research E2 (33.6% against 28.0%), so the
    rise is a property of E2, not of the production inputs.
- **Main cost.** E2 re-enters three sessions later in late January 2023. That
  extends the 2022 bear drawdown to its trough on 2023-03-13.

## Data feasibility

| Question | Finding |
|---|---|
| QQQ closes at runtime | Alpaca daily bars with `adjustment="splits"`: split-adjusted price, dividends excluded (`runtime._warmup_symbols`, `data/adjustment.py`). The SIP verifier and the IEX/SIP cross-check use the same basis. |
| Total-return path | None. Dividends exist only as backtest corporate actions (cash credited to holdings), never in the signal series. Switching QQQ to `adjustment="all"` would also move the SMA vote off VT35's price basis and the provider-authority verifier. Rejected. |
| History at decision time | Warm-up fetches `minimum_history_bars + 5` daily bars per symbol. History then grows with stream bars and is never trimmed. E2 sets `minimum_history_bars = max(200, 252 + 1, 126, 21) = 253`, so warm-up fetches 258 bars. Every warm-up, readiness and verifier path reads `minimum_history_bars`, so no other change is needed. |
| Backtest / Stage B | The same split-adjusted SIP bars (`profit_max_v1_daily_split_sip.csv`, from 2016-01-04). 2016 has 252 sessions, so E2 can decide from 2017-01-03 exactly, the first Stage B session. |
| T-bill series | None at runtime. |

### T-bill hurdle options

| Option | Verdict |
|---|---|
| (a) BIL as a signal-only symbol | Rejected. On the production feed, BIL's split-adjusted 252-session price return is −0.44% to +0.48% (median +0.01%) over 2017–2026, while T-bills paid up to ~5%. BIL distributes its yield, so its price is flat. Without a total-return path, BIL **is** a zero hurdle. It would still add a new symbol to stream, warm up, verify and keep from trading. |
| (b) Fixed configured hurdle | Rejected. A constant rate is wrong in most regimes and invites tuning. Historically it is also worse: 2% → 13.59%, 4% → 13.14% (m = 2, against 14.82% for research E2). |
| (c) External rate feed (FRED DTB3) | Rejected. It adds a new runtime network dependency and failure mode. With price-only TSM it also does **worse** than a zero hurdle (13.97% against 14.67%), because the missing dividend yield then stacks on the T-bill hurdle. |
| **(d) Zero hurdle on price** | **Chosen.** No new data. Price return > 0 means total return > the dividend yield (~0.6–1%/yr for QQQ), which partly stands in for the hurdle. It is the closest to research E2 of the options production can run. |

## Deviation from research E2

Method:

- The archived `signal_ensemble_v1` screen (engine, data and mechanics from
  tag `archive/research/signal-ensemble-v1-2026-10`) is re-run with the
  TSM/DON inputs substituted.
- Window 2000-03-09 → 2026-09-29.
- The harness check passes: C0 and research E2 reproduce the published
  13.39% / 14.82% (m = 2) and 13.68% / 15.11% (m = 1).

| Arm (base financing) | CAGR m = 2 | Max DD m = 2 | CAGR m = 1 | Sharpe m = 2 | Turnover/yr | Decisions ≠ research E2 | 2017-01 → 2026-07 CAGR / DD (m = 2) |
|---|---:|---:|---:|---:|---:|---:|---|
| C0 (VT35) | 13.39% | 49.6% | 13.68% | 0.614 | 6.48 | 1.39% | 29.70% / 28.0% |
| Research E2 (TR, DTB3 hurdle, TR channel) | 14.82% | 49.6% | 15.11% | 0.659 | 6.22 | — | 30.30% / 33.6% |
| **Production E2 (price, zero hurdle, price channel)** | **14.67%** | **49.6%** | **14.95%** | **0.654** | **5.97** | **0.48%** | **31.42% / 32.5%** |
| Price TSM + DTB3 hurdle, price channel | 13.97% | 49.6% | 14.29% | 0.632 | 6.82 | 0.34% | 29.41% / 32.5% |
| TR TSM + zero hurdle, TR channel | 14.92% | 49.6% | 15.19% | 0.662 | 5.88 | 0.33% | 31.07% / 33.6% |
| Price TSM + DTB3, TR channel | 14.30% | 49.6% | 14.60% | 0.643 | 6.49 | 0.18% | 29.61% / 33.6% |
| Price TSM + fixed 2% | 13.59% | 49.6% | 13.89% | 0.619 | 6.63 | 0.58% | 29.20% / 32.5% |
| Price TSM + fixed 4% | 13.14% | 49.6% | 13.46% | 0.605 | 7.08 | 0.84% | 28.99% / 32.5% |

With calibrated financing (DTB3 + 0.75%) at m = 2: C0 12.83%, research E2
14.25%, production E2 14.10%.

**Dividend and total-return effect:**

- Removing dividends alone, with the DTB3 hurdle kept, costs 0.85 pp (13.97%
  against 14.82%).
- Dropping the hurdle alone, on total return, adds 0.10 pp (14.92%).
- The production combination lands 0.15 pp below research E2. The two
  errors offset, because the dividend yield acts as a small fixed hurdle.
- The hurdle choice moves the result by ±0.7 pp. That is larger than E2's
  bootstrap edge, which is about zero, so treat the 2000–2026 gain as
  regime-dependent, as both research campaigns concluded.

## Stage B on the production engine

Setup:

- Runner: the archived `paper_config_v2/run_stage_b.py`, with the harness
  modules from `archive/research/sleeve-candidates-v2-v3-2026-09`. These
  needed eleven import and keyword adaptations to current `src`
  (`adapt_harness.py`); none touches the execution model.
- Window 2017-01-03 → 2026-07-30, $100k, shipped risk and allocation.
- **Data substitute.** The canonical v5 equity CSV (882 MB, gitignored, in no
  tag) is unavailable here. QQQ, SPY and QLD come from the campaign file
  `profit_max_v1_daily_split_sip.csv` (same Alpaca SIP split-adjusted source,
  plus its corporate actions). The strategy reads only these symbols.
- **Reproduction check.** The VT35 arm reproduces S1 exactly: NAV
  1,173,537.61, 615 fills, 29.35% CAGR, 27.70% max DD.
  - Decision hash `c9adec34…4829` and fill hash `8c00d14f…a9f0` match
    `crypto_removal_v1`, and so do fees of 193.53.
  - A second E2 run gave identical hashes (`ec634c45…65eb` /
    `6aadd358…eaff9`) and an identical NAV series.
- Each run takes about 1 s and well under 1 GB, against about 6 GB with the
  full v5 file.

| Arm | Costs | Ending NAV | CAGR | Max DD | Sharpe | Turnover/yr | Fills | 2017–21 CAGR | 2022–26 CAGR |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **VT35 (1.0.0)** | 1x | **1,173,537.61** | **29.35%** | **27.70%** | **1.054** | **4.24** | **615** | 37.24% | 21.31% |
| VT35 (1.0.0) | 2x | 1,156,890.18 | 29.16% | 27.84% | 1.049 | 4.24 | 616 | 37.03% | 21.13% |
| **E2 (1.1.0)** | 1x | **1,329,642.62** | **31.05%** | **32.19%** | **1.098** | **3.84** | **617** | 41.05% | 20.99% |
| E2 (1.1.0) | 2x | 1,313,969.74 | 30.89% | 32.33% | 1.093 | 3.84 | 618 | 40.89% | 20.83% |

- **Accounting.** Invariants hold in all four runs.
- **Max DD episodes.**
  - VT35: peak 2020-02-19 → trough 2020-04-09.
  - E2: peak 2021-11-19 → trough 2023-03-13.
- **Time in market.** E2 is in market at 83.8% of decision closes, VT35 at
  82.8%.
- **Round trips.** E2 has 13 in-market episodes, VT35 18.

**Calendar years that differ** (1x; every other year is identical):

| Year | VT35 | E2 |
|---|---:|---:|
| 2019 | +26.2% | +44.7% |
| 2022 | −19.7% | −20.3% |
| 2023 | +62.5% | +53.4% |
| 2025 | +19.5% | +26.0% |

### Entries and exits that differ

Dates are decision closes; fills are at the next open. There are 29 differing
decision sessions out of 2,406.

| Episode | VT35 | E2 | Votes on the differing days |
|---|---|---|---|
| 2019 recovery | Enters 2019-02-15. Whipsaws: out 02-21, in 02-22, out 03-07, in 03-11, out 05-31, in 06-04. Exits 2020-03-09. | Enters 2019-02-04. Holds to the 2020-03-09 exit. | SMA−, TSM+, DON+ |
| 2022 bear rally | In 2022-03-29, out 03-30, in 04-04, out 04-05 | In 2022-03-28, out 2022-04-06 | SMA−, TSM+, DON+ |
| 2023 re-entry | In 2023-01-26, out 01-30, in 01-31 | In 2023-02-01 | SMA+, TSM−, DON− |
| 2025 re-entry | Enters 2025-05-12 | Enters 2025-05-01 | SMA−, TSM+, DON+ |

Both arms exit 2023-03-10, and both hold 2025-05 → 2026-03-20. On 2026-09-29,
the last close in the research data, all three votes are up, so E2 and VT35
agree. Switching would not trade by itself.

## Implementation

- **Strategy.** `src/parayabuck/portfolio/equity_leveraged_trend.py`:
  - New parameter `trend_rule`: `sma` is the class default (VT35);
    `majority_sma_tsm_don` selects E2.
  - `tsm_bars` (default 252) and `donchian_bars` (default 126) are accepted
    only with `majority_sma_tsm_don`.
  - Strategy version is 1.0.0 for `sma` and 1.1.0 for E2. Receipts, the
    configuration digest and idempotency keys carry it.
  - Under `sma`, the parameter tuple, metadata and version are
    byte-identical to VT35.
  - The VT35 golden backtest and S1 reproduce unchanged.
  - Any vote without enough history, or with a non-positive TSM base close,
    gives `insufficient_history` (cash), never a 2-of-2 vote.
  - Metadata adds `trend_rule` and `trend_signals`: per-vote `up`,
    close/SMA, return/hurdle, midpoint, `bars`, and `votes_up`.
- **Config.**
  - `config/settings.yaml` sets `trend_rule: majority_sma_tsm_don`,
    `tsm_bars: 252` and `donchian_bars: 126`.
  - The packaged default (`config.py`) adds only `trend_rule`, so a YAML
    block with `trend_rule: sma` loads plain VT35.
- **No signal-only symbol.**
  - The universe stays `[SPY, QQQ, QLD]`.
  - `decision_symbols` and the cross-check symbols stay QQQ and QLD.
  - The only weight the strategy ever emits is QLD.
  - The IEX/SIP cross-check re-decides through `decide()`, so a vote flip
    between sources is caught as `trend_mismatch`.
- **Observability.**
  - The daily summary appends `votes 2/3 (SMA+ TSM- DON+)` for E2.
  - `scripts/backtest_live_comparison.py` re-decides each live decision with
    the parameters that decision recorded. VT35-era sessions are therefore
    not flagged against E2.
- **Tests.**
  - `tests/test_equity_leveraged_trend_e2.py`: votes, the 2-of-3 vote,
    history edge cases, VT35 parity, metadata, validation, config, no extra
    traded symbol, cross-check, comparison script and summary.
  - An E2 golden on the VT35 fixture (`tests/test_vt35_golden.py`).
  - The frozen-parameter tests are updated.

## Caveats

- **The bootstrap gates still fail.** Both research campaigns put E2's
  structural edge at about zero (−0.05 pp on QQQ, −0.001 pp pooled on five
  markets). The Stage B gain comes from 2019 and 2025. The loss in 2023 is
  the biggest single cost, and it raises 2017–2026 max DD by 4.5 pp.
- **Price-only inputs.** TSM uses a zero hurdle and DON uses the price
  channel. In high-rate years (2023–2026 T-bills at ~4–5%), TSM is easier to
  satisfy than research E2's. In the 2017+ screen window this helps by about
  +1.1 pp; that is luck of the window, not design.
- **Stage B data substitute.** QQQ, SPY and QLD come from the profit_max_v1
  campaign file, not the canonical v5 file. VT35 reproduces S1 bit-for-bit on
  it.

## Evidence

The runner, its configs, the data substitutes, `DEVIATION.json`,
`STAGE_B_RESULTS.json` and the run dumps were in `research/e2_integration_v1/`
in the evidence commit on this branch. They were removed from the tree in the
following commit and are to be preserved in tag
`archive/research/e2-integration-v1-2026-10`.

The archived inputs are byte copies of tagged files and are not committed:
`archive/research/signal-ensemble-v1-2026-10`,
`archive/research/profit-max-v1-2026-09` and
`archive/research/sleeve-candidates-v2-v3-2026-09`. The setup steps are in
the docstring of `adapt_harness.py`. The run is offline and deterministic:
four Stage B runs of about 1 s each, and a deviation screen of about 20 s.

## Decision

Ship E2 (`trend_rule: majority_sma_tsm_don`, version 1.1.0) as the PAPER
trend rule, with the production approximation above. Plain VT35 stays one
config line away (`trend_rule: sma`, remove `tsm_bars`/`donchian_bars`).
