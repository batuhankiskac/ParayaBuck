# short_term_reentry_v1 — tradeable short-term exit + rebound-confirmed re-entry on K

> Evidence: tag `archive/research/short-term-reentry-v1-2026-10` (not on `main`).

**Verdict (2026-10-22): FAIL. None of the six pre-registered arms passes any gate except G2
(drawdown) and, for three of them, G9 (turnover); nothing is selected.** More importantly, the
result explains *why* the short-term prize measured by `short_term_oracle_v1` is not tradeable:

- The **exit** is not the problem. It works: max drawdown falls 0.7–7.8 pp pre-tax, and in the
  October 2026 episode every arm cuts the account's loss from **−14.17 %** to **−10.4…−13.0 %**.
- The **re-entry** is the problem, and it destroys far more than the exit saves. On the main
  window the arms lose **4.2–8.4 pp/yr pre-tax** and **4.1–15.1 pp/yr after Turkish tax**
  (2000 / 2010 / 2017 windows: −4.1…−7.8 / −4.4…−10.3 / −7.1…−15.1 pp), and every clean
  bootstrap median is negative (−1.1…−5.0 pp, 14–38 % of paths positive).
- The 2x leg's mean daily return on the sessions the overlay is out is **+0.14…+0.34 %/session**
  against **+0.099 %/session** unconditionally: the rule sits out the strongest days, i.e. the
  rebound it was built to wait for. The re-entry fill is **above** the exit fill on 78–85 % of
  completed round trips, by **+2.6…+4.7 %** on average.
- **This is not a tax story.** The tax-free diagnostic variants (no sells, no tax, the leg's
  return zeroed while out — the upper bound for the same reaction with an option overlay) lose
  **3.6–8.3 pp/yr pre-tax** and **2.6–9.4 pp after tax**. Even a free, untaxed overlay of this
  timing is worse than holding K.
- The oracle's +4…+11 pp was the *low*, not the trigger. `short_term_oracle_v1` showed that
  false alarms barely hurt **when the restore knows the low**; a causal restore that waits for
  a rebound is above the exit price four times out of five.

Control = production **K** (`equity_leveraged_trend_v1` 1.2.0), reproduced cell-for-cell
(15.7649 / 15.1785 % pre-tax, DD 46.6698 / 46.7948 %, after tax 10.7533 / 15.2354 / 23.9720 % at
$100k). R2 is a reference row. Engine `research/fast_engine` (`parity --quick` PASS); the only
new code is a numba signal kernel that builds the target-weight arrays.

## 1. Question and what is new versus the failed family

`short_term_oracle_v1` found the prize for a perfect short-term exit/restore to be +4…+11 pp/yr
after tax and showed that trigger precision was not the binding constraint *because the oracle's
restore knew the episode low*. This study tests the causal version: exit K's QLD after a 3 % or
5 % QQQ pullback from the 20-session high, then re-enter only after a **rebound confirmed from
the post-exit low** (2 % or 4 % above it, or above SMA10).

That is the device `intraday_stop_v1` (fixed cooldown), `dip_overlay_v1` (buying dips),
`vote_hysteresis_v1` and the drawdown-velocity/governor arms did not have. The rule is decided
on QQQ closes, filled at the next open, with K's weight path, A1u gain band and H2 December
deferral running unchanged underneath.

## 2. Method

* **Overlay (pre-registered §3).** While K's target `w[t] > 0` and the overlay is *in*:
  exit if `close[t] <= (1 − d) × H[t]`, where `H[t]` is the highest close over the trailing
  20 sessions (`t−19…t`) truncated at the current in-stint's first session. While *out*:
  `L = min(close)` since the exit close (inclusive); re-enter at the first later session with
  `close[t] >= (1 + r) × L` (arms A–D) or `close[t] > SMA10[t]` (arms E–F), target `w[t]`.
  If `w[t] ≤ 0` the overlay is in and a new stint starts. The target is 0 on every out session.
  The truncation is the one interpretive choice; without it a re-entry would immediately
  re-exit whenever the price is still `d` % below the pre-exit high (the normal case) and the
  rebound rule would be inert. The untruncated sensitivity is *worse* (§7).
* **Arms (6, fixed):** A (d 3 %, r 2 %), B (3 %, 4 %), C (5 %, 2 %), D (5 %, 4 %),
  E (3 %, SMA10), F (5 %, SMA10).
* **Tax-free diagnostic variants** `Ah…Fh` (non-selectable): K's targets, the 2x leg's return
  zeroed on the out sessions (`masked_prices`, the existing return-mask option).
