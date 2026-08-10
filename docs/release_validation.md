# ParayaBuck Release Validation

## Identity

```
HEAD:               <filled at completion commit>
validation date/time UTC: 2026-08-10 (final runs completed ~23:50 UTC)
Environment:
  Python version:   3.14.6
  OS:               Darwin 25.5.0 (arm64, Apple M5)
  Alpaca historical feed configured: sip (from config/settings.yaml)
  Alpaca historical feed effective: offline CSV (see data source)
  data source:      data/historical/equity_1200_2019_2026.csv
                    (offline, 1194 symbols, 2019-05-28 .. 2026-07-30)
```

## QUALITY

```
ruff:        pass (0 errors)
format:      pass (100 files formatted)
mypy:        pass (strict, 63 source files, 0 errors)
pytest:      pass (350 tests at last full run)
number of tests: 350
build:       pass (parayabuck-0.1.0 sdist + wheel)
paper chaos: pass (offline chaos gate, 63 marked scenarios)
pip-audit:   pass (no known vulnerabilities in requirements.lock)
Docker build: NOT RUN — external/environment blocked (no Docker daemon:
             OrbStack/colima/podman absent on this machine)
Docker smoke: NOT RUN — same environment block
```

## PAPER

```
read-only connectivity:     NOT RUN — no PAPER credential write opt-in used;
                            engine start/stop against the configured PAPER
                            account verified (started healthy, stopped clean)
paper execution smoke:      NOT RUN — PARAYABUCK_ALLOW_PAPER_SMOKE=YES was not
                            present; the opt-in is never manufactured
PAPER write opt-in present: not set during this validation
```

## EQUITY DATA

```
date range:        2019-05-28 .. 2026-07-30 (daily bars)
downloaded symbols: 1194
historical scanner methodology:
    daily production scanner policy on trailing 60d metrics; inputs strictly
    precede snapshot; Stage-A shortlist capped at 300 symbols by trailing
    median dollar volume; historical spread and broad sector/tradability
    metadata unavailable and explicitly degraded (point-in-time universe)
survivorship limitation:
    candidate metadata reflects currently supported Alpaca assets; delisted
    assets cannot be reconstructed from the offline bar set
data feed:          offline CSV (SIP feed used for the original download)
dataset hash / reproducibility identifier:
    sha256 of CSV: 20bb2fb58ac8e929cde2d24fad1af9b01c9f2bc98cd652fc53f1aa18728fabfd
```

## EQUITY BASELINE

Run with the CURRENT production strategy `equity_dynamic_trend_1d_v1` and its
CURRENT default parameters (no optimization), after the execution-semantics
changes (shared rebalance band, effective-dated fees, two-phase ordering).

```
parameters (defaults): trend_window=150 momentum_window=60 volatility_window=20
                       top_n=6 max_single_weight=0.10
                       annual_volatility_target=0.10 correlation_floor=0.25
                       rebalance_frequency=weekly
TRAIN (2020-01-02..2024-12-31):
  1x: return 4.86% | CAGR 0.95% | Sharpe 0.32 | Sortino 0.09 | maxDD 7.32%
      Calmar 0.13 | PF 2.96 | trades 12 | win rate 58% | turnover 2.17
      avg exposure 1.6% | max exposure 50% | SPY +80.4% | excess -75.5%
  2x: return 4.80% | Sharpe 0.32 | maxDD 7.32%
  3x: return 4.73% | Sharpe 0.31 | maxDD 7.33%
OOS (2025-01-02..2026-07-30):
  1x: return -5.30% | CAGR -3.41% | Sharpe -0.72 | Sortino -0.33
      maxDD 8.06% | Calmar -0.42 | PF 0.28 | trades 20 | win rate 35%
      turnover 3.60 | avg exposure 6.4% | max exposure 50%
      SPY +26.9% | excess -32.2%
  2x: return -5.40% | Sharpe -0.74 | maxDD 8.11%
  3x: return -4.32% | Sharpe -0.65 | maxDD 6.40% | trades 17
```

Interpretation (recorded, not hidden): the TRAIN period's small positive
return comes almost entirely from Jan-Mar 2020; the account then breached
the 5% portfolio drawdown halt and, holding cash, never recovered above
peak-5%, so entries stayed latched for the rest of TRAIN. OOS is negative
under every cost assumption and loses to SPY by a wide margin.

## OPTIMIZATION

