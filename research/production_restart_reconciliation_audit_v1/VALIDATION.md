# Validation receipt

All commands ran from the isolated audit worktree on 2026-09-24. No test used real Alpaca credentials or a real broker. Audit probes use synthetic in-memory broker/ledger objects.

| Check | Result |
|---|---|
| Audit-only synthetic tests | 4 passed |
| Focused global slot, restart, incident, rebalance, and loss suites | 161 passed |
| Existing Alpaca OPEN pagination suite | 18 passed |
| Complete \`pytest -q\` in restricted sandbox | 1165 passed, 4 skipped, 6 failed because \`127.0.0.1\` socket bind was denied by sandbox |
| Six affected existing local-loopback tests rerun with socket permission | 6 passed |
| \`ruff check .\` | Passed |
| \`ruff format --check .\` | Passed; 258 files formatted |
| \`mypy --strict\` on audit-only Python | Passed |
| \`python -m json.tool\` on both audit JSON files | Passed |
| Secret-pattern scan of audit directory | No matches |
| Staged \`git diff --check\` | Run after staging/commit; see final response |

The six socket tests belong to webhook and local web bind functionality, not Alpaca trading. Their first failures were sandbox permission errors before any application assertion; rerun passed without changing files. The combined evidence covers every test executed in the full suite, though the single restricted-sandbox invocation itself did not have a zero exit code.
