"""ic37c_confirm_v1 -- the confirmation study of IC37-C (off-state Treasury sleeve, real-yield
gated) on an extended bond history.

Read `PREREGISTRATION.md` first: IC37-C was selected *after* seeing `batch_bank_v3`
(N = 303 ledger trials), so this study's only sound content is the **new** evidence
(E1-E5): a synthetic IEF total-return proxy back to 1969 built from FRED constant-maturity
yields, a point-in-time (ALFRED vintage) CPI real-yield proxy for the pre-2003 segment, and
the pre-registered gates at $100k on the standard main window.

    python -m research.ic37c_confirm_v1.data --fetch      # once: the FRED/ALFRED/Yahoo inputs
    python -m research.ic37c_confirm_v1.screen --harness  # before any arm
    python -m research.ic37c_confirm_v1.screen --run      # RESULTS.json + SUMMARY.txt
"""

__all__ = []
