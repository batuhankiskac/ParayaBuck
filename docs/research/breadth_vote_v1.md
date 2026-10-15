# breadth_vote_v1 — breadth / cross-index trend vote on top of R2

Date: 2026-10-15. Branch `research/breadth_vote_v1`. Idea **I1** of
[`idea_survey_v3.md`](idea_survey_v3.md) §5, with exactly the four arms B1–B4 that survey
pre-specifies. Code, pre-registration, results and diagnostics (`research/breadth_vote_v1/`) are not on `main`;
they are preserved in tag `archive/research/breadth-vote-v1-2026-10`.

## 1. Verdict

**No arm passes. Production R2 is unchanged; nothing goes to Stage B.**

The gate set is hostile to this idea in a way the survey predicted and the data confirms:

| arm | idea | closest miss |
|---|---|---|
| **B4** | 5-vote majority (QQQ SMA/TSM/DON + SPY TSM + IWM TSM, ≥ 3 of 5) | the only arm that improves pre-tax return (+0.17 pp CAGR, DD −0.31 pp, clean bootstrap **+0.74 pp**) — and it still fails G3 (2017+ −2.08 pp), G4, G7, AT (2017 window −1.00 pp) and F3. |
| **B2** | latched breadth-confirmed exit | pre-tax ≈ C0 (15.41 % vs 15.48 %), fewer trades (5.16 vs 5.99 turn/yr), bootstrap +0.36 pp, and **after tax +0.64 pp in the 2010 window** — but −0.18 pp in 2000 and *identical* to R2 in 2017 (the latch changes nothing there), plus worse DD (+1.7 pp) at m = 2. |
| **B3** | breadth-gated state | −0.74 pp CAGR, +0.68 pp DD, bootstrap −0.69 pp: extra exits, not fewer. Its 21-ETF DD record is the *best* of the wave (20/21 within +3 pp) but the return is gone. |
| **B1** | read the votes on SPY | decisively worse: −1.79 pp CAGR, +7.3 pp DD, bootstrap −0.28 pp, and after tax −1.6 / −1.0 / **−6.6 pp**. On foreign instruments it is catastrophic (^N225 DD 91.6 % vs 65.0 %). |

The mechanism the survey hoped for (fewer confirmed exits ⇒ fewer FIFO lots consumed ⇒ smaller
realised TRY gain) **does not exist at a useful size**. Realised gain is resizes, not exits
(§6), and the arms that do cut round trips (B2: 78 → 66, B4: 78 → 63 in 26.5 years) leave the
trim count and the tax bill essentially unchanged (B2 2000-window tax 740 k vs R2 766 k USD).

## 2. Method

