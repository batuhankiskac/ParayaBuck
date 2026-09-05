# Crypto sleeve complementarity diagnostic — 2026-09-05

Status: `DIAGNOSTIC_CURVE_BLEND_CORRECTED`. This is a derived curve measurement from completed native C3 MAX and C4 signed-risk outputs. It is not a V3 combined result, shared-ledger replay, PAPER/LIVE evidence, or a candidate promotion/elimination decision. Attempt 01 remains immutable.

Corrected manifest: `data/research_manifests/portfolio_expansion_crypto_diagnostic_2026_09_attempt_02.json`. Corrected output: `data/research/portfolio_expansion_crypto_diagnostic_2026_09/attempt_02_corrected/diagnostic_results.json` (`9b7706c0a960d59dc753b546a5b72f1fda0a13710301f72a227749bdafb1f877`). Canonical C3/C4 inputs remain immutable.

The baseline is the native `low_variance` arm at the same 5% crypto sleeve budget. Each mixture averages normalized standalone account NAVs 50/50, giving 2.5% effective weight per component and retaining a 5% crypto budget. These are diagnostic target weights in normalized curves, not realized or production allocations. Native TR/CAGR/hourly DD/hourly Sharpe use the complete hourly curve, `INITIAL_CASH=100000` at the window-start day-0 sentinel, and the common terminal mark. Daily Sharpe and correlation use common UTC day-end returns, including the first day-end return from initial cash; they are separate daily metrics. There are exactly 12 cells.

## Results

| mix | window | cost | hourly n | daily corr | native TR | native CAGR | hourly DD | hourly Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| lowvar + max | full | 1x | 46744 | 0.9791 | 0.0366 | 0.0068 | 0.0704 | 0.1948 |
| lowvar + signed_risk | full | 1x | 46744 | 0.8739 | 0.0832 | 0.0151 | 0.0757 | 0.4152 |
| lowvar + max | full | 2x | 46744 | 0.9789 | 0.0215 | 0.0040 | 0.0729 | 0.1234 |
| lowvar + signed_risk | full | 2x | 46744 | 0.8739 | 0.0632 | 0.0116 | 0.0775 | 0.3230 |
| lowvar + max | full | 3x | 46744 | 0.9788 | 0.0065 | 0.0012 | 0.0753 | 0.0506 |
| lowvar + signed_risk | full | 3x | 46744 | 0.8781 | 0.0359 | 0.0066 | 0.0792 | 0.1945 |
| lowvar + max | recent | 1x | 22631 | 0.9740 | 0.0152 | 0.0059 | 0.0449 | 0.2287 |
| lowvar + signed_risk | recent | 1x | 22631 | 0.9155 | 0.0163 | 0.0063 | 0.0491 | 0.2212 |
| lowvar + max | recent | 2x | 22631 | 0.9740 | 0.0069 | 0.0027 | 0.0475 | 0.1109 |
| lowvar + signed_risk | recent | 2x | 22631 | 0.9155 | 0.0070 | 0.0027 | 0.0512 | 0.1035 |
| lowvar + max | recent | 3x | 22631 | 0.9739 | -0.0014 | -0.0006 | 0.0501 | -0.0069 |
| lowvar + signed_risk | recent | 3x | 22631 | 0.9146 | -0.0043 | -0.0017 | 0.0558 | -0.0397 |

The six C3/C4 `low_variance` source controls were checked for exact timestamp and equity-array identity, including initial cash, day-0 sentinel, and terminal mark. Their ledger hashes and terminal policies also match. This source-control identity is distinct from the superseded attempt 01 claim that daily-resampled statistics matched native statistics. Full windows contain 1,948 UTC day-end observations; recent windows contain 943.

The result is descriptive: MAX mixtures show high daily correlation (0.9739–0.9791) and negative native return deltas in every cell. Signed-risk mixtures show lower daily correlation (0.8739–0.9155), positive full-window native return deltas, and negative recent-window native return deltas. These observations do not establish joint execution benefit because fills, shared cash, max-two crypto netting, and risk suppression are absent. Turnover/fee fields are weighted standalone proxies; no new economic run was performed.

## Provenance and limits

- Protocol: `docs/research/portfolio_expansion_batch_2026_09_05_protocol.md` SHA256 `bd3d29a6ecc700fb70f0650f0a82e97be0d4129de6b7fe5164d4e422f2a6de02`.
- Corrected script/output hashes are recorded in the attempt 02 invocation and manifest.
- MAX input: `data/research/crypto_max_return_economic_2026_09/attempt_01_canonical/economic_results.json` SHA256 `0a007600e4ecb603928bf372d7bc958f9c3f736a5079c15ea367de46797a001c`.
- Signed-risk input: `data/research/crypto_signed_risk_economic_2026_09/attempt_01_canonical/economic_results.json` SHA256 `10555181eaf17b91d36fe431bed431127955398d7c197606556ce2dce4c1e826`.
- Invocation record: `data/research/portfolio_expansion_crypto_diagnostic_2026_09/attempt_02_corrected/invocation.json`.
- The configured crypto pool and bar availability are not venue-PIT completeness. No regime conclusion is inferred from these curves.
- The prior P1 endpoint/cadence finding and P2 unlabeled-cadence finding remain documented in `docs/research/portfolio_expansion_crypto_diagnostic_audit_2026_09_05.md`; attempt 02 is the corrected closure artifact.
