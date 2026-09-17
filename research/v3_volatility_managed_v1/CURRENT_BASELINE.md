# Current baseline

This campaign starts from the clean production baseline requested by the
research brief.

| Item | Recorded value |
|---|---|
| Repository | `/Users/batuhankiskac/Desktop/ParayaBuck` |
| Starting checkout | `main` |
| Local `main` | `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1` |
| `origin/main` after `git fetch --prune origin` | `5175a58b2fb4084e5c5a00854d3c3c94b0e819a1` |
| Baseline commit | `fix backtest lifecycle and observation identity` |
| Starting worktree | clean |
| Research branch | `research/v3-volatility-managed-v1` |

The old checkout was `research/spy-defensive-overlay-v1`. It was clean but was
not used as a source of code, data, or economic evidence. The requested
research branch was created only after verifying `main` and `origin/main`.

## Production configuration observed on `main`

- Runtime mode: `paper`.
- Equity identity: `equity_residual_momentum_v3`, 1d signal, weekly cadence,
  maximum allocation `0.55`.
- Crypto identity: `crypto_low_variance_hysteresis_v1`, maximum allocation
  `0.05`.
- Shared risk limits: maximum gross `0.60`, minimum cash `0.40`, maximum
  positions `6`, equity per-position cap `0.10`, daily-loss limit `0.005`,
  drawdown limit `0.05`.
- LIVE: `live.enabled=false`, `runtime.mode` is not `live`, and no live
  authorization was attempted.
- No production registry, YAML configuration, PAPER identity, risk limit,
  crypto setting, or broker state is changed by this campaign.

## V3 integrity inspected

The control uses the current `EquityResidualMomentum1dV3` directly. Its
positive 12-1 gate, 6/9/12 raw percentile consensus, idiosyncratic-volatility
filter, top-name selection, alpha ranks, inverse-volatility sizing, weekly
cadence, next-tradable-bar execution, effective-dated equity fees, and current
risk overlay are not reimplemented or retuned. Treatment code is a research
subclass and uses only the post-sizing exposure seam plus audit metadata.

