# E2 confirmation v1 (2026-10-12)

**E2 is not confirmed. It fails K5, the pooled block-20 bootstrap, so under
the pre-registered rule E2 is closed and VT35 stays unchanged.**

E2 is the 2-of-3 majority of SMA200, TSM252 and DON126 from
`signal_ensemble_v1`. This campaign tested it against C0 (the VT35 rule) on
five markets and periods where E2 had never been evaluated:

- **Historical results favour E2 again, by a small margin.**
  - E2's CAGR is at least C0's in 4 of 5 markets, with a median difference of
    +0.37 pp.
  - Max DD is lower or equal in 4 of 5 markets. The exception is the DAX
    (+4.4 pp).
  - The same holds at 1x costs, so K1–K4 all pass.
- **In short-trend bootstrap worlds the edge is zero.**
  - Pooled over 10,000 ten-year paths, the median of E2 − C0 is −0.001 pp.
    The gate requires ≥ 0.
  - E2 beats C0 on 49.95% of the paths, and the mean difference is
    +0.02 pp.
  - Only ^IXIC is positive (+0.08 pp).
  - The Monte Carlo standard error of the median is 0.014 pp. The miss is far
    inside the noise, so the honest reading is "no difference", not "slightly
    worse".
- **It repeats `signal_ensemble_v1`'s pattern.** E2's historical gains come
  from a few choppy years. Resampled in one-month blocks, those gains
  disappear. Seven windows now point the same way: E2 ≥ C0 in 6 of 7, with a
  median of +0.55 pp. Both bootstraps put the structural edge at about zero.

Evidence: `research/e2_confirmation_v1/` on branch `research/e2-confirmation-v1`.

- `PREREGISTRATION.md`, `screen.py`, `download_data.py`, `validate_data.py`,
  `VALIDATION.json`, `DATA_MANIFEST.json` and
  `data/historical/e2_confirmation_v1_{index,tb3ms}.csv` were committed in
  `392a83e`, before either arm was simulated on any of these markets.
- `RESULTS.json` and `SUMMARY.txt` come from `screen.py --run`. The run is
  offline and takes about 12 s. Two runs gave byte-identical `RESULTS.json`.

## Method

- **Markets and windows.** All are Yahoo price indices with no dividends.
  Each market uses its own signals.

  | Key | Index | Measured window | Sessions | T-bill |
  |---|---|---|---:|---|
  | M1 | ^IXIC Nasdaq Composite | 1975-01-02 → 1999-12-31 | 6,319 | DTB3 |
  | M2 | ^GSPC S&P 500 | 1935-01-08 → 1956-12-31 | 5,502 | TB3MS monthly (forward-filled) to 1953-12-31, then DTB3 |
  | M3 | ^N225 Nikkei 225 | 1966-01-19 → 2026-09-29 | 14,922 | DTB3 (USD proxy) |
  | M4 | ^GDAXI DAX | 1989-01-03 → 2026-09-29 | 9,546 | DTB3 (USD proxy) |
  | M5 | ^FTSE FTSE 100 | 1985-01-02 → 2026-09-29 | 10,544 | DTB3 (USD proxy) |

  M2–M5 start at the first session with a full 252-session warm-up. M2's data
  begin 1934-01-02, the first TB3MS month.
- **Instrument:** synthetic daily-reset 2x of the index's daily price return,
  minus 0.95%/yr expense, minus T-bill financing, minus the calibrated extra
  0.75%/yr.
- **Arms:**
  - **C0:** `SMA · vt`.
  - **E2:** `[SMA + TSM + DON ≥ 2] · vt`.
  - `vt = min(1, 0.35/vol20)` on the instrument; total weight capped at 0.98.
  - Every signal uses the index price close. TSM is the 252-session price
    return against cumulative T-bills.
- **Mechanics:** unchanged; the engine is imported from
  `trend_robustness_v2`.
  - Decide on the close, trade at the next open; 0.5 pp band.
  - Cost 4 bps × m, with m = 2 primary and m = 1 gated through K4.
  - Idle cash earns zero.
