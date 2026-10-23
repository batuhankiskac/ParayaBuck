# collar_v1 — collar overlays (long QQQ put + short QQQ call) on production K

Status: **candidate study with gates.** Committed before any collar arm was simulated on real data.
Owner request 2026-10-23 ("try them all"), candidate 2 of the short-term follow-up list.

## 1. Question

`put_hedge_v1` showed protective puts fail after tax because of their premium (3.8–4.7 pp/yr for
notional hedges). A **collar** finances the put by selling an out-of-the-money call on the same
units. It caps the upside the strategy needs (`short_term_restore_v1`: the sessions after a
pullback are the strongest), so the prior is negative; this measures how negative.

## 2. Everything except the arms is `put_hedge_v1` unchanged

Pricer, VXN data, skew `s = 2.0`, `q = 0.006`, DTB3 rate, third-Friday roll, 5 % premium cost per
side, notional sizing (`2 · w · NAV / S`, ratio 1, only while `w > 0`), premium/credit at the roll
close (QLD funding sale if cash is short), daily BS mark, liquidation at the window end, option tax
(progressive, no indexation, option losses offset only option gains in the same year; `offset = 1`
sensitivity non-selectable), the reduced main window 2001-01-23 → 2026-09-29, after-tax windows
2001 / 2010 / 2017 at $100k, both bootstraps (seed 20261011, block 20, 5,000 / 1,000 paths), the
^NDX 1990–1999 OOS window, the PPUT sanity check, and the gates G1–G7, G9, AT1, AT2 exactly as in
`PUT_HEDGE_V1_PREREGISTRATION.md` (copied verbatim in this directory). G8 n/a as there.

## 3. The only mechanism change: a short call leg

`short_otm < 0` means the short leg is a **call** at strike `K_c = (1 − short_otm) · S_roll`
(e.g. −0.05 → 105 % of spot), same units as the long put, priced on the same model by put-call
parity (`C = P + S e^{−qT} − K e^{−rT}`, with `σ(K_c) = VXN · (1 + s · ln(S / K_c))`, i.e. the skew
makes OTM calls cheaper), credit received net of the 5 % cost, settled at intrinsic `max(S − K_c, 0)`
at expiry, marked daily, bought back at model value + 5 % at the window end. `short_otm ≥ 0` is
the `put_hedge_v1` kernel bit for bit (checked: H1/H2/H3 NAV and after-tax CAGR identical).

## 4. Arms (fixed)

| arm | tenor | long put | short call | sizing |
|---|---|---|---|---|
| **K** | — | — | — | — |
| **Q1** | 1 month | 10 % OTM | 5 % OTM | notional, ratio 1 |
| **Q2** | 1 month | 10 % OTM | 10 % OTM | notional, ratio 1 |
| **Q3** | 3 months | 15 % OTM | 10 % OTM | notional, ratio 1 |
| **Q4** | 1 month | 5 % OTM | 5 % OTM | notional, ratio 1 |

Selection: among arms passing every gate, the highest after-tax CAGR on the 2001 window. None
passing → nothing selected.