Control **C0 = production R2** (`equity_leveraged_trend_v1` 1.2.0: E2's 2-of-3 vote on the QQQ
price close, `min(1, 0.35/vol20)` sizing capped at 0.98, and the half-weight SMA50/vol-median/
20-session-momentum early re-entry leg while E2 is off). Panel
**Panel(X) = {X, SPY member, IWM member}**, each member carrying the three production votes on
its own closes; SPY member = SPY from 1993-01-29 else `^GSPC x c`, IWM member = IWM from
2000-05-26 else `^RUT x c` (level-continuous splices, the survey's "IWM = ^RUT before
2000-05-26"). Main window X = QQQ; the ETF/instrument stage uses {X, SPY, IWM}; the OOS stage
uses the index members. Decide at the close, fill at the next open, 0.5 pp band, 4 bps × m.
Every parameter, the gate list, the reduced OOS set and the selection rule were committed in
`PREREGISTRATION.md` (commit `5bebf8d6`) **before any non-control arm was simulated on real
data**.

Fidelity (all asserted in `screen.py --run`, `RESULTS.json`):

- C0 reproduces production R2 to ≤ 0.00005 pp at m = 1/2 × base/calibrated (CAGR, max DD,
  turnover) and the pure-E2 anchor exactly; panel splices exact; the account engine equals the
  reference band simulator bit for bit.
- C0's target series equals a verbatim copy of `fast_reentry_v1`'s R2 branch (smoke test).
- `^RUT` and `^GSPC` (the two network-fetched files: URLs, date ranges, SHA-256 in
  `DATA_MANIFEST.json`) are used only through the pre-registered splices.
- Both bootstraps reproduce the published R2 anchors exactly: G5 pre-tax medians 7.8856 /
  7.4346 % (fast_reentry_v1) and B1/B2 after-tax 3.5155 / 3.9678 % at $100k (tax_harvest_v2),
  including the pure-E2 anchors 3.079 / 3.5201 %.
- `RESULTS.json` is byte-identical across two consecutive `--run` invocations
  (sha256 `e15ec6f0be383ed39e58c914aff3e83eb5bdf5f148acc6d1d13d4c01382092af`).

Stages: pre-tax main window (base/calibrated, m = 1/2/4), pre-tax block-20 bootstrap (5,000
paths), three after-tax windows at $100k (progressive, liquidated) + m = 4 stress, two clean
after-tax bootstraps (1,000 paths, block 20, real 2015-09 → 2026-09 TRY/Yİ-ÜFE calendar), four
reduced OOS windows (calibrated, m = 2), the pooled OOS bootstrap (8,000 paths), and the 21
quality-passing ETFs.

## 3. Pre-tax main window (2000-03-09 → 2026-09-29, 6,679 sessions)

| arm | CAGR base | max DD base | Calmar | 2017+ | P1 | P2 | P3 | turn/yr | boot20 Δ | m=1 CAGR | CAGR cal | DD cal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **C0** | 15.48 % | 46.76 % | 0.331 | 32.71 % | −0.06 % | 14.30 % | 32.71 % | 5.99 | — | 15.75 % | 14.90 % | 46.88 % |
| B1 | 13.69 % | 54.09 % | 0.253 | 24.45 % | −2.81 % | 21.03 % | 24.45 % | 7.39 | −0.275 pp | 14.03 % | 13.12 % | 54.20 % |
| B2 | 15.41 % | 48.46 % | 0.318 | 32.71 % | −1.85 % | 16.37 % | 32.71 % | 5.16 | +0.359 pp | 15.65 % | 14.82 % | 48.59 % |
| B3 | 14.74 % | 47.44 % | 0.311 | 28.15 % | −0.41 % | 17.23 % | 28.15 % | 7.61 | −0.692 pp | 15.09 % | 14.19 % | 47.54 % |
| B4 | **15.65 %** | **46.45 %** | 0.337 | 30.63 % | −1.17 % | 18.57 % | 30.63 % | 5.49 | **+0.735 pp** | 15.91 % | 15.06 % | 46.59 % |

Trades (base, m = 2; sells/yr = exits + downward resizes — the Turkish tax base):

| arm | round trips/yr | resizes/yr | **sells/yr** | turnover/yr | 2017+ round trips | 2017+ sells/yr | 2017+ turn/yr |
|---|---|---|---|---|---|---|---|
| C0 | 2.97 | 55.95 | 29.56 | 5.99 | 1.54 | 34.27 | 4.02 |
| B1 | 4.14 | 52.45 | 28.46 | 7.39 | 2.98 | 33.86 | 6.14 |
| B2 | 2.52 | 56.40 | 29.44 | 5.16 | 1.54 | 34.27 | 4.02 |
| B3 | 4.03 | 50.45 | 27.45 | 7.61 | 2.26 | 32.73 | 5.20 |
| B4 | 2.60 | 61.78 | 32.27 | 5.49 | 2.15 | 35.29 | 4.89 |

## 4. After tax (Turkish resident, progressive 2026 tariff, Yİ-ÜFE indexation, FIFO, liquidated at $100k)

| arm | 2000 window | Δ vs C0 | sells/yr | turn/yr | 2010 window | Δ | 2017 window | Δ | tax USD (2000) |
|---|---|---|---|---|---|---|---|---|---|
| **C0** | 10.16 % | — | 30.7 | 6.00 | 14.71 % | — | 22.89 % | — | 766,160 |
| B1 | 8.53 % | −1.63 pp | 29.6 | 7.41 | 13.68 % | −1.03 pp | 16.34 % | −6.55 pp | 555,752 |
| B2 | 9.98 % | −0.18 pp | 30.6 | 5.17 | **15.35 %** | **+0.64 pp** | 22.89 % | 0.00 pp | 740,098 |
| B3 | 9.63 % | −0.53 pp | 28.7 | 7.62 | 14.05 % | −0.66 pp | 19.48 % | −3.41 pp | 680,505 |
| B4 | **10.48 %** | **+0.32 pp** | 33.5 | 5.50 | **16.87 %** | **+2.16 pp** | 21.89 % | −1.00 pp | 756,485 |

AT gate = arm > C0 in **all three** windows: B1/B3 fail 3/3, B2 fails 2000 (−0.18 pp) and ties
2017 (exactly equal, i.e. not >), B4 fails 2017 (−1.00 pp). Dipping into the trade path: B2's
2017 window is *identical* to R2's (same CAGR, DD, tax, sells/yr, turnover) — since 2017 the
latch never delayed an exit.

Clean after-tax bootstraps (block-20, 1,000 paths, real 2015-09 → 2026-09 calendar):

| arm | B1-recent median Δ | share > 0 | B2-flat median Δ | share > 0 |
|---|---|---|---|---|
| B1 | −0.306 pp | 0.461 | −0.248 pp | 0.461 |
| B2 | **+0.216 pp** | **0.569** | **+0.229 pp** | 0.562 |
| B3 | −0.500 pp | 0.400 | −0.520 pp | 0.398 |
| B4 | **+0.560 pp** | 0.562 | **+0.422 pp** | 0.562 |

(B2 and B4 clear the median ≥ 0 and share ≥ 55 % legs; both still fail the AT window gate. The
share numbers sit right on the 55 % boundary for B2/B4, so nothing here is robust.)

## 5. Out of sample — the reduced, pre-registered window set

The panel needs peers with free long daily history, so the pre-registered OOS set is **U1–U4**
(measured windows: ^IXIC 1988-09-09 → 1999-12-31, ^N225 1988-09-21 → 2026-09-29, ^GDAXI
1989-01-03 → 2026-09-29, ^FTSE 1988-09-12 → 2026-09-29). `ewma_band_v1`'s `^GSPC 1957-1999` and
`e2_confirmation_v1`'s `M2 ^GSPC 1934-1956` are **declared not applicable** because their
instrument *is* the SPY member (the panel collapses to {^GSPC, ^GSPC, ^RUT}) and ^RUT does not
exist before 1987-09-10. G7/G8 use the same thresholds with 4 windows (≥ 3 of 4).

| window | instrument | C0 CAGR/DD | B1 | B2 | B3 | B4 |
|---|---|---|---|---|---|---|
| U1 | ^IXIC 1988–1999 | 26.27 % / 35.55 % | 26.66 / 40.12 | 26.93 / 37.24 | 24.42 / 35.86 | **29.86 / 31.46** |
| U2 | ^N225 1988–2026 | 2.05 / 65.04 | 1.54 / **91.56** | 1.48 / 81.20 | **4.15 / 55.79** | 1.72 / 86.16 |
| U3 | ^GDAXI 1989–2026 | 9.47 / 58.10 | **10.56 / 52.77** | 10.07 / 60.27 | 8.25 / 55.74 | 8.18 / 56.53 |
| U4 | ^FTSE 1988–2026 | −1.38 / 85.45 | **4.85 / 56.70** | −0.29 / 77.90 | 0.25 / 77.53 | −0.84 / 77.50 |

All four arms have a **positive pooled G8 median** (+2.311 / +0.494 / +0.348 / +0.684 pp over
8,000 paths) and G7's median CAGR leg passes for all four (+0.740 / +0.626 / +0.204 /
+0.105 pp). G7 still fails for every arm on the "max DD never > C0 + 8 pp" clause: on ^N225 the
SPY-member vote holds 77.4 % of sessions while the Nikkei's own vote holds 57.0 %, so B1 goes
flat far too rarely (DD 91.6 % vs 65.0 %) — the US-peer panel is simply not a breadth signal for
a foreign index. That was pre-registered as a caveat of the reduced set, and it is the OOS
verdict rather than a footnote.

