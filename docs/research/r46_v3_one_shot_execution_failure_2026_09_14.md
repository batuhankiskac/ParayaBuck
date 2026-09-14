# R46 v3 one-shot execution failure

This record is technical failure evidence only. No economic interpretation,
tuning, or retry was performed.

- Candidate: `R46`
- Recovery research ID: `r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14`
- Authorization commit: `e209fa3343ad6a2376efb2860e1b25923d407b9f`
- Runner invocation count: `1`
- Runner exit code: `1`
- Run log: `/tmp/r46-v3-economic-run.XXXXXX.log`
- Run log bytes: `237`
- Run log physical SHA-256: `efba81372ebd2f136a3f840327fb8b172e9806ef36ee32eff6d698d63d958037`
- Exact terminal failure: `{"error": "BLOCKED_R46_INCUMBENT_BASE_MISSING", "status": "BLOCKED"}`

## Preserved evidence

- Authorization receipt physical SHA-256: `7270806c0b17bfcd450fdd762c0dc7330849a631003a0e42abb97a0e8433de36`
- Authorization receipt canonical SHA-256: `dce81fa5d154dd50912a56790313166b0031f940a3471d4aa8de7c5cb34e47ac`
- Preflight receipt physical SHA-256: `0b172ac5a94bc7bb4335d5700387a89c64deb8c275577815a0fb039c8c540a7f`
- Preflight receipt canonical SHA-256: `630eddde21325321576dd1830a1741c6a57cf5b0b835cc132c38f1bfa6b202b6`
- Execution contract physical SHA-256: `98114b212997f86b72cf0b3b3de6841ef990050ce6401b771b81225ee3d8e825`
- Execution contract canonical SHA-256: `95b18f4f14673448dc584ae614587e9a095b698206a8f2868ad9bef8ff502e58`
- V3 reserved sidecar: `data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json.reserved.json`
- V3 reserved sidecar bytes: `1271`
- V3 reserved sidecar physical SHA-256: `5b20573007c76d3928d71c02c81756562db07293e52ccf523263efb81f54d3ed`
- V3 reserved transition canonical SHA-256: `4f852d9cda349c05782142c6289e0da00f1a501948bcb23d26308283e6faa964`
- V3 reserved sidecar status: `RESERVED`
- V3 consumed sidecar: absent
- V3 aborted sidecar: absent
- Output directory: absent
- `results.json`, `ledger.json`, `report.json`, and `run_receipt.json`: absent
- Independent verification receipt: not created because the runner exited nonzero

## State preservation

- V3 base state physical SHA-256 before/after: `59148f1e32af95ed52bbcea9db802470d7b9b5156a58ba7ffe25d8de824b6ac7`
- V3 base state canonical SHA-256: `0165ea19b68a5e913927cb5016a703ccc9d2be73ba01a7515372727b0bdb9421`
- V2 reservation physical SHA-256 before/after: `434b5ef71015d132516474a11078c4e5866d3c91e7d265a7f7c3b20d655141e4`
- Pre-run audit log: `/tmp/r46-v3-prerun.XXXXXX.txt`
- Pre-run audit log bytes: `1217`
- Pre-run audit log physical SHA-256: `c13c3ad7f9b579a691791dfdcf81c549f47dafa6931781e05e94111bc0318549`
