# high_proximity364 exposure audit (2026-09-05)

This read-only forensic audit consumes the frozen attempt-03 shard payloads and the pinned local hourly CSV. It performs no economic rerun, parameter change, production edit, or promotion.

The audit is bounded to `high_proximity364` and `low_variance22`, both full and recent windows, at 1x/2x/3x costs. It uses the recorded hourly cash ledger and marked positions for actual gross exposure, and reconstructs quantity checks from the recorded fills. Daily history readiness is a separate diagnostic over complete 24-hour local bars; it does not claim venue-PIT or delisting truth.

## Direct finding

At full 1x, high_proximity364 first becomes active at `2022-06-25T01:00:00+00:00`, trades `BTC/USD`, and last has a marked position at `2025-07-03T04:00:00+00:00`. It has 68 fills across 17784 active hourly marks and 22367 inactive marks. This is BTC-only exposure followed by cash, so the sparse fill count is not itself a low-exposure claim; the ledger-derived gross exposure statistics are reported below.

## Cell exposure and reconciliation

| Window | Cost | Arm | Fills | First active mark | Last active mark | Active hours | Inactive fraction | Mean gross exposure | Max gross exposure | Symbols |
|---|---:|---|---:|---|---|---:|---:|---:|---:|---|
| full | 1x | high_proximity364 | 68 | 2022-06-25T01:00:00+00:00 | 2025-07-03T04:00:00+00:00 | 17784 | 0.557 | 0.022 | 0.062 | BTC/USD |
| full | 2x | high_proximity364 | 68 | 2022-06-25T01:00:00+00:00 | 2025-07-03T04:00:00+00:00 | 17784 | 0.557 | 0.022 | 0.062 | BTC/USD |
| full | 3x | high_proximity364 | 68 | 2022-06-25T01:00:00+00:00 | 2025-07-03T04:00:00+00:00 | 17784 | 0.557 | 0.022 | 0.062 | BTC/USD |
| recent | 1x | high_proximity364 | 74 | 2024-01-01T01:00:00+00:00 | 2025-07-03T04:00:00+00:00 | 4462 | 0.803 | 0.010 | 0.060 | BTC/USD |
| recent | 2x | high_proximity364 | 74 | 2024-01-01T01:00:00+00:00 | 2025-07-03T04:00:00+00:00 | 4462 | 0.803 | 0.010 | 0.060 | BTC/USD |
| recent | 3x | high_proximity364 | 74 | 2024-01-01T01:00:00+00:00 | 2025-07-03T04:00:00+00:00 | 4462 | 0.803 | 0.010 | 0.060 | BTC/USD |
| full | 1x | low_variance22 | 5732 | 2022-01-01T01:00:00+00:00 | 2026-07-31T23:00:00+00:00 | 39584 | 0.014 | 0.049 | 0.087 | AVAX/USD, BCH/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, GRT/USD, LTC/USD, UNI/USD, XRP/USD |
| full | 2x | low_variance22 | 5718 | 2022-01-01T01:00:00+00:00 | 2026-07-31T23:00:00+00:00 | 39584 | 0.014 | 0.049 | 0.087 | AVAX/USD, BCH/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, GRT/USD, LTC/USD, UNI/USD, XRP/USD |
| full | 3x | low_variance22 | 5699 | 2022-01-01T01:00:00+00:00 | 2026-07-31T23:00:00+00:00 | 39584 | 0.014 | 0.049 | 0.087 | AVAX/USD, BCH/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, GRT/USD, LTC/USD, UNI/USD, XRP/USD |
| recent | 1x | low_variance22 | 5305 | 2024-01-01T01:00:00+00:00 | 2026-07-31T23:00:00+00:00 | 22064 | 0.025 | 0.048 | 0.083 | AVAX/USD, BCH/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, GRT/USD, LTC/USD, XRP/USD |
| recent | 2x | low_variance22 | 5300 | 2024-01-01T01:00:00+00:00 | 2026-07-31T23:00:00+00:00 | 22064 | 0.025 | 0.048 | 0.084 | AVAX/USD, BCH/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, GRT/USD, LTC/USD, XRP/USD |
| recent | 3x | low_variance22 | 5288 | 2024-01-01T01:00:00+00:00 | 2026-07-31T23:00:00+00:00 | 22064 | 0.025 | 0.048 | 0.084 | AVAX/USD, BCH/USD, BTC/USD, DOGE/USD, DOT/USD, ETH/USD, GRT/USD, LTC/USD, XRP/USD |

For every audited cell, the JSON records cash plus marked positions reconciliation error, fee sum error, terminal NAV error, and fill-to-terminal quantity mismatch. Any nonzero mismatch would block attribution; no mismatch is silently converted to zero. Terminal positions are allowed because the frozen policy uses a final mark without forced liquidation.

## 364-day readiness diagnostic

The candidate’s first full-window entry is compared with the locally reconstructed first date having 364 consecutive complete daily bars for the traded symbols. This tests the mechanical history gate only. It cannot separate that gate from ranking, hysteresis, scanner universe membership, or opportunity without decision traces, which the frozen payload intentionally omits.

- `BTC/USD`: first local 364-consecutive end `2022-06-21`, valid ends `739`, complete daily bars `2031`.
- `ETH/USD`: first local 364-consecutive end `2022-06-21`, valid ends `494`, complete daily bars `2030`.

Six configured trade symbols were mechanically ready at the full-window first active mark, but the frozen output has no per-decision score, rank, or PIT-universe snapshot. Therefore the data establish BTC-only selection and later cash directly, while the reason BTC won ranking or why each inactive interval occurred remains unobservable. Readiness is a diagnostic, not a causal attribution.

## Attribution limits

Monthly and yearly mark-to-market summaries, symbol concentration, BTC/ETH dominance, and half-incumbent/half-cash terminal comparisons are retained in the compact JSON. Inactive mechanism is explicitly `UNOBSERVABLE`: the ledger cannot distinguish not-yet-ready, no-target cash, scanner rejection, or a skipped rebalance batch. Gap-related stale marks are not treated as zero PnL or as proof of investability. Future integration remains conditional on a trace instrument that records per decision: complete daily bar count/contiguity per symbol, PIT eligibility reason, score/rank, retained/target symbols, order event, and as-of mark quality.

Artifact: `data/research/portfolio_expansion_crypto_screen_2026_09/attempt_03_canonical/audit/high_proximity364_exposure_audit_2026_09_05.json`. Input hashes and all compact cell outputs are embedded there.