- **Opens:** placeholder opens are replaced by the previous close for both
  arms. A placeholder open is null, non-positive, O = H = L = C, or outside
  [low, high]. Counts in the measured windows:
  - M1: 2,474 (1975–84).
  - M2: all.
  - M3: 5,481 (1966–88).
  - M4: 1,237 (1989–93).
  - M5: 0, but Yahoo's ^FTSE open equals the previous close on most sessions
    since 2000.
- **Bootstrap (K5):**
  - Stationary block bootstrap, mean block 20, seed 20261011.
  - 2,000 paths per market, each 252 warm-up + 2,520 measured sessions.
  - Each market resamples its own measured sessions: overnight factor,
    intraday factor and T-bill rate jointly.
  - Both arms run on the same path at m = 2. The E2 − C0 CAGR differences are
    pooled over the 10,000 paths.

## Results (m = 2)

| Market | C0 CAGR | E2 CAGR | Δ CAGR | C0 max DD | E2 max DD | Δ DD | C0 / E2 Calmar | m = 1 C0 / E2 CAGR | Index price hold |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| M1 ^IXIC 1975–1999 | 20.60% | 20.71% | +0.11 | 38.6% | 38.6% | 0.0 | 0.53 / 0.54 | 20.84% / 20.97% | 18.39% / 36.0% DD |
| M2 ^GSPC 1935–1956 | 10.63% | 10.99% | +0.37 | 63.3% | 62.5% | −0.7 | 0.17 / 0.18 | 10.89% / 11.24% | 7.47% / 60.0% DD |
| M3 ^N225 1966–2026 | 5.61% | 6.16% | +0.55 | 73.8% | 69.0% | −4.8 | 0.08 / 0.09 | 5.89% / 6.46% | 6.52% / 81.9% DD |
| M4 ^GDAXI 1989–2026 | 9.68% | 9.65% | −0.03 | 49.4% | 53.8% | **+4.4** | 0.20 / 0.18 | 9.94% / 9.92% | 8.14% / 72.7% DD |
| M5 ^FTSE 1985–2026 | −1.15% | −0.05% | +1.10 | 87.9% | 83.4% | −4.4 | −0.01 / −0.00 | −0.77% / +0.30% | 5.30% / 52.6% DD |

On M3 and M5, both 2x trend arms trail the unlevered price index. These
indices spent decades in sideways or falling regimes: post-1989 Japan, and the
FTSE from 1999 to 2016. Whipsaw at 2x with a USD T-bill financing charge
eroded the result. On M5 the worst drawdown of each arm runs from 1998/1999
to 2025 and has not recovered.

### Gates

| Gate | Condition | Result | Value |
|---|---|---|---|
| K1 | E2 CAGR ≥ C0 in ≥ 4/5 markets | **pass** | 4/5 (M4 −0.03 pp) |
| K2 | median Δ CAGR > 0 | **pass** | +0.37 pp |
| K3 | Δ DD ≤ +3 pp in ≥ 4/5, never > +8 pp | **pass** | 4/5; max +4.37 pp (M4) |
| K4 | K1 and K2 at m = 1 | **pass** | 4/5; median +0.34 pp |
| K5 | pooled block-20 bootstrap median Δ CAGR ≥ 0 | **fail** | −0.001 pp (−0.00001) |
| **Decision** | all must pass | **E2 not confirmed: closed** | |

### Bootstrap (block 20, 2,000 × 10-year paths per market, m = 2)

| Market | Median Δ (E2 − C0) | Share E2 ≥ C0 | C0 median CAGR | E2 median CAGR |
|---|---:|---:|---:|---:|
| M1 | +0.083 pp | 53.3% | 21.84% | 22.06% |
| M2 | −0.032 pp | 48.9% | 5.29% | 5.09% |
| M3 | −0.041 pp | 48.8% | 1.16% | 1.26% |
| M4 | −0.012 pp | 49.6% | 3.57% | 3.43% |
| M5 | −0.018 pp | 49.3% | −0.54% | −0.60% |
| **Pooled (10,000)** | **−0.001 pp** | 49.95% | — | — |

