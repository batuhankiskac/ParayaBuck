# put_hedge_v1 — protective-put overlays on production K (Turkish after tax)

Pre-registered 2026-10-22 on branch `research/put_hedge_v1`, before the formal `--run` was executed.
The code is `research/put_hedge_v1/` (`screen.py`, `overlay_sim.py`, `config.py`, `study_data.py`,
`pput_probe.py`, `prep_data.py`, `make_manifest.py`); it is built on the mandatory Numba engine
`research/fast_engine/` and imports it for the signals, tax engine, data loaders, bootstraps and
parity. Control identity: production **K** = `equity_leveraged_trend_v1` 1.2.0 (R2 + A1u + H2).

## 0. Why this study exists

K is a trend rule: it predicts the drawdown and exits to cash. A protective put is *insurance
instead of prediction*. If the insurance is cheap enough it might (a) raise after-tax return by
letting the account hold more QLD, or (b) cut the drawdown at equal return. `exposure_k_v1` found
that vol targets 0.375–0.45 improve the realised after-tax windows but break the drawdown budget;
a put overlay is the obvious candidate for making a higher target affordable. The idea was never
tested: `idea_survey_v3` set options aside only because of an assumed account-type restriction,
which the orchestrator is re-checking separately.

## 1. Question

Can a systematic, mechanical protective-put overlay on K improve the Turkish after-tax return
and/or cut the drawdown — alone, or by making vol target 0.45 affordable — without failing the
pre-registered gates?

## 2. Data and provenance

Downloaded public data (allowed by the worker protocol) and converted by `prep_data.py`; SHA-256,
source URL and date range in `DATA_MANIFEST.json`:

| file | source | use |
|---|---|---|
| `put_hedge_v1_vxn.csv` | Yahoo `^VXN` | QQQ/NDX 30-day implied vol, 2001-01-23 → 2026-10-22 |
| `put_hedge_v1_vix.csv` | Yahoo `^VIX` | pre-registered fallback; PPUT sanity |
| `put_hedge_v1_spx.csv` | Yahoo `^SPX` | PPUT sanity; 1990–1999 OOS underlying |
| `put_hedge_v1_sp500tr.csv` | Yahoo `^SP500TR` | S&P 500 total return (PPUT dividend leg) |
| `put_hedge_v1_pput.csv` | Cboe `PPUT_History.csv` | published PPUT (sanity target) |
| `put_hedge_v1_ndx.csv` | Yahoo `^NDX` | provenance only |

