# vt_level_tax_v1 pre-registration

## Question
Is production E2's vol target 0.35 the right level, judged both pre-tax and
after Turkish tax? Test E2 with vol target 0.30, 0.35 (= C0), 0.40, 0.45,
0.50, and no target (= tax_frontier A1 context). Everything else = production.

## Arms (fixed)
- C0: E2, vol target 0.35 (production).
- VT30, VT40, VT45, VT50: E2 with vol target 0.30/0.40/0.45/0.50.
- NOVT: E2 with no vol target (weight = CAP = 0.98 when signal).

All arms: E2 price-close votes (SMA200, TSM252>0, DON126; 2 of 3),
daily-reset synthetic QLD* (base 0.95%+DTB3 and calibrated +0.75%),
decide at close fill next open, cost 4 bps x m, 0.5 pp resize band, total cap 0.98.

## Data
Existing historical files only: ewma_band_v1_*, stress_robustness_v1_*,
after_tax_tr_v1_*. See DATA_MANIFEST.json.

## Gates
Pre-tax ewma_band_v1 G1-G8 for each candidate vs C0, plus leverage_frontier_v1
tail gate P(DD>70%) <= C0 bootstrap +5 pp. Candidate also must beat C0
after tax in all three windows (2000/2010/2017) at $100k and B1-recent/B2
median delta >= +0.3 pp with share >0 >= 60%. Plateau: a neighbouring target
must also pass G1-G3.

## Selection
A target != 0.35 is candidate only if all above. Plateau neighbour passes G1-G3.
