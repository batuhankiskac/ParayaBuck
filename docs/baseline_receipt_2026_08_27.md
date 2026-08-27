# Baseline Receipt — 2026-08-27 Pre-Deployment

HEAD: acd98d56e5b976851542e8e375b45a68d8e8869e
Status: clean (no untracked, 907 passed 11 skipped pre-hardening)
Config: config/settings.yaml
  equity: equity_residual_momentum_v3 (0.60, top8, weekly)
  crypto: crypto_low_variance_hysteresis_v1 (0.05, lookback 22, top2, retention 4, 96h)
  runtime.mode: paper
  live.enabled: false
  live.hard_dollar_cap: 0
Gates pre-hardening:
  pytest: 907 passed 11 skipped 0 failed
  ruff: PASS (All checks passed)
  mypy: PASS (Success)
  build: PASS (wheel built via uv --with build)
  docker compose config: PASS (127.0.0.1:8000:8000 loopback)
Strategy parity:
  equity V3 frozen (formation 252 skip 21 beta273 resid63 top8 0.10 ivol0.70)
  crypto T1 hysteresis 22/2/96h retention4 verified via shadow parity
