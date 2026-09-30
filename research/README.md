# research/

Evidence directories. The `RESULTS*.json`, `*_SUMMARY.txt` and preregistration
files here are the committed record; nothing in this tree is imported by
`src/`, by the test suite or by the runtime, and no harness here participates
in a trading decision.

Status per directory (verified 2026-10-01):

- **crypto_sleeve_decision_v1 — archive-only.** `account_sim.py` simulates the
  0.05 crypto sleeve, which was deleted in 2026-09
  (`docs/research/crypto_removal_v1.md`, tag `archive/code/crypto-sleeve-2026-09`),
  over QQQ/QLD/SPY bars that exist only in tag
  `archive/research/profit-max-v1-2026-09`. It imports cleanly and overwrites
  its committed `RESULTS.json` when pointed at restored inputs.
- **execution_model_audit_v1 — archive-only.** `run_stage_b_audit.py` imports
  `research/physical_availability_clock_v2` and
  `research/sleeve_candidates_v2.stage_b` (tag
  `archive/research/sleeve-candidates-v2-v3-2026-09`) and needs the gitignored
  canonical v5 equity CSVs (882 MB, in no tag). `analyze_stage_b_audit.py` and
  `fill_timing.py` import cleanly but need the run dumps, respectively
  `profit_max_v1_*` from tag `archive/research/profit-max-v1-2026-09`. The
  committed figures (`STAGE_B_AUDIT_SUMMARY.txt`, `RESULTS_FILL_TIMING.json`)
  describe the pre-crypto-removal run (crypto on, equity allocation 0.93,
  ending NAV 1,141,851.32), **not** the current equity-only reference.
- **paper_config_v2 — archive-only, and the current reference.** Same missing
  harness modules and data as above. `RESULTS.json` is the record: S0 1x
  1,141,851.32 (pre-removal, crypto on), **S1 1x 1,173,537.61 = the current
  equity-only VT35 reference** (adopted; re-reproduced unchanged after the
  crypto deletion), S2 1x 1,185,460.98 (measured, not adopted). `evaluate.py`
  imports cleanly and runs offline but needs the `S{0,1,2}_*.json.gz` run dumps
  and OVERWRITES the committed `RESULTS.json`.
- **rule_variants_v1 — runnable after one restore step.** Run
  `research/rule_variants_v1/recover_inputs.sh` (extracts the QQQ/QLD/SPY/VOO
  bars from tag `archive/research/profit-max-v1-2026-09` into the gitignored
  `data/research/rule_variants_v1/` and verifies their SHA-256), then
  `screen.py` runs offline from committed code; re-ran 2026-10-01 and it
  reproduced `RESULTS.json` byte-identically. It overwrites that file.
- **signal_cross_check_v1 — runnable with one restored input.**
  `analyze.py --sip <profit_max_v1_daily_split_sip.csv from tag
  archive/research/profit-max-v1-2026-09> --out <file>`; re-ran 2026-10-01 with
  that restored CSV and it reproduced `RESULTS.json` byte-identically. The
  comparison bar is the equity-only Stage B (NAV 1,173,537.61, 615 fills).
- **stress_robustness_v1 — runnable from committed code.** `stress.py` reads
  only the two committed CSVs under `data/historical/` and is deterministic
  (seeded Monte Carlo); re-ran 2026-10-01 in 6 s and it reproduced `RESULTS.json`
  byte-identically. It overwrites that file.

## Archive-only policy

A harness in this tree is archive-only when the code it needs was deleted or
was never committed. Such a file says so in its own docstring, names the tag
and the input files, and (for the two Stage B harnesses) refuses to start with
that message instead of dying on a `ModuleNotFoundError`. No hash, NAV or
timestamp in this tree was regenerated or invented on 2026-10-01.
