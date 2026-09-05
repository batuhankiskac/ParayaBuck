# Exposure audit script review (2026-09-05)

Reviewed `scripts/research/high_proximity364_exposure_audit_2026_09_05.py` at SHA `b3906b02403b095c51c064b6818df54a52071295e99b9266e6fbf141f0188979` read-only.

The initial-capital denominator is preserved: every curve and period summary uses `INITIAL_CASH = 100000.0`, and the ledger gate requires the first cash and equity values to equal that amount. Ledger checks are coherent for the supplied payload: cash plus marked position values versus equity, summed fill fees versus reported fee total, terminal NAV versus terminal ledger equity, and net fill quantities versus terminal quantities.

No blocking accounting bug was found. Two scope limitations should remain visible to diagnostic_audit: symbol concentration is stored as raw hours and value-hours rather than normalized dominance percentages, and the 364-day readiness table covers the configured 22-symbol trade pool rather than proving actual point-in-time scanner/rank eligibility. These are reporting limitations, not reasons to invalidate the completed audit.
