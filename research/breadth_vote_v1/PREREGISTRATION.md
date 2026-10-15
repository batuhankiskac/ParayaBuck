# breadth_vote_v1 — pre-registration (2026-10-15)

Idea **I1** of `docs/research/idea_survey_v3.md` ("breadth / cross-index trend vote"), run with
exactly the four arms B1–B4 that survey specifies. Written and committed (with `screen.py`,
`tax_engine.py`, `fetch_panel_data.py` and `DATA_MANIFEST.json`) **before any non-control arm was
simulated on any real data**. The only computations on real data before this commit are
`screen.py --harness` (C0 = production R2 only: the R2/E2 anchor reproduction, the panel-splice
checks, the engine equality check) and the data fetch. Every arm's code path was exercised on
synthetic random-walk prices in `screen.py --smoke` (which asserts that each arm's target series is
finite, differs from C0's, and that C0's target series equals a verbatim copy of
`fast_reentry_v1.arm_signals`' R2 branch).

## 1. Question

Production R2 reads three trend votes — close > SMA200, 252-session price return > 0, close >
midpoint of the 126-session close max/min — on the **held index** (QQQ) and goes flat when its
2-of-3 majority breaks. Does replacing/extending that majority with a **panel** of three
economically different equity indices reduce the number of false exits (and therefore the number
of FIFO lots consumed and the TRY gain realised per year) without giving up return?

Tested end to end, pre-tax (G1–G9) and after Turkish tax at $100k on three windows plus two clean
bootstraps. This campaign changes no `src/`, `config/`, `tests/`, `scripts/` or `benchmarks/`.

## 2. Control C0 = production R2

`equity_leveraged_trend_v1` 1.2.0 = production E2 plus the early re-entry path:

- **E2 vote** on the QQQ split-adjusted price close: at least 2 of {close > SMA200, 252-session
  price return > 0, close > the midpoint of the max/min of the 126 closes ending at t}.
- **Sizing**: `vt(vol) = min(1, 0.35 / vol20)` on the synthetic QLD\* leg, total weight capped at
  0.98.
- **Early re-entry** while E2 is off: half weight (`0.5 x vt`) while QQQ close > SMA50 **and** QQQ
  vol20 < its own trailing 252-session median **and** QQQ close > QQQ close 20 sessions ago; exit
  the early position below SMA50; E2's own vote overrides (it ends the early leg).
- Decide at the completed close, fill at the **next open**, 0.5 pp resize band, idle cash earns
  zero, cost 4 bps × m per unit of traded notional.

**Harness anchors this study must reproduce** (`--harness`, main window 2000-03-09 → 2026-09-29):
R2 15.48 % base / 14.90 % calibrated CAGR at m = 2, 15.75 % base at m = 1, max DD 46.76 %;
the pure-E2 series (the survey's old C0) 14.67 % / 14.10 % CAGR, max DD 49.56 %; after tax at
$100k (progressive, liquidated) 10.16 / 14.71 / 22.89 % on the 2000 / 2010 / 2017 windows;
B1-recent / B2 median after-tax CAGR 3.5155 % / 3.9678 %.

## 3. The panel (fixed for every arm and every instrument)

**Panel(X) = {X, the SPY member, the IWM member}**, each member carrying the three production
votes on **its own closes**:

- **SPY member** = SPY's split-adjusted close (`range_vol_v1_spy.csv`, from 1993-01-29) from
  1993-01-29 onwards; before that, `^GSPC` x `c_spy`, with
  `c_spy = SPY(1993-01-29) / ^GSPC(1993-01-29)`, so the spliced series is level-continuous and the
  junction day's return is the index's own.
- **IWM member** = IWM's split-adjusted close (`range_vol_v1_iwm.csv`, from 2000-05-26) from
  2000-05-26 onwards; before that, `^RUT` x `c_iwm`, `c_iwm = IWM(2000-05-26) / ^RUT(2000-05-26)`.
  (This is the survey's "IWM = ^RUT before 2000-05-26".)
- A member's closes are aligned to the window's own calendar by forward-filling its last available
  close; a member with fewer than 252 sessions of history reads "vote off" (every vote is
  NaN → False).

In the main window X = QQQ, so the panel is exactly the survey's {QQQ, SPY, IWM}. **For a non-QQQ
instrument (the 21-ETF gate, the OOS windows) the panel is {the instrument, SPY member, IWM
member}** — the pre-registered rule this task asks for. No de-duplication: for X = SPY the panel is
literally {SPY, SPY, IWM} (so B1's vote is the control's and B1's OOS/instrument delta there is
0 by construction), and for the `^GSPC`-instrument OOS windows the panel would collapse to
{^GSPC, ^GSPC, ^RUT} — see §6, those windows are declared not applicable.

## 4. Arms (four; every parameter fixed)

Common to all arms: the panel above; the production sizing and 0.5 pp band; decide at the close,
fill at the next open; the **early re-entry path unchanged** — the same four production conditions
on **the instrument's own closes** (QQQ in the main window), active only while the arm's main vote
is off, half weight, exit below SMA50.

| Arm | Main on-state (replaces E2's vote) |
|---|---|
| **B1** | The three votes read on the **SPY member** instead of on the instrument; 2-of-3 majority. QLD stays the instrument. |
| **B2** | Breadth-confirmed **exit**, latched: the state turns on exactly when the instrument's own majority holds (re-entry unchanged), and turns off only when the instrument's majority is broken **and** at least 1 of the other 2 panel members' majorities is also broken (i.e. ≥ 2 of the 3 panel members broken). |
| **B3** | Breadth-**gated** state: on iff the instrument's own majority holds **and** ≥ 2 of the 3 panel members' majorities hold (with X one of the three, this is `inst_maj AND (spy_maj OR iwm_maj)`). |
| **B4** | 5-vote majority: votes = {instrument SMA200, instrument TSM252, instrument DON126, SPY-member TSM252, IWM-member TSM252}; on iff ≥ 3 of 5 hold. |

For B2/B3/B4 the *broken* condition is the exact complement of the majority (`≤ 1 of 3` holds).
B2's latch starts **off** at the first session of the window and evolves over the whole series
(warm-up included). Turnover is not re-specified: every arm keeps C0's 0.5 pp band, so G9's limit is
the **2×** arm limit of the shared protocol, not ewma_band_v1's band-arm 1× limit.

## 5. Data (fixed)

- Main window: `data/historical/stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY bars and DTB3);
  synthetic QLD\* = daily-reset 2x on QQQ total return, 0.95 %/yr expense, financing DTB3 (base) or
  DTB3 + 0.75 % (calibrated).
- Panel: `range_vol_v1_{spy,iwm}.csv`, `ewma_band_v1_index.csv` (^GSPC 1927–1956, ^IXIC 1971–1999,
  ^N225, ^GDAXI, ^FTSE), and two **network-fetched** index files (see §6):
  `breadth_vote_v1_rut.csv` (^RUT 1987-09-10 →, Yahoo chart API) and `breadth_vote_v1_gspc.csv`
  (^GSPC 1970-01-02 →, Yahoo chart API). URLs, date ranges and SHA-256 in `DATA_MANIFEST.json`.
- The ^GSPC file is needed because the repository's index file holds ^GSPC only for 1927–1956 and
  ^IXIC only for 1971–1999; neither spans the reduced OOS windows.
- After tax: `after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (CBRT USD buying rate, Yİ-ÜFE) through
  `tax_engine.py`.
- 21 instruments: the `range_vol_v1/v2_*.csv` ETF files (EWC excluded by range_vol_v2's fixed
  quality rule).
- **No other network access.** All SHA-256 in `DATA_MANIFEST.json`.

## 6. Out-of-sample windows (reduced set — pre-registered reduction)

The panel needs peers with free long daily history. ^RUT starts 1987-09-10 and the fetched ^GSPC
1970-01-02, so **every OOS window's date array begins 1987-09-10 and the first measured session is
its 254th session** (`START = 253`; warm-up is not measured). The four windows are:

| Key | Instrument | Window (measured) | Panel members |
|---|---|---|---|
| **U1** | ^IXIC | 1988-09-09 → 1999-12-31 | ^GSPC→SPY member, ^RUT→IWM member |
| **U2** | ^N225 | 1988-09-21 → 2026-09-29 | as above |
| **U3** | ^GDAXI | 1989-01-03 → 2026-09-29 | as above |
| **U4** | ^FTSE | 1988-09-12 → 2026-09-29 | as above |

(U3 starts later only because `ewma_band_v1_index.csv`'s ^GDAXI series begins 1987-12-30; the
others begin at the panel-first date 1987-09-10. Measured years: 11.31 / 38.02 / 37.74 / 38.05.)

Each window: the **control** is production R2 with E2 on the window's own index closes and its own
2x leg (calibrated financing `y + 0.75 %`, m = 2), exactly as `fast_reentry_v1.oos_window`; the arm
reads the panel above. Turnover is reported, not gated, in the OOS stage.

**Declared NOT APPLICABLE** (with reason): `^GSPC 1957-1999` and `e2_confirmation_v1`'s
`M2 ^GSPC 1934-1956`. Their instrument *is* the SPY member, so the panel collapses to
{^GSPC, ^GSPC, ^RUT}, and in both windows ^RUT does not exist (it starts 1987-09-10). Running them
would mean inventing a second panel rule after seeing which rule is convenient, which this
pre-registration refuses. Consequently **G7 and G8 are evaluated on the reduced four-window set**
(with the same thresholds applied to 4 instead of 6 windows: ≥ 3 of 4 rather than ≥ 5 of 6), and
`RESULTS.json` records the reduction and the reason. The reduced gate is the pre-registered
"reduced gate" the task requests; U1 (1988–1999) is the long-horizon, fully independent window.

## 7. Gates (all must pass; "both" = base and calibrated financing)

- **G1** (m = 2, both): CAGR ≥ C0, **or** (Calmar ≥ 1.1 × C0 and CAGR ≥ C0 − 0.5 pp).
- **G2** (m = 2, both): max DD ≤ C0 + 1 pp.
- **G3** (m = 2, both): 2017-01-03 → end CAGR ≥ C0 − 1 pp.
- **G4** (m = 2, base): CAGR > C0 in ≥ 2 of 3 sub-periods P1 2000-03-09 → 2008-12-31,
  P2 2009 → 2016, P3 2017 → end.
- **G5** (both): main block-20 stationary bootstrap, seed 20261011, 5,000 ten-year paths, draws
  identical to `signal_ensemble_v1`/`fast_reentry_v1` (chunk order, 3-session back-extension), m = 2;
  median over paths of (arm − C0) CAGR ≥ 0.
- **G6** (m = 1, both): G1 at 1× costs.
- **G7** (reduced OOS set U1–U4, calibrated, m = 2): median over the 4 of (arm − C0) CAGR ≥ 0,
  **and** arm CAGR ≥ C0 − 0.5 pp in ≥ 3 of 4, **and** max DD ≤ C0 + 3 pp in ≥ 3 of 4 and never
  > C0 + 8 pp.
- **G8** (reduced OOS pooled bootstrap): block 20, seed 20261011, one generator over U1..U4 in
  order, 2,000 paths × (252 warm-up + 2,520) per window, m = 2, calibrated: pooled median over
  8,000 paths of (arm − C0) CAGR ≥ 0.
- **G9** (turnover, m = 2, base): turnover/yr ≤ **2 ×** C0's.
- **AT** (after tax): progressive Turkish regime, FIFO, Yİ-ÜFE indexation, liquidated at the end,
  $100k: arm after-tax CAGR **>** C0's in **all three** windows (2000-03-09, 2010-01-01, 2017-01-03
  → 2026-09-29).
- **B1** and **B2**: clean after-tax bootstraps on the fixed real **2015-09-01 → 2026-09-30**
  calendar (market returns resampled, block 20, seed 20261011, 1,000 paths, $100k): B1 = progressive,
  B2 = flat 20 % with loss carry-forward. Pass iff median Δ(arm − C0) ≥ **0.0 pp** **and** share of
  paths with Δ > 0 ≥ **55 %**.
- **F3** (21 instruments): the ETF stage above; pass iff the share of the 21 instruments with
  Δmax DD ≤ **+3 pp** is ≥ **0.80**.

## 8. Selection rule (fixed before any result)

Among arms passing **all** gates (G1–G9, AT, B1, B2, F3), the selected arm is the one with the
highest after-tax liquidated CAGR at $100k on the 2000-03-09 window (tie: highest base m = 2 CAGR).
If none passes, the study's verdict is "no arm"; production R2 is unchanged and no Stage B is run.
No parameter, arm, window or threshold is changed after any result is seen. Any bug fix is reported
in an `## Amendments` section of `RESULTS.json`'s companion doc, never by editing §4–§8.

## 9. Report only (not gated)

Per arm: Sharpe, the three sub-periods, the 2017+ window, time in market, round trips/yr,
**sells/yr** (exits + downward resizes), turnover/yr split into entries+exits and resizes, for the
full window and 2017+; bootstrap medians and turnover; the after-tax rows at $100k with **sells/yr
and after-tax turnover/yr per arm**; the 21-instrument table; the m = 4 cost stress; per-instrument
`F3` detail.

## 10. Caveats known now

- **Breadth is ~0.9 correlated with QQQ**: an arm can be pure lag, and the 2000–02 and 2008 exits
  can only arrive later (deeper DD). `trend_robustness_v2` M1/M2 showed the cost of splitting on a
  second index.
- The 21-instrument gate is hostile (`relstrength_v1` lost 1.2–4.1 pp on 4/4 arms; `fast_reentry_v2`
  nearly failed its instrument gate) and the survey's own expected effect is only +0.1…+0.8 pp.
- **G4 (2017+) is the single most likely gate to fail**, since breadth confirmation delays every
  re-entry.
- For a **non-US instrument** the panel members are nonetheless US benchmarks (SPY/IWM), because no
  free long daily peer history exists for ^N225/^GDAXI/^FTSE peers outside those three indices
  themselves. The U2–U4 windows therefore test "does a US-breadth filter help this foreign index?",
  a weaker question than U1's; this is a documented limitation of the reduced OOS set, not a result.
- After-tax deltas below ~0.1 pp are inside modelling uncertainty (Yİ-ÜFE month convention, bracket
  deflation by a not-yet-published October index); not tax advice.
- The main window's synthetic QLD\* convention (and vol sizing on the synthetic leg) is the
  harness's, so a promoted arm would have to be re-run on QLD's real bars first
  (`instrument_cost_v1` precedent).
