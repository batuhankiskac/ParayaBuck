# T-bill sweep v1 (2026-10-12)

**S2_10 passes every pre-registered gate and is selected. The trend-off-only
sweep S1 fails the block-20 bootstrap gate (T5).** S2_10 holds BIL at 0.98
while E2 is off. While E2 is on, it also parks the residual `0.98 − w` in BIL,
re-sized only when the BIL weight leaves a 10 pp band.

- **Screen, 2000-03-09 → 2026-09-29, m = 2, base financing.** CAGR 14.88% against
  14.67% (+0.21 pp). Max DD 46.0% against 49.6%.
- **Bootstrap, block 20.** The median of S2_10 − C0 is +0.18 pp, and S2_10
  beats C0 on 75.6% of paths. For S1 the median is −0.08 pp.
- **Stage B on the production engine, 2017-01-03 → 2026-07-30.** S2_10 adds
  +0.42 pp CAGR at 1x and +0.30 pp at 2x. Max DD falls by 1.0 pp and 0.9 pp.
- **The gain is small and depends on rates.** S2_10 loses 0.5–1.2 pp a year in
  2009–2012, 2016 and 2020, when bills paid about zero and the trend
  whipsawed.
- **A broker cash sweep would beat both ETF arms.** If Alpaca paid about
  DTB3 − 0.5 pp on idle cash with no trading, it would add +0.52 to +0.68 pp
  in the screen.

Production is unchanged by this campaign. Integration is a separate step;
its requirements are listed below.

## Question

Production E2 (`equity_leveraged_trend_v1` 1.1.0) is in cash on 25.7% of
decision closes, and idle cash earns zero. `paper_config_v2` swept all idle
budget into BIL on every session. It failed at 2x costs because every daily
vol resize of QLD forced a matching BIL trade, which doubled turnover.

This campaign asks two questions:

- Does holding a T-bill ETF **only while the trend is off** add return, net of
  costs, without that turnover?
- Secondarily, does sweeping the residual `1 − w` too add more, if the bill
  leg is re-sized only on wide-band breaches?

## Method

`research/tbill_sweep_v1/PREREGISTRATION.md`, `screen.py` and the data were
committed in `f323a4c` before any non-control arm was simulated.

- **Harness.** The two-leg simulator with an empty bill leg equals the
  archived `trend_robustness_v2` engine bit for bit. C0 reproduces
  `e2_integration_v1`'s production-E2 numbers with zero difference: 14.67% /
  49.6% at m = 2, and all eight CAGR, DD and turnover values for base and
  calibrated financing at m = 1 and m = 2.
- **C0** is production E2:
  - QLD\* weight `min(0.98, [≥ 2 of 3 votes] · min(1, 0.35/vol20))`.
  - Votes on the QQQ price close: SMA200, 252-session return > 0, and the
    126-session channel midpoint.
  - Decide at close, fill at next open, 0.5 pp band, 4 bps × m per side.
  - Idle cash earns zero.
