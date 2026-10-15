# cash_yield_v1 — idle-cash yield survives Turkish tax (idea I5)

**Verdict (2026-10-15): ADOPT Z2** — credit a broker cash sweep at `max(0, DTB3 − 0.50 pp)/yr` on the
account's uninvested cash. All 11 pre-registered gates pass for Z1, Z2 and Z3, pre-tax **and** after
Turkish income tax: pre-tax CAGR 16.4054 % vs K's 15.7649 % (+0.64 pp), max DD **42.54 % vs 46.67 %**
(−4.13 pp), after tax at $100k **+0.485 / +0.234 / +0.429 pp** on the 2000 / 2010 / 2017 windows, clean
bootstrap medians **+0.62 / +0.64 pp with 100 % of paths > 0** (progressive / flat). The idea the survey
ranked last is the one that survived; the ETF form of the same idea (`r2_tbill_combo_v1`) died because a
fund realises taxable gains, and a sweep realises nothing.

Two qualifications that matter more than the ranking:

1. **Z1 and Z2 are the same arm.** Pre-tax they are bit-identical (the declaration bar is a tax rule),
   and after tax they differ by **+0.007 pp in one window only** (`$100k`, 2000: Z2 11.2380 % vs Z1
   11.2317 %). The frozen selection rule breaks the tie on that difference, but the substantive finding
   is the *yield channel*, not the 22,000 TL bar: Z2's advantage is inside the modelling-uncertainty
   band (`idea_survey_v4` §6 caveat 2, ±0.1 pp). Z1 is the conservative implementation — it does not
   depend on any reading of the bar — and Z2 additionally requires a *mali müşavir* opinion
   (§2.2). The true upper bound, Z1x (interest never taxed), is only +0.10 pp above Z2.
2. **It is an account feature, not a strategy change.** The trading rule is untouched: turnover ratio
   **1.00014**, sells/yr identical (10.1657), and only **5 of 6,679 sessions (0.07 %)** have a different
   fill decision vs K. Nothing here changes `src/`, `config/`, the deployed bot or PAPER behaviour — and
   a paper account probably cannot realise any of it (§6).

## 1. Question

Production K leaves the account's uninvested cash — the 0.98 cap buffer, the `0.98 − w` residue of every
vol-targeted weight, and the money parked for March/July instalments — earning **zero**. Idea I5 of
`docs/research/idea_survey_v4.md` asks whether a broker **cash sweep** on that balance beats K *after
Turkish income tax*. The direction was genuinely open: the pre-tax precedent is positive
(`tbill_sweep_v1` arm A_ALL at `DTB3 − 0.5 pp` was +0.68 pp against E2), but the same idea via the BIL
ETF was **after tax** a loss in every window (`r2_tbill_combo_v1`, clean-bootstrap median −1.08 pp),
because a fund's resizes realise taxable capital gains. A sweep has a different tax base: interest
income, never bought or sold, no tracking error, no spread, no re-sizing band — and, unlike a capital
gain, **no indexation relief and no deferral**.

Pre-registered expectation was pre-tax +0.2 … +0.7 pp and after tax **+0.05 … +0.30 pp**. Measured:
pre-tax **+0.64 pp**, after tax **+0.19 … +0.49 pp** — above the expected band, and (this is the part
that decides) not a repeat of the BIL failure.

## 2. Method

Control **C0 = K** (production `equity_leveraged_trend_v1` 1.2.0): E2's 2-of-3 QQQ vote, R2's early
re-entry leg, the A1u gain-scaled downsize band and H2 December deferral; synthetic QLD\* (daily-reset
2× on QQQ total return, 0.95 %/yr expense, financing DTB3 + 0.00 % base / +0.75 % calibrated); cap
0.98; 0.5 pp resize band; 4 bps × m × |Δw|; decide on the close, fill next open. Harness built on
`research/band_h2_combo_v1`'s; arms, parameters and gates were frozen in
`research/cash_yield_v1/PREREGISTRATION.md` and committed before any non-control arm ran on real data.

**Yield mechanics (identical in every arm; Alpaca's own documented behaviour).** Accrual each session on
the session's **ending** uninvested cash balance at `max(0, DTB3[i−1] − spread)/252` (one-session lag,
clamped at zero because a sweep pays nothing rather than charging); credited and compounded on the **last
session of each calendar month**; the pending accrual is **not** NAV until credited. `spread` = 0.50 pp
(Z1, Z2) / 1.00 pp (Z3). The accrue-daily/credit-monthly convention is A6 in the pre-registration
(`idea_survey_v4` wrote "accrued daily"; Alpaca documents monthly compounding), worth < 0.02 pp.

