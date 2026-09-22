# Historical child-security taxonomy audit

Retrieved 2026-09-22. The repository's [equity-universe limitation](../../docs/research/equity_universe_limitations.md) and [public-data policy](../../docs/research/public_data_research_policy_v1.md) remain governing: historical `US_EQUITY` does not prove ordinary common-stock-only membership, and current ticker metadata cannot repair that retrospectively.

## Is an issuer-linked subset possible?

**Yes for identified cases, conditionally.** A child's contemporaneous Exchange Act registration and information statement can specify issuer/CIK, the exact registered common-share class, parent distribution, exchange and intended ticker. A dated completion/trading notice can then confirm the ordinary child share's regular-way identity. [Solventum's SEC Form 10 index](https://www.sec.gov/Archives/edgar/data/1964738/000162828024005591/0001628280-24-005591-index.htm), its [information statement](https://www.sec.gov/Archives/edgar/data/1964738/000162828024010988/exhibit991-8xk.htm), and [3M's trading release](https://www.sec.gov/Archives/edgar/data/66740/000006674024000023/pressrelease3-8x24.htm) establish the form of this chain for `SOLV`. [GRAIL's information statement](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm) and [corrective 8-K/A](https://www.sec.gov/Archives/edgar/data/1699031/000119312524155278/d833704d8ka.htm) do likewise for `GRAL`, while revealing a separate `GRALV` when-issued symbol. These are **partial** spin examples, not proof of the strict full-spin supply.

This method avoids needing a complete historical product taxonomy for the **entire** broad equity universe: eligible membership must be affirmative and issuer-linked. It does **not** exempt the candidate from a clean taxonomy for each proposed child instrument, nor from checking the canonical bar entity. SEC CIK is an issuer identifier, not an instrument identifier; a multi-class issuer, ADR/ADS, REIT, rights, units, warrants, preferred, tracking stock or WI line can share issuer context while failing the strict child-common definition. Form 10 cover/class and exchange/ticker evidence must agree on the **same share class** as the canonical price series.

## Required proof by field

| Field | Acceptable vintage evidence | Failure mode |
|---|---|---|
| Parent and child legal identity | Parent/child SEC filings, separation agreement and information statement accepted before decision | Name collision or post-event rename. |
| Child CIK | Child filing header/index, not accession prefix alone | Agent filed accession; wrong filer. |
| Equity security type and class | Form 10/8-K class description and issuer security terms | Broad `US_EQUITY` record might be an ETP, preferred, warrant, unit or WI line. |
| U.S. listed ordinary share | Then-current exchange/issuer listing and regular-way notice | Current broker asset class or current SEC ticker metadata backfills history. |
| Canonical price entity | Historical symbol interval and first RW bar, with ticker reuse and CA reconciliation | Same ticker assigned to a different issuer or price line. |

The [SEC APIs page](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) says submissions JSON includes **current** name and stock exchange/ticker metadata. It does not claim those fields are a point-in-time security master. Preserve original filings; do not use current EDGAR/broker ticker fields as the primary historical instrument assignment. Ambiguity is `UNKNOWN/INELIGIBLE`, including an inability to distinguish normal child common from WI, ADR/ADS, parent ex-distribution, or another class. This means the issuer-linked subset is **feasible in principle but not yet validated at scale**; no mapping-success denominator or missing-class count has been established.

## Source record

| Title; issuer/author; date; sample period; access | Exact claim |
|---|---|
| [Solventum Form 10 filing index](https://www.sec.gov/Archives/edgar/data/1964738/000162828024005591/0001628280-24-005591-index.htm); SEC/Solventum; 2024-02-20; 2024 event; full index | Child CIK and class-registration pathway. |
| [Solventum information statement](https://www.sec.gov/Archives/edgar/data/1964738/000162828024010988/exhibit991-8xk.htm); Solventum; 2024-03-13; 2024 event; full text | Common stock, NYSE SOLV approval subject to issuance, and WI versus RW distinction. |
| [3M trading release](https://www.sec.gov/Archives/edgar/data/66740/000006674024000023/pressrelease3-8x24.htm); 3M; 2024-03-08; 2024 event; full text | Dated ticker/trading regime. |
| [GRAIL information statement](https://www.sec.gov/Archives/edgar/data/1699031/000119312524132022/d556103dex991.htm); GRAIL; 2024-05-06; 2024 event; full text | Child legal form conversion and ordinary common shares. |
| [GRAIL 8-K/A](https://www.sec.gov/Archives/edgar/data/1699031/000119312524155278/d833704d8ka.htm); GRAIL; 2024-06-05; 2024 event; full text | WI ticker GRALV versus regular-way GRAL. |
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces); SEC; page last reviewed 2025-04-08; API scope; full text | Current exchange/ticker metadata and filing history; no historical instrument taxonomy claim. |

All retrieved 2026-09-22. Repository authority: `docs/research/equity_universe_limitations.md` and `docs/research/public_data_research_policy_v1.md`, current 2026-09-22. No external access limitation for cited texts; full historical event coverage remains unmeasured.