- **Bill instrument BIL\*.** Synthetic: each session accrues
  `(DTB3 − 0.1356%)/252` (BIL's expense ratio). It trades at the same
  4 bps × m as QLD, which is conservative for a ~1 bp-spread ETF.
  - SGOV\* uses a 0.09% expense ratio (report only).
  - Against real Alpaca SIP BIL total return over 2017-01 → 2026-07, BIL\*
    tracks within −0.008 pp/yr. Real SGOV is +0.056 pp/yr against SGOV\*
    over 2020-06 → 2026-07.
- **Arms:**
  - **S1:** BIL 0.98 while E2 is off, otherwise 0.
  - **S2_5 / S2_10:** S1, plus a residual sweep while E2 is on. BIL is kept
    in `[0.98 − w* − X, 0.98 − w*]` and reset to `max(0, 0.98 − w* − X/2)`
    on a breach, with X = 5 or 10 pp. Total target is never above 0.98.
  - **Report only:**
    - **S1_SGOV:** S1 with SGOV\* instead of BIL\*.
    - **A_OFF:** cash earns `max(0, DTB3 − 0.5 pp)` on trend-off sessions,
      with no trading.
    - **A_ALL:** the same rate on all idle cash, every session.
- **Windows:**
  - Main: 2000–2026, base and calibrated (+0.75%) financing.
  - 2017+.
  - OOS ^GSPC 1957–1999, with fills at the previous close (gated).
  - The five `e2_confirmation_v1` markets (report only).
  - Block bootstrap: 5,000 paths of 10 years, seed 20261011, blocks
    20 / 60 / 250. Path draws are identical to `signal_ensemble_v1`.
- **Runtime.** The run is offline and takes about 32 s. Two runs gave
  byte-identical `RESULTS.json` and `SUMMARY.txt`.

## Results (main window, m = 2, base financing)

| Arm | CAGR | Max DD | 2017+ CAGR | CAGR m = 1 | DD m = 1 | Turnover/yr | Bill trades | Boot20 median Δ vs C0 | Share > C0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 (production E2) | 14.67% | 49.6% | 32.44% | 14.95% | 49.2% | 5.97 | 0 | — | — |
| S1 trend-off only | 14.73% | 47.3% | 32.67% | 15.27% | 46.3% | 11.73 | 156 | **−0.076 pp** | 38.6% |
| S2_5 | 14.87% | 45.9% | 32.72% | 15.41% | 45.3% | 11.64 | 745 | +0.181 pp | 75.6% |
| **S2_10** | **14.88%** | **46.0%** | **32.73%** | **15.41%** | **45.3%** | **11.42** | **469** | **+0.181 pp** | **75.6%** |
| S1_SGOV (report) | 14.75% | 47.2% | 32.67% | 15.29% | 46.3% | 11.73 | 156 | −0.060 pp | 41.2% |
| A_OFF, DTB3 − 0.5 pp (report) | 15.19% | 46.4% | 32.86% | 15.47% | 46.1% | 5.97 | 0 | +0.610 pp | 100% |
| A_ALL, DTB3 − 0.5 pp (report) | 15.35% | 45.7% | 33.06% | 15.62% | 45.3% | 5.97 | 0 | +0.857 pp | 100% |

Calibrated financing lowers every arm by about 0.57 pp and leaves the
differences unchanged: C0 14.10%, S1 14.16%, S2_5 14.30%, S2_10 14.31%. Block
60 and block 250 give the same signs (S1 −0.09 / −0.06 pp; S2_10 +0.17 /
+0.15 pp).

**Segments (CAGR, base, m = 2):**

| Arm | 2000–08 | 2009–15 (ZIRP) | 2016–19 | 2020–21 (ZIRP) | 2022–26 |
|---|---:|---:|---:|---:|---:|
| C0 | −1.10% | 15.58% | 26.06% | 48.56% | 23.53% |
| S1 | −0.51% | 14.87% | 25.71% | 48.33% | 24.02% |
| S2_10 | −0.21% | 14.92% | 25.74% | 48.17% | 24.17% |
| A_OFF | −0.10% | 15.58% | 26.21% | 48.56% | 24.21% |

**Out of sample (calibrated, m = 2):**

| Market | Window | C0 | S1 | S2_10 | A_OFF |
|---|---|---|---|---|---|
| **^GSPC (gated)** | 1957–1999 | 9.98% / 39.0% | 11.58% / 33.4% | 11.61% / 33.4% | 11.99% / 31.3% |
| ^IXIC | 1975–1999 | 20.68% / 38.6% | 22.43% / 35.7% | 22.52% / 35.7% | 22.87% / 35.1% |
| ^GSPC (TB3MS) | 1935–1956 | 11.26% / 62.5% | 10.99% / 63.7% | 10.97% / 63.7% | 11.38% / 62.5% |
| ^N225 | 1966–2026 | 6.33% / 67.2% | 7.15% / 60.3% | 7.22% / 60.3% | 7.59% / 59.2% |
| ^GDAXI | 1989–2026 | 9.41% / 58.6% | 9.63% / 58.6% | 9.75% / 58.2% | 10.06% / 57.9% |
| ^FTSE | 1985–2026 | −0.05% / 82.7% | 0.04% / 83.8% | 0.07% / 83.7% | 0.69% / 80.2% |

Each cell is CAGR / max DD. N225, GDAXI and FTSE use US T-bills as a USD
proxy, the same proxy that market's financing used.

## Gates

T1–T5 and T7 are checked under both base and calibrated financing; the
results are identical under both.

| Gate | Rule | S1 | S2_5 | S2_10 |
|---|---|---|---|---|
| T1 | CAGR m = 2 ≥ C0 | PASS (14.73 vs 14.67) | PASS | PASS (14.88) |
| T2 | CAGR m = 1 ≥ C0 | PASS (15.27 vs 14.95) | PASS | PASS (15.41) |
| T3 | max DD m = 2 and m = 1 ≤ C0 + 0.5 pp | PASS (47.3 vs 49.6) | PASS | PASS (46.0) |
| T4 | 2017+ CAGR m = 2 ≥ C0 | PASS (32.67 vs 32.44) | PASS | PASS (32.73) |
| T5 | block-20 median of arm − C0 ≥ 0 | **FAIL (−0.076 pp)** | PASS (+0.181) | PASS (+0.181) |
| T6 | OOS ^GSPC 1957–99: CAGR ≥ C0, DD ≤ C0 + 0.5 pp | PASS (11.58 / 33.4 vs 9.98 / 39.0) | PASS | PASS (11.61 / 33.4) |
| T7 | turnover m = 2 ≤ 2 × C0 (11.94) | PASS (11.73) | PASS (11.64) | PASS (11.42) |
| | **All** | **FAIL** | PASS | **PASS** |

**Selection.** S1 fails, so the pre-registered rule picks the passing S2 arm
with the highest base m = 2 CAGR. That is S2_10 (14.88% against 14.87%), which
also makes 37% fewer bill trades than S2_5.

## Stage B on the production engine

**Setup:**

- Runner: the archived `e2_integration_v1` runner, adapted inside the
  campaign folder.
- Window 2017-01-03 → 2026-07-30, $100k.
- **Sweep shim.** A research-only `decide()` wrapper adds BIL to the
  unchanged E2 target. For S2 it reads the current BIL weight from
  `context.holdings × prices / equity` to apply the band.
- **BIL data.** Real Alpaca SIP split-adjusted BIL bars and dividends from the
  `rule_variants_v1` download. BIL is added to the arm config's equity
  universe.
- No production code was changed.
- **Reproduction check.** E2 reproduces `e2_integration_v1` exactly: NAV
  1,329,642.62 at 1x and 1,313,969.74 at 2x.
- **Checks on every run:**
  - Accounting invariants hold.
  - Maximum target gross is 0.98.
  - BIL distributions are credited: 17 in S1, 38 in S2_10.
- **Determinism.** A rerun of S2_10 at 1x gave identical hashes and an
  identical NAV path.

| Arm | Costs | Ending NAV | CAGR | Max DD | Sharpe | Turnover/yr | Fills (BIL) | BIL notional | 2017–21 | 2022–26 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| E2 (production) | 1x | 1,329,642.62 | 31.05% | 32.19% | 1.098 | 3.84 | 617 (0) | 0 | 41.05% | 20.99% |
| S1 | 1x | 1,360,867.30 | 31.37% | 31.24% | 1.106 | 5.93 | 648 (31) | 10.2M | 41.13% | 21.53% |
| S2_5 | 1x | 1,371,883.88 | 31.48% | 31.13% | 1.109 | 7.11 | 915 (298) | 16.0M | 41.15% | 21.73% |
| **S2_10** | 1x | **1,370,954.58** | **31.47%** | **31.14%** | **1.109** | 6.75 | 798 (181) | 14.3M | 41.16% | 21.70% |
| E2 (production) | 2x | 1,313,969.74 | 30.89% | 32.33% | 1.093 | 3.84 | 618 (0) | 0 | 40.89% | 20.83% |
| S1 | 2x | 1,335,041.51 | 31.11% | 31.58% | 1.099 | 5.93 | 649 (31) | 10.1M | 40.89% | 21.26% |
| S2_5 | 2x | 1,342,420.15 | 31.18% | 31.42% | 1.101 | 7.11 | 916 (298) | 15.8M | 40.86% | 21.43% |
| **S2_10** | 2x | **1,342,823.74** | **31.19%** | **31.44%** | **1.101** | 6.73 | 796 (179) | 14.0M | 40.88% | 21.42% |

S2_10 against E2:

- **1x:** +0.42 pp CAGR, −1.05 pp max DD.
- **2x:** +0.30 pp CAGR, −0.90 pp max DD.
- **2017–21 at 2x:** −0.01 pp, because near-zero yields do not cover the
  trading costs.
- **2022–26:** the whole gain, +0.59 to +0.71 pp.

## Findings beyond the gate

- **Why S1 fails.** E2 flips 5.9 times a year. Each round trip into BIL costs
  2 × 0.98 × 4 bps × m, so S1's bill turnover is 5.8/yr against a yield of
  about 0.55%/yr (25.7% off × 2.18% mean DTB3 when off).
  - The net gain is only +0.06 pp at m = 2. It turns negative in whipsaw
    years: 2010 −1.2 pp, 2011 −1.1 pp, 2016 −1.2 pp.
  - In short-block bootstraps flips are more frequent, so the median goes
    negative.
  - This confirms `rule_variants_v1`'s near-miss on VT35.
- **Why S2 beats S1 despite more bill trades.**
  - On a trend-on flip, S2 sells BIL only down to `0.98 − w* − X/2`, not to
    zero, so each round trip is smaller.
  - The residual it keeps then earns yield while QLD is vol-scaled below
    0.98. That residual is large in 2000–2002 and 2008, when QLD vol was
    high and rates were 2–6%.
  - The band keeps residual re-sizes rare: 469 bill trades in 26.6 years for
    S2_10, against the daily resizes in `paper_config_v2`.
  - Total turnover (11.4/yr) is below S1's.
- **Max DD.** The lower drawdown (49.6% → 46.0%) comes from 2000–2003: the
  bot sat in cash during most of the bear market while T-bills paid 1–6%.
  It is a rate effect, not a risk change.
- **Rates matter more than design.** The arms rank the same in every
  window, but the sign of the gain follows the T-bill rate.
  - ^GSPC 1935–56 (TB3MS 0–2%): S1 and S2 lose 0.3 pp and add 1.2 pp of DD.
  - ZIRP 2009–2015: S2_10 −0.66 pp/yr.
  - High-rate windows: +0.6 to +1.8 pp.
- **SGOV against BIL.** SGOV's lower fee is worth +0.02 pp/yr. Before
  2020-05-28, SGOV did not exist.
- **Real ETF check.** The real BIL bill leg gives S1 31.74% at m = 1, against
  31.81% with BIL\* (2017-01 → 2026-07). The synthetic overstates S1 by
  about 0.07 pp. That is BIL's small day-to-day price noise and its ex-date
  drops, which cross the band; the gate margins are larger.

## Alpaca cash sweep (context, not gated)

Suppose Alpaca's opt-in cash interest paid about `DTB3 − 0.5 pp` on idle
cash with zero trading:

| Option | Gain vs C0 | Notes |
|---|---|---|
| A_OFF (trend-off cash only) | +0.52 pp | Bootstrap median +0.61 pp; positive on 100% of paths |
| A_ALL (all idle cash) | +0.68 pp | Bootstrap median +0.86 pp; positive on 100% of paths |
| S2_10 | +0.21 pp | |

Over 2017–2026 A_OFF adds +0.43 pp, against S2_10's +0.29 pp. The broker
sweep still beats S2_10 when it pays as little as DTB3 − 2.5 pp:

| Sweep rate | A_ALL CAGR |
|---|---:|
| DTB3 − 1.5 pp | 15.09% |
| DTB3 − 2.5 pp | 14.93% |
| S2_10 (for comparison) | 14.88% |

These two rows are a post-hoc check, `posthoc_alpaca_breakeven.py`, not
pre-registered.

The two are substitutes, not complements. The ETF sweep moves cash out of
the interest-bearing balance.

Not verified here (no network): Alpaca's actual rate, eligibility, whether
the PAPER account accrues interest, and how the rate is set. Check before
LIVE. If the cash interest is available and close to DTB3 − 0.5 pp, it
dominates the ETF sweep and needs no code change.

## Production integration requirements (not done here)

1. **Strategy** (`portfolio/equity_leveraged_trend.py`).
   - **Recover the old code.** The deleted opt-in `cash_sweep_symbol` was
     added in `d31088e` and removed in `513711d`. It is in tag
     `archive/code/crypto-sleeve-2026-09`:
     - `equity_leveraged_trend.py`: the parameter at lines ~58–100 and the
       sweep in `decide()` at ~160–185;
     - `config.py`: the universe check at ~620–627;
     - its tests in `tests/test_equity_leveraged_trend.py` (~161–230) and
       `tests/test_config.py` (~287–300).
   - **Change the sizing.** The old code swept `1 − w` every session (the
     failed design). It must change to:
     - BIL = `maximum_allocation` (0.98) on `downtrend`;
     - while `uptrend`, the band rule: keep the current BIL weight inside
       `[0.98 − min(w, 0.98) − 0.10, 0.98 − min(w, 0.98)]`; otherwise target
       `max(0, 0.98 − min(w, 0.98) − 0.05)`;
     - no BIL on `insufficient_history` or `hold_symbol_unavailable`.
   - **Inputs it needs.** The allocation, passed as a parameter (the
     strategy does not see `maximum_allocation`), and the current BIL weight
     from `context.holdings`, `prices` and `equity`. That makes `decide()`
     depend on holdings; the runtime context already carries them
     (`PortfolioDecisionService.build_context`).
   - **Identity.**
     - New parameters (`cash_sweep_symbol`, `cash_sweep_band`) are left out
       of the parameter tuple when unset, so E2's digest and decisions stay
       byte-identical.
     - Strategy version 1.2.0 when set.
     - Metadata records the sweep weight, band and current weight.
2. **Universe, config and data.**
   - Add BIL to `universe.equity.symbols`, and to `decision_symbols` so the
     complete-closed-bar batch requires a current BIL bar and the price is
     available.
   - The stream goes from 3 to 4 symbols. Warm-up, the SIP split-basis
     verifier and the IEX/SIP cross-check then cover BIL.
   - Restore the config check that the sweep symbol is in the universe.
   - The configuration digest changes: new runtime session and a new soak
     window (`scripts/audit_paper_soak.py` `EXPECTED` needs the extra
     symbol).
3. **Risk overlay.**
   - `max_position_pct` 0.98 and `max_gross_exposure_pct` 0.98 already fit,
     because QLD + BIL ≤ 0.98 by construction. Keep it so; the old `1 − w`
     design relied on the overlay to cut 1.0 to 0.98.
   - 2 of 8 position slots are used.
   - The 0.02 cash buffer stays cash.
4. **Execution and settlement.**
   - On a trend-on flip the runtime sells BIL and buys QLD at the same
     open. The existing two-phase SELL → deferred BUY path must fund the QLD
     buy from the BIL sale.
   - On a LIVE **cash** account, BIL proceeds settle T+1. Buying QLD with
     unsettled proceeds and selling it before settlement risks good-faith
     violations, which are frequent with E2's ~3 round trips a year and
     whipsaw exits. Use a margin-enabled account, or keep the QLD buy within
     settled cash.
5. **Reconciliation and restart.**
   - BIL becomes a second managed, pinned symbol. Pending-target persistence
     and restart catch-up must carry two weights (the engine already
     supports multi-symbol targets).
   - The decision is idempotent given broker holdings.
   - The intent watchdog compares holdings to the target. The band emits the
     current weight, so no false "holds more than targeted" alarm.
6. **Distributions.**
   - BIL pays monthly cash distributions. The backtest credits them, and
     Stage B shows them credited.
   - On ex-dates the split-adjusted price drops about 0.3–0.4%. That stays
     inside the bands. Accrued cash is re-swept only on a band breach (S2),
     or on a > 0.5 pp drift in downtrend.
   - No signal reads BIL.
7. **Observability and tests.**
   - Update the daily summary, dashboard and `backtest_live_comparison.py`
     (which re-decides with the recorded parameters and now needs holdings).
   - Tests: band edges, the downtrend/uptrend switch, insufficient history,
     digest parity when unset, and a golden backtest.
   - Run the full suite.
8. **Before integrating, settle the Alpaca cash-interest question above.**
   It may make this unnecessary.

## Caveats

- **Small, rate-dependent edge.** The main-window gain (+0.21 pp) and the
  bootstrap median (+0.18 pp) are small. They depend on the T-bill level and
  on trading costs; m = 2 (8 bps) is conservative for BIL's ~1 bp spread.
- **S2_5 vs S2_10.** The two differ by 0.01 pp. Selection picks the larger
  band only because its CAGR is higher; it also trades less.
- **Rate proxies.** DTB3 is a discount rate and is used as the yield, as in
  the financing convention. Non-US markets use US bills.
- **Stage B data substitute.** QQQ, SPY and QLD come from the
  `e2_integration_v1` substitute file, as in that campaign. BIL comes from
  the `rule_variants_v1` Alpaca SIP download.

## Evidence

Evidence was committed on this branch in three steps; the last one holds
everything and is the commit to tag:

- **Pre-registration (`f323a4c`).** `PREREGISTRATION.md`, `screen.py`, the
  vendored engine, `DATA_MANIFEST.json` and
  `data/historical/tbill_sweep_v1_*.csv` (byte copies of tagged files).
- **Screen results (`291bb33`).** `RESULTS.json` and `SUMMARY.txt`.
- **Evidence (`8702583`).** Adds `posthoc_alpaca_breakeven.py` /
  `POSTHOC.json` and the Stage B runner, shim, configs, runs and
  `STAGE_B_RESULTS.json`.

The setup steps are in the docstring of `adapt_harness.py`. Everything under
`research/tbill_sweep_v1/` and `data/historical/tbill_sweep_v1_*` was removed
from the tree in the following commit. It is to be preserved in tag
`archive/research/tbill-sweep-v1-2026-10` (on `8702583`).

## Decision

S2_10 is the pre-registered winner and Stage B confirms it: +0.30 to
+0.42 pp CAGR and −0.9 to −1.05 pp max DD on the production engine.
Production E2 is unchanged.

Integrating S2_10 is a separate step, with the requirements above. Do it
only if Alpaca's idle-cash interest turns out to be unavailable, or well
below DTB3 − 2.5 pp, for the account that will run LIVE.
