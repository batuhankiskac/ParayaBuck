# Repository agent instructions

## Production validation

- Keep unit and contract tests deterministic, focused on one invariant, and
  backed by small synthetic fixtures or `tmp_path` state.
- Tests must not write production paths, use network access, or require
  credentials. Offline backtests and paper-safety fixtures must remain
  separate from runtime state.
- Preserve the current strategy/configuration, risk, persistence, backup,
  recovery, and PAPER/LIVE boundaries unless a task explicitly requests a
  behavior change.
- Run focused checks during iteration and the complete normal test suite before
  completion. Do not report an unrun check as passing.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is
the user's decision.

<!-- CODEGRAPH_END -->
