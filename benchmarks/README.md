# Benchmarks

Executable benchmark scripts use deterministic synthetic fixtures or explicit
local datasets and never participate in runtime decisions. Outputs are
disposable diagnostics.

No parity reference fixture is committed. `parity_guard.json` was a frozen
fixture of the superseded unified-equity pipeline: it carried no
`combined_hash`, so `parity.py --check` against it raised `KeyError` instead
of comparing anything, while its `live: false` and green lint fields read as a
passing guard. It was removed on 2026-10-01 and cannot be regenerated here
(its dataset is in no tag). `parity.py --check` now refuses a missing,
unreadable or key-less reference and says how to produce one, so there is no
fixture left to mistake for evidence.

Run a benchmark from the repository root with its documented CLI or `--help`.
