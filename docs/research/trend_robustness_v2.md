# Trend robustness v2 (2026-10-11)

**No arm passes the pre-registered gate, so VT35 stays unchanged.** Neither an
SMA ensemble (100/150/200/250) nor splitting the sleeve between Nasdaq-100
(QLD) and S&P 500 (SSO) improves on VT35 over 2000–2026. All three candidates
give up 1.2–2.7 pp of CAGR. The two-index arm M1 has the only real advantage:
max DD 42.8% against 49.6%. It still fails on return (G1), its Calmar gain is
only 5%, below the 10% bar (G3), it gives up 7.5 pp/yr since 2017 (G4), and it
does worse in the bootstrap (G5).

Evidence: `research/trend_robustness_v2/` (`PREREGISTRATION.md` committed in
`98d9e41` before any arm was simulated; `screen.py`; `RESULTS.json`;
`SUMMARY.txt`). The only inputs are the committed
`data/historical/stress_robustness_v1_{daily,dtb3}.csv`. The run is offline
and deterministic and takes about 10 s. Two runs gave byte-identical
`RESULTS.json`.

## Method

- **Mechanics:** the same close-to-close Stage A2 mechanics as
  `stress_robustness_v1`. The signal is taken on the completed close and
  trades at the next open. There is a 0.5 pp band, the total weight is capped
  at 0.98, and idle cash earns zero.
- **Instruments:** every arm, the control included, trades synthetic daily-reset
  2x instruments: QLD\* on QQQ and SSO\* on SPY, with expense plus T-bill
  financing.
- **Harness check:** C0 on the `stress_robustness_v1` window reproduces that
  file's VT35 exactly (13.7576% CAGR, 49.5643% max DD; difference 0.0000 pp).
- **Common window:** 2000-03-06 → 2026-09-29. It starts late enough for the
  250-session SMA warm-up.
- **Arms:**
  - **C0:** VT35.
  - **E1:** QLD\* sized by the share of {100, 150, 200, 250}-session SMAs that
    QQQ is above.
  - **M1:** half QLD\* on QQQ > SMA200 and half SSO\* on SPY > SMA200, each leg
    with its own 35% vol target.
  - **M2:** M1 with ensemble scores.

## Results (2x costs, gated)

| Arm | CAGR | Max DD | Calmar | Sharpe | 2017+ CAGR | Turnover/yr | Boot. median CAGR | Boot. share > SPY |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **C0 VT35** | **13.37%** | **49.6%** | **0.27** | **0.61** | **30.7%** | 6.5 | 7.40% | 46.5% |
| E1 ensemble | 12.09% | 51.9% | 0.23 | 0.59 | 26.7% | 7.4 | 7.47% | 46.5% |
| M1 two indices | 12.14% | 42.8% | 0.28 | 0.63 | 23.2% | 6.8 | 6.25% | 37.8% |
| M2 both | 10.68% | 49.5% | 0.22 | 0.58 | 21.2% | 8.0 | 6.22% | 37.3% |
| SPY hold | 8.47% | 55.2% | 0.15 | 0.52 | 15.2% | 0 | 8.68% | — |

At 1x costs the ordering is unchanged: C0 13.66%, E1 12.43%, M1 12.45%, M2
11.04%.

| Gate | E1 | M1 | M2 |
|---|---|---|---|
| G1 CAGR ≥ C0 − 0.5 pp | fail | fail | fail |
| G2 DD ≤ C0 | fail | pass | pass |
| G3 CAGR > C0 or Calmar ≥ 1.1 × C0 | fail | fail | fail |
| G4 2017+ ≥ C0 − 2 pp | fail | fail | fail |
| G5 bootstrap | pass | fail | fail |
| G6 G1–G3 at 1x | fail | fail | fail |
| G7 turnover ≤ 2 × C0 | pass | pass | pass |

## Findings beyond the gate

- **The ensemble does not reduce parameter risk where it matters.** E1 lost
  more in 2000–2002 than C0: −43.2% against −41.6%. It was also underwater for
  9.7 years against C0's 7.5. Averaging the SMAs adds partial positions and
  turnover without avoiding the whipsaws.
- **Diversifying across indices trades return for drawdown at a poor rate.**
  The S&P 500 leg cushions 2008 (M1 −9.8% that year against C0 −18.3%), but
  it costs about 1.2 pp/yr over the full window.
- **The bootstrap shows the core risk, and it is not new.** On 10-year paths
  built from ~1-month blocks, VT35's median CAGR (7.4%) is below SPY's (8.7%),
  and VT35 beats SPY in only 46.5% of paths. This matches
  `stress_robustness_v1`. VT35's edge comes from long-lasting trends in real
  history (13.4% against 8.5%). When trends are short, the rule loses to buy
  and hold, and none of the arms changes that.
- **SSO\* check (report only):** correlation of SSO\* daily returns with
  2 × SPY daily returns is 1.0000. SSO\* made 10.26% CAGR against SPY's 8.47%
  over the common window.

## Decision

VT35 stays the PAPER identity with no parameter change. This campaign is
closed. No code, configuration or runtime changed.
