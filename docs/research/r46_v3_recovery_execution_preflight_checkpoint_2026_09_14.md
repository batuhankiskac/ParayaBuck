# R46 v3 recovery execution preflight checkpoint

This checkpoint records outcome-free execution readiness only. No R46 economic
execution was authorized or run.

## Identity and freeze

- Candidate: `R46`
- Recovery research ID: `r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14`
- Parent governance commit: `640097e30db857a011caa5d623e08541b2793cc8`
- `C_SRC`: `5871c2e121891784107ec0f7d8103f4e73f54cc0`
- `C_FREEZE`: `d762770cdc3a7d9717600b49110d5bcfd7309e58`
- Source-to-freeze commit count: `1`
- Source-to-freeze changed paths: execution contract and execution contract schema only

## Execution contract

- Path: `data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.json`
- Physical SHA-256: `98114b212997f86b72cf0b3b3de6841ef990050ce6401b771b81225ee3d8e825`
- Canonical SHA-256: `95b18f4f14673448dc584ae614587e9a095b698206a8f2868ad9bef8ff502e58`
- Canonical execution root: `/Users/batuhankiskac/Desktop/ParayaBuck`
- Frozen future output path: `/Users/batuhankiskac/Desktop/ParayaBuck/data/research/runs/r46_treasury_auction_unwind_tlt_v3_recovery_final_2026_09_14`
- Parent v2 package canonical SHA-256: `c67cfdfeebc4cbc0847d5e2e6fbdebdf6928e04b3e5103606eb8bd0c3d03f6c6`
- V3 governance canonical SHA-256: `bc49ab40e00e21deb8b4ef305c18400665d4154d4c1077a8a31dbc7da452c3db`

## Outcome-free preflight

- Status: `RECOVERY_PRE_RUN_OK`
- Blockers: `[]`
- Receipt path: `data/research_receipts/r46_v3_recovery_preflight_2026_09_14.json`
- Receipt physical SHA-256: `0b172ac5a94bc7bb4335d5700387a89c64deb8c275577815a0fb039c8c540a7f`
- Receipt canonical SHA-256: `630eddde21325321576dd1830a1741c6a57cf5b0b835cc132c38f1bfa6b202b6`
- Runner SHA-256: `3612e080d74bc48137d7ae8600df1ea98eeedaa8c7d37e3d246c8b375c94f539`
- Independent verifier SHA-256: `8231db083153d8dee74392f8ab0112bcfc8ac30c512e1412f771a1a429522982`
- Preflight implementation SHA-256: `f87ba9a4def135c0fbd10e7cb306d921ddc26058bd1a74232b10ccc70a58a7eb`
- Dependency lock SHA-256: `61ca56bb0ebb35d1b3acae118c50cf4584026668903afbe0f7efcd5bb9c5032`

## State and incident preservation

- V3 base state physical SHA-256 before/after: `59148f1e32af95ed52bbcea9db802470d7b9b5156a58ba7ffe25d8de824b6ac7`
- V3 base state canonical SHA-256: `0165ea19b68a5e913927cb5016a703ccc9d2be73ba01a7515372727b0bdb9421`
- V3 state: `SEALED_UNUSED`; run count `0`; release count `0`
- V2 reservation physical SHA-256 before/after: `434b5ef71015d132516474a11078c4e5866d3c91e7d265a7f7c3b20d655141e4`
- V2 consumed sidecar: absent
- V2 aborted sidecar: absent
- V2 incident receipt canonical SHA-256: `bdd668747423960faddbb31982566e6c49fcc3b64fd32bb29c40775a621acb8b`

## Authorization and economic boundary

- Authorization schema exists: yes
- Real authorization receipt: absent
- Missing-authorization gate: `BLOCKED_V3_EXECUTION_AUTHORIZATION`
- V3 reserved, consumed, and aborted sidecars: absent
- Economic runner: not executed
- Economic outcomes: not generated or accessed
- PAPER/LIVE: untouched
- Next step requires independent orchestrator review and explicit execution authorization.