* **Data.** QQQ/SPY/QLD bars + DTB3, CBRT USD/TRY, Yİ-ÜFE: existing committed research files
  (`DATA_MANIFEST.json`, SHA-256). Main window 2000-03-09 → 2026-09-29. After tax at $100k,
  progressive 2026 tariff, liquidated; clean bootstrap seed 20261011, block 20, 5,000 pre-tax /
  1,000 after-tax paths; six OOS windows (^GSPC 1957–99, M1–M5) and the pooled OOS bootstrap;
  a Yahoo extension of QQQ/QLD through 2026-10-22 for the October episode (report only).
* **Gates.** `ewma_band_v1`'s G1–G8 against K, G9 turnover ≤ 2 × K, G10 after-tax beats K in all
  three windows, G11/G12 clean B1-recent/B2 medians ≥ 0 with ≥ 55 % of paths positive. All must
  pass. Selection: highest after-tax 2000-window CAGR among passers.

## 3. Pre-tax, main window (m = 2)

| arm | base CAGR | cal CAGR | base max DD | cal max DD | sells/yr | turnover/yr | exits |
|---|---:|---:|---:|---:|---:|---:|---:|
| **K (control)** | **15.76** | **15.18** | **46.67** | **46.79** | 10.17 | 5.46 | — |
| R2 (reference) | 15.48 | 14.90 | 46.76 | 46.88 | 29.56 | 5.99 | — |
| A (3 %, 2 %) | 7.59 | 7.14 | 42.53 | 42.99 | 14.80 | 15.10 | 203 |
| B (3 %, 4 %) | 7.34 | 6.97 | 38.92 | 40.11 | 11.45 | 11.90 | 155 |
| C (5 %, 2 %) | 10.56 | 10.03 | 46.00 | 46.12 | 13.93 | 9.43 | 91 |
| D (5 %, 4 %) | 11.21 | 10.72 | 44.00 | 44.15 | 11.37 | 8.29 | 78 |
| E (3 %, SMA10) | 7.78 | 7.33 | 43.42 | 44.34 | 14.19 | 14.91 | 194 |
| F (5 %, SMA10) | 11.61 | 11.09 | 43.60 | 43.75 | 12.50 | 9.02 | 82 |

At m = 1 the same ordering holds (K 16.02 / 15.43 %; A 8.24 / 7.79, B 7.85 / 7.47,
C 10.98 / 10.45, D 11.58 / 11.08, E 8.43 / 7.98, F 12.02 / 11.49). A 3 % trigger is the worst
choice in every dimension: it fires 7.3–7.6 times a year and its turnover is 2.7–2.8 × K.
Every arm loses G1 on return: CAGR is 4.2–8.4 pp below K (calibrated: 4.1–8.2 pp), and although
the Calmar *ratio* is 1.56–2.33 × K, G1's Calmar route also requires CAGR ≥ K − 0.5 pp, which
fails by 3.7–7.9 pp.

Sub-periods (base, Δ CAGR vs K): A −3.61 / −7.28 / −14.16 % (P1/P2/P3), B −4.09 / −7.23 / −14.43,
C −2.90 / −3.36 / −9.54, D −2.35 / −5.29 / −6.38, E −4.88 / −9.83 / −9.73,
F −3.35 / −3.31 / −5.82. **No arm beats K in any sub-period.**

## 4. After Turkish tax ($100k, progressive, liquidated)

CAGR %, Δ vs K in parentheses; K = 10.75 / 15.24 / 23.97 % on the 2000 / 2010 / 2017 windows.

| arm | 2000 | 2010 | 2017 | sells/yr | tax paid (2000) |
|---|---:|---:|---:|---:|---:|
| R2 | 10.16 (−0.59) | 14.71 (−0.53) | 22.89 (−1.09) | 30.69 | 766,160 |
| A | 2.92 (−7.83) | 4.91 (−10.32) | 8.91 (−15.06) | 15.21 | 176,608 |
| B | 3.48 (−7.27) | 7.14 (−8.10) | 10.74 (−13.23) | 11.78 | 163,684 |
| C | 5.73 (−5.02) | 9.23 (−6.01) | 14.05 (−9.92) | 14.46 | 307,892 |
| D | 6.46 (−4.29) | 10.60 (−4.64) | 16.77 (−7.20) | 11.94 | 350,494 |
| E | 3.14 (−7.62) | 6.04 (−9.19) | 12.14 (−11.83) | 14.57 | 176,870 |
| F | 6.66 (−4.09) | 10.88 (−4.35) | 16.88 (−7.09) | 13.03 | 390,702 |
| *Ah* (diagnostic) | 5.26 (−5.49) | 8.15 (−7.09) | 16.25 (−7.72) | 11.22 | 182,293 |
| *Bh* | 4.58 (−6.17) | 8.31 (−6.92) | 14.62 (−9.36) | 12.99 | 145,825 |
| *Ch* | 7.50 (−3.25) | 11.34 (−3.89) | 17.16 (−6.81) | 11.33 | 360,751 |
| *Dh* | 8.13 (−2.62) | 12.48 (−2.75) | 18.86 (−5.12) | 11.63 | 422,880 |
| *Eh* | 5.50 (−5.25) | 9.41 (−5.82) | 19.38 (−4.60) | 11.63 | 189,906 |
| *Fh* | 8.09 (−2.66) | 12.57 (−2.67) | 19.35 (−4.62) | 11.14 | 412,105 |

