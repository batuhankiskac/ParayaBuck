# Crossasset X02-X06 acquisition and causal gate

This is a research-only data gate. The target cutoff is `2026-07-31T23:59:59Z`; publication, retrieval, event, and decision times remain separate. Raw responses and normalized files are hash-pinned in `crossasset_x02_x06_source_matrix_2026_09_08.json`.

X02 requires dated venue and contract identity, settlement, collateral, margin, liquidation, expiry, roll, and fee fields. CME settlement and CFTC/exchange or issuer alternatives are probes only until those fields and point-in-time publication evidence are present. Current public probes do not authorize OHLCV, current snapshots, or other proxies as substitutes.

X03 uses SEC issuer submissions and N-PORT/N-CSR samples. `MCHI` is tied to issuer CIK `0001100663`, series `S000031717`, and class `C000098688` by the existing local raw N-PORT XML. A fresh official SEC route binds one sample each for `EWJ` (CIK `0000930667`, series `S000004249`, class `C000011953`, report `2026-02-28`, 185 investment tags) and `EWZ` (same CIK, series `S000004264`, class `C000011968`, report `2025-02-28`, 54 investment tags). The MCHI local XML parser finds 565 investment tags, but its exact filing accession and retrieval receipt are not independently bound. All 804 parsed tags remain metadata-only schema evidence with `admitted_holding_rows=0`; filing/report dates do not establish continuous point-in-time price/book or price/earnings vintages. No holding row or economic feature is admitted.

X04 has a research-only synchronized two-leg schema with borrow rate, financing rate, recall flag, and availability fields. Public short-interest or securities-lending pages cannot supply historical borrow/locate/recall observations. Production remains long-only and is unchanged.

X05 retains 1,152 Binance Vision open-interest metric rows with venue `binance`, contract type `USD-M_PERPETUAL`, and instruments `BTCUSDT`/`ETHUSDT`. Archive `Last-Modified` is a publication bound, while row `available_at` is blank; continuous point-in-time OI is therefore blocked.

X06 retains 518 Binance Vision liquidation snapshot rows for `BTCUSD_PERP` and `ETHUSD_PERP`. They contain 259 exact duplicate pairs and no row availability timestamp. Exact duplicates stay in audit storage and are excluded from admission; non-identical revisions require an explicit revision identifier and publication timestamp. Observed funding intervals are bounded to `28,799,997..28,800,003` ms with a 3 ms tolerance.

No X02-X06 result is eligible for economic returns, candidate promotion, V3, PAPER, or LIVE.
