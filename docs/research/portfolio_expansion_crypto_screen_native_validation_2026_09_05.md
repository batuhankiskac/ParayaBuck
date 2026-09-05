# Portfolio expansion crypto screen native validation

Date: 2026-09-05

This validation covers one real CLI production cell only. It does not claim a
42-cell economic run or promotion.

## Acceptance evidence

The test creates a real OHLCV CSV under `tmp_path` with BTC/USD and ETH/USD,
24 hourly bars per day from 2021-12-01 through 2022-01-05. The fixture therefore
contains more than the required 23 strict daily warmup prices and execution-hour
bars in the frozen full window. The test writes a canonical temporary manifest
with the fixture SHA, the nine frozen source SHA pins, protocol/settings pins,
and a digest computed by `canonical_manifest_digest`.

It invokes the real script as a subprocess with:

```text
.venv/bin/python scripts/research/portfolio_expansion_crypto_screen_2026_09.py \
  --manifest <tmp_path>/fixture_manifest.json --run \
  --output <tmp_path>/native_output.json --shard-index 0 --shard-count 42 \
  --checkpoint-dir <tmp_path>/checkpoints \
  --invocation-receipt <tmp_path>/native_invocation.json
```

The production path completed `full/low_variance22/1x` and asserted all of the
following from its output: one assigned canonical cell, a real buy fill,
nonzero crypto fee total, a nonempty timestamped cash ledger, a final marked
position, and `cash + marked positions == final ledger equity`. The atomic
checkpoint and invocation receipt each recorded one completed cell.

The focused test command passed:

```text
.venv/bin/python -m pytest -q tests/research/test_portfolio_expansion_crypto_screen_2026_09.py
27 passed in 2.66s
```

The draft manifest CLI gates also passed as designed:

```text
--validate-inputs: exit 0, INPUTS_VERIFIED_NO_RUN, cells 42
--run:             exit 2, BLOCKED_MANIFEST_DIGEST_PLACEHOLDER
```

Independent tests cover missing and mismatched source, protocol, and settings
pins. A separate digest test proves changing only `manifest_sha256` does not
change the canonical digest.

## Frozen pins

The source SHA supplied for
`scripts/research/portfolio_expansion_crypto_screen_2026_09.py` is
`ca623c3fc79dc67a4e655237cc85fdab3384f1edd0868ea08112d0b6c70df0da`; the
protocol SHA is
`39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52`.
All source, protocol, and settings hashes in the draft matched the current
working tree during validation.

## Limits

This is a bounded native proof for one canonical shard partition (`shard-count
42`, one cell). It does not establish `FULL42COMPLETE`, economic acceptance of
the seven-arm matrix, or PAPER/LIVE readiness.
