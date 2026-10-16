# research/plugins_b2 — IB02/IB06/IB10/IB14/IB18/IB22/IB26/IB31/IB35 plugins for `batch_screen`

Nine ideas of `docs/research/idea_bank_v1.json`, written as `research/batch_screen` plugins on top
of `research/fast_engine`.  **No stage was run on this branch and no performance number was
computed**: the plugins, the data they need and their extra-data entries are committed for the
orchestrator's batch (`run --batch <name> --ideas IB02,IB06,IB10,IB14,IB18,IB22,IB26,IB31,IB35`).
27 arms: 24 simulatable, 3 registered `needs_kernel` (never simulated, counted by the ledger).

The *smoke* exits non-zero here by design: the framework's own tally counts a `needs_kernel` arm as
a failure, and the four such arms of this batch (IB26's three, IB35/C) are declared, not broken —
no arm reports `error`.  This branch computes no result: the idea bank plus these committed
plugins, frozen before any arm runs, are the pre-registration the registry's commit gate pins.

## Files

| path | what |
|---|---|
| `research/ideas/IB02.py` … `IB35.py` | the nine plugins: `ARMS` + a vectorised `targets()` |
| `research/plugins_b2/build_extra.py` | builds this branch's extra series, merges the registry entries, writes the manifest, runs `extra.verify()` |
| `research/plugins_b2/check_plugins.py` | mechanical unit checks of every arm's signal (no metric; run it after any edit) |
| `research/plugins_b2/DATA_MANIFEST.json` | every added file: source URL, range, rows, bytes, SHA-256, plus the Yahoo cross-check of the OHLC the ratios are derived from |
| `data/historical/batch_screen_plugins_b2_hl_*.csv` | the six high/low ratio series (see below) |
| `data/historical/batch_screen_v1_{baa,aaa}.csv` | FRED monthly Moody's Baa/Aaa yields |
| `research/batch_screen/EXTRA_DATA.json` | 14 new series, 4 new keys — additions only, byte-identical otherwise |

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.batch_screen.extra --verify            # hashes and ranges
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas IB02,IB06,IB10,IB14,IB18,IB22,IB26,IB31,IB35
PYTHONPATH=. $R -m research.plugins_b2.check_plugins               # 61 mechanics checks, all pass
PYTHONPATH=. $R -m research.plugins_b2.build_extra [--offline]     # rebuild the data + entries
```

## The arms (rules exactly as the bank states them; parameters frozen in each `spec`)

| idea | arms | what changes on top of production K | `NotApplicable` windows |
|---|---|---|---|
| IB02 | A/B/C | the *level* vote's location statistic: MED200 / MED100 in the 2-of-3, or a 3-of-4 `{SMA200, MED200, TSM252, DON126}` | none |
| IB06 | A/B/C | early-path entry gates (`TR > 1.5·ATR20`, `range7 < 0.5·ATR20`) and a 2.5·ATR chandelier exit that zeroes the on-state | `GSPC_1957_1999` |
| IB10 | A/B/C | the vol-target input: `σ_atr = gearing·0.63·(ATR20/close)·√252` alone / blended 50-50 with vol20 / `max` with vol20 | `GSPC_1957_1999` |
| IB14 | A/B/C | credit-spread momentum: veto / halve above a 60-session `BAA10Y` change of +0.30 / +0.15 pp, or as a 4th vote | `M3`, `M4`, `M5` |
| IB18 | A/B/C | calendar tilt: `×0.75` in every expiration week / only the quarterly ones, or `×0.85` outside them | none |
| IB22 | A/B/C | early-entry gate on the same-session European move (and the previous Nikkei session), or a `×0.75` risk cut after a −2 % European day | `GSPC_1957_1999`, `M1`, `M2` |
| IB26 | A/B/C | off-state leg = IEF chosen by the curve slope — **`NEEDS_KERNEL`** | all (never simulated) |
| IB31 | A/B/C | the early leg's exit: a vol spike at 1.5× / 2.0× the trailing 252-session median of vol20, or the union with production's SMA50 break | none |
| IB35 | A, B | the vol multiplier is sticky: it updates only when vol20 is outside its trailing `[P25,P75]` / `[P10,P90]`; **arm C (3 pp fixed band) is `NEEDS_KERNEL`** | none |

Every arm keeps production's E2 vote, early re-entry state machine, `min(1, 0.35/vol20)` sizing,
0.98 cap, `leg = 2x` and the **K execution layer** (`rule = "K"`), except that IB02/IB14-C replace
the vote (which is the arm) and IB35's A/B make the multiplier sticky (which is the arm).

## Interpretation decisions (ambiguous spec → most literal reading, documented at each module's top)

* **IB10's gearing.**  Production's sizing input is the *traded* instrument's vol (`data.vol` = the
  synthetic 2x leg, mean 0.456 on the main window), not the underlying's (QQQ, 0.228), and the
  panel exposes the underlying's high/low.  The ATR estimate is therefore lifted to the traded leg
  by the leg's gearing (`2x → 2`, read from the panel), the published `range_vol_v1`–`v3`
  construction ("Parkinson P20 × 2 (2x fund)") — IB10's own closest-tested family.  Frozen per arm
  as `gearing`.  Without it, `σ_atr` averages ~0.22 and arm C's `max(σ_atr, vol20)` *is* vol20,
  i.e. production's sizing exactly, and arm B's blend is dominated by vol20 the same way: the arm
  would be a no-op.  Arm A's mean vt is 0.849 vs production's 0.812 — same scale, as the bank's
  "medium-low" expectation implies.
* **IB06/IB10 high/low.**  The framework passes the underlying's high/low only on the 21 ETF
  windows; the main and OOS windows are `high=None`, and the bank lists no new download for these
  ideas.  The two families of windows therefore read this branch's registered `hl_high`/`hl_low`
  keys — **high/close and low/close ratios** derived from the same repo files the panels' prices
  come from (`stress_robustness_v1_daily.csv` QQQ; `ewma_band_v1_index.csv` `^IXIC/^GSPC/^N225/
  ^GDAXI/^FTSE`), which makes them session-for-session aligned with `data.price` by construction.
  A ratio, not a level, because a bootstrap chunk's price is a reconstructed index (it starts at
  1) while the extra series replays the drawn source session's own value.  The ETF windows use the
  panel's own high/low.  `GSPC_1957_1999` raises `NotApplicable`: it is built close-only
  (`ewma_band_v1_gspc.csv`, `h == l == c` on 100 % of its rows) and the map falls back to the QQQ
  series, which does not cover its dates.  Disclosed degeneracy on the *evaluable* OOS windows:
  `h == l == c` on 100 % of `^GSPC` 1927-1956 (M2), 47 % of `^IXIC` 1971-1999 (M1) and 38 % of
  `^N225` (M3) — there TR degenerates to |Δclose|, IB10's own disclosed caveat.
* **IB14's series and scope.**  Daily `baa10y` (ffill'ed, 1986+) on `main`, both bootstrap
  families and the ETF windows; the monthly `BAA − AAA` (this branch's `baa_m`/`aaa_m`, FRED,
  1919+) used only from the first session of the following month on the three US OOS windows, i.e.
  the series is substituted and the arm's own 60-session change and thresholds kept.  `M3`–`M5`
  raise `NotApplicable`: the idea declares its evaluable set as those three US windows
  (credit_vote_v1's convention — the reduced 3-window G7 its note refers to).  Data exists on
  M4/M5 (97 %/93 %), so this is declared scope, not a missing input — flagged so the reduced G7 is
  read as pre-registered rather than accidental.  A `NaN` change is neutral (no veto/halving, a
  "no stress" vote).
* **IB18/IB14 calendars in chunks.**  Both read the panel column calendar through
  `extra_days`/`extra_index` (the drawn source sessions), the framework's own extra-series
  convention, rather than `data.days` — see finding 1 below.
* **IB22 missing values are neutral** (no entry block, no risk cut) so the arm degenerates to
  production where a cross-market series has no value.  On `M3` (61 % coverage) and `M5` (89 %)
  that matters.  Disclosed: on `M3` the same-date DAX/FTSE closes happen *after* the Nikkei close
  (so the "lead" is not strictly causal there), and on `M4`/`M5` the European series includes the
  window's own instrument (a same-close cross-market vote, not a lead-lag).  The main window, where
  the timing argument holds, is unaffected.
* **IB31** copies `fe_signals.r2_state`'s loop verbatim with the exit condition substituted; the
  check script proves the copy with production's exit reproduces `fe_signals.r2_state` bit for bit
  (same for IB06's and IB22's gated copies with a trivial gate).
* **IB35's sticky multiplier** updates production's `min(1, 0.35/vol20)` only on sessions whose
  vol20 is outside the trailing percentile band and carries the previous value elsewhere; the vote
  and the early path are untouched (a state change must still fill).  Before 252 sessions the
  percentiles are undefined and the multiplier updates every session, i.e. the warm-up is
  production's.  Measured on the main window: 2 331 vs production's 3 795 multiplier changes
  (P25/P75), 1 375 (P10/P90).
* **IB35/C and IB26** are `needs_kernel` — see findings 2 and 3.

## Data added (see `DATA_MANIFEST.json` for the hashes, and `EXTRA_DATA.json` for the registry)

| file | source | content | range |
|---|---|---|---|
| `batch_screen_plugins_b2_hl_qqq.csv` | `stress_robustness_v1_daily.csv` (Yahoo chart API provenance) | QQQ `high/close`, `low/close` | 1999-03-10 → 2026-09-29 |
| `batch_screen_plugins_b2_hl_{ixic,gspc,n225,gdaxi,ftse}.csv` | `ewma_band_v1_index.csv` (Yahoo chart API) | that index's `high/close`, `low/close` | 1971-02-05 → 1999-12-31 / 1927-12-30 → 1956-12-31 / 1965-01-05 → 2026-09-29 / 1987-12-30 → / 1984-01-03 → |
| `batch_screen_v1_baa.csv`, `batch_screen_v1_aaa.csv` | FRED CSV (`id=BAA`, `id=AAA`) | monthly Moody's Baa / Aaa yield | 1919-01-01 → 2026-09-01 |

The Yahoo cross-check in the manifest re-fetches the raw OHLC of all six symbols and reports the
maximum relative difference on the shared sessions: 0.0 for `close`/`high`/`low` on QQQ, ^IXIC,
^GDAXI and ^FTSE (one QQQ `high` at 1.7e-07 = the repo file's own float32 rounding), 0.0 on the
13 950 sessions ^N225 shares, and no shared sessions for ^GSPC 1927-1956 (Yahoo does not serve that
range), where the ratios rest on the repo file alone.

## Findings for the framework owners (reported, not edited)

1. **`MarketData.days` does not have the panel's width in a bootstrap chunk** (and
   `year`/`month` inherit it, since `__post_init__` builds them from `days`).  The plugin
   docstrings say `days (T,)`; in the clean bootstrap `days` is the chunk's *synthesised*
   2015-2026 label calendar and **one column shorter than the market arrays** (the artificial base
   column 0 has no label — the same asymmetry `extra.aligned` compensates for with
   `_extra_index`), and in the OOS pooled bootstrap it is the *whole market's* session list
   (e.g. 5 755 entries against a 2 777-column chunk), not the chunk's calendar at all.  A plugin
   that reads `data.days` (or `data.year`/`data.month`) therefore either crashes on a chunk or
   silently uses the wrong calendar; the workaround used here is to read the drawn source sessions
   through `extra_days`/`extra_index`.  Making `days` the `(T,)` labels of the columns (base column
   included) would remove the trap.
2. **`data.high`/`data.low` are not exposed where the inputs exist.**  `fe_data.load()` and
   `load_market()` read the repo files' high/low and drop them, so the main window and all six OOS
   windows offer `high=None` although `stress_robustness_v1_daily.csv` and
   `ewma_band_v1_index.csv` carry them.  Every true-range idea (IB06, IB10, and IB03/IB23-style
   follow-ups) therefore needs a derived extra series, which cannot represent the *leg's* bars (only
   the underlying's) — the gearing question IB10 runs into.
3. **A fixed band width is unreachable from a plugin.**  `rules.register_rule(name, band, usd_rule,
   defer)` re-combines three booleans, while the base resize band is the compiled constant
   `fe_sim.C0_BAND` (`fe_config.BAND = 0.5 pp`).  A published family — `ewma_band_v1`'s 2.5/5/10 pp
   output bands, and IB35's arm C — is therefore only expressible with a kernel copy, and a plugin
   must not monkey-patch `fe_sim.C0_BAND` (the arm's process shares the kernel with the batch's
   control).  `NEEDS_KERNEL` is the honest answer, and this is why IB35/C is registered that way.