The pooled interquartile range of Δ is −0.89 to +0.93 pp, and the mean is
+0.022 pp. These were added after the pre-registration and are report-only:
the Monte Carlo s.e. of the pooled median is 0.014 pp, and the 5–95% band from
resampling the paths is −0.024 to +0.022 pp. The gate result is a tie decided
by its sign convention.

## Report-only

### Whipsaw and time in market (m = 2)

| Market | Arm | Time in market | Round trips/yr | Losing share | Turnover/yr |
|---|---|---:|---:|---:|---:|
| M1 | C0 / E2 | 75.4% / 75.1% | 2.36 / 2.56 | 62.7% / 65.6% | 5.0 / 5.4 |
| M2 | C0 / E2 | 67.8% / 68.2% | 2.82 / 2.55 | 80.6% / 80.4% | 6.0 / 5.5 |
| M3 | C0 / E2 | 63.9% / 64.1% | 3.21 / 3.30 | 82.1% / 78.0% | 6.8 / 6.9 |
| M4 | C0 / E2 | 70.5% / 71.2% | 2.84 / 3.02 | 72.9% / 77.2% | 5.9 / 6.2 |
| M5 | C0 / E2 | 68.6% / 68.8% | 4.82 / 4.43 | 84.6% / 83.8% | 9.4 / 8.6 |

E2 disagrees with SMA alone on only 0.8–2.0% of decision closes. It does not
reduce round trips consistently: fewer on M2 and M5, more on M1, M3 and M4.

### Worst drawdown episodes (m = 2, peak → trough, recovered)

| Market | C0 | E2 |
|---|---|---|
| M1 | 38.6% 1983-06 → 1984-12 (1986-02); 32.6% 1986-07 → 1989-01 | 38.6% same; 35.6% 1992-02 → 1992-10; 33.2% 1986-07 → 1989-01 |
| M2 | 63.3% 1937-03 → 1942-09 (1945-09); 34.5% 1946-05 → 1949-07 | 62.5% 1937-03 → 1942-09 (1945-06); 34.8% 1946-05 → 1949-07 |
| M3 | 73.8% 1989-12 → 2012-11 (still open at 2026-09); 51.8% 1973-01 → 1975-12 | 69.0% 1989-12 → 2012-11 (recovered 2026-01); 51.3% 1973-01 → 1975-12 |
| M4 | 49.4% 1998-07 → 1999-10 (2004-01); 46.5% 2018-01 → 2020-10 | 53.8% 1998-07 → 1999-10 (2005-08); 53.2% 2018-01 → 2020-10 |
| M5 | 87.9% 1998-04 → 2025-04 (open); 50.1% 1987-07 → 1988-11 | 83.4% 1999-04 → 2025-04 (open); 47.3% 1987-07 → 1988-11 |

### Where the differences come from (m = 2, calendar-year Δ, E2 − C0)

As in `signal_ensemble_v1`, the differences are concentrated in a few years:

| Market | Years E2 gained | Years E2 lost |
|---|---|---|
| M3 | 2024 (+14.4 pp), 2018 (+12.3), 2020 (+10.6), 2012 (+10.5) | 2016, 1979 (−6.1 each), 1977 (−5.6) |
| M5 | 1998 (+17.2 pp) | 2011 (−3.7) |
| M4 | 1992 (+7.7), 2015 (+7.5) | 2018 (−7.9), 1998 (−5.9) |
| M2 | 1944 (+9.2), 1941 (+5.2) | 1937 (−5.5), 1947 (−3.4) |

### Combined view with `signal_ensemble_v1` (m = 2, calibrated)

