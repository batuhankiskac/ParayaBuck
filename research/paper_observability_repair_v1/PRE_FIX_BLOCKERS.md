# Pre-fix production observability blockers

Authority: fetched origin/main 7724a2bc9e61ba325a6a252c8f518cabd99d6c69. This branch starts directly from it. Prior audit commit 2920adfd6e01bebf13cbb477a1bf09c262034deb was read as evidence, not merged. PRE_FIX_REPRODUCTION.json records synthetic results obtained before source changes.

B1: src/parayabuck/runtime.py:3406 gates its receipt writer on equity_residual_momentum_v3. The scheduled equity batch reaches it. Scheduled crypto is excluded. _run_startup_catch_up at 3587 calls PortfolioRuntime.startup_catch_up directly, so neither equity nor crypto catch-up reaches it. The old PaperReceipt.write filename is date+market+strategy: two decisions on one day produced the same path in a temp directory.

B2: Current receipt target_weights and intended_orders are intent. submitted_order_ids are broker IDs returned by submission, but are not a complete lifecycle. fills is empty at decision write. positions_after adds intended quantity to a predecision account snapshot, and actual_gross_exposure multiplies that projected quantity by a fixed 100. These values are neither broker-confirmed positions nor fills. The SQLite portfolio_decisions -> portfolio_orders -> portfolio_fills and execution-events tables remain authoritative for later status/fill truth.

B3: A temp SQLite DB with one current crypto decision and no equity decision was passed to scripts/audit_v3_paper_soak.py. It returned observations.status=no_data. That tool filters EXPECTED_MARKET=equity and EXPECTED_STRATEGY=equity_residual_momentum_v3; it cannot certify crypto identity, cap2, both-market effective slots or fresh BUY admission.

B4: Synthetic BTC/LTC positions produced a capacity payload with positions_count=2 and effective_slots=2. Adding a working DOGE opening order raised global effective_slots to 3, but no crypto effective-claim field appeared. The dashboard separately reports held crypto_positions and max_crypto_positions; it does not display the third working crypto claim.

No broker, scanner provider, or PAPER runtime was called. All temporary state lived under a temporary directory.