The arms pay **much less** tax than K (176k–391k against 831k on the 2000 window) because they
hold far smaller gains, so the after-tax loss is *slightly smaller* than the pre-tax loss
(A: −7.83 vs −8.18 pp). Turkish tax is not what kills this family: the **tax-free** upper bound
(an untaxed option overlay with no sells) is also 2.6–9.4 pp worse after tax.

## 5. Clean bootstrap (seed 20261011, block 20; 5,000 pre-tax / 1,000 after-tax paths)

Median Δ vs K (pp) / share of paths > 0.

| arm | pre base | pre cal | B1-recent | B2-flat |
|---|---:|---:|---:|---:|
| R2 | +0.01 | +0.01 | −0.29 / 25 % | −0.13 / 36 % |
| A | −5.03 | −4.93 | −4.99 / 14 % | −4.33 / 17 % |
| B | −4.60 | −4.44 | −4.17 / 22 % | −3.59 / 27 % |
| C | −1.95 | −1.91 | −2.16 / 23 % | −1.71 / 28 % |
| D | −1.67 | −1.60 | −1.77 / 33 % | −1.39 / 36 % |
| E | −4.09 | −4.00 | −4.17 / 16 % | −3.40 / 22 % |
| F | −1.14 | −1.10 | −1.47 / 32 % | −0.96 / 38 % |

B1 p05/p95 spreads are wide and negative-centred (e.g. A −13.6 / +2.2; F −7.7 / +3.5): the arms
win only on the paths where 2020-style crashes recur often.

## 6. Why: the re-entry, quantified

Main window, base model. "depth" = the pullback from the exit's reference high to the minimum
close before the episode ends; "out-day ret" = the 2x leg's mean close-to-close return on the
sessions the hedge variant masks (unconditional: **+0.099 %/session**).

| arm | exits/yr | false alarms (<8 %) | median depth | depth histogram (3–5 / 5–8 / 8–10 / ≥10 %) | mean re-entry vs exit | re-entry above exit | out-day ret |
|---|---:|---:|---:|---|---:|---:|---:|
| A | 7.64 | 89 % | 4.46 % | 120 / 60 / 16 / 7 | **+2.64 %** | 78 % | **+0.234 %** |
| B | 5.84 | 81 % | 5.06 % | 76 / 49 / 12 / 18 | +4.70 % | 78 % | +0.154 % |
| C | 3.43 | 74 % | 7.01 % | 0 / 67 / 16 / 8 | +2.93 % | 85 % | +0.335 % |
| D | 2.94 | 63 % | 7.43 % | 0 / 49 / 11 / 18 | +4.60 % | 82 % | +0.142 % |
| E | 7.30 | 89 % | 4.35 % | 122 / 50 / 10 / 12 | +2.64 % | 84 % | +0.218 % |
| F | 3.09 | 70 % | 7.05 % | 0 / 57 / 12 / 13 | +2.82 % | 80 % | +0.205 % |

Three facts explain the whole result:

1. **Most exits are false alarms** (63–89 % of episodes never fall 8 %; the median 3 %-trigger
   episode bottoms 4.5 % below the high). The drawdown saved on those is small.
2. **The out sessions are the good sessions.** After a pullback the next sessions are
   disproportionately positive; the arm is flat exactly then, and even the 2 % rebound rule
   re-enters only after most of the bounce. Compounded over the window, the sessions the hedge
   masks earn **+66 % to +485 %** on the 2x leg (D … B).
3. **So the round trip costs money on average**, by 1.5–4.7 % of the position per exit, and the
   arms take 2.9–7.6 exits a year. The exit's drawdown benefit (0.7–7.8 pp) is an order of
   magnitude smaller than the return cost (4.2–8.4 pp/yr).