| Window | C0 CAGR / DD | E2 CAGR / DD | Δ CAGR |
|---|---|---|---:|
| QQQ TR 2000-03 → 2026-09 (seen) | 12.83% / 49.6% | 14.25% / 49.6% | +1.42 |
| ^GSPC 1957–1999 (seen) | 9.70% / 39.1% | 10.26% / 32.6% | +0.56 |
| M1 ^IXIC 1975–1999 | 20.60% / 38.6% | 20.71% / 38.6% | +0.11 |
| M2 ^GSPC 1935–1956 | 10.63% / 63.3% | 10.99% / 62.5% | +0.37 |
| M3 ^N225 1966–2026 | 5.61% / 73.8% | 6.16% / 69.0% | +0.55 |
| M4 ^GDAXI 1989–2026 | 9.68% / 49.4% | 9.65% / 53.8% | −0.03 |
| M5 ^FTSE 1985–2026 | −1.15% / 87.9% | −0.05% / 83.4% | +1.10 |

E2 ≥ C0 in 6 of 7 windows, with a median of +0.55 pp. Both campaigns'
bootstraps put the difference at about zero: −0.05 pp on QQQ and −0.001 pp
pooled here. Two readings fit the evidence:

- The historical gain is a real but small edge that only shows up across many
  regimes.
- It is the luck of specific choppy years, which resampling removes.

The pre-registered rule does not take a position between them, and it closes
E2.

## Data issues

- **Validation** (`VALIDATION.json`; nothing dropped):
  - No |daily return| > 25% on any index. Every move above 10% is a known
    event.
  - All calendar gaps are exchange holidays: ^N225 64, ^GDAXI 10.
  - Stale closes:
    - ^N225 has one broken run, 21 identical closes from 1972-03-31 to
      1972-04-28. It was kept as served; the month's move lands on
      1972-05-01, for both arms.
    - Other runs are 3–4 bars.
  - Null-close holiday rows were dropped at download: ^N225 625, ^GDAXI 168,
    ^FTSE 183.
  - Saturday sessions are missing: ^GSPC to 1952, ^N225 in early decades.
- **Found after the run (M1).** I reconciled C0 against
  `gold_riskoff_oos_v1`'s CASH arm (17.37% / 44.3%, ^IXIC 1975-01-02 →
  1998-12-30):
  - The difference comes only from the open rule. That campaign set every
    ^IXIC open before 1985 to the previous close. This campaign keeps the 56
    non-flat 1984 opens.
  - With that campaign's rule this code reproduces 17.37% / 44.26% exactly.
  - Some of those late-1984 bars carry one-day close spikes that reverse at
    the next open (1984-11-27 +3.0%, 1984-12-10 +4.6%). These are probably
    data errors, below the 25% check.
- **Effect of the open rule** (report-only, gates unchanged):
  - M1 full window: C0 20.14% / 44.3%, E2 20.25% / 44.3%. Δ CAGR is still
    +0.11 pp, Δ DD still 0.
  - The pooled bootstrap median is still −0.001 pp.
  - So no gate outcome depends on it.

## Caveats

- **Price-only indices.** TSM's hurdle is harder by the dividend yield. Both
  arms lack dividends, but only E2 uses TSM.
- **USD T-bills as a proxy for local rates on M3–M5.** UK rates were often far
  higher, and Japanese rates far lower, than US rates. The proxy affects
  financing for both arms, and the TSM hurdle for E2 only.
- **TB3MS is a monthly average** used from the 1st of its month (M2 before
  1954). This is a small look-ahead in the hurdle; rates were 0.01–3.2% then.
- **Many fills at the previous close,** identically for both arms.
- **C0 on most of M1's window was already seen,** from `gold_riskoff_oos_v1`.
  E2 was not.
- **Synthetic 2x.** No 2x product existed in most of these windows.
- **Seen before writing:** the `signal_ensemble_v1` results, the
  `gold_riskoff_oos_v1` C0-on-^IXIC result, and general market history,
  including Japan's post-1989 bear market and the FTSE's flat 1999–2016
  period.

## Decision

E2 is **not confirmed** and is **closed**. It passes K1–K4 and fails K5 by
0.001 pp, which is a statistical tie. VT35 remains the PAPER identity with no
parameter change. No production campaign follows. No code, configuration or
runtime changed.
