# Public / free-data research policy v1

Status: current operating policy (2026-09-22). Research uses public/free/easy-access sources only
until the exception rule below is explicitly satisfied.

## Objective

Direct scarce research effort at data actually obtainable without vendor sales conversations,
commercial quotes, waiting on email replies, negotiated entitlements, paid proprietary historical
feeds, or enterprise procurement. ParayaBuck already has enough accessible data to keep searching
intelligently. Be strict about what those data prove.

## Allowed source classes

### Tier 1 — existing canonical data

Existing validated ParayaBuck datasets: canonical equity OHLCV, entity lifetimes, corporate
actions, canonical crypto data, existing validated research datasets. Prefer these first.

### Tier 2 — official public sources

SEC EDGAR (including bulk datasets and `data.sec.gov`), SEC bulk files, FINRA public datasets,
Treasury / Federal Reserve / government datasets where relevant, exchange public
symbol/reference/corporate-action files, issuer filings and releases with reliable publication
timestamps. Source must be directly downloadable or queryable with documented provenance.

### Tier 3 — genuinely open reproducible datasets

Allowed only if all hold: historical provenance documented; download direct and reproducible;
terms permit research use; no bespoke sales conversation required.

### Tier 4 — simple low-friction API if needed

Consider only when: public signup or API key is straightforward; the free tier suffices for the
research question; historical PIT semantics are acceptable; no sales negotiation is required.
Document the dependence (provider, tier, limits, key handling) in the campaign.

## Disfavored by default

Defer candidates whose critical input requires Bloomberg, FactSet, Capital IQ, I/B/E/S,
OptionMetrics, institutional securities-lending feeds, Wall Street Horizon commercial archives,
commercial historical security masters, bespoke paid alternative-data contracts, or any dataset
requiring vendor negotiation or quotes before basic research can proceed.

Do NOT label such hypotheses false. Record disposition `DEFER_EXTERNAL_DATA_BURDEN`.

## Exception rule (no absolute ban)

A commercial dataset may be reconsidered only if ALL hold:

1. a very strong externally supported hypothesis exists FIRST;
2. the dataset is the only material blocker;
3. candidate architecture strongly fits ParayaBuck (concentrated, long-only, liquid, slot-efficient);
4. expected research value clearly justifies procurement effort;
5. the operator explicitly authorizes reopening commercial-data evaluation.

Until then: public/free/easy-access sources only.

## PIT rule — public does NOT mean causally safe

Every source still requires, as applicable: publication date; publication time when material;
revision semantics (corrections, restatements, replacements); historical availability;
effective date; identifier mapping; survivorship handling; point-in-time causality.

If these cannot be established, reject or defer the candidate. Never rescue a candidate by
retrospective current labels, ticker matching, current exchange classifications, or current SEC
metadata. Unknowns fail closed.

## Practical taxonomy rule

Historical `US_EQUITY` is not proven PIT common-stock-only (see
[`equity_universe_limitations.md`](equity_universe_limitations.md)). If a candidate's validity
depends critically on a historical security taxonomy that cannot be established from easily
accessible PIT sources: penalize it, restrict it to an issuer-linked subset if honestly possible,
or reject/defer it. Do NOT restart commercial security-master procurement merely to rescue the
candidate.
