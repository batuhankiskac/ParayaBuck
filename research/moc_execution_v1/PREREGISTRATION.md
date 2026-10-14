# moc_execution_v1 — pre-registration (2026-10-14)

## Question
Production decides at the close and fills at the next open, so every trade
carries the overnight gap after the signal. Alpaca supports market-on-close
orders (`time_in_force="cls"`), so the bot could compute the signal a few
minutes before the close and fill at that close. How much CAGR/DD does
same-close execution add vs next-open, and is it robust to the signal being
computed on a pre-close price instead of the true close?

## Control C0
Production E2 exactly (ewma_band_v1 C0): decide at close t, fill at next open,
cost 4 bps x m, `simulate_band` / `trv2.simulate_multi` equality must hold.

## Arms (fixed)
- **C0**: decide at close t, fill at next open (control).
- **X1**: decide at close t, fill at close t (entry, exit, resize). Cost 4 bps x m.
- **X1n**: X1 execution, but the decision uses close x (1+e), e~N(0, 0.25%)
  i.i.d. per session (fixed base seed 20261014; 20 seeds). Fill at true close.
  Report median and worst; gate on median.
- **X2**: exits and downward resizes at close t, entries/upward resizes at
  next open (mixed).
- **X3 (report-only, not a candidate)**: fill at close t+1 (one-day delay).

## Data
stress_robustness_v1 daily + dtb3 (committed); ewma_band_v1 index (real-open
OOS windows). Usable OOS windows (all opens in the measured window are real):
IXIC 1990-04-06..1999-12-31, GDAXI 1997-05-30..2026-09-29,
N225 2017-03-28..2026-09-29, FTSE 1985-01-02..2026-09-29. ^GSPC
pre-1957 and ^IXIC pre-1984 windows are **not** usable for an open-vs-close
comparison (placeholder opens) and are excluded; G7/G8 reduced from 6 to 4
windows with pass thresholds n_c >= 3 and n_d >= 3.

## Gates
Reuse G1-G8 unchanged except reduced G7/G8 as above. G9 turnover <= 2x C0.
Selection: highest m=2 base CAGR among passing candidates.

## Decomposition (report-only)
QQQ total return split into overnight close->open and intraday open->close
over 2000-2026, and how much of C0's return arrives in each.

