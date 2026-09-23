# Six-position occurrence classification

| Occurrence | Class | Reason |
|---|---|---|
| `config/settings.yaml:184` `max_positions: 6` | CONFIG_DRIVEN | Current production setting; the only proposed future config delta |
| `src/parayabuck/execution/portfolio.py:95` default `maximum_positions=6` | CONFIG_DRIVEN | Default for direct construction; configured PAPER/LIVE mapping overrides it |
| `src/parayabuck/config.py:217` dataclass default 5 | CONFIG_DRIVEN | Loader default, not a hard six |
| `tests/test_global_slot_accounting.py` cap-6 fixtures | TEST_EXPECTATION | Current-cap invariants; no runtime constant |
| Frozen migration branch cap-6 arm and traces | RESEARCH_ONLY | Control arm, never production |
| Research and README top-six descriptions | DOCUMENTATION_ONLY | Strategy rank/universe wording must not be read as account slot enforcement |

No `HARDCODED_PRODUCTION_DEPENDENCY` was found in the inspected global-slot paths. This is not a claim that changing config alone is safe: the pure-BUY gross defect and outstanding reconciliation repair block rollout.
