# Alpaca capability audit

## Verdict

`ALPACA_NOT_A_FORM4_SOURCE`

The documented [Alpaca API reference](https://docs.alpaca.markets/reference),
[OpenAPI surface](https://docs.alpaca.markets/us/openapi), and
[market-data overview](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api)
contain market data, assets, news, trading, and account functions, but no Form
4/4-A feed, reporting-owner CIK, accession, ownership relation, transaction
code, footnote, or raw ownership XML endpoint.

Alpaca remains useful after a defensible SEC-to-security bridge exists:

- symbol/asset validation and price bars;
- [corporate actions](https://docs.alpaca.markets/us/reference/corporateactions-1);
- later economic execution modeling, if separately authorized.

Those capabilities do not solve SEC source provenance, filing availability,
owner history, amendment semantics, or security-class identity. No Alpaca
credentials were requested or used in this reassessment.