At the pre-registered `d = 5 %` the trigger is less noisy (63–74 % false alarms) and the loss is
smaller, but still 4.1–7.1 pp after tax; the 3 % arms are strictly worse everywhere. There is no
interior optimum: the more the rule trades, the more it loses.

## 7. Out of sample and the October 2026 episode

OOS, calibrated, m = 2, Δ CAGR / Δ max DD (pp) vs K, and the pooled G8 bootstrap:

| arm | ^GSPC 57–99 | M1 | M2 | M3 | M4 | M5 | median Δ | pooled boot |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | −3.60 / −0.2 | +1.44 / −5.3 | −5.12 / −1.8 | −2.22 / +10.5 | −4.48 / −8.5 | −1.40 / +4.8 | −2.91 | −2.21 |
| B | −5.64 / −1.9 | +1.56 / −12.9 | −6.80 / +1.3 | −1.82 / +3.5 | −4.61 / −5.2 | −1.38 / +5.2 | −3.22 | −2.99 |
| C | −0.82 / −4.9 | +0.30 / +1.1 | −4.88 / +6.9 | −0.95 / +8.3 | −1.04 / −7.1 | −0.18 / +3.1 | −0.88 | −2.22 |
| D | −1.91 / +2.4 | +2.03 / −4.7 | −6.16 / +7.1 | −0.37 / −3.5 | −1.36 / −5.6 | −0.20 / +3.5 | −0.86 | −2.25 |
| E | −3.48 / −1.5 | +0.90 / +2.5 | −6.52 / +8.0 | −2.22 / +13.4 | −4.96 / −2.9 | −1.62 / +5.7 | −2.85 | −2.29 |
| F | −1.00 / −6.1 | +0.56 / +0.3 | −5.13 / +11.1 | −1.01 / +10.0 | −2.01 / −6.5 | −0.65 / +4.3 | −1.00 | −2.05 |

Only M1 (Nasdaq 1975–99) is positive, and only for the 3 % arms. The drawdown deltas are
unstable in sign: the overlay deepens the drawdown in M3/M4 (up to +13.4 pp) because it re-enters
into further declines and pays the round trip.

**October 2026** (report only; extended Yahoo series through 2026-10-22; QQQ peak 759.66 on
2026-10-06, −7.27 % to the end, real QLD −14.40 %). All arms were *in* at the peak, exited once
during the episode and were out 3–7 sessions:

| | K | A | B | C | D | E | F |
|---|---:|---:|---:|---:|---:|---:|---:|
| return from the peak | −14.17 % | −12.97 | −10.58 | −12.81 | −10.43 | −10.60 | −10.43 |
| max DD inside | 13.72 % | 12.52 | 10.12 | 12.35 | 12.27 | 10.14 | 12.27 |

The exit does what it was asked to do — 1.2–3.7 pp less loss in the one episode that motivated
the study — but that is 0.15–0.3 pp/yr spread over 26.5 years of history, and the same mechanism
costs 4–8 pp/yr the rest of the time.

## 8. Gates

| gate | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| G1 return (base/cal) | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G2 DD ≤ K + 1 pp | pass | pass | pass | pass | pass | pass |
| G3 2017+ ≥ K − 1 pp | FAIL (−14.2) | FAIL (−14.4) | FAIL (−9.5) | FAIL (−6.4) | FAIL (−9.7) | FAIL (−5.8) |
| G4 ≥ 2 of 3 sub-periods | FAIL (0/3) | FAIL (0/3) | FAIL (0/3) | FAIL (0/3) | FAIL (0/3) | FAIL (0/3) |
| G5 pre-tax bootstrap ≥ 0 | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G6 G1 at m = 1 | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G7 six OOS windows | FAIL (1/6) | FAIL (1/6) | FAIL (1/6) | FAIL (2/6) | FAIL (1/6) | FAIL (1/6) |
| G8 pooled OOS bootstrap | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G9 turnover ≤ 2 × K | FAIL (2.77×) | FAIL (2.18×) | pass | pass | FAIL (2.73×) | pass |
| G10 after-tax all 3 windows | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G11 B1 median ≥ 0, share ≥ 55 % | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| G12 B2 median ≥ 0, share ≥ 55 % | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| **Result** | **FAIL** | **FAIL** | **FAIL** | **FAIL** | **FAIL** | **FAIL** |

Selected: **none** (`gates.selected = null`).

## 9. Interpretation

`short_term_oracle_v1`'s precision sweep had no break-even because a false alarm still exited at
a price above the low the oracle knew. This study supplies the missing half: a causal restore.
The result is the mirror image of that finding.

