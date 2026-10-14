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

## Amendments (before any non-control arm was simulated on real data)
The pre-registration above is unchanged: same arms, same gates, same selection
rule. Three implementation defects found while resuming the interrupted run
(after the pre-registration commit, before the first real `--run`) were fixed:

1. Arm lookup. The merged screen keyed the per-arm target arrays by
   `ARM_PARAMS[arm]["kind"]` (= "vol20" for every arm), a KeyError leftover from
   the ewma_band_v1 harness. Fixed to index by arm name. No effect on the
   pre-registered arms or on C0.

2. Undefined name. `main()` still referenced a removed `etf_results` key and the
   vestigial ETF loader helpers from ewma_band_v1; the key and the dead helpers
   were deleted (the ETF cross-check is not part of this pre-registration).

3. After-tax bootstrap control instrument (substantive). In tax_asym_v2's
   bootstrap the control arm C0 was run on the *unlevered* QQQ total-return
   series (`qo/qc`) while every other arm ran on the 2x synthetic QLD* (`so/sc`),
   so any leveraged candidate beats C0 by construction (its C0 median max-DD is
   ~35%, not ~50%). Here C0 runs on the same synthetic QLD* (base financing) as
   the candidate arms, so the after-tax delta isolates the vol target. This
   changes the after-tax deltas versus tax_asym_v2 and is why the B1-recent/B2
   numbers are not comparable with that study's.

Also removed: the unused `sha256`, `_run_one_path_market`, `main_run_after_tax`,
and the tax_asym A2* arm registrations / non-production trade modes.
