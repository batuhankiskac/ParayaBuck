# sealed_window_v1: a forward sealed validation window

Decision record, 2026-10-27 (audit item 27). This is not a backtest.

## Verdict

No usable sealed window exists in the past. Every session from 2000-03-09 to 2026-09-29 has
been used to choose or reject a rule, so any "holdout" carved from it now would be contaminated.
From now on, the window is sealed **forward**: market sessions after **2026-10-30** are reserved.

## Rules

1. **Sealed data.** No research study may read QQQ, QLD, SPY or macro data dated after
   2026-10-30 to choose a rule, a parameter, an arm or a gate. Screens must clip inputs at that
   date (`SEALED_FROM = date(2026, 10, 30)`), and the provenance stamp (`research/gates_v1/provenance.py`)
   records the clip.
2. **Opening.** The window opens once, on or after **2027-10-29** (one year, about 250 sessions).
   At that point every rule still alive (K, plus any candidate that passed all gates in the
   meantime) is evaluated once on the sealed year. The comparison is pre-registered in
   `research/sealed_window_v1/PREREGISTRATION.md` before the data is read.
3. **What it decides.** A candidate replaces K only if it beats K on the sealed year after
   Turkish tax and the full in-sample gates still hold. One year is short, so a sealed-year
   loss of more than 3 pp after tax against K rejects the candidate. A smaller loss or any win
   keeps the in-sample verdict. K itself is never removed on the sealed year alone.
4. **PAPER is not the window.** The live PAPER record is evidence about execution, not a
   holdout for rule selection. It must not be used to tune rules either.
5. **Registry.** Each study that runs before the opening still adds its trials to the registry
   (gates_v1), so the DSR at the opening uses the full count.

## Why forward only

Re-using a past window as "sealed" would be the selection bias the DSR and PBO tooling
(gates_v1) is meant to catch. A forward window is the only out-of-sample period left that no
decision has seen.
