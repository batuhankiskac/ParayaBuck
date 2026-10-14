# shortside_v1 — inverse Nasdaq-100 in downtrends

**Verdict: FAIL.** No arm passes all gates (G1–G9). Shorting in downtrends
helped in-sample main-window CAGR/DD for H4 (15.76% / 41.25% vs C0
14.67% / 49.56%) but fails the block-20 bootstrap (−2.00 pp median), the
sub-period gate (only P1), and the pooled OOS bootstrap (−0.81 pp). Every
other arm loses more. Production E2 remains unchanged (cash when off).

## Method

Base C0 = production E2 1.1.0. Inverse synthetics PSQ* (−1x) and QID* (−2x)
use daily-reset total return with 0.95%/yr expense and conservative T-bill
credit (max(0, DTB3 − 0.75% base / −1.5% calibrated)). All arms use the
production E2 vote (0–3) and decide at the close, fill next open, 0.5 pp band,
4 bps × m costs. Six OOS windows and the block-20 bootstrap as in
ewma_band_v1.

Arms: H1 PSQ* at min(1, 0.20/vol20) when 0 votes; H2 when E2 off (0–1
votes); H3 QID* at min(1, 0.35/vol20_QID*) when 0 votes; H4 = H1 but only
while QQQ 20-session vol is below its trailing 252-session median.

## Main window (2000-03-09 → 2026-09-29, m=2)

| Arm | CAGR base/cal | Max DD base/cal | 2017+ base | Boot median Δ base/cal | Turnover |
|---|---|---|---|---|---|
| C0 | 14.67 / 14.10 | 49.56 / 49.65 | 32.44 | 0 | 5.97 |
| H1 | 13.18 / 12.56 | 47.37 / 47.57 | 29.03 | −4.00 / −4.06 | 10.01 |
| H2 | 12.08 / 11.43 | 51.04 / 51.23 | 31.00 | −6.61 / −6.68 | 11.70 |
| H3 | 11.71 / 11.11 | 53.10 / 53.28 | 26.22 | −7.54 / −7.55 | 9.75 |
| H4 | 15.76 / 15.16 | 41.25 / 41.47 | 31.50 | −2.00 / −2.02 | 8.56 |

H4 only wins P1 of the three sub-periods (P2/P3 slightly worse than C0).

## OOS and bootstrap

- Pooled OOS bootstrap (M1–M5, 10,000 paths) median Δ: H1 −2.19 pp,
  H2 −3.49 pp, H3 −4.33 pp, H4 −0.81 pp.
- OOS window median cagr Δ: H1 −0.015 pp, H2 −0.010 pp, H3 −0.030 pp,
  H4 −0.002 pp; all fail the 5/6 window and DD conditions (H1 DD rises
  in the 2000–02 windows).

## Gates

| Arm | Failed gates |
|---|---|
| H1 | G1 (both), G3 (both), G4, G5 (both), G6 (both), G7, G8 |
| H2 | G1, G2 (calibrated), G3, G4, G5, G6, G7, G8 |
| H3 | G1, G2, G3, G4, G5, G6, G7, G8 |
| H4 | G5 (both), G4, G7, G8 |

Only H4 also passes G1/G2/G3/G6/G9. Its failures are the bootstrap (−2.00 pp)
and the OOS gates: even though the main-window edge looks attractive, the
resampled paths overwhelmingly prefer cash.

## Amendments

After pre-registration, `screen.py` was corrected to call `trv2.simulate_multi`
instead of an undefined local helper and to cap weights per session rather
than across the time axis (caught by the harness, which now reproduces C0
to 0.00 pp). Re-run remained byte-identical after the fix.