Engine inputs are the `fast_engine_v1` archive files: `stress_robustness_v1_{daily,dtb3}.csv`
(QQQ/SPY/QLD bars, DTB3), `after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY conversion, Yİ-ÜFE),
`ewma_band_v1_tb3ms.csv` (rate fallback). The QQQ and VXN series are forward-filled onto the
engine's QQQ∩SPY sessions; the reduced window starts on the first session with a VXN close
(2001-01-23). The TRY/Yİ-ÜFE calendar starts 2000-01, so every after-tax window is usable.

## 3. Control

**K**, bit for bit, through the engine: R2's target series (E2 2-of-3 vote on the QQQ close +
the 0.5·vt early re-entry, `vt = min(1, 0.35/vol20)`, cap 0.98), the production execution layer
(decide at the close, fill at the next open, 0.5 pp band, A1u's gain-scaled downsize band,
H2's December deferral, 4 bps × m × |Δw|, synthetic QLD* = daily-reset 2× QQQ total return,
0.95 %/yr expense, DTB3 + {0 %, 0.75 %} financing), and the corrected Turkish after-tax engine.
K is re-simulated on the **same reduced window** as every arm in every table.

## 4. Pricing model (all parameters fixed)

European Black–Scholes on QQQ, one option position at a time:

- **Implied vol at strike**: `σ(K) = VXN_t · (1 + s · ln(S_t / K))`, `s = 2.0`. Source: S&P 500
  index-option skew is about 4–6 implied-vol points per 10 % of strike below spot in the
  literature (Onan, Salih & Yasar 2015, Table IV and Fig. 2: OTM put IV ≈ 14 % against ATM
  ≈ 10 % in 2006 with VIX 13.1 %); in the multiplicative form at a ~20 % vol level that is
  `s ≈ 2.0`. The ±50 % rows (`s = 1.0`, `s = 3.0`) are **non-selectable** sensitivity rows.
- **Dividend yield**: constant `q = 0.006` (QQQ trailing yield; OOS SPX uses `q = 0.018`).
- **Rate**: DTB3 at the session (the engine's own rate series).
- **Roll calendar**: the **third Friday** of the month; if that Friday is not a trading session,
  the preceding session. The first roll is the first third Friday on/after the window's first
  session; thereafter every tenor (1, 2 or 3 calendar months). One position at a time; held to
  expiry; expiry is the next roll's session; cash settlement = intrinsic at that close.
- **Strikes**: `K = (1 − otm) · S_roll`. H1 10 %; H2/H4/H5 long 10 %, short 25 % (same units);
  H3 15 %. Every strike is a fraction of spot (no listed-strike rounding).
- **Transaction cost**: **5 % of the premium per side** (long buy and short sell), i.e. a 10 %
  round-trip premium cost. The underlying equity keeps the engine's 4 bps × m cost.
- **Sizing**: spot notional `units = hedge_ratio · 2 · w_close · NAV_close / S` with
  `hedge_ratio = 1`, `2` = QLD's beta to QQQ, `w_close` = the QLD value / NAV at the roll close,
  `NAV_close` = cash + QLD value + any option mark before the premium. **Hedge only while
  `w_close > 0`.** H5 additionally requires the E2 vote to be on at the roll. H3 is a fixed
  premium budget: `units = (0.01 / 4) · NAV_close / (BS · 1.05)`, i.e. 0.25 % of NAV spent at
  each quarterly roll, 1 % per year.
- **Funding**: the premium is paid from cash at the roll close. If cash is short, QLD units are
  sold at that same close at the close price with the production cost (a taxable `option_funding`
  sale; A1u/H2 do not apply to this funding sale).
- **NAV path**: the option is marked to the same Black–Scholes model every session (unrealised;
  no tax). At the window end an outstanding option is liquidated at its model value with the
  5 % selling cost. The mark affects the NAV, the drawdown and the QLD weight; the tax base uses
  only realised cash flows.
- **Option tax (flag for adviser; not advice)**: net option P&L per calendar year in TRY at each
  cash flow's CBRT rate, under the 2026 progressive tariff, **no Yİ-ÜFE indexation** on the
  option book. Option losses offset **only option gains within the same year** (conservative);
  a negative net option year contributes zero. B2 (flat 20 %) applies the same conservative
  netting. A **non-selectable** sensitivity row lets option losses offset equity gains
  (`offset = 1`) on the three after-tax windows.

## 5. Sanity check before any QQQ arm

Price **PPUT's own rule** with this pricer on SPX + VIX: one SPX unit, 5 % OTM 1-month put,
third-Friday roll, intrinsic settlement, S&P 500 dividends from `^SP500TR`, close-to-close
approximation of the published SOQ/VWAP roll. Compare with the published PPUT over 1990–1999 and
2001–2026. **Pass criteria**: at `s = 2.0`, `|synthetic CAGR − PPUT CAGR| ≤ 1.5 pp/yr` in **both**
windows and the monthly-return correlation `≥ 0.95` in both. The ±50 % rows are reported. If the
check fails, the study stops at a feasibility report and no QQQ arm is run.

## 6. Arms (five, every parameter fixed; no tuning)

| Arm | Tenor | Long put | Short put | Sizing | Vol target | Roll gate |
|---|---|---|---|---|---|---|
| **K** | — | — | — | — | 0.35 | — |
| **H1** | 1 month | 10 % OTM | — | spot notional, ratio 1 | 0.35 | w > 0 |
| **H2** | 2 months | 10 % OTM | 25 % OTM, same units | spot notional, ratio 1 | 0.35 | w > 0 |
| **H3** | 3 months | 15 % OTM | — | 1 % NAV/yr premium (0.25 % per roll) | 0.35 | w > 0 |
| **H4** | 2 months | 10 % OTM | 25 % OTM, same units | spot notional, ratio 1 | **0.45** | w > 0 |
| **H5** | 2 months | 10 % OTM | 25 % OTM, same units | spot notional, ratio 1 | 0.35 | **E2 on**, w > 0 |

H4's target series is the production rule with `vt = 0.45` (verified bit-identical to the engine
at 0.35). H5 does not hedge the early re-entry leg: at a roll where E2 is off no new option is
opened; an existing option is held to expiry. All arms keep K's fill rule (A1u + H2) unchanged.

## 7. Windows and stages

- **Reduced main window** `2001-01-23 → 2026-09-29` (first VXN session; the full 2000-03-09
  window is not VXN-covered). K is re-run on it and is the control for every table.
- **After-tax windows** (`$100k`, progressive, liquidated): 2001-01-23 → end, 2010-01-04 → end,
  2017-01-03 → end.
- **Pre-tax table**: base/calibrated × m = 1/2/4 on the reduced window.
- **Clean bootstraps** B1-recent (progressive) and B2 (flat 20 %): seed 20261011, block 20,
  5,000 pre-tax / 1,000 after-tax paths, $100k, the fixed 2015-09-01 → 2026-09-30 calendar with
  its realised USD/TRY and Yİ-ÜFE; QQQ returns block-resampled; each arm vs K on the same paths.
  The overlay uses the path's drawn VXN and reconstructed QQQ close.
- **OOS (reduced, pre-registered)**: ^SPX + ^VIX, measured from the first session with the
  253-session warm-up (1990) to 1999-12-29, pre-tax, calibrated 2× leg, m = 2, same rule; no
  after-tax OOS is possible (the TRY calendar starts 2000). **G7 is this one window** (Δ CAGR ≥ 0
  and Δ DD ≤ +3 pp); **G8 is n/a** (no OOS bootstrap).
- **Report-only**: drawdown episodes (>10/20/30 %), worst 21/63-session loss, annual hedge cost
  (premium − payoff over average NAV), and the option cash flows in 2001–02, 2008, 2020 and 2022.

## 8. Gates (all must pass; vs K on the reduced window, m = 2)

G1–G9 are `ewma_band_v1`'s, with the main window changed to the reduced one:

- **G1** (base and calibrated): CAGR ≥ K, **or** Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp.
- **G2** (both): max DD ≤ K + 1 pp.
- **G3** (both): 2017+ CAGR ≥ K − 1 pp.
- **G4**: CAGR above K in ≥ 2 of 3 sub-periods (start–2008, 2009–2016, 2017–end).
- **G5** (both): block-20 pre-tax bootstrap median of arm − K ≥ 0.
- **G6** (both): G1 at m = 1.
- **G7** (reduced): the 1990–1999 SPX+VIX OOS: Δ CAGR ≥ 0 and Δ DD ≤ +3 pp.
- **G8**: n/a (reduced OOS, pre-registered).
- **G9**: equity turnover ≤ 2 × K (K's own turnover, including option-funding sales).
- **AT1**: after tax at $100k ≥ K in **all three** windows.
- **AT2**: B1-recent and B2: median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ **55 %**.

**Selection rule.** An arm is a candidate iff every gate passes. If more than one does, the
selected arm maximises the **minimum** after-tax Δ vs K across the three windows (maximin). If
none passes, K stays production.

## 9. Reproduction requirement

`--harness` must: (a) run `python -m research.fast_engine.parity --quick` and PASS; (b) reproduce
K's published anchors (pre-tax 15.7649 / 15.1785 % base/cal m2, max DD 46.670 / 46.795 %;
m1 16.0173 / 15.4303 %; after tax at $100k 10.7533 / 15.2354 / 23.9720 %, max DD 46.670 /
44.627 / 34.977 %; R2 reference 15.4766 / 14.8961 % and 10.1600 / 14.7059 / 22.8861 %);
(c) prove zero flows in the copied kernel reproduce `fe_sim` **bit-for-bit** on the NAV path,
the trace and every scalar (C0/A1u/H2/K × pre-tax/progressive/flat); (d) prove the bootstrap
driver with K and zero flows reproduces `fe_boot.boot` bit-for-bit (64 paths, both financing
models, both tax modes); (e) prove `main_inputs_vt(0.35)` is bit-identical to the engine; and
(f) pass the PPUT sanity check of §5.

## 10. Caveats (stated before the run)

Black–Scholes with a single VXN level and a constant multiplicative skew, no term structure, no
vol-of-vol, no jump premium; the roll is at the close while the real PPUT uses the SOQ and a VWAP;
option marks are model values and the drawdown path inherits them; 5 % of premium per side is a
modelling assumption, not a measured QQQ option spread; the tax treatment of options is
pre-registered and **not adviser-confirmed**; the after-tax OOS is not possible before 2000; the
2015-09 → 2026-09 bootstrap calendar cannot price another FX/inflation regime; one history per
cell. **Not tax advice.**

## 11. Pre-registration honesty note

Before this commit, on this branch, exploratory arm simulations were run to validate the copied
kernel and size the compute: all six labels (K, H1–H5) on the reduced window, pre-tax and
progressive, plus a first-roll/2020-flow diagnostic. Every parameter above (the five suggested
arms, 1/2/3-month tenors, 10/25/15 % strikes, vt 0.45, 1 %/4 budget, `s = 2.0`, `q = 0.006`,
5 % per side, the VXN window, the gates and the selection rule) was fixed in `config.py` **before**
those probes; no parameter, arm, gate or selection rule was changed after seeing any exploratory
result. The probes showed the direction of the answer (the insurance is expensive relative to
what K already avoids). The formal `--run` is the pre-registered evidence; the exploratory
numbers are not reported as results.

## 12. Amendments

**A1. Calibrated pre-tax rows used the base synthetic QLD basket (bug fix, after the first complete
run).** The first complete `--run` produced identical base and calibrated pre-tax rows: `run_arm`
always used `win["o"], win["c"]`, which are `main_inputs(0.0)`. The fix loads the
`main_inputs(0.0075)` / `main_inputs_vt(0.0075, 0.45)` basket and selects it for the calibrated
rows. This changes only the calibrated pre-tax cells (K 17.901 → 17.289 %, and the arms
similarly); the base rows, the after-tax windows, the bootstrap (which already had its own
calibrated legs) and every gate outcome are unchanged. No arm, parameter, gate, window or
selection rule was changed. The run was repeated twice after the fix; `RESULTS.json` is
byte-identical (`60ee19f7…8de19a2`).
