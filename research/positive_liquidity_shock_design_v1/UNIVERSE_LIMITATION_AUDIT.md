# Broad US_EQUITY universe limitation audit

## Verdict

`UNIVERSE_LIMITATION_INVALIDATES_LIQUIDITY_SHOCK_TEST`

Bali et al. study CRSP common stocks. ParayaBuck's historical `US_EQUITY`
bars lack a complete effective-dated security-type taxonomy for 2016–2026.
ETFs, ETNs, preferreds, closed-end funds, structured products and other
non-operating-company instruments can enter; current labels cannot be
backfilled as historical truth.

Within-security normalization reduces static cross-product level differences,
but it does not neutralize product-specific flow shocks, creations/redemptions,
leverage resets, bond-market liquidity or index-rebalance effects. Those can
populate the extreme `LIQU` tail for mechanisms unrelated to the common-stock
underreaction hypothesis.

Reserved-role isolation excludes SPY, SH, PSQ and DOG from ordinary targets,
but the leveraged/inverse list is finite and does not classify the remaining
9,010 histories. Consequently a local run could only claim performance in the
canonical broad product universe, not a Bali common-stock replication. Since
the campaign explicitly requires robustness to this limitation, the gate
fails. This is an additional reason not to authorize economics; it is not
silently relabeled as common-stock evidence.
