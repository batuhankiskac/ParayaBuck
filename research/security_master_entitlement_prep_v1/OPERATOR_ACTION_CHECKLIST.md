# Operator action checklist — security-master entitlement prep — 2026-09-22

Decision of this task: `READY_TO_CONTACT_NYSE` (contact NOT yet made).

## Before any contact

- [ ] Read `FINAL_ENTITLEMENT_PREP_REPORT.md` (decision + blockers).
- [ ] Confirm the research budget owner accepts a commercial quote path (`COMMERCIAL_QUOTE_REQUIRED`).
- [ ] Fill placeholders in `NYSE_VENDOR_INQUIRY_DRAFT.md` at send time only. Do not commit personal data.

## Contact (manual, only with explicit authorization)

- [ ] Send the inquiry via the operator's chosen channel (ICE dashboard / sales email).
- [ ] Do NOT press Purchase Now, create a paid subscription, accept click-through terms, start a
      contractual trial, or provide payment details.
- [ ] File the vendor's written reply (and any quote) as the next task's input — not in this branch.

## After vendor reply (next task, not this one)

- [ ] Score every answer against `VENDOR_RESPONSE_ACCEPTANCE_MATRIX.json`.
- [ ] Apply `HARD_BLOCKERS.md`: any HARD FAIL → `ENTITLEMENT_REJECTED` → contingency.
- [ ] All HARD PASS (+ retention CONDITIONAL at most) + in-scope quote →
      `ENTITLEMENT_ACCEPTED_PROCEED_TO_HISTORICAL_SAMPLE`.
- [ ] Anything CONDITIONAL without FAIL → `ENTITLEMENT_CONDITIONAL_REQUEST_CLARIFICATION`.

## What NOT to buy / accept yet

- No historical backfill, no daily subscription, no trial with contractual acceptance.
- No redistribution tier, no multi-user/seat expansion, no CUSIP/ISIN/FTSE add-ons beyond the quote.
- No Nasdaq/Cboe product — contingent only (`NASDAQ_CBOE_CONTINGENCY.md`).

## Production confirmation

- [ ] `git status --porcelain` clean except the new research directory; no production code/config changed.
