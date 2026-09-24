# Official source map

| Source | Relevant record and fields | Public-time strength | Limitation |
|---|---|---|---|
| [FPDS / FAR 4.604](https://www.acquisition.gov/far/4.604) | Contract Action Report: action/date signed, PIID, modification, recipient, UEI/CAGE, obligation and award values | Submission is later than action; not first-public proof | Corrections, reporting lag, DoD withholding |
| [USAspending data guide](https://www.usaspending.gov/data/about-the-data-download.pdf) | FPDS-derived contract transactions and award aggregates | Published after FPDS ingestion | Current record does not preserve original public vintage |
| [SAM Opportunities API](https://open.gsa.gov/api/get-opportunities-public-api/) | Award notice postedDate, award date/number/amount/name/UEI | Notice posting date exists | API key, latest-version API, historical version proof needed |
| [SAM Contract Awards API](https://open.gsa.gov/api/contract-awards/) | PIID/mod, UEI/CAGE, ultimate parent, created/approved/modified and publicDeltaDate | Current public reveal available | New API and field; no retrospective 2016 first-public clock |
| [Defense contract archive](https://www.defense.gov/News/Contracts/) | Dated announcement, recipient, PIID, mod, headline and obligated amount | Official dated publication; planned 5 p.m. ET release | Individual exact posting time not proved; threshold and mixed event types |
| Agency releases | NASA, DOE, HHS, VA, DHS, GSA portals may expose dated releases | Must be established agency by agency | No unified complete, versioned federal archive established |
| Issuer release / SEC EDGAR | Issuer publication and filings can establish early information and ownership | Use actual archived posting/acceptance time | Press releases may precede award record and differ in value |

An agency announcement can be an earlier public source than FPDS. A same-contract match must be proved before using later FPDS metadata to enrich it. No universal federal announcement corpus was demonstrated.
