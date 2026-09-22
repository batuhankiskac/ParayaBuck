# Forecast surprise: reconstruction boundary

Research specification only, 2026-09-22. No surprise values or ranks were calculated.

## What the final article establishes

[Final paper, p.962, Eq.1 and footnote 4](https://link.springer.com/content/pdf/10.1007/s11142-012-9217-4.pdf): `S=(point_or_range_midpoint−AF)/P`; AF = nonsplit I/B/E/S Summary median; P = day−2 price; CRSP split-factor reconciliation; previous-year quintiles. Table notes pp.972,976 instead say mean. No latest-individual/180-day freshness recipe appears.

Sample coding, pp.961–962: CIG point `A/F/Z`, closed-range `B/G/H`; CRSP share `10/11`, exchange `1/2/3`; annual/quarterly common EPS. Exclusions include EPS-discontinuity events, date-after-entry errors, missing CUSIP/link/required variables. Collection starts 1995; prior-year ranking removes that year.

## Required engineering specification, not an attributed paper recipe

The formula does not by itself prove a causal implementable dataset. Before any use, a separate approved data contract must establish:

1. **Comparable target.** Join issuer, security, fiscal-period end, annual/quarterly horizon, currency, per-share denominator and accounting basis. An annual forecast must not meet a quarterly consensus. A diluted adjusted EPS range must not silently meet GAAP/basic EPS. Unresolved comparability fails closed.
2. **Availability.** Preserve management-release availability and consensus snapshot publication/observation times. Require consensus information to precede the public forecast. A vendor's current historical display is not evidence of what investors saw. Same-day ambiguous snapshots cannot be assumed pre-release.
3. **Forecast representation.** Preserve raw point or both range endpoints and the original wording. Retain currency and sign. Do not turn qualitative language, open-ended ranges, reaffirmations, withdrawals or changes of basis into numerical surprises without separate authorization.
4. **Corporate actions.** Reconcile per-share quantities using independently timestamped split evidence. Test with synthetic fixtures; never silently substitute current split-adjusted series or today's shares. Retain raw and reconciled values plus transformation provenance.
5. **Consensus lineage.** A summary-snapshot median and a median rebuilt from selected analyst records are different measurements until equivalence is demonstrated. A freshness cutoff, latest-per-analyst rule and fiscal-period resolver need explicit independent authority. The proposed 180-day recipe is not verified as the final article's procedure.
6. **Historical ranking.** Preserve the eligible reference-distribution vintage and its exclusions before ranking. Missing prior-year observations cannot be replaced by future/full-sample breakpoints. Quantile construction also needs a ties/no-news policy and a minimum-history policy before implementation; none is silently selected here.
7. **Execution clock.** Maintain event date and public instant separately. A daily next-open strategy must explicitly resolve session calendars, publication latency, halts, ties at the open and ingestion readiness. The article's conflicting day descriptions cannot be converted into a tested executable timestamp by assertion.

## Non-equivalent shortcuts

Management's increase over its own earlier guidance measures revision, not surprise relative to market expectations. Using that substitute would require a separate literature-supported hypothesis. Future analyst agreement or eventual realized earnings cannot improve announcement-time selection. Prior accuracy is causal only when the earlier forecast and its already-public outcome were available before the new event.

These are acceptance requirements for any future pipeline, not a claim that the repository currently meets them. The source ambiguity and absent operational details preclude an exact-replication claim; they do not establish negative economic performance.