* **The trigger is not the constraint; the restore is.** A 3 % pullback from a 20-session high
  happens 7.6 times a year and is a normal wiggle 89 % of the time. Exiting on it costs one
  round trip whose expected sign is negative, because the price after a pullback is, on average,
  about to rise.
* **The exit's value is real but tiny in aggregate.** It removes 0.7–7.8 pp of max drawdown and
  1.2–3.7 pp in October 2026, but the prize the oracle measured (+4…+11 pp/yr) comes from being
  out for the *descent and back in at the low*, and the second half is not obtainable with a
  rebound rule: waiting for +2 % above an unknown low puts you above your own exit price 4 times
  in 5.
* **No tax or cost trick rescues it.** The tax-free overlay is 2.6–9.4 pp worse after tax than
  K; the arms pay 53–80 % less tax than K and still lose. Cost is not the issue either: the
  cost-free hedge variant loses more than the sell arms' extra frictions cost.
* **This closes the short-term-exit family as specified** (trailing-high exit + rebound/low-based
  re-entry) on top of K: not because the drawdown tool is worthless, but because the re-entry it
  implies is a systematic "sell after a dip, buy after a bounce" against a drifting, mean-reverting
  index. Any future idea on this axis must fix the *restore*, not the trigger — e.g. a restore
  rule that is at least not above the exit fill on average (limit-order restore at the exit price,
  or a restore conditioned on the pullback being deep), which the oracle's numbers say is the only
  part with value left.

## 10. Caveats

* Single history, one instrument (QQQ/QLD*), 6,679 main-window sessions; the October episode is
  outside the gated window and rests on a 30-session Yahoo download (overlap with the committed
  bars: QQQ closes identical to 3e-5).
* The reference-high truncation is an interpretive choice (§2). It is the *generous* convention:
  the untruncated variant gives 4.89 / 8.95 / 10.20 % pre-tax for A / C / F against 7.59 / 10.56 /
  11.61 %, so no conclusion rests on it.
* The tax-free variants are an upper bound for an option overlay, not an implementable trade: the
  hedge payout is priced on an effective path, and a later sale by K realises it (the oracle's
  documented caveat). That can only raise the hedge's tax, so its 2.6–9.4 pp after-tax loss is
  conservative in the arm's favour.
* The engine's fill is the next open; the exit bears the overnight gap and the re-entry misses
  the overnight gap into the fill session (one gap at each end versus the hedge's close-to-close
  mask). This is the same convention as the oracle study.
* The after-tax figures are the research convention: 2026 tariff, Yİ-ÜFE indexation, FIFO,
  instalments, liquidated at the window end, $100k, zero idle-cash yield, 4 bp × m. Not tax advice.
* No production file was read or written; K stays the production rule.

## 11. Reproduction

* `python -m research.short_term_reentry_v1.screen --harness` runs
  `python -m research.fast_engine.parity --quick` (**PASS**) and checks: K's anchors (pre-tax
  15.7649 / 15.1785 % at m = 2 and 16.0173 / 15.4303 % at m = 1, DD 46.6698 / 46.7948 %; after tax
  10.7533 / 15.2354 / 23.9720 %; K − R2 +0.5933 / +0.5294 / +1.0859 pp), all within 0.01 pp; the
  overlay kernel against a Python reference for all six arms × both financing models (mask and
  every episode record); a never-firing overlay reproducing K's target array and NAV bit for bit;
  the hedge identities including the six assembled price paths (4,958 masked sessions); and the
  bootstrap extension with K's targets against `fe_boot.boot` (8/8 cells, exact).
* `--run` executes harness + every stage in **27.2 s** (wall clock in `SUMMARY.txt`) and is
  deterministic: two consecutive runs produce byte-identical `RESULTS.json`
  (SHA-256 `55d33b8d534e09a9a84a0288e99821b82e5bd3600ad381901d276ff75822c33a`).
* `--smoke` runs synthetic-only checks (kernel vs reference, mechanics, hedge zero return).
* Files: `research/short_term_reentry_v1/{PREREGISTRATION.md, screen.py, RESULTS.json,
  SUMMARY.txt, DATA_MANIFEST.json}` and `engine/fe_overlay.py` (the only new kernel; target
  construction only). The simulation, tax and bootstrap code are `research/fast_engine` and, for
  the exogenous-target bootstrap, the parent study's `fe_boot_targets` — both unchanged. Inputs:
  the committed research data plus the two report-only Yahoo files, SHA-256 in `DATA_MANIFEST.json`.
