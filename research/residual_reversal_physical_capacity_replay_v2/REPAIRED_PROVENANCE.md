# Repaired physical capacity replay provenance

This continuation descends from blocked evidence commit `4812b94b051354e4893e21d5332adf74ca2a65dc` and merges accepted production authority `7724a2bc9e61ba325a6a252c8f518cabd99d6c69` (core repair `a167a49cb64cba43d322ffe282368b84d0ded277` plus an additional regression). The original failed receipt and trace remain in `research/residual_reversal_physical_capacity_replay_v1/` without replacement:

- `CAP8_INTEGRITY_FAILURE_RECEIPT.json` SHA256 `91adcb25c13bbc54c34623d3ba81ce8654b8a9fe7c4f6010c12593c1b63c58e9`
- `CAP8_BLOCKED_STRUCTURAL_TRACE.json.gz` SHA256 `4d449448ec4fff28569f717e0dc5c4b93d51f2eecdd0c1c5769a3f3b9f4f114c`

In the invalid trace, DOGE was deferred while BTC SELL worked. At `2026-06-29T01:00:00+00:00`, BTC SELL filled 0.05601639764099569 BTC but left 0.0582293832077028 BTC; LTC also remained held. The old global-only deferred admission activated a DOGE opening reservation at that hour. DOGE filled at `2026-06-29T02:00:00+00:00`, creating three nonzero crypto positions under a cap of two. The invalid arm is provenance only; none of its M1–M8 materiality results are interpreted.

The original frozen M1–M8 specification and cap6/cap8 arm definitions remain byte-identical. This runner writes fresh results only under the versioned `v2` directory and imports the independent verifier unchanged. The scheduler's deferred opening admission uses the accepted production helper with the explicit crypto market cap. No return or other economic series is produced.

The isolated worktree omits two ignored historical files and its tracked equity lifetime file does not match the pinned canonical SHA256. Two pre-arm launches stopped in source acquisition before any replay result. The runner can set `PARAYABUCK_CAPACITY_DATA_ROOT` to the original checkout's `data/historical` directory. All four source files there passed the existing pinned SHA256 checks before the treatment rerun. This is a path selection only; no source hash, strategy parameter, cap, or frozen gate changed.
