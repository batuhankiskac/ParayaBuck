# Opportunistic classification specification

Status: reconstructed but **not frozen for economics**.

## Literature-faithful baseline state machine

Identity is reporting-owner CIK. Names never merge identities. For each owner,
use only purchase/sale trades whose filing availability preceded the
classification decision.

At the start of calendar year `Y`:

1. If the owner has never been classified, require at least one eligible
   purchase or sale in each of `Y-3`, `Y-2`, and `Y-1`; otherwise the state is
   `UNCLASSIFIED_INSUFFICIENT_HISTORY`.
2. On initial eligibility, a nonempty intersection of traded calendar months
   across all three years produces `ROUTINE`; otherwise it produces
   `OPPORTUNISTIC`.
3. `ROUTINE` is absorbing: every later trade remains routine.
4. An already `OPPORTUNISTIC` insider is rechecked prospectively each year and
   becomes permanently routine if a same-month pattern exists across the prior
   three years; otherwise the insider remains opportunistic.
5. The appendix's fully rolling variant, which may reclassify in both
   directions, is separate robustness and is not substituted silently.

Classification never looks at a trade or filing accepted after the decision.
Transaction date alone does not make a trade observable.

## Owner and event limitations

CMP includes officers, directors, and >10% beneficial owners, but SEC owner CIK
can identify natural persons, entities, trusts, and joint filers. The current
repo has no deterministic historical natural-person/entity taxonomy. Multiple
owners in a filing remain separate participation records; issuer transactions
are not multiplied by owner count.

Because the campaign is rejected on modern liquid long-only and one-slot
evidence, it does not adapt the owner scope, choose fixed versus rolling
production semantics, or authorize this state machine for a return test.
