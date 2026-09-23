# Physical availability clock V2 closure

Decision: `PHYSICAL_AVAILABILITY_CLOCK_V2_VALIDATED_WITH_LIMITATIONS`.
Migration readiness: `MIGRATION_REPLAY_READY`.

The immutable, annotated infrastructure tag
`infrastructure/physical-availability-clock-v2-cap6-2026-09` peels to
`11890e93ad1a5223cb11483488deb4d5a6aec3d3`. Its research-only code,
input identities, tests and receipts are the authority for a future frozen
six-versus-eight account-capacity study. The tag is published; the physical
clock V2 branch need not remain open. Do not merge its full artifact tree into
production main.

Equity V3 physical-session parity and corrected causal crypto 454/406 parity
passed. The combined shared-account cap-six replay passed with zero over-cap
timestamps, at most six actual positions and six occupied/reserved claims,
zero causality violations, and exact repeat determinism. The full evidence
and source hashes are in the tagged
`research/physical_availability_clock_v2/` receipts.

The obsolete label-clock V2 archive has no complete event trace, so exact
historical counts of scarce-slot first-claimant changes and fill shifts
attributable solely to the clock cannot be reconstructed. This comparison
limit does not differentiate a future migration's two arms: both must use the
same tagged physical replay and input identities, with only the global
position-count cap varied. It does not validate any old label-clock economics.

The next research step is a pre-outcome frozen cap-six versus cap-eight study
on a new branch. No cap-eight arm was run in this clock campaign. Production
`maximum_positions` remains six; strategy parameters, rebalance band,
PAPER/LIVE modes and broker behavior remain unchanged.