## 6. Why it fails — mechanism

`DIAGNOSTICS.txt` (report-only, signals only, no simulation):

- Vote agreement with QQQ's own majority over the main window: **SPY 91.1 %**, IWM 82.6 %. The
  panel is largely redundant, exactly as the survey's own agreement rates suggested.
- B1/B4 disagree with C0's main on-state on 8.9 % of sessions and make 103 / 63 round trips vs
  C0's 78; B3 makes 104 (more exits, not fewer); **B2's latch adds only 77 sessions of "delayed
  exit"** in 26.5 years (1.2 % of in-sample sessions) — which is why its 2017+ leg is
  bit-identical
  to R2's.
- The tax channel is the resize path, not exits. Realised TRY gain by reason, 2000 window
  (millions of TRY): C0 51.6 resize / 7.5 liquidation / 0.7 tax; B2 49.4 / 7.2 / 0.7; B4 48.8 /
  7.9 / 0.7. Cutting round trips by 15 % moves the resize gain by ~4 %, and the after-tax result
  tracks the pre-tax result, not the exit count.
- The arms that hold less (B1, B3) do cut the tax bill (555 k / 680 k vs 766 k USD) but lose more
  pre-tax return than they save in tax; the arm that holds slightly more (B4) wins after tax in
  2000/2010 and loses in 2017.