```
grid:       pass 1: trend_window {100,150,200} x momentum_window {40,60,90}
            (one-at-a-time plus corners, defaults elsewhere) — 9 trials
            pass 2: around the pass-1 winner with
            top_n {4,6,8} x annual_volatility_target {0.08,0.10,0.12} x
            max_single_weight {0.08,0.10} — 13 trials
number of combinations: 22
TRAIN ranking metric:   risk_adjusted (config backtest.ranking_metric)
best TRAIN parameters:  trend_window=200 momentum_window=90
                        annual_volatility_target=0.08 top_n=6
                        max_single_weight=0.10 volatility_window=20
                        correlation_floor=0.25 weekly
                        TRAIN 2020-2024: return 45.07% Sharpe 1.233
                        maxDD 6.53% trades 150
top neighboring configurations (TRAIN):
                        trend=200 mom=90 vol=0.10 -> return 54.4% Sh 1.238
                        trend=200 mom=90 vol=0.12 -> return 53.8% Sh 1.153
                        trend=200 mom=90 cap=0.08 -> return 51.2% Sh 1.206
                        trend=200 mom=90 top_n=4 -> return 39.9% Sh 1.001
                        trend=200 mom=90 top_n=8 -> return 34.0% Sh 1.024
parameter stability notes:
                        The trend=200/momentum=90 plateau is broad (Sharpe
                        1.0-1.24 across the vol/top_n/cap neighbors), so the
                        TRAIN winner is not a single lucky point. The
                        default trend=150 config is the outlier (TRAIN
                        Sharpe 0.32, mostly flat after the 2020 drawdown
                        latch). Stability does NOT transfer out of sample:
                        see FINAL EQUITY OOS.
```

## FINAL EQUITY OOS

```
(equity_dynamic_trend_1d_v1 with the best TRAIN parameters, run exactly once)
return:          -5.84% (1x) | -5.89% (2x) | -5.93% (3x)
CAGR:            -3.76% (1x)
Sharpe:          -0.934 (1x) | -0.943 (2x) | -0.951 (3x)
Sortino:         -0.313
max drawdown:    7.73%
Calmar:          -0.487
profit factor:   0.17
trades:          14 (closed positions)
win rate:        35.7%
turnover:        1.77
average exposure:2.8%
maximum exposure:50%
SPY return:      +26.9% (2025-01-02..2026-07-30)
excess return:   -32.7%
```

## CRYPTO

```
validated:      NOT VALIDATED
enabled:        no (disabled by default)
reason:         TRAIN (2024-08..2024-12) +3.7% / Sharpe 1.32 at 1x but 0.43
                at 3x costs; OOS (2025-01..2026-07) -4.85% / Sharpe -1.49 at
                1x, negative at every cost level. The 25 bps tier-1 taker
                cost plus high turnover (2.4 OOS) destroys the edge. Crypto
                remains disabled; this is the valid v1 outcome.
```

## KNOWN LIMITATIONS

- Historical regulatory fees are approximations of the official schedules
  (SEC Section 31, FINRA TAF, CAT NMS Plan); Alpaca/FINRA/SEC/CAT rounding
  and billing details can differ. Never claim broker-exact historical fees.
- Docker build/smoke could not be executed in this environment (no Docker
  daemon available); the gate is recorded as externally blocked, not passed.
- Survivorship bias: delisted symbols are absent from the offline bar set.
- The equity CSV was downloaded through the configured historical feed;
  liquidity/spread proxies remain estimates.
- Web backtests are mutually exclusive with a running trading engine (by
  design); CLI backtests from a separate process are not blocked.
- The portfolio 5% drawdown halt latches entries until equity recovers
  within 5% of the peak; a cash-holding account can remain latched
  indefinitely. This is the configured risk behavior, mirrored in
  production; it dominates the empirical results above.

## FINAL VERDICT

```
EQUITY STRATEGY: NOT VALIDATED
    - Default parameters: TRAIN +4.9% / Sharpe 0.32, OOS -5.3% / Sharpe -0.72.
    - Best TRAIN parameters (trend=200, momentum=90, vol target 0.08):
      TRAIN +45.1% / Sharpe 1.23 with a broad stable plateau, but the single
      clean OOS run is -5.8% / Sharpe -0.93 (1x/2x/3x: -5.84/-5.89/-5.93).
    - OOS is negative in every configuration and cost assumption; excess
      return vs SPY is deeply negative (-33pp over 19 months).
    - The TRAIN-only optimization did not peek at OOS; the final OOS was run
      exactly once after selection and failed, so it remains a clean
      one-shot validation.
CRYPTO: NOT VALIDATED, stays disabled (OOS -4.9% / Sharpe -1.49).
V1 RELEASE CANDIDATE: NO
    - The execution/risk/readiness engineering work in this closure IS
      complete and gated green (see QUALITY), and the release-validation
      artifact is committed, but the current equity strategy does not
      validate out of sample under the recorded evidence. Releasing it as a
      trading product would require the strategy evidence to change.
```
