# XBRL concept mapping

Compustat variables do not map one-to-one to XBRL tags. The following are
candidate concept families, not a global synonym substitution table.

| Paper component | Standard starting concepts | Fail-closed cautions |
|---|---|---|
| `REVT` | `Revenues`, `SalesRevenueNet`, `RevenueFromContractWithCustomerExcludingAssessedTax` | Product/segment facts are not consolidated total |
| `COGS` | `CostOfRevenue`, `CostOfGoodsAndServicesSold`, `CostOfGoodsSold` | D&A-included/excluded variants are not automatically equivalent |
| `XSGA` | `SellingGeneralAndAdministrativeExpense`; possible complete sum of `SellingAndMarketingExpense` and `GeneralAndAdministrativeExpense` | Requires presentation/calculation proof |
| `XRD` | `ResearchAndDevelopmentExpense` | Narrow software/IPR&D tags are not total R&D |
| `RECT` | `AccountsReceivableNetCurrent` | Notes/loans/broad receivable aggregates rejected |
| `INVT` | `InventoryNet` | Component sum requires complete calculation tree |
| `XPP` | `PrepaidExpenseCurrent` | `PrepaidExpenseAndOtherAssetsCurrent` is not equivalent |
| `DRC/DRLT` | legacy deferred-revenue current/noncurrent; Topic-606 contract-liability current/noncurrent | Prevent double counting; missing current/noncurrent side is unknown |
| `AP` | `AccountsPayableCurrent` | AP-plus-accrued aggregate rejected |
| `XACC` | `AccruedLiabilitiesCurrent` or taxonomy-vintage exact equivalent | `OtherLiabilitiesCurrent` rejected |
| `AT_(t-1)` | `Assets` | Comparative prior-year consolidated instant required |

Each taxonomy-year registry entry must validate definition, authoritative
reference, balance/period type, statement location, and filing calculation/
presentation relationships. The imported taxonomy URI is stored as the
version.

## Context and sign gates

- primary consolidated entity only;
- no segment/geography/product/subsidiary dimension;
- annual duration for flows, exact fiscal-year-end instants for balances;
- USD unit, non-nil value, valid scale/decimals;
- duplicate facts must agree within SEC decimals consistency ranges;
- raw lexical and parsed values retained;
- debit/credit balance, calculation weight, and statement presentation jointly
  determine economic sign.

## SG&A/R&D trap

If R&D is a sibling outside as-filed SG&A, adding it back would double count.
Only a filing relationship proving R&D lies inside an SG&A subtotal supports
`SG&A - R&D`. A combined custom “SG&A and R&D” tag without deterministic
decomposition is rejected. Fuzzy caption matching is prohibited.

FASB resources: [taxonomy implementation guides](https://fasb.org/projects/fasb-taxonomies/resources/taxonomy-implementation-guides),
[revenue guide](https://xbrl.fasb.org/impdocs/Rev2_TIG/Revenue.htm), and
[consolidated/nonconsolidated guide](https://xbrl.fasb.org/impdocs/CNE_TIG/Consolidatedandnonconsolidatedentities.htm).