## 7. Gates (all must pass)

`G1..G3`, `G5`, `G6` are evaluated under both financings (base, calibrated); `G7`/`G8` on the
reduced four-window OOS set + its pooled bootstrap; `AT` = after-tax $100k beats C0 in all three
windows; `B1`/`B2` = clean after-tax bootstraps (median Δ ≥ 0 and share > 0 ≥ 55 %); `F3` = ≥ 80 %
of the 21 ETFs with Δmax DD ≤ +3 pp.

| arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | AT | B1 | B2 | F3 | pass |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | F | F | F | F | F | F | F | P | P | F | F | F | F | **FAIL** |
| B2 | F | F | P | F | P | F | F | P | P | F | P | P | F | **FAIL** |
| B3 | F | P | F | F | F | F | F | P | P | F | F | F | **P** | **FAIL** |
| B4 | P | P | F | F | P | P | F | P | P | F | P | P | F | **FAIL** |

*(each cell is the gate under both financings where applicable; a single F in either financing is
reported as F)*. Selected: **none** (`eligible: []`). 21-ETF detail: Δmax DD within +3 pp on
11/21 (B1), 11/21 (B2), **20/21** (B3), 13/21 (B4); worst instruments EWS +31.9 pp (B1), EWH
+21.5 (B2), EEM +6.8 (B3), EEM +23.0 (B4). Cost stress at m = 4 base: C0 14.92 % / B2 14.93 %,
B4 15.15 %, B1 13.02 %, B3 14.05 %.

## 8. Amendments

Disclosed for completeness; **no arm, parameter, window, threshold, gate or selection rule was
changed after any result was seen.**

1. *Pre-run implementation refactor (no statistical change).* The first committed `screen.py`
   computed the pre-tax G5 bootstrap and the after-tax B1/B2 bootstrap inside one per-path loop
   (~23 min/run, of which 20 min was the account engine running the pre-tax sizing of all four
   arms). It was split into the vectorised `boot_main` (pre-tax, identical draws and statistics)
   and `boot_tax` (after tax only, identical draws). Both reproduce the published anchors exactly
   (§2), so the split is a runtime change only.
2. *Bug fixed before the reported run.* The first `--run` attempt crashed in
   `gates_and_selection` on a stale key (`boot["pre"][md]` after the refactor renamed the
   bootstrap output). Fixed; the reported run then completed with exit 0 twice, byte-identical.

## 9. Caveats

- Everything rests on one history and a synthetic 2x QLD\* leg; the vol sizing is on the synthetic
  leg (the harness convention), so a promoted rule would have to be re-run on QLD's real bars.
- After-tax deltas below ~0.1 pp are inside modelling uncertainty (Yİ-ÜFE month convention,
  deflation of the brackets by a not-yet-published October index). Not tax advice.
- The OOS stage measures the fixed US-peer panel on foreign instruments; a foreign-peer panel
  (^N225/^GDAXI/^FTSE among themselves) is a *different* study and was not run.
- B2/B4's bootstrap shares (0.562–0.569) sit on the 55 % boundary; the sign is not robust to the
  path count even where the median is positive.
- The 21-instrument panel is {ETF, SPY, IWM}, so for the SPY and IWM instruments themselves the
  panel is degenerate (B1 ≡ C0 for X = SPY by construction). No de-duplication was applied; that
  was pre-registered.
