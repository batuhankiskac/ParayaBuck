# Attempt 02 native validation bundle

Status: validation-only, canonical run not started pending review.

Corrected frozen manifest:
`data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen.json`

Its raw and canonical manifest SHA-256 is
`b26c2afae9a03bbb93fe8d09bc26ff0df087f05716659848e3ce07fd9bc56710`.
The corrected runner SHA is
`8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d`.
The test SHA is
`67a503d43fd429d2baeb7bec27606b8e432f0174e3581d6b13fa5b6fee9e162c`.
The original protocol remains byte unchanged at
`39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52`.

## Test and guard proof

Command:

```text
.venv/bin/python -m pytest -q tests/research/test_portfolio_expansion_crypto_screen_2026_09.py
```

Result: exit 0, `28 passed in 2.70s`.

The suite includes a real CLI temporary CSV fixture, canonical manifest and
native engine cash/position reconciliation. It also independently checks
missing and mismatched source, protocol and settings pins, and calls each new
adapter score method with a known finite expected downside score.

The corrected frozen manifest CLI check was:

```text
.venv/bin/python scripts/research/portfolio_expansion_crypto_screen_2026_09.py \
  --manifest data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen.json \
  --validate-inputs
```

Result: exit 0, `INPUTS_VERIFIED_NO_RUN`, `cells: 42`.

## Native historical adapter evidence

The real `data/historical/crypto_expanded_1h.csv` was loaded through
`load_csv_bars`; each case used `run_case(config, bars, window="full",
cost_multiplier=1.0)`, which invokes `aggregate_historical_bars(...,
require_complete=True)`, `build_product_universe`, `PortfolioBacktestEngine.run`
and the native execution policy. Results were:

| arm | window | cost | fills | fee total | curve points |
|---|---|---:|---:|---:|---:|
| downside22 | full (2022-01-01..2026-07-31) | 1x | 4212 | 761.8635392541277 | 40151 |
| reversal70 | full (2022-01-01..2026-07-31) | 1x | 10795 | 1576.4450010259347 | 40151 |
| high_proximity364 | full (2022-01-01..2026-07-31) | 1x | 68 | 87.61515954663696 | 40151 |
| low_disagreement5_45 | full (2022-01-01..2026-07-31) | 1x | 17327 | 2519.3087659873763 | 40151 |

Each run produced nontrivial native fills and nonzero fees. The calls were
validation-only; no weights, thresholds or policy decisions were changed.

## Attempt history

Attempt 01 remains immutable under
`data/research/portfolio_expansion_crypto_screen_2026_09/attempt_01_canonical/`.
Its `FAILED_TECHNICAL.json` records sessions 7735, 2101 and 56139, each blocked
at runner line 127 before final output. The original runner SHA was
`ca623c3fc79dc67a4e655237cc85fdab3384f1edd0868ea08112d0b6c70df0da`.
No byte-for-byte source archive from that attempt was present; the old source
identity is preserved by its SHA and failure sidecar. The original protocol
file was not edited.

Attempt 02 canonical 42-cell execution is intentionally not started in this
bundle.
