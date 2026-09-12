# Research test architecture — 2026-09-12

Research iteration now has a repository-wide fast/deep lifecycle. The generic
`research_deep(reason="...")` marker extends the existing R46 tiering to future
missions. Existing `research`, `historical_research`, and `r46_deep` selections
remain compatible. Expensive assurance is retained, including frozen R46 tests.

Use these commands from the repository root with the project environment active:

```bash
pytest -m "not research"
pytest -m "research and not historical_research and not research_deep"
pytest -m "research and not historical_research"
pytest -m "research and not historical_research and research_deep"
```

The fast command is for iteration; full current and relevant frozen/historical
assurance remain required before closure. Existing historical modules still
require `--include-historical-research` and an explicit selection.

`tests/research_testkit/` supplies deterministic bars and compact series, PIT
snapshots/universes, explicit corporate-action and terminal records, root-bound
non-overwriting artifact writes, and immutable canonical-hash construction.
Factories preserve caller-controlled ordering and economic fields. No helper
implicitly loads repository data or starts a broker, runtime, or clock. Speculative broker,
Git, and backtest frameworks were deliberately not added without a current
consumer.

The synthetic scenarios in `tests/test_terminal_aware_engine.py` now consume the
kit. Their original OHLC ranges, timeframe, volume, PIT metadata, terminal
policies, and assertions are retained. Historical and frozen test files were
not rewritten. New kit, lifecycle-policy, and performance-tool contracts add
29 current tests.

All research modules are explicitly classified in `tests/conftest.py`. New
unregistered research modules, including nested mission directories, fail
collection instead of disappearing into historical collection. Lifecycle marker
conflicts, missing deep rationale, and attempts to defer always-on pins fail
closed. R46's existing selector inventory continues to reject stale entries.

The root `AGENTS.md` and `docs/research/README.md` make small deterministic
fixtures, kit reuse, temporary paths, explicit deep reasons, and full closure
validation the permanent authoring policy. Ordinary CI runs product tests and
fast research, retains an explicit frozen-pin step, and prints advisory slow
durations. The existing deep workflow now covers general research dependencies,
including source, tests, configuration, historical data, and research evidence,
with relevant-change, weekly, and manual triggers.

`scripts/research/report_test_performance.py` summarizes captured pytest logs.
It aggregates setup/call/teardown per node, supports baseline comparisons and
advisory categories, and can inspect supplied fixture metadata and heuristic
source clues. It preserves test failure status and labels incomplete duration
logs honestly. Source clues are static hints, not a runtime I/O audit; complete
fixture-size accounting requires supplied metadata. Capture with
`--durations=0 --durations-min=0` for a complete profile. The README contains
the capture and comparison commands. No timing threshold is a test assertion.

Measurements were made locally on macOS, Python 3.13.12, pytest 9.1.1, beginning
at clean commit `d43a54e19082db7d31b13b0d5f62b3246a793962`:

| Run | Passed | Pytest elapsed | Interpretation |
| --- | ---: | ---: | --- |
| Initial full current research | 433 | 270.05 s | Baseline full feedback loop |
| Final fast research | 401 | 45.27 s | Uncontended iteration measurement |
| Final full current research | 462 | 376.88 s | Correctness passed; concurrent frozen assurance affected timing |
| Product | 1,028 | 106.72 s | Passed with localhost socket permissions |

The final fast loop is about one sixth of the initial full-suite time. This is
a comparison of assurance scopes, not evidence of a sixfold speedup over the
previous R46-specific fast command, whose original latency was not measured.
Do not compare the contended final full time with the baseline as a performance
regression. The baseline's default duration display hid very short phase rows;
its total elapsed time is valid, but its per-node profile is incomplete.

Exact node inventory comparison found zero losses: original current 433 plus
29 additions equals 462; original fast 372 plus the same additions equals 401;
all 61 original deep items remain. Fast and deep are disjoint and their union
equals current. The product inventory remains 1,028.

The main retained cost is the frozen strict R46 file-backed replay: 211.60 s of
call time in the baseline, versus 309.68 s total during the contended final run.
Its integration/governance round trip also stays deep. Both are full-fidelity
assurance and immutable boundaries. The slowest uncontended fast nodes were
worker-process contracts at approximately 0.76 s each; no fast node exceeded the
reporter's advisory one-second threshold. Existing terminal tests still include
a weak terminal-firing assertion and wall-clock use; strengthening those can be
a separate focused follow-up without changing frozen evidence.

Final proof includes the R46 pin guard; canonical native attestation
`VERIFIED_NATIVE_LEDGER` (two fills, promotion false); read-only all-34 closure
verification with no issues plus 10 closure tests; 27 M3 R5 contracts; and 22 M3
OCP attestation R2 contracts. The canonical R46 CLI needs repository import
visibility when invoked by file path, so validation used `PYTHONPATH=.`.
All-34's record SHA remains
`48b3525cb52a72a540e83e34edea9367ee4265b4ddf1f7be02f970b7b8204cf6`.

Tracked production source, datasets, manifests, receipts, existing
`tests/research/**`, and immutable research evidence are unchanged. Touched
Python lint/format checks, workflow YAML parsing, and `git diff --check` passed.
CI was validated locally; no remote workflow was dispatched. No economic
research campaign, LIVE action, promotion, or commit was performed.

Local raw logs, exact node inventories, and complete final JSON/text profiles
are under `/private/tmp/parayabuck-test-architecture/`; these are local validation
artifacts, not frozen research evidence.
