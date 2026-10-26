# collar_v1 — collar overlays (long QQQ put + short QQQ call) on production K

> Evidence: commit `c7e4e59c` (`research/collar_v1/`), removed from the tree in the
> following commit; not on `main`. Built on tag `archive/research/put-hedge-v1-2026-10` (pricer,
> data, harness). Pre-registration and the short-call leg were committed (local research commit
> `618650a3`, unchanged in the evidence commit) before any collar arm was simulated.

**Verdict (2026-10-23): FAIL. No arm passes; nothing is selected.** Financing the put by selling
an OTM call makes the hedge cheap at the roll but expensive over time, because the call sells
exactly the rallies K lives on:

- **After tax every collar loses to K in all three windows**: Q1 (put 10 % / call 5 %, monthly)
  −0.38 / −0.13 / −0.85 pp; Q2 (10 % / 10 %) −1.82 / −1.28 / −1.14; Q3 (3-month 15 % / 10 %)
  −3.50 / −6.09 / −8.77; Q4 (5 % / 5 %) −4.70 / −4.73 / −9.17.
- **The bootstraps are decisive**: median Δ vs K −3.9…−7.1 pp pre-tax and **−4.9…−7.8 pp after
  tax**, 3–10 % of paths positive. Q1's near-miss on the realised path (pre-tax 18.26 % vs 17.90 %,
  passing G1–G4, G6, G7) rests on one event: the March 2020 crash paid the Q1 put **$460k** on a
  $100k start, worth +35 pp of NAV in that episode. Without a 2020-type crash in the path, the
  capped upside costs ~4–5 pp/yr.
- **The drawdown benefit is real only for the tight collar**: Q4 cuts max DD to **29.9 %**
  (K 38.5 % on this window) but costs 9 pp/yr of hedge drag. Q1/Q2/Q3 change max DD by
  −1.1 / +1.9 / +3.7 pp.
- Letting option losses offset equity gains (non-selectable tax sensitivity) adds +0.6…+3.1 pp
  and still leaves Q2–Q4 negative; Q1 would turn slightly positive on the realised path only,
  with the bootstrap unchanged.

With `put_hedge_v1` (puts alone) this closes listed-option hedging of K on this pricer.

Control = K on the VXN-covered reduced window 2001-01-23 → 2026-09-29 (pre-tax 17.90 %, max DD
38.46 %, after tax 12.42 % at $100k), reproduced through the same harness as `put_hedge_v1` (K
anchors, zero-flow parity, bootstrap parity, PPUT pricer sanity check: all PASS).

## 1. What changed vs `put_hedge_v1`

Only the arms and one mechanism: a negative `short_otm` is a **short call** at
`(1 − short_otm) · S`, same units as the long put (notional `2 · w · NAV / S`), priced on the same
VXN Black–Scholes model by put-call parity with the same skew (`σ(K) = VXN · (1 + 2 ln(S/K))`, so
OTM calls are cheaper), credit received net of 5 %, intrinsic settlement, daily mark, bought back
at the window end. The put-only path is unchanged bit for bit: re-running `put_hedge_v1`'s H1, H2,
H3 through the new kernel reproduces their NAV paths and after-tax CAGRs exactly. A smoke check
pins the call price against a direct Black–Scholes call (max error 9 × 10⁻¹⁵) and its expiry
payoff.

## 2. Results

| arm | tenor | put / call OTM | pre-tax CAGR | max DD | after tax 2001 | Δ 2001 / 2010 / 2017 (pp) | B1 Δ / share | B2 Δ / share |
|---|---|---|---:|---:|---:|---|---:|---:|
| **K** | — | — | **17.90** | **38.46** | **12.42** | — | | |
| Q1 | 1 m | 10 % / 5 % | 18.26 | 37.40 | 12.05 | −0.38 / −0.13 / −0.85 | −4.89 / 10 % | −4.70 / 12 % |
| Q2 | 1 m | 10 % / 10 % | 16.24 | 40.38 | 10.60 | −1.82 / −1.28 / −1.14 | −5.30 / 3 % | −5.17 / 4 % |
| Q3 | 3 m | 15 % / 10 % | 15.24 | 42.14 | 8.92 | −3.50 / −6.09 / −8.77 | −7.83 / 3 % | −7.68 / 4 % |
| Q4 | 1 m | 5 % / 5 % | 13.94 | 29.89 | 7.72 | −4.70 / −4.73 / −9.17 | −7.72 / 6 % | −7.52 / 8 % |

Pre-tax clean bootstrap median Δ (base): Q1 −3.95, Q2 −4.90, Q3 −7.13, Q4 −6.59 pp.
OOS (SPX + VIX 1990-06 → 1999-12, pre-tax, calibrated): Q1 +1.06 pp (DD −0.7), Q2 −2.26,
Q3 −2.75, Q4 −4.51 pp.

Crash episodes (net option P&L on $100k; NAV return arm vs K): 2020 Q1 +$460k (+21.0 % vs
−14.7 %), Q4 +$346k; 2022 Q4 +$134k (−14.8 % vs −24.3 %); 2008: K already in cash, little effect;
2001–02 dot-com: no arm helps (−11.7…−15.6 % vs −13.0 %).

## 3. Gates

| gate | Q1 | Q2 | Q3 | Q4 |
|---|---|---|---|---|
| G1 | pass | FAIL | FAIL | FAIL |
| G2 | pass | FAIL | FAIL | pass |
| G3 | pass | pass | FAIL | FAIL |
| G4 | pass | FAIL | FAIL | FAIL |
| G5 pre-tax bootstrap | FAIL | FAIL | FAIL | FAIL |
| G6 | pass | FAIL | FAIL | FAIL |
| G7 (one OOS window) | pass | FAIL | FAIL | FAIL |
| G9 | pass | pass | pass | pass |
| AT1 after tax, 3 windows | FAIL | FAIL | FAIL | FAIL |
| AT2 B1 / B2 bootstraps | FAIL | FAIL | FAIL | FAIL |

G8 n/a (reduced OOS, as in `put_hedge_v1`).

## 4. Caveats

Model prices (VXN, fixed multiplicative skew, no term structure, no listed strikes); 5 % of
premium per side; rolls at the close. The skew makes OTM calls cheap, which works against the
collar (a flatter call wing would earn more credit); the bootstrap gap (−4.9 pp) is far larger
than any plausible pricing error (the pricer reproduces PPUT to ±0.7 pp/yr). Option tax treatment
as pre-registered in `put_hedge_v1`, not adviser-confirmed. No after-tax OOS (TRY data from 2000).

## 5. Reproduction

`python -m research.collar_v1.screen --smoke` (PASS, incl. the call checks), `--run` in **131 s**
(harness + PPUT sanity + every stage); two runs byte-identical (`RESULTS.json` SHA-256
`830f5c9590f14c8f63d2e490bf12c1107b6a5369afb08119c173fc719239567d`). `SUMMARY.txt`'s one-line
verdict prose is inherited from `put_hedge_v1`'s template (it mentions vol target 0.45, which no
collar arm uses); its tables and gates are this study's.
