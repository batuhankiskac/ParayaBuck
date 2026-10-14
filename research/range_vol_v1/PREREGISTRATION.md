# range_vol_v1 — pre-registration (2026-10-14)

Written and committed with `screen.py`, `DATA_MANIFEST.json`, data copies and the
harness E2 deviation before any non-control arm is simulated on real data. Only
`--harness` (C0 = production E2) is run before this commit; the full `--run` path
was smoke-tested on synthetic random prices only.

## Question

On top of production E2 (C0), do range-based volatility estimators that use
daily OHLC (Parkinson 1980, Garman-Klass 1980, Yang-Zhang 2000) improve the
PAPER rule? This is a different information set from ewma_band_v1 (EWMA on
close-to-close) and vol_window_v1 (windows on close-to-close).

## Data

- Main QQQ OHLC: `data/historical/stress_robustness_v1_daily.csv` (from
  1999-03-10), high/low/open adjusted by `adjclose/close`.
- Financing / rates: `stress_robustness_v1_dtb3.csv`.
- OOS indices: `ewma_band_v1_index.csv`, `ewma_band_v1_gspc.csv`,
  `ewma_band_v1_tb3ms.csv` (index OHLC + rates).
- OOS ETFs (downloaded from Yahoo chart API): `range_vol_v1_spy.csv`,
  `range_vol_v1_dia.csv`, `range_vol_v1_iwm.csv`? (SPY, DIA, IWM).
- Harness C0 reference: `E2_INTEGRATION_DEVIATION.json`.

OHLC is scaled by `adjclose/close` on QQQ and on the ETFs. Volatility is
estimated on QQQ adjusted OHLC and converted to the QLD basis as 2x QQQ vol
(C0 remains on the harness synthetic QLD* close, vol20).

## Mechanics (unchanged)

Synthetic QLD* = daily-reset 2x on QQQ total return, 0.95%/yr expense + DTB3
(base) or DTB3 + 0.75% (calibrated); decide at close, trade next open; total
weight cap 0.98; cost 4 bps x m per unit traded (m=2 primary, m=1 gated);
idle cash at zero. Main window 2000-03-09 -> 2026-09-29. Signal: production E2
(`close > SMA200; 252-session price return > 0; close > 126-session max/min
midpoint`, 2 of 3). vt(v)=min(1, 0.35/v).

All arms use the production 0.5 pp resize band.

## Arms (five; no others)

- **C0**: production E2, vol20 (close-to-close) on synthetic QLD*.
- **G1x**: Yang-Zhang 20-session vol x2.
- **G2x**: Garman-Klass 20-session vol plus close-to-open gap variance x2.
- **G3x**: Parkinson 20-session vol scaled by the expanding (pre-date, no
  look-ahead) ratio of close-to-close to Parkinson vol, x2.
- **G4x**: average of 20-session close-to-close (QQQ x2) and Yang-Zhang 20 x2.

Window n=20 sessions for all estimators.

## OOS data-quality rule (pre-registered)

A market/window passes only if at most 5% of its sessions have `high==low` or a
placeholder open (`open == prior close` or open outside [low,high]). Quality-fail
markets are excluded from G7/G8; reported in RESULTS.

## Gates

Reuse ewma_band_v1 G1-G9. G7/G8 use only the quality-passing OOS markets
(M1-M5 indices after quality, plus SPY/DIA/IWM). G9 turnover <= 2x C0 (all arms
use the production band so this is loose).

## Deliverables

Run twice, byte-identical RESULTS.json; SUMMARY.txt; report
`docs/research/range_vol_v1.md`; no `src/` or `config/` changes.
