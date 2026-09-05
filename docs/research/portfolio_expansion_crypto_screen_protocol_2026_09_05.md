# Portfolio expansion crypto screen protocol — 2026-09-05

**Status:** pre-results protocol; no run authorization or promotion decision.

This screen freezes seven score adaptations under the incumbent crypto
portfolio policy: `low_variance22`, `low_max22`, `signed_risk22`, `downside22`,
`reversal70`, `high_proximity364`, and `low_disagreement5_45`. Each arm uses
the same HYST4 retention (held symbol is retained only when PIT-eligible and
rank <= 4), top-2 equal targets, UTC 96-hour decisions, the configured crypto
allocation cap (5% account sleeve), native risk limits, and next-later-hour
execution. These are score adaptations for a common-budget portfolio screen;
they are not replays of the legacy weekly/no-hysteresis economic studies.

The source feed is `data/historical/crypto_expanded_1h.csv`. The scanner uses
raw hourly bars and warmup. Signal history is independently aggregated to
strict complete daily bars (24 hourly observations and no forward fill), then
shifted by 24 hours so only completed D1 data is visible. Prices must be finite
and positive; all score ties use `(score, symbol)` ascending ordering. Missing
or insufficient history makes a symbol ineligible. The terminal policy is
native final mark, no forced liquidation, zero-yield cash, with actual fees,
slippage, and spread recorded.

| Arm | Exact score on completed D1 data | Minimum prices | Direction |
|---|---|---:|---|
| low_variance22 | sample standard deviation of 22 simple returns | 23 | ascending |
| low_max22 | `max(r)` over 22 returns | 23 | ascending |
| signed_risk22 | `sum(min(r,0)^2)/sum(r^2)`; denominator > 0 | 23 | ascending |
| downside22 | `sqrt(mean(min(r,0)^2))` | 23 | ascending |
| reversal70 | trailing simple return over 70 returns; strictly `< 0` only | 71 | ascending (most negative first) |
| high_proximity364 | `close_t / max(last 364 closes)`, including current close | 364 | descending |
| low_disagreement5_45 | mean of prior five nonzero volumes / mean of preceding 45 nonzero volumes; both counts exact | 50 | ascending |

The seven arms × full/recent windows × 1x/2x/3x cost are 42 independent
fresh-reset cells. Full is `2022-01-01T00:00:00Z..2026-07-31T23:59:59Z` and
recent is `2024-01-01T00:00:00Z..2026-07-31T23:59:59Z`. The 2022 full start is
deliberate because high-proximity requires 364 completed D1 prices; it is not
the same sample as the frozen C3/C4 economic studies. No result is promoted
automatically. Required output is compact: per-cell metrics, timestamped
NAV/equity curve, cash/position terminal proof, compact fills and fee totals;
per-decision score traces are excluded.

Technical validity requires manifest, protocol, settings, source, and input
hashes; exact 42 unique cells; finite aligned timestamps; causal as-of data;
cash + marked positions = equity; fill fees = fee total; and final NAV equals
the final cash ledger equity. Missing evidence is `BLOCKED`, never zero-filled.
The first planned diagnostic is incumbent-vs-arm net-return correlation and
marginal drawdown/Sharpe under the shared 5% budget. It is descriptive and
does not override the frozen production selection.

### Canonical manifest and shard storage

The draft manifest may be used only with `--validate-inputs`; its
`DRAFT_COMPUTE_AFTER_FREEZE` digest is never accepted by `--run`. A canonical
manifest digest is SHA-256 of canonical JSON after removing only the
`manifest_sha256` field, and a run fails closed unless the declared digest
matches. The full 42-cell `canonical_cell_ids` list is immutable; shard `i` of
`n` executes exactly `canonical_cell_ids[i::n]`. Shards must write to distinct
persistent paths under
`data/research/portfolio_expansion_crypto_screen_2026_09/attempt_01_canonical/`
(`shard_0/`, `shard_1/`, `shard_2/`), with separate output, log, receipt, and
checkpoint directories. A shard result is partial even when all its cells
pass; only a later merge/audit may establish `FULL42COMPLETE`.

### Archive-versus-adaptation decisions

The archive records low-disagreement as a `5d vs 45d volume ratio` with
“zero-volume gates” (`docs/rejected_strategies.md:170-187`), but does not state
whether a partially nonzero slice is accepted. This screen therefore freezes
the conservative **adaptation** `count(nonzero recent)==5` and
`count(nonzero prior)==45`; it is not claimed to be the legacy executable
rule. A symbol failing either count is ineligible and is recorded as such.
The common HYST4/top2/96h policy is also an explicit portfolio adaptation:
legacy weekly cadence and no-hysteresis behavior are not replayed; only each
archived score formula is retained.

The reversal archive says “formation_bars 70” and “trailing 70-bar return”
(`docs/rejected_strategies.md:134-140`) but its removed implementation is no
longer available to resolve whether that meant 70 prices or 70 return
intervals. This screen explicitly chooses the conservative **adaptation** of
70 return intervals, hence 71 completed D1 prices. That choice is frozen in
the protocol before results and must be reported as an adaptation, not as a
proven legacy implementation detail.
