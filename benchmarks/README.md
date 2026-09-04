# Benchmark Lifecycle

`benchmarks/BASELINE_LABELS.md` is the artifact-status index and the authority
for whether a benchmark is current, frozen, legacy or historical.

- `benchmarks/parity_guard.json` is the frozen canonical parity fixture.
- `benchmarks/history/` contains immutable historical aliases and evidence.
- Root benchmark paths contain current measurements as well as retained legacy
  paths that historical reports still reference.
- Frozen artifacts must never be overwritten, reserialized or regenerated in
  place.
- A new measurement receives a new mission-specific filename and an explicit
  label in `BASELINE_LABELS.md`.
- Cleanup of root/history duplicates is deferred until every inbound reference
  has been migrated and validated.
