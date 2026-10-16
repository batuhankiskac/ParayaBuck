"""research/two_leg_kernel_v1 -- the two-leg kernel's parity evidence.

Not an idea screen: this directory holds the *parity* of the two-leg kernel
(`research/fast_engine2/`), the code deliverable of the `two_leg_kernel_v1` engineering task.

    riskoff_ref.py   `riskoff_after_tax_v1`'s after-tax engine, copied verbatim from tag
                     `archive/research/riskoff-after-tax-v1-2026-10` -- the parity (b) oracle
    parity_b.py      the fixed synthetic two-leg case: the kernel against that reference
                     (bit-for-bit where the conventions coincide, and the deliberate
                     both-legs-realise-in-one-year case where they do not)
    run.py           `--harness` / `--run`: every parity cell, the engine's own parity run, the
                     framework harness, wall clock -> `HARNESS.json` / `RESULTS.json` / `SUMMARY.txt`

Engine-level parity (a) -- leg B identically zero against `research/fast_engine` (pre-tax,
after-tax, bootstrap, the published K anchors and the engine's own `parity --quick`) -- lives with
the engine, in `research/fast_engine2/fe2_parity.py`.
"""

__all__ = []
