# Causality audit

## Allowed decision-time information

- Exact raw filing package available after conservative public dissemination.
- Acceptance timestamp, form type, initial/amendment status, subject issuer, reporting-person roster, covered security class, ownership facts, and Item 3/4 text contained in that package.
- Effective-dated security identity, trading calendar, prior completed market bars, and scanner facts available before the decision.
- Regulatory regime in force at the filing/trigger date, with transition ambiguity retained.

## Prohibited future or revised information

- Pre-filing returns or activist accumulation gains.
- Filing-day prices used to assume a fill before dissemination.
- Later amendments, settlements, board changes, takeovers, campaign success/failure, exits, or media classification.
- Current ticker/name/class substituted for historical identity.
- Later activist reputation, later group membership, or a revised accession/index silently replacing the archived vintage.
- Any ranking, holding, or eligibility rule chosen from candidate returns.

## Timing rule that would be required

`raw EDGAR acceptance -> conservative dissemination lag -> first eligible regular-session market open -> fill using only information fixed before that open`

An intraday filing after the market open cannot receive that same opening price. An after-hours filing cannot be considered known at the prior close. The existing month-end helper is inapplicable; a research-only event scheduler would be necessary.

## Verdict

The causal clock can be specified, but the central papers do not evaluate it and the repository cannot currently reconstruct all required inputs. `CAUSAL_EVENT_DESIGN_SPECIFIABLE_BUT_NOT_EVIDENCE_AUTHORIZED`.
