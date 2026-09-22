# Final entitlement-prep report — 2026-09-22

## Entitlement-preparation decision

`READY_TO_CONTACT_NYSE`

No contact was made, nothing was purchased, no terms were accepted, no trial was started.
The inquiry draft is prepared for manual operator sending only after explicit authorization.

## Starting repository state

- Local `main` and `origin/main`: `91adb80a6157a10457e1b526e4a96b7fb8f293f3`, working tree clean.
- Prior audit branch `research/security-master-source-audit-v1` and commit
  `7366ea51ac819de7772ef67cc650869248ea7d07` verified present.
- Work performed on new branch `research/security-master-entitlement-prep-v1` from clean `main`.

## Prior source-audit result

`SECURITY_MASTER_SOURCE_LEAD_VALID_BUT_ENTITLEMENT_REQUIRED`, lead NYSE Group Security Master,
next step `REQUEST_COMMERCIAL_ENTITLEMENT_DETAILS`. Vintage `VINTAGE_SEMANTICS_UNCLEAR`, price
`COMMERCIAL_QUOTE_REQUIRED`. Receipt: `PRIOR_SOURCE_AUDIT_RECEIPT.json`. No part of the audit redone.

## Current NYSE product revalidation

Spec v4.1 (2026-06-24, live 2026-07-06) still current; only delta vs v4.0.x is the TXSE Primary
Market value. Start-of-Day evening delivery for the next session; daily equity full+delta at 12am
ET; history from 2015-12-16; SFTP/S3 delivery; `4.0` filename stem retained. Purchase via ICE
dashboard with no public price. No public trial docs; generic contract exhibits resolve nothing
about backtest/retention/identifiers. Full detail: `CURRENT_NYSE_PRODUCT_REVALIDATION.md`.

## Public sample findings

Two public equity fulls (20241202/20241203, SHA-256 in `PUBLIC_SAMPLE_DEEP_AUDIT.md`): 157-field
pipe layout, uniform rows, unique Stock Numbers (11,152/11,158 rows; +8/−2 day-over-day).
Non-test taxonomy: common 5,098, ETF 3,640, warrants 454, preferred 447, ADR common 403, funds 386,
REIT issuers 300, leveraged/inverse segmentation populated on Arca rows; unknown 104 explicit.
CUSIP on all non-test rows, ISIN except 124, change-day old-value rows present, Symbol Reuse Flag
unpopulated. No public equity delta sample exists, so delta semantics are unexercised.

## Questions that public documentation still cannot answer

Vintage: original-vs-restated files (V2), byte immutability and replacement/version history (V3),
correction-arrival reconstruction (V4), full+delta archive completeness (V5), historical taxonomy
population and layout-era fidelity (V7), Stock Number event enumeration and successor links (V8),
exact NMS/venue coverage (V9). Licensing: backfill model (L1), backtest/derived-use permission (L2),
raw/derived retention incl. post-termination (L3), CUSIP/ISIN/FTSE terms (L4), user/device limits (L5),
evaluation terms (L7), one-user quote (L8). See `HISTORICAL_VINTAGE_QUESTION_SET.md`,
`LICENSING_QUESTION_SET.md`.

## Original-vintage hard requirement

The subscriber knowledge state on date `t` must be recoverable — not a clean reconstruction at
`t+n`. `ORIGINAL_VINTAGE_CONFIRMED` requires exact original bytes (or originals plus separately
timestamped corrections); anything restated-only without arrival lineage is `RESTATED_HISTORY_ONLY`
and fails HARD blocker 5 absent a proven PIT-safe alternative.

## Licensing / research-use requirements

One-user internal quantitative research / point-in-time classification: backtesting permitted,
derived internal tables permitted, local raw+checksum+backup storage while subscribed, explicit
post-termination deletion/retention terms, CUSIP/ISIN/Stock Number storage clarified, no
redistribution purchased.

## Hard blockers

Six predeclared blockers in `HARD_BLOCKERS.md` (history start, stable identity, taxonomy,
research rights, restated-only history, storage path); scoring rules in
`VENDOR_RESPONSE_ACCEPTANCE_MATRIX.json`. No weakening after quoting.

## Sample/trial request

4 dates (2016, 2018/2019, 2022, recent) as full+next-delta pairs, byte-identical to the archive,
covering rename/ETF/ETN/preferred/ADR/REIT/warrant-unit/IPO/delist/split/correction events, with a
six-step vintage proof procedure. See `HISTORICAL_SAMPLE_REQUEST.md`.

## Nasdaq/Cboe contingency

No contact yet. Nasdaq Daily List + Fundamental Data triggers on NYSE coverage/taxonomy/identity
failure for Nasdaq listings; Cboe BZX reports trigger on the 937 `BATS` lifetimes. Current public
snapshots are cross-validation only, never historical evidence. See `NASDAQ_CBOE_CONTINGENCY.md`.

## Vendor inquiry draft

`NYSE_VENDOR_INQUIRY_DRAFT.md`: 13-question professional draft, use case stated only as historical
internal quantitative research / point-in-time security classification. Placeholders for identity;
no personal data persisted. DRAFT ONLY — do not send without explicit user approval.

## Operator action checklist

`OPERATOR_ACTION_CHECKLIST.md`: budget confirm → manual send → file written reply → score matrix →
post-reply decision (`ENTITLEMENT_ACCEPTED_PROCEED_TO_HISTORICAL_SAMPLE` /
`ENTITLEMENT_CONDITIONAL_REQUEST_CLARIFICATION` / `ENTITLEMENT_REJECTED`). Lists what not to buy.

## What NOT to buy/accept yet

No backfill, no subscription, no contractual trial, no click-through terms, no payment details, no
redistribution tier, no multi-user expansion, no identifier add-ons beyond the quote, no Nasdaq/Cboe
product. No production integration, no universe change, no V3/PAPER/LIVE change.

## Production state

Unchanged. No scanner, universe, strategy, risk, configuration, corporate-action, PAPER or LIVE
behavior changed; no backtest or migration run. Verification: focused JSON validation + full normal
test suite + `git diff --check` (results below); vendor bytes kept outside the repo.

## Files

`research/security_master_entitlement_prep_v1/`: `PRIOR_SOURCE_AUDIT_RECEIPT.json`,
`CURRENT_NYSE_PRODUCT_REVALIDATION.md`, `PUBLIC_SAMPLE_DEEP_AUDIT.md`,
`HISTORICAL_VINTAGE_QUESTION_SET.md`, `LICENSING_QUESTION_SET.md`,
`VENDOR_RESPONSE_ACCEPTANCE_MATRIX.json`, `HARD_BLOCKERS.md`, `HISTORICAL_SAMPLE_REQUEST.md`,
`NASDAQ_CBOE_CONTINGENCY.md`, `NYSE_VENDOR_INQUIRY_DRAFT.md`, `OPERATOR_ACTION_CHECKLIST.md`,
`ENTITLEMENT_PREP_DECISION.json`, `FINAL_ENTITLEMENT_PREP_REPORT.md` (this file).

## Final branch SHA

Reported after commit and validation; a commit cannot embed its own SHA without changing it.
