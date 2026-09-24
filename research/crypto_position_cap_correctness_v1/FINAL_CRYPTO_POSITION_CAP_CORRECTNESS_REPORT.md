# Production crypto position cap correctness

Decision: `PRODUCTION_CRYPTO_POSITION_CAP_REPAIR_ACCEPTED`.

The shipped global cap was eight, crypto cap two, and LIVE was disabled. An offline real-runtime reproduction showed that a rejected BTC SELL left BTC and LTC held while the deferred path submitted DOGE BUY. The same class of error appeared in the research replay. The production final admission helper now counts both global and explicitly capped market slots from broker positions, working and durable opening reservations, and same-cycle admissions. Deferred work stays retryable when a newly needed SELL remains and slot admission rejects its replacement BUY.

Regression coverage includes rejected, canceled, partial-terminal, and full BTC SELL outcomes; add-to-held, working order, alias, sequential batch, risk-reducing SELL, and cross-market global/crypto count cases; repeated advancement and restart. Focused groups passed, full pytest passed with `1231 passed, 4 skipped`, Ruff and strict source mypy passed. No strategy parameter, cap, PAPER, or LIVE setting changed. No economic analysis was performed.

The original research cap-eight trace remains invalid. Repair and a fresh no-return structural rerun are required before any M1–M8 interpretation.
