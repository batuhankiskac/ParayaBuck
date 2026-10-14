# credit_vote_v1 — credit spreads and the yield curve as macro risk-off (2026-10-14)

> Evidence for this report (`research/credit_vote_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/credit-vote-v1-2026-10`.

**Verdict: FAIL — no arm passes.** All four credit/yield-curve overlays lose on
the block-20 bootstrap and the 2017+ window; none clears G1–G3 on both financing
models. Production E2 is unchanged.

## Arms

| Arm | Rule |
|---|---|
| C0 | Production E2 (control). |
| K1 | Veto: market on iff E2 on AND BAA10Y <= its 200-session SMA (daily, 1-session lag). |
| K2 | 4-vote majority: market on iff >= 3 of {SMA200, TSM252, DON126, credit vote}. |
| K3 | Curve veto: cash while 10y-3m has been inverted for 20+ consecutive sessions. |
| K4 | Credit replaces TSM252 in the 2-of-3 majority. |

Credit details in `PREREGISTRATION.md`; monthly (BAA-AAA, GS10-TB3MS) with a
one-month lag is used for the US OOS windows only.

## Main window (2000-03-09 → 2026-09-29, 6,679 sessions, m = 2)

| Arm | CAGR base | CAGR cal | max DD base | max DD cal | 2017+ CAGR base | boot20 median Δ base | boot20 median Δ cal |
|---|---|---|---|---|---|---|---|
| C0 | 14.67% | 14.10% | 49.56% | 49.65% | 32.44% | 0.00 pp | 0.00 pp |
| K1 | 9.45% | 9.08% | 46.47% | 46.91% | 16.77% | −2.56 pp | −2.38 pp |
| K2 | 11.42% | 10.89% | 44.82% | 44.87% | 24.70% | −0.76 pp | −0.72 pp |
| K3 | 9.79% | 9.33% | 48.05% | 48.12% | 20.49% | −1.21 pp | −1.18 pp |
| K4 | 11.57% | 11.02% | 58.86% | 58.94% | 27.84% | −0.02 pp | −0.01 pp |

## OOS (US windows only; reduced gate pre-registered)

| Window | C0 CAGR | K1 | K2 | K3 | K4 |
|---|---|---|---|---|---|
| ^GSPC 1957–1999 | 10.61% | 5.12% | 8.70% | 11.64% | 9.57% |
| M1 ^IXIC 1975–1999 | 20.74% | 9.35% | 22.21% | 20.95% | 23.23% |
| M2 ^GSPC 1935–1956 | 11.26% | 9.24% | 8.60% | 11.26% | 7.61% |

Pooled OOS bootstrap median (arm − C0): K1 0.00 pp, K2 0.00 pp, K3 0.00 pp,
K4 +0.0049 pp (over 4,000 paths; only K4 marginally positive, and all fail on
the main and 2017+ metrics).

## Gates

| Arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 |
|---|---|---|---|---|---|---|---|---|---|
| K1 | F | P | F | F | F | F | F | P | P |
| K2 | F | P | F | F | F | F | F | P | P |
| K3 | F | P | F | F | F | F | P | P | P |
| K4 | F | F | F | F | F | F | F | P | P |

G7 uses the reduced 3-window gate (median ≥ 0, CAGR ≥ C0 − 0.5 pp in all 3,
DD within 3 pp in all 3, max DD diff ≤ 8 pp).

## Interpretation

The credit/yield-curve overlays that widen the cash rule (K1, K2, K4) cut the
2000–2026 CAGR by 3–5 pp and the 2017+ CAGR by 5–15 pp; the curve veto K3
matches that pattern (−12 pp in 2017+) despite being the one signal with some
OOS improvement. K4 takes the deepest drawdown (+9 pp). None clears the
bootstrap gate. Credit and curve information, at least in these simple forms,
destroys trend-following carry rather than reducing bear-market damage.

One amendment to the pre-registration is noted in `PREREGISTRATION.md`
(`credit_up_from_raw_daily` rolling-mean fix).

## Commit

Pre-registration commit `f16c0dc`, fix `c99a15b`; run output in
`research/credit_vote_v1/RESULTS.json`, `SUMMARY.txt`.
