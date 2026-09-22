# Shortlist — 2026-09-22 (pre-outcome; at most 5; zero is valid)

## Included

### 1. Spin-child post-distribution drift — `equity_spinoff_child_drift_v1` (PROPOSED, not frozen)

- Exact information source: EDGAR bulk index + `data.sec.gov` submissions JSON:
  parent/child Form 10-12B/A (effectiveness + exhibits), parent 8-K with Ex-99.1
  distribution detail (ratio, record date, distribution date, regular-way start).
- Exact mechanism: conglomerate-discount release + analyst neglect of the smaller child +
  focus-driven operating improvement → months-long drift as coverage/ownership normalize.
- Natural ranking: children in first 12 regular-way months; recency first;
  liquidity-screened (trailing median $Vol, $20M-floor-compatible); optional frozen focus
  flag (strategic-focus language or SIC distance, frozen before outcomes).
- Data acquisition path: (1) crawl full-index for 10-12B effectiveness; (2) parse parent
  8-K/Ex-99.1 for ratio/record/distribution/regular-way dates; (3) assert child
  CIK→ticker→entity plus regular-way start; (4) emit 1 PIT row per spin (4 event dates +
  acceptance datetime); (5) month-end rank, next-tradable-open entry.
- Publication/availability semantics: causal notice = EDGAR acceptance datetime; eligibility
  from first regular-way session only; when-issued trading explicitly ineligible; engine
  next-open execution. No press scraping, no current-master backfill.
- Long-only evidence: Cusatis–Miles–Woolridge 1993 child excess ~3yr; Desai–Jain 1999 focus
  channel; Veld surveys 2004/2009; Chemmanur operating channel. Pre-2010-weighted —
  post-2015 liquid-only update REQUIRED before freeze (gating condition).
- Modern evidence: NOT YET. The update above is the gate; fail there kills the nomination.
- Large/liquid evidence: NOT YET. Same gate; decisive failure mode is premium surviving
  only in small/micro names.
- Expected horizon: 6–12M hold per child; monthly decisions; ~1–2 names/mo turnover;
  months-long drift survives next-open entry.
- Top-six thesis: 10–30 concurrent eligibles fill 6 diversified slots most months.
- Top-one thesis: weak — most-recent liquid child maximizes neglect but single-event
  variance dominates; 1-slot variant documented only.
- Distinctness from V3: filing-event info vs 12-1/9-1/6-1 residual momentum; children
  structurally screen OUT of V3 (short price history + high ivol reject) → low overlap
  by construction.
- Taxonomy interaction: strongest posture in screen — issuer-linked (child's own Form 10
  CIK, operating common); rights/when-issued/ETF/warrant excluded by rule. No commercial
  master needed.
- Likely decisive failure mode: liquid-filtered test shows no excess vs matched controls;
  or regular-way/when-issued mistiming; or spin ratios unrepresentable without a loader
  extension (scoped, research-only).

## Withheld / deferred (NOT shortlisted)

- Patent-grant efficiency (D1): `DEFER_EXTERNAL_DATA_BURDEN` — evidence exists (HHL 2013;
  Kogan 2017 thru 2010) but no free PIT assignee→ticker concordance after 2010. Hypothesis
  kept; no economic time until deterministic concordance is shown. No fuzzy-match promotion.
- CEO/CFO buys (S-C) and 5.02 leadership (S-E): closest SEC runners-up, explicitly NOT
  nominated (old/announcement-only, no post-2010 drift).
- FINRA short-interest surprise (F-1): NOT promoted merely for being public — VW
  insignificance, subperiod instability, OOS deterioration, staleness, momentum overlap.
- Second shortlist slot intentionally WITHHELD: nothing else clears modern + long-only +
  liquid + concentration + PIT jointly.

## Why only one

One weak-gated nomination is honest: 29 of 32 families fail at least one hard gate, D1 is a
principled DEFER, and padding the shortlist would manufacture confidence. Zero would be
valid if A1's classical channel were weaker; it survives as a gated nomination because the
mechanism is issuer-specific, naturally concentrated, PIT-clean, taxonomy-robust, and
V3-distinct — with the modern-liquid update as an explicit kill-gate.
