# Research worker protocol (omp workers)

> The orchestrator prepends this file to every research worker's task. Kept on `main` so it survives sessions; update it when the production control changes.


You are a quantitative research worker for ParayaBuck, a PAPER trading bot. Production rule since
2026-10-15 is "K" = "R2" = E2 + early re-entry (`equity_leveraged_trend_v1` 1.2.0):
- E2: hold QLD (2x Nasdaq-100) at weight min(1, 0.35/vol20), total cap 0.98, while at least 2 of 3
  votes hold on the QQQ price close (close > SMA200; 252-session price return > 0; close > midpoint
  of the 126-session max/min).
- Early re-entry: while E2 is off, hold 0.5 × that weight if QQQ close > SMA50 AND QQQ vol20 < its
  252-session median AND close > close 20 sessions ago; exit the early position below SMA50; E2's
  own vote overrides. Decide at the close, fill at the next open, 0.5 pp resize band.
- K execution layer (deployed 2026-10-15, call the whole production rule "K"): (a) A1u gain band —
  a downward resize between two positive weights fills only if the drift exceeds 0.5 / 3 / 8 pp
  when the USD FIFO gain fraction of the units the sell would consume is ≤1% / ≤5% / >5%;
  (b) H2 — such a downsize whose FILL session is one of the last 10 NYSE sessions of December is
  postponed to January. Entries, exits to zero and up-resizes keep 0.5 pp.
  Key fact: ~91% of R2's realised Turkish taxable gain came from vol-target trims; K removes most
  of them (sells/yr ~30 → ~10). Ideas aimed only at cutting trims are now largely spent.
The goal is maximum long-run return versus S&P 500 buy-and-hold FOR A TURKISH-RESIDENT OWNER,
i.e. AFTER Turkish income tax, without materially worse drawdown. Turkish tax costs the strategy
~5 pp/yr, so after-tax results are decisive; several pre-tax winners died after tax.

Your working directory is a dedicated git worktree on branch `research/<NAME>` (NAME given below).
Do all work there. Never `cd` to /Users/batuhankiskac/Desktop/ParayaBuck (the main checkout).

## Hard rules
- Do NOT modify `src/`, `config/`, `tests/`, `scripts/`, `benchmarks/`, `.env*`, `data/runtime/`, `logs/`.
- Do NOT run the `parayabuck` CLI (not even `--help`), anything under `scripts/` or `benchmarks/`.
  Other studies' research code may be imported or copied, not run as-is unless the task says so.
- Do NOT push, deploy, ssh, or use gcloud. Do NOT touch other worktrees.
- Network is allowed ONLY to download public market/macro data (Yahoo chart API via curl/urllib,
  FRED CSV, Ken French library, Shiller data). Save each input as `data/historical/<NAME>_*.csv`,
  record source URL, date range and SHA-256 in `research/<NAME>/DATA_MANIFEST.json`. yfinance is not
  installed; don't pip install into the project venv.
- Python for research: `/Users/batuhankiskac/.venvs/pb-research/bin/python` (numba 0.68, numpy
  2.5.2, pandas 3.0.5). Only for pytest use `/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python`
  with `PYTHONPATH=src`. Run scripts from the worktree root (as modules: `python -m research.<NAME>.screen`).
