# Repository agent instructions

## Research validation

- Keep ordinary research checks fast: use `pytest -m "research and not historical_research and not research_deep"` for iteration.
- The complete current surface remains `pytest -m "research and not historical_research"`; deep checks are deferred to the deep workflow or an explicit selector.
- A `research_deep` mark requires a non-empty `reason="..."`. Keep the test in the current-research classification; historical tests cannot be deep.
- Frozen R46 pin checks are always-on. Do not place `R46_PIN_ALWAYS` tests in `research_deep`; retain the `r46_deep` compatibility marker for existing callers.
- Each unit or contract test should prove one invariant with the smallest sufficient deterministic fixture. Do not use multi-year datasets, full historical loads, or production replays for unit or contract logic.
- Use `tests/research_testkit` and `tmp_path` for deterministic fixtures; extend the kit only when an actual consumer needs the helper. Do not add production-duration sleeps or wall-clock assertions: inject a clock or use an event-driven scheduler instead. Do not add production-path writes, network calls, or credentials to research tests.
- Preserve manifests, receipts, attestations, reports, datasets, source pins, and closure records byte-for-byte. Run the full all-34 closure verifier read-only before claiming closure.
- Before research closure, run the complete current suite plus each relevant `research_deep` and frozen-pin assurance selector; fast iteration results are not closure evidence.
- Research evidence remains separate from economic promotion, PAPER, and LIVE decisions.

For a new research contract, add its path to exactly one of
`_CURRENT_RESEARCH_TEST_PATHS` or `_HISTORICAL_RESEARCH_TEST_PATHS` in
`tests/conftest.py`; an unregistered root or `tests/research/` module fails
collection. If an active contract needs deferred assurance, keep it current
and mark the module or test with an explicit rationale, for example:

```python
pytestmark = pytest.mark.research_deep(reason="file-backed provenance contract is change-sensitive")
```

The timing report is advisory and never a correctness gate. To summarize a
captured fast run, use:

```bash
pytest -m "research and not historical_research and not research_deep" \
  --durations=0 --durations-min=0 > /tmp/research-fast.txt 2>&1
research_pytest_status=$?
python scripts/research/report_test_performance.py /tmp/research-fast.txt \
  --exit-code "$research_pytest_status" --repo-root . --output-json /tmp/research-fast.json
```

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is
the user's decision.

<!-- CODEGRAPH_END -->
