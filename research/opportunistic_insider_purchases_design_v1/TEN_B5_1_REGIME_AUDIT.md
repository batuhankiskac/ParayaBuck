# Rule 10b5-1 regime audit

## Structural break

Forms 4 and 5 filed on or after 2023-04-01 must indicate whether a reported
transaction was intended to satisfy Rule 10b5-1(c). Raw XML uses the
filing-level `aff10b5One` element. The plan adoption date is required in the
Explanation of Responses, not a dedicated structured date. In July 2025 the
SEC refreshed 2023-2025 bulk files to add `AFF10B5ONE`.

## Deterministic longitudinal treatment

- Before 2023-04-01: `UNKNOWN_PRE_REGIME`; absence is never `false`.
- On/after 2023-04-01 with `aff10b5One=1`: filing is plan-related, but a mixed
  filing needs footnote/text attribution before the flag can be assigned to a
  particular row.
- `aff10b5One=0` is not proof that no other pre-planned arrangement existed.
- Free-text adoption dates are derived attributes with provenance, never equal
  to structured source truth.

Options A and B from the campaign cannot be executed honestly: excluding only
flagged post-2023 trades creates a structural break, while a complete
pre-existing-plan classification is not reconstructible across 2016-2026.
Ignoring the flag preserves longitudinal consistency but leaves economically
different plan transactions mixed and does not cure the other blockers.

No 10b5-1 exclusion is frozen.

Sources: [SEC final rule 33-11138](https://www.sec.gov/files/rules/final/2022/33-11138.pdf),
[SEC rule modernization summary](https://www.sec.gov/newsroom/modernizing-rule-10b5-1-insider-trading-plans),
[bulk dataset page](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets).
