# Final Decision

**`CORRECTED_V3_BASELINE_VALID`**

The original G5 was **`G5_MISSPECIFIED`**: it treated the production
account-level 5% drawdown entry latch as a direct bound on an offline alpha
NAV whose drawdown breaches are intentionally shadow-only.

After freezing and hashing `V3_BASELINE_PROTOCOL_V2.md` before economic-output
inspection, the persisted corrected replay passed every V2 gate. No 11.7M-row
replay was needed, and no production behavior or configuration was changed.

This is a research-baseline decision only. It does not promote the strategy,
change PAPER, enable LIVE, or alter the production `main` branch.