**Tax on the interest (modelled per arm, frozen).** The TRY value of each month's credit is taken at the
credit session's CBRT buying rate and is declared as *menkul sermaye iradı* at the progressive 2026
tariff deflated by the previous October's Yİ-ÜFE, **with no indexation**, added to the same year's
declared base as the realised capital gain (so it pays the tariff's **marginal** rate — under K that is
the top bracket), paid with the same March/July instalments. A capital loss (already floored at zero)
cannot offset it (GVK 88). **No withholding credit** (see §6). Z2 applies the `22,000 × scale(year)` TRY
declaration bar to the year's TRY interest: at or below it nothing is declared, above it the whole amount
is. Z1x (report-only, never gated) is the same with the interest never taxed.

**Data.** No download. Inputs restored unchanged from `research/band_h2_combo_v1` (QQQ/SPY/QLD bars,
FRED DTB3 1954-01-04 → 2026-09-28 — the financing series *and* the new yield rate, CBRT USD/TRY, Yİ-ÜFE,
the six OOS window series, the 21 quality-passing ETFs); SHA-256 in `research/cash_yield_v1/DATA_MANIFEST.json`.
Main window 2000-03-09 → 2026-09-29, 6,679 sessions, 26.56 years. Main-window mean DTB3 1.915 %/yr; the
sweep pays **zero** in the ZIRP years the model itself shows (no credited interest in 2009-2017 or 2021
except 2018-19), which is why the arm's whole pre-tax gain comes from 2000-2008 and 2022-2026.

