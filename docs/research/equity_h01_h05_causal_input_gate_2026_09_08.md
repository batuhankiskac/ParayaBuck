# H01-H05 causal input gate — 2026-09-08

This research-only diagnostic freezes the first causal input contract for the
eight names in `equity_h01_h05_minimal_pit_pilot_2026_09_07`. It uses only the
already acquired SEC Companyfacts/submissions, the v5 lifetime table, the v5
split-adjusted daily bars, and the v5 corporate-action table. It does not
modify production, configuration, PAPER, LIVE, or the earlier pilot outputs.

The diagnostic forms month-end decisions from January 2022 through December
2024. Every accounting observation must have `available_at` no later than the
decision close. Accepted EDGAR timestamps are used when present; a missing
acceptance time uses the next NYSE open after the filed date and is retained as
a weaker availability basis. A next-session execution would be required for a
return run.

The frozen formulae are:

* H01: `StockholdersEquity / (split-adjusted close * reported common shares)`.
* H02: `GrossProfit / Assets`.
* H03: `(NetIncomeLoss - operating cash flow) / average(current Assets, prior Assets)`.
* H04: `current annual Assets / prior annual Assets - 1`; a quarterly instant is
  never relabeled as the annual comparator.
* H05: `(abs(dividend payments) + abs(common repurchases) - abs(common issuance)) / market cap`.

Annual 10-K flows use the reported period. A 10-Q flow is converted from YTD
to a quarter delta against the prior YTD period only when that predecessor was
available by the current flow's own availability boundary; later restatements
are blocked. H03 requires income and operating cash flow to have matching
form/unit/start/end period keys. H03 uses an ending Assets instant at the
actual flow end and a beginning Assets instant immediately before the flow
start, within a frozen 0–7 calendar-day tolerance; it does not reuse H04's
annual comparator. H05 requires
dividend, repurchase, and issuance values to share one exact reporting-period
key and a duration no longer than 430 days; stale components are not combined.
H05 selects a same-span common-stock repurchase whenever one exists and blocks
when that fact is stale, unavailable, or unusable. It falls back to the equity
repurchase tag only when no same-span common fact exists. Missing or invalid components produce a missing feature;
numeric zero remains zero.

The market-cap gate uses the canonical split-adjusted close at the decision
close, reported common shares available by that boundary, and an explicit
same-decision post-action factor of 1.0. A row with a future split after the
decision is closed fails the join. The provider's historical raw-price
equivalence remains unproved, so H01 and H05 are explicitly blocked even when
the structural row is complete. H02-H04 are `READY_SCHEMA_ONLY` only when at
least the frozen 50% row-breadth threshold is met. H02 has 72/288 rows, H03
has 286/288 rows after two period-mismatch blocks, H04 has 19/288 rows after
the fresh annual comparator gate, and H05 has 32/288 rows after requiring one
fresh common period. H02 retains missing gross-profit inputs as
missing; no zero or cross-sectional
imputation is performed. The two H03 period-mismatch rows are explicitly
blocked and excluded from the zero violation count. Current issuer/CIK matching is reported separately
from historical common-stock security continuity, which remains unresolved
even when a common-shares fact is present. The output is a causal
schema and coverage diagnostic only. Economic returns are prohibited until the
reviewer approves the schema and price/share gate, and no candidate is
promoted.

Artifacts: the frozen manifest, runner, targeted tests, and generated rows and
receipt under `data/research/equity_h01_h05_causal_input_gate_2026_09_08_r11/`. The
manifest pins the formatted runner hash, a deterministic inventory of every
raw Companyfacts/current-submissions/archived-submissions file read, and every
input and output hash;
the recorded future-predecessor, period-alignment, and H03/H04-reuse violation
counts are all zero; two H03 rows remain explicitly blocked for mismatched
flow periods.

Revision r11 supersedes the preserved r2–r10 artifacts. The base and current git
identifier at review time is `63cb510198a5c4e1f213ad4505ff901f35d4bbae`; no
commit was created. Changed owned paths are this report, the runner at
`scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py`, and its
targeted test at
`tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py`.

Validation commands and observed output:

* `.venv/bin/ruff format scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py` — `2 files left unchanged`.
* `.venv/bin/ruff check scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py` — `All checks passed!`.
* `.venv/bin/python -m py_compile scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py` — exit `0`.
* `.venv/bin/python scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py` — `COMPLETE_DIAGNOSTIC`, `288` rows, coverage `H01 288 / H02 72 / H03 286 / H04 19 / H05 32`.
* `.venv/bin/python -m pytest -q tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py` — `17 passed`.
* `.venv/bin/ruff check scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py` — `All checks passed!`.
* `.venv/bin/mypy --no-incremental scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py` — `Success: no issues found`.
* `.venv/bin/python -m py_compile scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py tests/research/test_equity_h01_h05_causal_input_gate_2026_09_08.py` — exit `0`.
* Independent inventory and boundary verifier — `57` raw files, `288` rows across `8` symbols and `36` decisions, missing availability bases `0`, future lineage facts `0`, future shares `0`, H05 periods longer than 430 days `0`; recorded future-predecessor `0`, period-alignment `0`, and H03/H04-reuse `0`.

The r11 generated paths are
`data/research_manifests/equity_h01_h05_causal_input_gate_2026_09_08_r11.json`,
`data/research/equity_h01_h05_causal_input_gate_2026_09_08_r11/causal_input_rows.jsonl`,
`data/research/equity_h01_h05_causal_input_gate_2026_09_08_r11/source_inventory.json`,
and its `diagnostic_receipt.json`. SHA-256 values are: runner
`d97ce058b621428573d3e3333643cc1fbb63835f59a01ced2b5e3bbbd5a7c217`, tests
`c592ee3ed75b907eddab276f649ca30acd157429f4d0536b883a79dd023ca972`, manifest
`751acab33a9725857f496df7c3f57b56bd0ddec2365bbdf799e9ea3967fdb849`, rows
`ced463b9f8889d66d5322fa97fa59b1c2c46a413a90981eab0a1639176890806`, inventory
`e0753ccee5cb69b76053149279bdb7a66e228303f50548df0bf04954acffde91`, and
receipt `3a62aeee225c85038c241d24e6a0e9eb2cb2911521fd2d1ca079d9a9b6f297fe`.

Residual concerns remain: the fixed eight-name universe is date-known
eligibility plumbing rather than index-membership proof; historical
common-security continuity and historical raw-price equivalence remain
unproven; H01/H05 remain blocked by the price/share basis gate; and no economic
run was launched.
