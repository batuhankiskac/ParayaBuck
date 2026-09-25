# Gold + dividend sleeve: production validation (2026-09-25)

Decision: **shipped by operator decision, with one pre-registered gate missed by
a margin inside path noise.** The PAPER equity identity stays
`equity_residual_momentum_v3_dividend_sleeve_v1` (no code change). Its
parameters change from V3 core 0.40 + SCHD/VIG/DGRO 0.15 to **V3 core 0.30 +
a 0.25 sleeve of SCHD, VIG, DGRO, GLD and OUNZ at 0.05 each** (dividend growth
0.15, gold 0.10). Market allocation (0.55), crypto (0.05) and every risk limit
are unchanged. LIVE stays disabled. Gold (`gold_v1b`, GLD/OUNZ) passed Stage B
in `docs/research/sleeve_candidates_v2_v3_closure.md`; this campaign tests it
together with the shipped dividend sleeve. Evidence (pre-registration, arm
configs, runner, evaluator, runs, `RESULTS.json`) is preserved at tag
`archive/research/gold-sleeve-production-v1-2026-09`.

## Arms

Same production code path and shared-ledger harness as
`docs/research/dividend_sleeve_production_v1.md` (2017-01-03 → 2026-07-30).
Each arm is a configuration change only: the five sleeve ETFs are equal-weight,
pinned into the IEX stream (V3 keeps 21 ranked slots, was 23) and slot exempt.
XLRE and XLC left the unused static fallback list so the configured stream
stays within 30; the backtest universe does not read that list.

| Arm | V3 core | Dividend | Gold | CAGR | Sharpe | Max DD | 2022 | 2022–2026 CAGR | Ending NAV |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P (was shipped) | 0.40 | 0.15 | — | **7.22%** | 0.780 | 17.2% | −12.5% | 4.23% | 194.9k |
| G1 | 0.40 | 0.09 | 0.06 | 6.65% | 0.740 | 19.4% | −14.2% | 4.19% | 185.3k |
| G2 | 0.35 | 0.12 | 0.08 | 6.95% | 0.807 | 17.6% | −13.0% | 4.71% | 190.2k |
| **G3 (shipped)** | **0.30** | **0.15** | **0.10** | 7.17% | **0.869** | **16.9%** | **−11.7%** | **5.61%** | 193.99k |

Costs 2x / 3x: P 6.72% / 6.25%, G3 6.66% / 6.22% CAGR (Sharpe 0.730 / 0.682
vs 0.812 / 0.764).

G3 1x sleeve P&L per 100k start: SCHD +11.9k, VIG +8.8k, DGRO +8.7k,
GLD +9.6k, OUNZ +9.7k.

## Gates (pre-registered against P)

G3 passes accounting invariants, the 30-symbol stream bound and the 8 non-sleeve
slot bound, Sharpe, max drawdown, 2022 and the 2022–2026 validation-window CAGR.
It **fails H4 (full CAGR ≥ P) and H8 (CAGR ≥ P at 2x/3x)**, by 0.05, 0.06 and
0.03 pp (about $950 of ending NAV per 100k at 1x). Shared-ledger runs move by
roughly ±$1–4k through path dependence, so the shortfall is within noise. G1
and G2 fail several gates. Under the pre-registered rule no arm is selected. The
operator chose to ship G3 anyway: equal return, lower risk.

## Caveats

- **Post-hoc override.** The ship decision overrides a pre-registered gate after
  seeing results. It is recorded here as such.
- **Return edge moves out of the development window.** G3 trails P in
  2017–2021 (8.63% vs 10.05% CAGR) and leads in 2022–2026 (5.61% vs 4.23%).
  That is the V3 allocation cut (0.40 → 0.30): V3 was strong early and weak
  recently. In a strong momentum period G3 will lag.
- **Gold is a single asset.** Its edge in the sleeve campaign was concentrated
  in 2025, and Holm p ≈ 0.15 still applies. The durable case is
  diversification (lower drawdown, smaller 2022 loss).
- **Fewer V3 stream slots.** V3 keeps 21 ranked stream slots, down from 23.
- **Data.** Canonical v5 bars lack SCHD, GLD and OUNZ. The validation adds them
  from the sleeve-campaign file. A single-market product backtest on canonical
  data trades VIG/DGRO only.
- **Universe refresh.** The pinned set changes, so startup
  (`ensure_active_universes`) regenerates the persisted equity universe.

## Rollback

To restore the previous identity parameters, set `core_allocation: 0.40` and
`sleeve_allocation: 0.15`. Then remove GLD/OUNZ from `sleeve_symbols`,
`universe.equity.sleeve_trade_symbols`, `risk.slot_exempt_equity_symbols` and
`fallback_trade_symbols`, and restore XLRE/XLC to the fallback list. The full
V3-only rollback is in `docs/research/dividend_sleeve_production_v1.md`.
