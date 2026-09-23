# Account capacity migration V3 research closure

`research/account-capacity-migration-8-v3` ended at `9c5e61382e8cdb71fdd3442d945719024accedd9`. The frozen independent verifier passed, and the research decision is `ACCOUNT_ARCHITECTURE_MIGRATION_8_ACCEPTED`. Account slot denials fell from 1,112 at cap 6 to 438 at cap 8. The local annotated archive tag `archive/research/account-capacity-migration-8-v3-accepted-2026-09` peels to that exact commit; it has not been pushed by this audit.

This is research acceptance only. Cap 8 observed realized gross above 0.60 at 9,152 of 50,194 account marks, with a peak of 0.615507, while target gross stayed at or below 0.60. The production parity audit in `research/cap8_production_parity_v1/` found a separate pure-BUY headroom defect and blocks deployment. Production `config/settings.yaml` remains `max_positions: 6`; PAPER/LIVE modes and all strategy and risk parameters remain unchanged.
