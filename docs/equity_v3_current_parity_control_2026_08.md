# Current-Parity V3 Control Receipt (R1)

Date: 2026-08-31  
Status: clean deterministic research comparator; not a PAPER/LIVE promotion receipt.

This receipt was generated twice from a clean tracked checkout and the two
runs matched every recorded component. It is the operational comparator for
the runtime-sized observation-context implementation. It does not replace the
immutable historical `SYMMETRIC_FULL_T1` receipt in
`docs/v3_historical_validation_final.md`.

## Source and policy

| Field | Value |
| --- | --- |
| Source commit | `34a9a3f192b19ba207cee0316ffd60aae7ab3e21` |
| Runner SHA-256 | `0870f7648f0074a5dedcee92235f0e906d89bec3b39592dfd201321bc4c3452c` |
| Config digest | `1833407d7c42fcef45b1f2aef200a3fd14e344d6cdcc9b03c578f8e494152a3e` |
| Bars SHA-256 | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` |
| Lifetimes SHA-256 | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` |
| Corporate actions SHA-256 | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` |
| Allocation / terminal policy | `0.55` / `T1` |
| Evaluation end | `2026-07-30T00:00:00+00:00` |

## Result

| Metric | Value |
| --- | ---: |
| Total return | `76.4538%` |
| CAGR | `5.5197%` |
| Sharpe | `0.571342` |
| Max drawdown | `20.3809%` |
| Turnover | `149.724817` |
| Rebalance decisions | `495` |
| Fills | `1,890` |
| Terminal events | `12` |
| Incomplete strategy batches | `0` |

## Determinism evidence

Run `R1-a` and `R1-b` both recorded empty tracked-worktree paths before and
after execution and the same receipt hash:

`81bfdd8361c9819de0830e38e6fd91595f081bbc2ef473c191719a8ea031bf0a`

| Component | SHA-256 |
| --- | --- |
| Universe | `d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011` |
| Target sequence | `b3b39c78d7731776d9dcc90a9eff2352b2e18007848bb14d23f68027d77f15c9` |
| Fill sequence | `dff4000e6c71074bcd7ffde96c98a209eb9b54c74233ddb264b7fb151d1b0b63` |
| Equity curve | `9d08f9c3b3a6959189fb0a482c7de6d294fd4a72e1cec3ae7fe7f79d6f2e02fb` |
| Terminal events | `8fd87474a7aaf9a7c5977dcb6c5d0cd02b09af0254d9fdefe81ba9ca0c766c95` |

Raw JSON outputs remain under the gitignored
`data/research/equity_v3_current_parity_control_2026_08/` directory. Recreate
them with:

```text
.venv/bin/python scripts/research_v3_current_parity_control.py --output data/research/equity_v3_current_parity_control_2026_08/control_r1.json
.venv/bin/python scripts/research_v3_current_parity_control.py --output data/research/equity_v3_current_parity_control_2026_08/control_r2.json
```

The runner refuses a dirty tracked worktree and does not update tracked
manifests or product strategy registration.
