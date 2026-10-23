# mf_sleeve_v1 — a managed-futures (trend-following) sleeve beside K

> Evidence: branch `claude/project-thread-wn9fnc` (research worktree on top of tag
> `archive/research/closeout-2026-10`): `research/mf_sleeve_v1/` (proxy builder, registration,
> PREREGISTRATION.md), plugin `research/ideas/MF1.py`, results
> `research/batch_screen/results/mf_sleeve_v1/`. Nothing shipped; production K unchanged.

## Verdict (plain language)

**A trend-following sleeve does not raise K's return. It buys a smaller drawdown, a little more
cheaply than lowering the vol target, and costs 0.5–1.9 pp/yr after tax.** All three arms fail
Stage 1 on the after-tax gate.

| arm | after tax 2000→ | 2010→ | 2017→ | max DD after tax (2000→) |
|---|---|---|---|---|
| K (control) | 10.75 % | 15.24 % | 23.97 % | 46.7 % |
| S10 (10 % sleeve) | 10.26 % (−0.49) | 14.13 % (−1.11) | 22.23 % (−1.74) | 42.6 % |
| S20 (20 % sleeve) | 9.52 % (−1.23) | 12.69 % (−2.55) | 19.97 % (−4.00) | 39.3 % |
| S30 (30 % sleeve) | 8.85 % (−1.90) | 11.50 % (−3.74) | 18.24 % (−5.73) | 38.0 % |

Clean bootstrap B1-recent median Δ is positive (+0.36 / +0.53 / +0.58 pp, 58–62 % of paths)
and B2-flat about zero (+0.05 / −0.10 / −0.35 pp): on resampled markets without K's long Nasdaq
bull run the sleeve's diversification pays roughly for itself, on the realised history it does
not.

**Comparison with the other drawdown knob.** `vt_level_k_v1`: vt 0.30 costs −0.87 / −1.07 /
−1.83 pp for 2.0 pp of after-tax max DD. S10 costs −0.49 / −1.11 / −1.74 pp for 4.1 pp. If the
owner ever wants less drawdown, a 10 % sleeve is the cheaper way per point; it is not a way to
earn more.

## Caveat: the proxy is weak

No free daily managed-futures series reaches 2000 (DBMF 2019+, KMLM 2020+), so the sleeve is a
nine-market time-series-momentum proxy (`build_proxy.py`, parameters in PREREGISTRATION.md).
It gets 2008 right (+33 %) but misses 2014 and 2022 (−4 % each, where the SG Trend Index gained
about +19 % and +27 %) because nine markets cannot copy a 50–100-market CTA book. A real fund
would likely do better than the proxy in those years. Simple arithmetic still bounds the answer:
a CTA index earning ~5 %/yr before tax next to K's ~16 % costs about `s × 11 pp` before
rebalancing gains, so even a faithful sleeve is very unlikely to turn the sign. The real-fund
check (DBMF/KMLM 2019+) is pending network access to the Yahoo chart API.
