# Turkish tax rules used by the TL ledger

Decision record, 2026-10-27. The owner is a Turkish tax resident (tam mükellef)
who holds US-listed ETFs at Alpaca. There is no tax adviser, so each rule below
is the most defensible reading of the law and of GİB guidance. The TL ledger
(`src/parayabuck/tax/`) implements these rules; change them here first.
This is software documentation, not tax advice.

| # | Question | Rule used | Basis | Confidence |
|---|---|---|---|---|
| 1 | Does Yİ-ÜFE indexation apply to foreign ETF/share sale gains? | Yes. The cost is indexed when the index rise is at least 10%. | GVK mük. 81, last paragraph: it covers "elden çıkarılan mal ve haklar" in general, not only real estate. GİB's "Diğer Kazanç ve İratların Vergilendirilmesi Rehberi" applies it to share sales outside geçici 67. | Medium-high |
| 2 | Which months? | Yİ-ÜFE of the month before acquisition and of the month before sale. When the ratio minus 1 is at least 0.10, the TL cost is multiplied by the full ratio; otherwise the nominal TL cost is used. | GVK mük. 81 ("elden çıkarıldığı ay hariç") and the GİB guide's worked examples. | Medium |
| 3 | Loss offset | Gains and losses on these disposals net within the same calendar year. A net loss does not offset other income and is not carried forward. | GVK 88 (losses from non-commercial activities); GİB practice for mük. 80 items. | Medium |
| 4 | Exchange rate | Each leg uses the TCMB USD döviz alış kuru valid on the trade date: the rate TCMB announced at 15:30 on the previous business day. The FX movement is part of the gain, because the gain is computed in TL. | GİB guidance on foreign-currency acquisitions (VUK 280 valuation practice); bank and adviser notes. | Medium (basis), low (exact day) |
| 5 | US dividend withholding | Treaty cap is 20% for an individual (15% only for a company holding 10% or more). Alpaca applies it with a W-8BEN. The US tax is credited against the Turkish tax on the same income (GVK 123), but not above it. US bank or sweep deposit interest paid to a non-resident carries no US withholding. | US-Türkiye treaty Art. 10(2); IRS Table 1; IRC 871(i). | High (dividends), medium (interest) |
| 6 | Thresholds, brackets, filing, FIFO | No exemption or threshold applies to değer artışı from foreign securities. Dividends and interest (menkul sermaye iradı) not withheld in Türkiye are declared when they exceed the GVK 86 limit (22,000 TL for 2026). 2026 non-wage brackets: 15% to 190,000; 20% to 400,000; 27% to 1,000,000; 35% to 5,300,000; 40% above. Income is declared in March of the following year. Lots are matched FIFO, which is also how Alpaca matches. | GVK mük. 80, 86, 103; Tebliğ 332 (2026 tariff); Tebliğ 257 (FIFO practice). | Medium-high |

## Worked example

On 2025-03-10, buy 10 QLD at $80. The rate is 36.00, so the TL cost is 28,800.
On 2026-02-12, sell at $100. The rate is 43.00, so the TL proceeds are 43,000.
Yİ-ÜFE was 3,200 in February 2025 and 3,700 in January 2026. The ratio is 1.15625,
which is a rise of at least 10%, so the indexed cost is 28,800 × 1.15625 = 33,300 TL.
The taxable gain is 43,000 − 33,300 = 9,700 TL. Fees are added to the cost and
subtracted from the proceeds at the same rates.

## Open points to revisit

- The exact TCMB rate day (rule 4) and loss carry-forward (rule 3). A GİB
  özelge (ivd.gib.gov.tr) would settle both. Until then the ledger keeps the
  per-leg rates and index months, so a later rule change is a recomputation,
  not a data loss.
