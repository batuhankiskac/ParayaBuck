# V4 validation receipt

Date: 2026-09-24. Scope: `research/public_data_alpha_discovery_v4/` in the isolated V4 worktree.

| Check | Result |
| --- | --- |
| Required artifact manifest, JSON/CSV parsing, unique IDs/families | PASS — `validate_v4.py` |
| Stage nesting and limits | PASS — 66 rows; A 9, B 6, C 3, shortlist 0 |
| Final disposition arithmetic | PASS — 66 equals sum of all disposition categories |
| Stage-A citation URLs and prior closed-family exclusion | PASS — validator; individual papers remain qualified where full methods were inaccessible |
| Public source-class index | PASS — official/free source URLs and publication semantics present |
| Automated no-peek audit | PASS — no forbidden candidate-local outcome field in CSV headers or JSON keys; no local outcome vectors created |
| `git diff --cached --check` | PASS |
| Credential/privacy pattern scan of V4 artifacts | PASS — no matches for AWS key IDs, private-key banners, common GitHub/OpenAI token shapes or password assignments |
| `ruff check` | PASS — validator only |
| `ruff format --check` | PASS — validator only |
| `mypy --strict` | PASS — validator only, run via `uvx --from mypy` |
| Production pytest | Not run; no production source/config changed and campaign protocol makes it optional |

The count and no-peek checks are structural proof, not evidence of candidate economic performance. Stage-B methods that could not be recovered in full were eliminated or qualified; no paper statistic was locally recomputed.
