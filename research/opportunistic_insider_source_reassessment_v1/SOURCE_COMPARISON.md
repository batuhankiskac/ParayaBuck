# Source comparison

| Source | Role | Coverage / limits | Authority | Decision |
|---|---|---|---|---|
| SEC quarterly Form 3/4/5 ZIP | Historical reconstruction | Official bulk history; 53 quarters cached, 2026-Q2 currently inaccessible here | Primary SEC dataset, raw filing still controls | Use |
| SEC raw EDGAR filing | Filing-level verification and ambiguity resolution | One accession at a time; fair-access limits | Authoritative filed document | Use for deterministic samples and exceptions |
| sec-api.io | Optional normalized acquisition | Forms 3/4/5 and amendments; pricing/query limits apply | Secondary vendor | Optional cross-check, never authority |
| Form4API | Recent normalized API | Public stats begin 2023-04-03; amendment rewriting is unsuitable for immutable PIT state | Secondary vendor | Reject as sole historical/PIT source |
| Knowable dataset | Recent validation corpus | Approximately 2024-01 through 2026-07, provider claims 99.7% coverage | Secondary community dataset | Optional recent completeness check only |
| Alpaca | Price/symbol/corporate-action support | No documented Form 4, owner CIK, accession, or insider-transaction endpoint | Broker/market-data provider | Not a Form 4 source |

References: [sec-api insider trading API](https://sec-api.io/docs/insider-ownership-trading-api),
[sec-api Form 4 files](https://sec-api.io/datasets/form-4-files),
[sec-api pricing](https://sec-api.io/pricing),
[Form4API docs](https://www.form4api.com/docs),
[Form4API statistics](https://api.form4api.com/v1/stats), and
[Knowable](https://huggingface.co/datasets/jwlutz/knowable).

No secondary provider can override a disagreement with the SEC filing. A
vendor may reduce parsing work, but it cannot repair the missing historical
first-observed timestamp or turn code P into “open-market only.”
