# Exit-reason audit

Classification uses the actual final sell decision timestamp and captured post-overlay target plus preserved strategy metadata. An originally selected name removed by the overlay is risk_overlay; an explicit global risk_off_detail is eligibility_gate_failure; otherwise an omitted equity target is alpha_no_longer_selected. The latter cannot distinguish individual eligibility loss from rank displacement with selected-only metadata. No cause is guessed. A remaining positive target would be rebalance_sizing. Terminal policies distinguish lifetime events from horizon censoring.

| Category | N | Median MFE $ | Median MFE return | Median final $ | Median final return | Median giveback $ | Median retention | Median hold days | Median peak→exit days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alpha_no_longer_selected | 673 | 359.5561 | 0.0286 | 58.6297 | 0.0044 | 240.0466 | 0.4506 | 13.0000 | 6.0000 |
| lifecycle_terminal | 2 | 396.2010 | 0.0354 | -453.0115 | -0.0409 | 849.2124 | -1.1262 | 28.5000 | 16.0000 |
| risk_overlay | 230 | 280.4838 | 0.0222 | 81.3603 | 0.0070 | 141.2070 | 0.6641 | 14.0000 | 5.0208 |

Horizon-censored liquidation count: 3 (excluded above). The zero-count categories are not evidence that the code cannot produce them. Risk overlay count is not equivalent to drawdown forced sales: count caps can remove a selected name, while drawdown is shadowed in this canonical environment. Crypto proportions are N/A pending parity.