**Control reproduction (mandatory).** `--harness` then `--run`: **792/792** published K cells reproduced,
0 mismatches, max|diff| 4.99e-07 (the parent's own rounding); the production-R2 reference row, the
`R2 engine == simulate_band` identity (1.3e-13), the E2/E1/E3 anchors, the B1/B2 control medians and the
A_ALL cross-check all pass. The A_ALL cross-check — the pure-E2 series *with* this study's yield against
`tbill_sweep_v1`'s archived report arm — reproduces its published 15.35 % CAGR at **15.343 %**
(−0.007 pp, tolerance 0.05 pp). `--run` was executed twice; `RESULTS.json` is byte-identical
(SHA-256 `d35641d8396cb38b3948f4197d8a5e6a55aa6251473046363dd6c4c5d3d424c8`).

## 3. Pre-tax result (main window, m = 2)

| arm | CAGR base | CAGR cal | max DD base | max DD cal | Calmar base | turnover/yr |
|---|---:|---:|---:|---:|---:|---:|
| **K** (control) | 15.7649 % | 15.1785 % | 46.6698 % | 46.7948 % | 0.3378 | 5.4561 |
| **Z1** | **16.4054 %** | 15.8159 % | **42.5362 %** | 42.6707 % | 0.3857 | 5.4569 |
| **Z2** | 16.4054 % | 15.8159 % | 42.5362 % | 42.6707 % | 0.3857 | 5.4569 |
| **Z3** | 16.2743 % | 15.6855 % | 43.3106 % | 43.4434 % | 0.3758 | 5.4569 |

Δ vs K: Z1/Z2 **+0.641 pp** base, **+0.637 pp** calibrated, **−4.13 pp** max DD (m = 1: +0.642/+0.639 pp;
m = 4: +0.638/+0.635 pp). Sub-periods (base, m = 2): P1 2000-08 **+1.19 pp**, P2 2009-16 **+0.00 pp**,
P3 2017-end **+0.58 pp**.

The DD *reduction* is not a discount: with the strategy rebalanced to `w`, the crediting cash keeps
compounding up while the equity leg falls, so the trough falls less relative to the peak. It scales with
the yield exactly — Z1 (−4.13 pp) carries 47,028 USD of gross interest, Z3 (−3.36 pp) 37,976 USD, a
0.807/0.813 ratio — and it appears in every OOS window with a non-zero rate.

## 4. After-tax result

**$100k (gate capital), progressive 2026 tariff, liquidated, base financing, m = 2:**

| arm | 2000 window | Δ vs K | 2010 window | Δ vs K | 2017 window | Δ vs K |
|---|---:|---:|---:|---:|---:|---:|
| **K** | 10.7533 % | — | 15.2354 % | — | 23.9720 % | — |
| **Z1** | 11.2317 % | +0.478 | 15.4693 % | +0.234 | 24.4005 % | +0.429 |
| **Z2** | 11.2380 % | +0.485 | 15.4695 % | +0.234 | 24.4009 % | +0.429 |
| **Z3** | 11.1372 % | +0.384 | 15.4247 % | +0.189 | 24.3192 % | +0.347 |
| **Z1x** (never taxed; report-only) | 11.3358 % | +0.582 | 15.5416 % | +0.306 | 24.5294 % | +0.557 |

After-tax max DD at $100k: Z1 45.04 % / 44.63 % / 33.78 % vs K 46.67 % / 44.63 % / 34.98 %.
Terminal (2000 window, $100k): K 1,506,985 USD → Z1 1,689,768 (+12.1 %), Z2 1,692,303.

**The gross, the tax and the net, kept separate (Z1, main window, $100k).** Gross interest credited
**47,028 USD** = 1,162,972 TRY; tax attributable to the interest **434,393 TRY** = **37.4 %** of the TRY
interest (the tariff's marginal rate); Z3: 37,976 USD / 962,144 TRY gross, 358,951 TRY tax (37.3 %). So
the sweep keeps ≈ 63 % of the interest at K's top bracket — the survey's "roughly half to two-thirds".
On its own that stream is small (0.105 % of ending NAV/yr); what makes it +0.64 pp pre-tax is that it is
credited into the account, and thereafter compounds at the strategy's own ~16 % rate for decades.

**Δ vs K after tax by capital (Z1; brackets sharpen the effect at small accounts):**

| capital | 2000 | 2010 | 2017 |
|---|---:|---:|---:|
| $10k | +0.500 | +0.241 | +0.443 |
| $30k | +0.466 | +0.240 | +0.426 |
| **$100k** | **+0.478** | **+0.234** | **+0.429** |
| $300k | +0.473 | +0.229 | +0.424 |
| $1M | +0.463 | +0.256 | +0.419 |

Unliquidated (mark-to-market) at $100k: Z1 +0.490 / +0.247 / +0.457 pp; Z2 +0.496 / +0.248 / +0.458.
m = 4 cost stress at $100k: Z1 +0.476 / +0.233 / +0.427 pp.

**Clean bootstrap (seed 20261011, block 20; B1-recent progressive and B2-flat at $100k).**

| arm | pre-tax median Δ base / cal | B1 median Δ | share > 0 | B2 median Δ | share > 0 |
|---|---:|---:|---:|---:|---:|
| Z1 | +0.795 / +0.790 | **+0.625** | **100 %** | **+0.642** | **100 %** |
| Z2 | +0.795 / +0.790 | **+0.636** | **100 %** | **+0.642** | **100 %** |
| Z3 | +0.647 / +0.644 | +0.510 | 100 % | +0.522 | 100 % |

At $1M: Z1/Z2 B1 +0.588 / +0.589, B2 +0.642. Gate B1B2 requires median ≥ 0 and share > 0 ≥ 55 %; the
measured share is 100 % on all 1,000 paths for every candidate and both tax systems.

**Out-of-sample (six `ewma_band_v1` windows, pre-tax, calibrated, m = 2, R-USD rule):** Δ CAGR vs K
Z1/Z2 / Z3 — ^GSPC 1957-99 **+1.99 / +1.83 pp**, M1 +2.21/+2.05, M2 +0.12/+0.07, M3 +1.33/+1.18,
M4 +0.77/+0.64, M5 (K at −0.62 %) +0.76/+0.65. Δ max DD is never positive (worst −0.00 pp, M2). Pooled
M1-M5 block-20 bootstrap (G8, 10,000 paths): Z1/Z2 median **+1.869 pp**, mean +1.622, share > 0
**100 %**; Z3 +1.686 pp, 80.0 %.

**Reported, not decisive.** E1 (21 ETFs, R-INDEX, $100k): Z1 median +0.394 pp, 21/21 > 0 (Z2 +0.410,
Z3 +0.320). E2 (21 January rolling starts): 21/21, medians +0.297 / +0.301 / +0.236 pp. E3 (fresh
bootstraps seed 20261015, blocks 10 and 40, 1,000 paths): Z1 +0.656 / +0.642 / +0.604 / +0.578 pp, Z2
+0.656 / +0.655 / +0.604 / +0.587, Z3 +0.536 / +0.526 / +0.486 / +0.471 — 100 % of paths > 0 in all
twelve cells. The pass is broad-based, not a QQQ idiosyncrasy.

## 5. Gates

| Gate | Threshold | Z1 | Z2 | Z3 |
|---|---|---|---|---|
| G1 pre-tax CAGR m = 2 | ≥ K or Calmar ≥ 1.1×K and CAGR ≥ K−0.5 pp | PASS (+0.641/+0.637) | PASS | PASS (+0.509/+0.507) |
| G2 max DD | ≤ K + 1.0 pp | PASS (−4.13/−4.12) | PASS | PASS (−3.36/−3.35) |
| G3 2017+ CAGR | ≥ K − 1.0 pp | PASS (+0.578/+0.575) | PASS | PASS (+0.466/+0.463) |
| G4 sub-periods | CAGR > K in ≥ 2 of 3 | PASS (3/3) | PASS (3/3) | PASS (2/3) |
| G5 pre-tax bootstrap median | ≥ 0, both financings | PASS (+0.795/+0.790) | PASS | PASS (+0.647/+0.644) |
| G6 m = 1 re-check | as G1 | PASS (+0.642/+0.639) | PASS | PASS (+0.511/+0.508) |
| G7 six OOS windows | median Δ ≥ 0; ≥5/6 within bounds; ΔDD never > +8 pp | PASS (+1.052, 6/6, worst ΔDD −0.00) | PASS | PASS (+0.913, 6/6) |
| G8 pooled OOS bootstrap | median Δ ≥ 0 | PASS (+1.869) | PASS | PASS (+1.686) |
| G9 turnover | ≤ 2 × K | PASS (ratio 1.00014) | PASS | PASS (1.00014) |
| AT after tax $100k | Δ ≥ 0 in all three windows | PASS (+0.478/+0.234/+0.429) | PASS (+0.485/+0.234/+0.429) | PASS (+0.384/+0.189/+0.347) |
| B1B2 clean bootstrap | median Δ ≥ 0 and share > 0 ≥ 55 % | PASS (+0.625/+0.642, 100 %) | PASS (+0.636/+0.642, 100 %) | PASS (+0.510/+0.522, 100 %) |

**No failed gate for any candidate.** The binding gates the pre-registration expected to bind (AT, B1B2)
are the ones Z clears by the widest margin.

## 6. Selection

Frozen rule: highest pre-tax base m = 2 CAGR among candidates clearing every gate; ties broken by the
after-tax 2000-window CAGR. Z1 and Z2 are **identical pre-tax** (16.4054 %), so the tie-break decides:
Z2 (11.2380 %) over Z1 (11.2317 %). **Adopted: Z2**, per the rule, unchanged after seeing the data.

Read honestly: the Z1/Z2 gap is +0.007 pp, two orders of magnitude inside the ±0.1 pp modelling band, and
its sign follows only from the years in which the bar binds. Z1 needs no legal reading at all; Z2 needs
the 22,000 TL bar's aggregation rule (foreign interest and foreign dividends sharing one bar) to hold,
which is the one item the pre-registration flagged as "medium confidence" and gated on a written opinion.
Z1x (upper bound) is just +0.10 pp above Z2, so the whole legal question is worth ≈0.1 pp. Operator
summary: **opt into the sweep**; choose Z2's tax treatment only if a *mali müşavir* confirms the bar
applies — otherwise declare in full (Z1) and lose 0.007 pp.

## 7. Turkish tax on the interest: what is modelled, what is uncertain

Modelled (frozen in §2.2 of the pre-registration): foreign-source interest is taxable for a TR resident as
*menkul sermaye iradı*; the TRY value is taken at the credit session's CBRT buying rate; it is declared in
full at the progressive tariff with **no Yİ-ÜFE indexation**, no expense deduction, no withholding
credit, in the year credited, with the same March/July instalments; GVK 88 forbids offsetting it with a
capital loss. The **legal uncertainty** the study brackets, not resolves: (i) the declaration bar — 18,000
TL (2025) / 22,000 TL (2026) — is a *bar*, not an exemption, and its aggregation across non-withheld MSI
items is not fully public (Z1 ignores it, Z2 applies it as written, Z1x is untaxed); (ii) no Turkish
withholding applies (*geçici 67* reaches Turkish intermediaries only) and US portfolio interest paid to a
non-resident alien is exempt (IRC 871(h)/(i)), so the model grants no withholding credit — if Alpaca
withholds, every Z number falls and the ranking is unchanged; (iii) the USD principal's FX gain is **not**
assessed (a sweep balance is never disposed of, so no *değer artışı kazancı* arises). **Not tax advice**;
Z2 assumes an adviser's written opinion. Sources: GİB *Menkul Sermaye İradı Vergi Rehberi 2026* §3.2
(the 22,000 TL bar; "yurt dışından elde edilen diğer menkul sermaye iratları" / "her türlü alacak
faizleri" explicitly in scope) — recorded in `DATA_MANIFEST.json` → `external_documentation`.

## 8. Availability (public Alpaca documentation, read-only, re-checked 2026-10-15)

- **What it is.** Alpaca's **High-Yield Cash** is the **FDIC Bank Sweep**: opt-in, eligible uninvested
  cash swept to program banks. `docs.alpaca.markets` (FDIC Sweep Program, updated 2025-09-24 and the
  `us/v1.1` mirror): *"eligible uninvested cash in a customer brokerage account can be swept"*;
  interest *"accrue[s] daily"*, *"compounded monthly"*, available *"last business day of the month. The
  accrued interest is booked to the customer account, but remains within the program to compound into
  yield"*; *"Daily liquid, and available for use as buying power"*. Requirements: USD base currency,
  pre-funded (non-JIT), not flagged PDT, **Individual Taxable and Custodial accounts** (Broker API:
  fully-disclosed partners only). This is exactly the mechanics the study models.
- **Non-US residents: yes, per Alpaca's own guide.** `alpaca.markets/learn/live-trading-account-non-us`
  (updated 2026-03-11): *"With your funded Trading API account, you can start trading US stocks, ETFs,
  crypto, and options. In the Alpaca dashboard, you can also participate in our High-Yield Cash offering
  to earn passive income on uninvested cash."* Alpaca supports 195+ countries of residence (Canada
  excluded); the 2024 Global High-Yield Cash API put ex-US customers explicitly in scope. Subject to the
  same opt-in, eligibility and country-support conditions.
- **Rate.** The program rate is variable ("subject to change without notice"). The pre-registration
  recorded Alpaca's blog quoting "up to 3.56 % APY" from 2026-09-22 against DTB3 4.10 % on 2026-09-28,
  i.e. `DTB3 − 0.5 pp`; the rate's *path* is not public, so DTB3 minus a constant spread is the only
  defensible back-cast and Z3 (`DTB3 − 1.00 pp`) is the pessimistic plateau.
- **PAPER: no.** Nothing in Alpaca's documentation says a **Paper Trading** account accrues the sweep; the
  Paper API is a simulation. The PAPER bot therefore cannot realise these numbers — they describe a
  **live** account. This is reported, not gated, and it is why the deliverable is an owner/account
  decision rather than a code change.

## 9. Caveats

1. **The rate is the biggest assumption.** DTB3 − constant spread is a back-cast, not history; the sign of
   the result is the finding, the magnitude is indicative. Z3 bounds it pessimistically and still passes
   every gate.
2. **No true out-of-sample after tax.** The CBRT/Yİ-ÜFE calendar starts in 2000, so the six OOS windows
   are pre-tax only; the counterweights are B1-recent, B2-flat, the 21 ETFs, the 21 rolling starts and
   four fresh bootstraps. §6 caveat 3 of the survey asks for a decoupled-PPI/TRY regime stress; not run
   here (it would affect K and Z almost equally — the yield channel has no deferral leg to stress).
3. **Z2's bar and the legal reading** are the study's own sensitivity; §7 and §6.
4. **Modelled, not modelled:** the engine's idle cash already includes the 0.98 buffer, the `0.98 − w`
   residue and instalment money parked; a real broker sweeps the settled balance only, and unsettled
   trades can differ. Second-order, unmodelled. The pending month's accrual is outside NAV until credited.
5. **The tax model is a model** (FIFO, yearly netting without loss carry-forward, 2026 tariff deflated by
   the previous October's Yİ-ÜFE, one CBRT series, liquidated end). `idea_survey_v4` §6 caveat 4 (QLD
   dividends credited but not assessed in the production replay) is untouched by this study.
6. **Nothing changes in PAPER**, and no file outside `research/cash_yield_v1/` and this document was
   written. INDEX.md is deliberately not edited.

## 10. Reproduction

```
PY=/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python
$PY research/cash_yield_v1/screen.py --smoke     # 28/28 synthetic checks
$PY research/cash_yield_v1/screen.py --harness   # 792-cell K anchor reproduction, A_ALL cross-check
$PY research/cash_yield_v1/screen.py --run       # ~25 min; writes RESULTS.json + SUMMARY.txt
```

`--harness` gate: 792/792 cells, 0 mismatches, max|diff| 4.99e-07 → PASS; harness PASS, 0 cells outside
tolerance. Two consecutive `--run`s produced a byte-identical `RESULTS.json`
(`d35641d8396cb38b3948f4197d8a5e6a55aa6251473046363dd6c4c5d3d424c8`, 363,786 bytes).

Amendments A1-A6 (all made **before** the first complete `--run`, none touching an arm, parameter, stage,
seed, threshold, gate or the selection rule) are documented in `research/cash_yield_v1/PREREGISTRATION.md`
§10 and were committed with the harness fixes.
