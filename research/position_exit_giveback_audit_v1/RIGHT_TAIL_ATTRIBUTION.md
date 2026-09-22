# Right-tail attribution

Equity actual exits; no trades removed and no strategy rerun. Top 10% uses ceil(905×0.10)=91 episodes. Net contribution shares may exceed 100% because other episodes lose money.

| Group | N | P&L $ | Share positive P&L | Share net P&L |
| --- | --- | --- | --- | --- |
| top1 | 1 | 8,659.6112 | 0.0265 | 0.0899 |
| top5 | 5 | 32,815.2099 | 0.1006 | 0.3405 |
| top10 | 10 | 53,451.4984 | 0.1638 | 0.5547 |
| top10pct | 91 | 178,439.9878 | 0.5468 | 1.8517 |

Largest ten contributors; full top-decile identities and excursions are in EQUITY_SUMMARY.json:

| Episode | Final P&L $ | MFE return | Final return |
| --- | --- | --- | --- |
| equity_residual_momentum_v3:AAPL:7 | 8,659.6112 | 0.6498 | 0.6393 |
| equity_residual_momentum_v3:APH:3 | 6,340.6361 | 0.3849 | 0.3752 |
| equity_residual_momentum_v3:GLW:4 | 6,114.2887 | 0.3322 | 0.3322 |
| equity_residual_momentum_v3:QLD:2 | 5,974.4207 | 0.4048 | 0.4048 |
| equity_residual_momentum_v3:ARKK:1 | 5,726.2532 | 0.7143 | 0.4248 |
| equity_residual_momentum_v3:META:2 | 5,670.3187 | 0.4604 | 0.4299 |
| equity_residual_momentum_v3:CAT:8 | 5,576.9025 | 0.4290 | 0.3176 |
| equity_residual_momentum_v3:RCL:4 | 3,401.9601 | 0.2069 | 0.2020 |
| equity_residual_momentum_v3:FCX:1 | 2,994.0567 | 0.2357 | 0.2129 |
| equity_residual_momentum_v3:UBER:2 | 2,993.0506 | 0.2925 | 0.2117 |

The top decile supplies 54.68% of positive P&L and 185.17% of net P&L. Long-run net economics depend materially on the right tail. Aggressive early profit taking could truncate those trends; this is a structural risk, not a tested claim about an exit rule. The +20% cohort has 17 profitable exits, median final return 23.58% and median retention 90.17%; +40% has five cases, median final 42.48%, median retention 93.36%. Some winners continued well beyond early diagnostic levels. Small tail samples prevent broad certainty. Crypto: N/A pending parity.
