# Management guidance source audit — 2026-09-22

Management forecasts can be recovered from primary SEC documents. This is evidence of a source path, not proof that a complete 2016–2026 signal corpus exists. The required analyst-expectation benchmark is a separate dependency: no PIT consensus was retrieved or matched in this audit. Public issuer forecasts and previous issuer forecasts cannot substitute for pre-announcement analyst consensus.

## Purposive primary specimens

`PRIMARY_GUIDANCE_SAMPLE.json` contains six documents from six issuers, ten EPS observations, and a separate nonnumeric withdrawal specimen. It is not an eligible event census. No counts describe population supply.

| Specimen | Primary evidence | Extraction issue |
|---|---|---|
| Boeing, October 2016 | [SEC exhibit](https://www.sec.gov/Archives/edgar/data/12927/000095012316020852/a201609sep308kpressrelease.htm) | Raised full-year GAAP $7.10–7.30 and core $6.80–7.00 ranges; issuer describes tax adjustment. Both bases must remain distinct. |
| B/E Aerospace, July 2016 | [SEC exhibit](https://www.sec.gov/Archives/edgar/data/861361/000094787116001362/ss4067_ex9901.htm) | Approximately $3.25 diluted EPS: approximate point, not exact point or invented closed interval. |
| Cummins, March 2020 | [8-K](https://www.sec.gov/Archives/edgar/data/26172/000002617220000011/cmimarch2020208-kpress.htm) | Item 8.01 withdrawal of full-year guidance; this document does not supply an EPS range. |
| PG&E, December 2024 signature | [8-K](https://www.sec.gov/Archives/edgar/data/1004980/000119312524268378/d861932d8k.htm) | Item 7.01 annual core-EPS reaffirmations in joint parent/utility filing; report and signature dates differ. |
| InterDigital, May 2024 | [SEC exhibit](https://www.sec.gov/Archives/edgar/data/1405495/000140549524000071/ex991q133124pressrelease.htm) | Simultaneous quarter and annual guidance, GAAP and non-GAAP; separate observations. |
| Amcor, February 2026 | [SEC exhibit](https://www.sec.gov/Archives/edgar/data/1748790/000174879026000004/exhibit991q22026.htm), [index](https://www.sec.gov/Archives/edgar/data/1748790/000174879026000004/0001748790-26-000004-index.html) | Item 2.02; fiscal June year-end and reverse split; reaffirmation can appear numerically five times larger. |

One exact SEC acceptance is verified. Other index URLs were inaccessible through the browser and remain null; neither release datelines nor signatures were promoted into acceptance fields. B/E Aerospace was available as primary search text, with full-page retrieval subsequently unavailable. No raw-byte historical source archive was persisted.

## Source architecture and coverage

[Form 8-K](https://www.sec.gov/files/form8-k.pdf) distinguishes 2.02 results, 7.01 Regulation FD, 8.01 other events and attached exhibits. The specimens establish all three paths across documents. Furnished information remains public; legal filed/furnished status does not determine informational availability. Do not restrict extraction to 8.01 or to exhibit 99.1: tables may be in the main report, releases or presentation exhibits. Read attached definitions/footnotes; images require verified extraction. A conference-call-only statement needs its own reliable primary timestamp and archived content.

[SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [filing access documentation](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) support historical filing enumeration and submission provenance. A future research build would capture raw filings/exhibits and headers with hashes, record errors, and reconcile guidance mentions across documents. Current company sites are discovery aids, not historical timestamp authorities. No exhaustive enumeration, extraction recall audit, canonical security join or missing-document denominator was completed here. Accessible specimens do not resolve corpus completeness or the consensus-license/PIT problem.

Conclusion: management-source path demonstrated; complete causally timed, basis-matched management-versus-consensus event dataset remains unestablished. No strategy is authorized by this source audit.
