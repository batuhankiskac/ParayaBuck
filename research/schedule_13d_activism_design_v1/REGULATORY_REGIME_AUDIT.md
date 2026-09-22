# Regulatory regime audit

## Exact transition

SEC Release Nos. 33-11253 and 34-98704 amended Regulation 13D-G. The final rule is at <https://www.sec.gov/files/rules/final/2023/33-11253.pdf> and the SEC rule page is <https://www.sec.gov/rules-regulations/2023/10/33-11180>.

| Regime | Initial Schedule 13D | Schedule 13D amendment | Filing cutoff |
|---|---|---|---|
| Before 2024-02-05 | Within 10 calendar days after crossing 5% (and specified 13G-loss cases) | Promptly after a material change | 5:30 p.m. ET |
| From 2024-02-05 | Within 5 business days | Within 2 business days after a material change | 10:00 p.m. ET |

For Regulation 13D-G, a business day excludes Saturday, Sunday, and federal holidays and spans 12:00 a.m.–11:59 p.m. ET. Acquisition or disposition of at least one percentage point is deemed material; smaller changes can still be material.

The EDGAR cutoff change is documented by the SEC release notes: <https://www.sec.gov/submit-filings/edgar-news-announcements/edgar-release-23-4>.

## Structured filing transition

- Voluntary Schedule 13D/G XML compliance: 2023-12-18.
- Mandatory Schedule 13D/G XML compliance: 2024-12-18.
- Historical filings before that date remain valid evidence and require deterministic legacy parsing.

## Research implications

The regimes must be explicit fields. A threshold crossing before 2024-02-05 whose old deadline falls after the effective date is a legal-transition ambiguity; it must not be silently reassigned. The shorter deadline plausibly reduces the activist's undisclosed accumulation interval and may alter leakage and announcement timing, but it is not evidence that a next-open outsider earns positive drift.

The ability to file through 10:00 p.m. increases after-hours acceptances. A daily strategy needs an exchange-calendar rule and conservative dissemination lag; it cannot treat a filing date as if it were known before that day's close.
