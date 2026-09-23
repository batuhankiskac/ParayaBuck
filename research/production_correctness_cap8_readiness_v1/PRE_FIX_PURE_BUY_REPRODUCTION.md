# Pure-BUY defect reproduced on corrected local main

The audit-only test fixture at substantive cap-8 audit SHA `e99d4226c2b74e23ee7dd6e6b01f1b4ddaadff57` was copied to `/private/tmp` and run against production code after reconciliation integration (`96254b7de6e045212002cfa807abd448bc6e5d0d`), before BUY code edits. All five observation tests passed with no broker network:

| Case | Pre-fix observed behavior |
| --- | --- |
| G1 | 0.605 realized gross; new-symbol TSLA BUY around $1,900 submitted despite 0.60 gross target. |
| G2 | 0.605 realized gross; add-to-held AAPL BUY around $1,500 submitted. |
| G3 | Over-gross risk-reducing AAPL SELL submitted. |
| G4 | Replacement submitted SELL first and persisted BUY for deferred replan. |
| G5 | Manual BTC exposure counted in gross and slots; cap 6 denies the new opening. |

Command: `PYTHONPATH=src /Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python -m pytest -q -p no:cacheprovider /private/tmp/test_cap8_unsafe_prefx_20260924.py -k 'case1 or case2 or case3 or case4 or case5'` → `5 passed, 2 deselected`. These old tests assert unsafe behavior and will remain audit evidence only; corrected production regressions must assert rejection/safe sizing.
