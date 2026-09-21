# Design red team

| Challenge | Finding | Effect |
|---|---|---|
| CMP effect too old? | Main sample ends 2007. | Material adverse evidence. |
| Faster disclosure removes outsider profit? | Post-SOX reactions are faster; modern fast-copy evidence is not scalable after liquidity/capacity and before costs. | Reject gate. |
| Long leg small-stock driven? | CMP large-half result is positive, but later value-weight/large evidence weakens. | Reject gate. |
| Value weighting? | CMP buy leg is mostly positive, but opportunistic-minus-routine VW buy differences are insignificant. | Reject gate. |
| Code P = open market? | No; official meaning includes private and derivative purchases. | Source blocker. |
| Remove private deals deterministically? | Not from P alone; needs explicit filing text/footnotes. | Source blocker. |
| Amendments PIT-honest? | Possible only append-only from amendment availability; original linkage can remain ambiguous. | Implementation blocker. |
| Owner identity stable? | CIK is best key, but entity/trust/joint-filer scope remains unresolved. | Mapping blocker. |
| Causal opportunistic classification? | CMP state machine is reconstructible in principle, using only previously available filings. | Feasible in principle. |
| Three years available before 2017? | Official bulk begins 2006, so source coverage is sufficient in principle. | Passes coverage only. |
| 10b5-1 consistency? | Structured flag starts 2023-04-01 and is filing-level; pre-regime negatives are unknowable. | Regime blocker/adaptation risk. |
| Common equity proven? | No CIK/security-class crosswalk in the lifetime table. | `INSIDER_SECURITY_MAPPING_BLOCKED`. |
| SPAC/10% entity contamination? | Cannot be excluded comprehensively with current source mapping. | Mapping blocker. |
| Defensible one-slot ranking? | CMP supports issuer-month transaction count predictiveness, not top-one concentration. | Reject gate. |
| Ranking needs shares/market cap? | Count does not; intensity and value weighting do. | Count feasible, slot efficacy unproven. |
| Enough $5/$20M opportunities? | Not measured because acquisition/mapping gates failed; cannot infer. | Unknown. |
| Monthly aggregation dilutes? | It is the literature-faithful causal design; modern evidence still does not establish liquid scalability. | No rescue. |
| Event-level entry too different? | Yes; CMP is monthly and current repo lacks an acceptance-event scheduler. | Do not substitute. |
| Costs manageable? | Modern evidence finds the public-copy strategy uneconomic before transaction costs when dollar capacity is constrained. | Reject gate. |
| Legality/public basis? | Form 4 is public, but only after dissemination; historical insider transaction date is not public information time. | Causally legal concept, no economic endorsement. |

## Red-team conclusion

The candidate is distinct from V3 at the information-source level, but
mechanism distinctness does not establish portfolio diversification. The
combined evidence does not justify a modern, liquid, long-only, scarce-slot
experiment. Mapping and provenance would also block implementation, but they
are not the reason to override the earlier evidence rejection.