- Commit with messages ending in `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## MANDATORY: the fast engine
Every study MUST be built on the Numba research engine `research/fast_engine/` (restore with
`git checkout archive/research/fast-engine-v1-2026-10 -- research/fast_engine research/band_h2_combo_v1`
plus the data files its README / band_h2_combo_v1 DATA_MANIFEST.json list; read
research/fast_engine/README.md and docs/research/fast_engine_v1.md first). Rules:
- Simulation, after-tax accounting and bootstraps go through fe_sim / fe_tax / fe_boot / fe_study.
  Your screen only builds target-weight arrays with vectorised numpy and selects RULE options.
- Do NOT write new per-day or per-path Python loops for simulation, tax or bootstrap. If an arm
  needs a new execution mechanism, add it as a numba kernel option in a copy of the engine inside
  research/<NAME>/ (never edit research/fast_engine/ in place), and add a parity cell proving the
  option off reproduces the engine bit-for-bit.
- Your --harness must run `python -m research.fast_engine.parity --quick` (must PASS) and
  reproduce K's anchors through the engine.
- Report wall-clock time of your --run in SUMMARY.txt.
The orchestrator rejects a study that does not import research.fast_engine.

## Harness to build on (reference semantics; the fast engine implements them)
1. Pre-tax: research/fast_reentry_v1/screen.py on branch `research/fast_reentry_v1` (restore with
   `git checkout research/fast_reentry_v1 -- research/fast_reentry_v1` and also
   `git checkout archive/research/ewma-band-v1-2026-10 -- research/ewma_band_v1 data/historical/ewma_band_v1_gspc.csv data/historical/ewma_band_v1_index.csv data/historical/ewma_band_v1_tb3ms.csv`
   which it imports; research/trend_robustness_v2/screen.py is in tag `archive/research/main-research-dir-2026-10`). Its R2 arm is production.
   Reference numbers your `--harness` must reproduce within 0.01 pp (main window 2000-03-09 →
   2026-09-29, m=2): R2 15.48% base / 14.90% calibrated CAGR, max DD 46.76%, m=1 15.75%;
   E2 (C0 there) 14.67% / 14.10%, DD 49.56%. Read docs/research/fast_reentry_v1.md,
   ewma_band_v1.md for mechanics, bootstrap (seed 20261011, block 20, 5,000 paths), OOS windows and
   gates G1–G9.
2. After tax: the corrected multi-instrument Turkish after-tax engine on branch
   `research/vt_level_tax_v1` (research/vt_level_tax_v1/screen.py and what it imports; inputs in
   that branch's data/historical, SHA-256 in its DATA_MANIFEST.json; restore with
   `git checkout research/vt_level_tax_v1 -- <paths>`). Read docs/research/vt_level_tax_v1.md,
   tax_asym_v3.md (the C0-unlevered bug to avoid), r2_tbill_combo_v1.md (how R2 was taxed).
   Reference: R2 after tax at $100k (liquidated) 10.16% / 14.71% / 22.89% on the 2000 / 2010 / 2017
   windows; E2 9.53 / 14.50 / 22.69. Clean after-tax bootstraps B1-recent (real 2016–26 TRY calendar,
   market returns resampled) and B2 (flat 20%).
3. K CONTROL: K is implemented in the research harness of branch `research/band_h2_combo_v1`
   (research/band_h2_combo_v1/screen.py + tax_engine.py, arm "K"; restore with
   `git checkout research/band_h2_combo_v1 -- research/band_h2_combo_v1` plus the data files its
   DATA_MANIFEST.json lists). Build on that harness. Reference K numbers (from its RESULTS.json /
   SUMMARY.txt; reproduce within 0.01 pp): pre-tax 15.76% base / 15.18% calibrated, max DD
   46.67% / 46.79%; after tax at $100k liquidated = R2 + 0.593 / 0.529 / 1.086 pp ≈ 10.75 / 15.24 /
   23.98% on the 2000 / 2010 / 2017 windows (take the exact values from that RESULTS.json).
Read docs/research/INDEX.md for everything already tested — don't repeat it.

## Discipline (mandatory)
1. Control = production K exactly (C0 = K; reproduce K's numbers above in a `--harness` mode;
   also report R2 as a reference row).
2. Write `research/<NAME>/PREREGISTRATION.md` (question, arms with every parameter FIXED, data,
   mechanics, gates, selection rule) plus screen.py and DATA_MANIFEST.json, and COMMIT them BEFORE
   simulating any non-control arm on real data. Keep arms few (≤ 6), parameters fixed in advance.
3. Gates for a candidate: pre-tax G1–G8 vs K (as ewma_band_v1 defines them), G9 turnover ≤ 2× K,
   AND after tax: beats K at $100k in all three windows AND clean bootstrap B1-recent and B2
   median Δ ≥ 0 with share > 0 ≥ 55%. If a window/OOS is not usable, pre-register the reduced gate.
   Also report, for every non-control arm (tooling in tags `archive/research/gates-v1-2026-10`
   and `archive/research/gates-v2-2026-10`): the DSR from `research/gates_v1/stats.py` with
   N = registry count + your arms; the pre/post-2020 after-tax gate (Δ vs K > 0 on both
   2000-03-09→2019-12-31 and 2020-01-01→end); PBO via `research/gates_v1/pbo.py` when the study
   is a grid of more than 4 arms; and the provenance stamp from `research/gates_v1/provenance.py`
   in RESULTS.json.
   Sealed window (docs/research/sealed_window_v1.md): never read data dated after 2026-10-30.
4. Run, write RESULTS.json and SUMMARY.txt, run twice and confirm byte-identical RESULTS.json, write
   `docs/research/<NAME>.md` (verdict first, method, tables, gates) and commit. Do NOT edit
   docs/research/INDEX.md.
5. Report honestly; never change gates or arms after seeing data; bug fixes go in "Amendments".

## Process rule
Run every long computation in the FOREGROUND and wait for it. Commit early and often (after each finished
unit of work) so an interruption loses little; a resumed run starts by inspecting git status/diff. Don't end your session with
background jobs running; you are done only when report and results are committed (check git log).

## Final answer (stdout)
≤ 30 lines: verdict, a table per arm (pre-tax CAGR base/cal, max DD, boot Δ vs K, OOS median Δ,
after-tax 2000/2010/2017 Δ vs K, B1/B2 median Δ), failed gates, commit hashes, caveats.
