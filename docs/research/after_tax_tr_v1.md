# After-tax value for a Turkish resident v1 (2026-10-12)

**After Turkish income tax, E2 still beats holding SPY/VOO, but its edge
shrinks to roughly 40–75% of the pre-tax edge.** This is a model built to
support a decision. It is not tax advice. **Consult a Turkish mali müşavir
(SMMM/YMM) before relying on it.**

- **2000-03 → 2026-09, all lots sold and taxed at the end.**
  - At $100k on the progressive tariff, E2's after-tax CAGR is 9.53%, against
    SPY 7.15% and QQQ 6.74%. The edge is +2.4 pp/yr over SPY, against +6.1 pp
    pre-tax.
  - At $10k: 11.05%, against SPY 7.41% (+3.6 pp).
  - At a flat 40% (top-rate sensitivity), the edge is still +1.2 to +1.5 pp.
- **2017-01 → 2026-07.**
  - At $100k: E2 22.14%, SPY 12.38%, QQQ 16.75%. Edges: +9.8 pp over SPY,
    +5.4 pp over QQQ.
- **Tax drag.**
  - E2 loses 3.6–5.1 pp/yr to tax on 2000–2026 and 6.0–9.1 pp/yr on
    2017–2026.
  - SPY loses 1.2–1.4 and 1.8–2.7 pp/yr.
  - E2 realises gains every year. Buy-and-hold pays only dividend tax until
    it sells.
- **QQQ since 2010 is the exception.** At $100k, QQQ buy-and-hold beats E2
  after tax by 1.3 pp/yr (15.76% against 14.50%). The pre-tax edge was
  +2.8 pp. At flat rates of 27% or more, QQQ also wins at $10k.
- **About 90% of E2's realised taxable gain comes from the daily vol-resize
  trims, not from trend exits.**
  - Under FIFO, each trim sells the oldest lots, which carry the largest gains.
  - Trend exits net to a small loss, because they usually come after a
    drawdown.
- **Band-width finding: not promising.** A 5 pp or 10 pp resize band changes
  after-tax CAGR by only −0.19 to +0.21 pp/yr. It misses the pre-registered
  +0.3 pp follow-up bar. Removing resizes altogether is much worse, both
  pre-tax and after tax.

## Tax rules modelled (2026 law, plain language)

| Rule | What the model does | Sources | Confidence |
|---|---|---|---|
| Gains on US ETFs held at a foreign broker are *değer artışı kazancı* (GVK mük. 80) | Declared on the annual return in March of the following year. There is no Turkish withholding: geçici 67 covers only Turkish intermediaries. | GİB *Diğer Kazanç ve İratlar Rehberi* (2025); Yapı Kredi, *Yabancı Hisse Senedi Gelirlerinde 2026 Yılı Vergi Durumu* | high |
| No exemptions apply | The 2-year exemption covers only Turkish companies' shares. The 150,000 TL *değer artışı* exemption (2026) excludes securities. There is no declaration minimum. | same | high |
| Progressive tariff, non-wage income, 2026 | 15% up to 190k TL, 20% up to 400k, 27% up to 1.0m, 35% up to 5.3m, 40% above. | GVK Genel Tebliği Seri No 332, Resmî Gazete 31.12.2025, issue 33124 (5. mükerrer); KPMG Türkiye 2026 bulletin | high |
| Gain is computed in TRY | Proceeds at the CBRT USD buying rate on the sale date, minus cost at the buying rate on the purchase date. TRY depreciation is taxed even when the trade lost money in USD. | Yapı Kredi note; common adviser practice | medium-high |
| Yİ-ÜFE indexation (GVK mük. 81) | The TRY cost is indexed when Yİ-ÜFE has risen ≥ 10% between the month before purchase and the month before sale. This is large: indexation removes 10–16m TL of gains at $100k. | GVK mük. 81; Yapı Kredi applies it to foreign shares; muhasebetr (K. C. Güneş, 2026) on which months to use | medium: no özelge on foreign ETFs, and the month convention is disputed |
| Losses | Gains and losses net within a calendar year. A net loss cannot be carried forward or set against other income. | GİB guide example; Yapı Kredi, which relies on a Finance Ministry statement; GVK 88 | medium |
| Lot matching | FIFO. | GVK Tebliğ 257 (geçici 67), applied by analogy; adviser practice | low-medium |
| Dividends (SPY/QQQ) | US withholding is 20%: treaty Art. 10 applies 20% to RIC dividends, with a W-8BEN on file. In Turkey the gross dividend is taxed in full (the 50% exemption is for Turkish companies only), but only if the year's non-withheld investment income exceeds 22,000 TL (2026). The US tax is then credited up to the Turkish tax on that dividend (GVK 123). | US–Turkey treaty (irs.gov); GVK 75, 86, 123; KPMG | high (US), medium-high (TR) |
| Payment | Tax is paid in two equal instalments, March and July of the following year. | GVK 117/118 | high |
| US tax on capital gains | None (treaty Art. 13). | treaty | high |

**Other points, not modelled:**

- **Idle cash interest and T-bill ETF payouts.** These would be taxed in
  Turkey as *menkul sermaye iradı* at the progressive rates, under the same
  22,000 TL threshold and tax credit.
  - On the US side, broker cash or bank-sweep interest is normally 0% (IRC
    871(h)/(i)).
  - T-bill ETF payouts are RIC dividends. They are exempt only when the
    broker applies the IRC 871(k) interest-related-dividend exemption.
    Otherwise 20% is withheld.
  - Alpaca's treatment is unconfirmed.
- **US estate tax.** It applies above $60k of US-situs assets, and there is
  no US–Turkey estate treaty.
- **Asset amnesty.** The 2026 *varlık barışı* (Law 7582) is relevant only if
  assets are brought to Turkey.
- **No pending change found.** No enacted or proposed 2025–26 change was
  found to the taxation of direct foreign ETF trades. The 17.5% withholding
  in Presidential Decree 11107 applies to Turkish funds only.

## Method

- **Arms.**
  - Archived `signal_ensemble_v1` harness: synthetic 2x QQQ, m = 2, base
    financing, decide at close and fill next open, cap 0.98, 0.5 pp band.
  - A dollar-and-lot simulator reproduces the harness NAV to 1.5e-14
    relative: production E2 14.67% / 49.6%, VT35 13.39% / 49.6%.
  - SPY and QQQ are bought and held, with dividends net of US tax reinvested.
- **Account.**
  - Every arm starts in cash with $10k or $100k at the window start.
  - Tax is paid from the account at the first US session on or after
    31 March and 31 July, selling FIFO lots when cash is short. Those sales
    are taxable too.
- **Tariff over time.** The 2026 brackets are deflated to each year by
  Yİ-ÜFE (Oct Y−1 / Oct 2025), which mirrors the annual revaluation run
  backwards. Today's rules are applied to every past year, and the investor
  has no other income.
- **Liquidated view (primary).** Every lot is sold at the last close and
  taxed. This charges buy-and-hold its deferred tax. The unliquidated view,
  which deducts only realised-but-unpaid tax, is also reported.
- **Data.**
  - CBRT daily indicative USD buying rates, 2000-01-03 → 2026-10-09, 6,676
    bulletins. The rate of the trade date is used, or the latest earlier
    bulletin on Turkish holidays; 95.8% of sessions have a same-day rate.
  - Yİ-ÜFE: monthly changes on the CBRT *Üretici Fiyatları* page from
    2003-01. Before that, a FRED/OECD Turkish domestic manufacturing PPI
    proxy.
  - USD/TRY moved from 0.579 to 48.91 over 2000–2026. Yİ-ÜFE rose about
    180-fold.

## Results (progressive tariff, liquidated at the end)

**2000-03-09 → 2026-09-29**

| Arm | Capital | Pre-tax CAGR USD / TRY | After-tax CAGR USD / TRY | Tax drag pp/yr | After-tax terminal USD | Tax paid (TRY m / USD k) |
|---|---|---|---|---:|---:|---|
| **E2** | $10k | 14.67% / 35.52% | **11.05% / 31.24%** | 3.62 | 161,813 | 2.06 / 64 |
| SPY | $10k | 8.59% / 28.34% | 7.41% / 26.94% | 1.18 | 66,746 | 0.71 / 15 |
| QQQ | $10k | 8.08% / 27.73% | 7.03% / 26.49% | 1.05 | 60,789 | 0.75 / 15 |
| **E2** | $100k | 14.67% / 35.52% | **9.53% / 29.45%** | 5.14 | 1,123,105 | 19.33 / 647 |
| VT35 | $100k | 13.39% / 34.00% | 8.53% / 28.26% | 4.85 | 880,012 | 14.47 / 489 |
| SPY | $100k | 8.59% / 28.34% | 7.15% / 26.63% | 1.44 | 626,754 | 9.11 / 186 |
| QQQ | $100k | 8.08% / 27.73% | 6.74% / 26.15% | 1.34 | 565,968 | 9.53 / 195 |

**2017-01-03 → 2026-07-30**

| Arm | Capital | Pre-tax CAGR USD / TRY | After-tax CAGR USD / TRY | Tax drag pp/yr | After-tax terminal USD | Tax paid (TRY m / USD k) |
|---|---|---|---|---:|---:|---|
| **E2** | $10k | 31.24% / 72.14% | **25.27% / 64.31%** | 5.97 | 86,572 | 0.79 / 24 |
| SPY | $10k | 15.12% / 50.99% | 13.30% / 48.60% | 1.82 | 33,062 | 0.20 / 4 |
| QQQ | $10k | 20.87% / 58.53% | 17.78% / 54.48% | 3.09 | 47,945 | 0.60 / 13 |
| **E2** | $100k | 31.24% / 72.14% | **22.14% / 60.21%** | 9.10 | 679,354 | 9.79 / 310 |
| VT35 | $100k | 29.52% / 69.89% | 20.92% / 58.61% | 8.60 | 617,137 | 8.49 / 268 |
| SPY | $100k | 15.12% / 50.99% | 12.38% / 47.40% | 2.74 | 305,783 | 3.22 / 68 |
| QQQ | $100k | 20.87% / 58.53% | 16.75% / 53.13% | 4.12 | 440,724 | 7.85 / 166 |

E2's 2017 pre-tax figure is 31.24%, against 31.42% in `e2_integration_v1`,
because here every arm starts in cash on 2017-01-03.

**E2 after-tax edge, USD CAGR pp/yr (liquidated; unliquidated vs SPY in brackets)**

| Window | Capital | Pre-tax vs SPY / QQQ | Progressive vs SPY / QQQ | Flat 27% | Flat 40% vs SPY / QQQ | No indexation vs SPY / QQQ |
|---|---|---|---|---|---|---|
| 2000-03 → 2026-09 | $10k | +6.08 / +6.59 | **+3.64 / +4.02** (+3.00) | +2.99 / +3.32 | +1.24 / +1.54 | — |
| 2000-03 → 2026-09 | $100k | +6.08 / +6.59 | **+2.38 / +2.79** (+1.51) | +3.07 / +3.35 | +1.46 / +1.63 | +1.75 / +2.05 |
| 2010-01 → 2026-09 | $10k | +7.71 / +2.81 | +4.92 / +0.65 (+3.31) | +4.06 / −0.69 | +2.12 / −2.48 | — |
| 2010-01 → 2026-09 | $100k | +7.71 / +2.81 | +3.13 / **−1.27** (+1.03) | +4.10 / −0.66 | +2.25 / −2.37 | +1.50 / −3.32 |
| 2017-01 → 2026-07 | $10k | +16.12 / +10.37 | **+11.98 / +7.49** (+10.61) | +11.34 / +6.30 | +8.77 / +4.27 | — |
| 2017-01 → 2026-07 | $100k | +16.12 / +10.37 | **+9.77 / +5.39** (+7.49) | +11.42 / +6.34 | +9.01 / +4.40 | +8.47 / +3.12 |

**Pre-registered verdict: "E2 keeps a meaningful after-tax edge" over SPY.**
The edge is ≥ 1.0 pp in the 2000 and 2017 windows at both capital levels,
and also at flat 40%.

**Caveats on the comparison:**

- **Without liquidation.** If buy-and-hold is never sold (the unliquidated
  view), E2's 2000–2026 edge over SPY falls to +0.3 to +0.6 pp at flat 40%.
  With no indexation it falls to +0.03 pp.
- **Against QQQ.** E2 does not beat QQQ in every window. It loses since 2010
  at $100k and at flat rates of 27% or more.

**Purchase-month indexation base.** Using the purchase month instead of the
month before it costs E2 0.2–0.6 pp and SPY 0.0–0.1 pp.

## Where E2's tax comes from (report-only)

Realised TRY gain by sale reason, after indexation, progressive, $100k:

| Window | Vol-resize trims | Trend exits | Tax-funding sales | Final liquidation | Resize share of net realised |
|---|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | 46.5m | −2.6m | 0.6m | 6.4m | 91% |
| 2017-01 → 2026-07 | 27.8m | −1.7m | 0.3m | 0.7m | 103% |

- E2 makes about 58 strategy trades a year in the 2000 window.
- Trend exits book net losses because they come after drawdowns. Those
  losses offset trim gains only within the same calendar year.
- Most of the tax is in the high-inflation years: 2024 (4.6m TL) and
  2025 (3.3m TL) at $100k. Indexation and small TRY gains keep 2000–2013
  near zero.

**Wider resize bands** (pre-tax → after-tax USD CAGR, progressive):

| Window | Capital | 0.5 pp (production) | 5 pp | 10 pp | No resize (trend flips only) |
|---|---|---|---|---|---|
| 2000-03 → 2026-09 | $10k | 14.67 → 11.05 | 14.59 → 11.15 | 14.12 → 10.90 | 11.62 → 9.08 |
| 2000-03 → 2026-09 | $100k | 14.67 → 9.53 | 14.59 → 9.74 | 14.12 → 9.61 | 11.62 → 7.89 |
| 2017-01 → 2026-07 | $10k | 31.24 → 25.27 | 30.89 → 25.16 | 30.49 → 25.08 | 23.20 → 19.43 |
| 2017-01 → 2026-07 | $100k | 31.24 → 22.14 | 30.89 → 22.24 | 30.49 → 22.26 | 23.20 → 17.18 |

- **Effect of wider bands.** They cut trades about 3–5x but move only some
  gains from trims into exits. After-tax CAGR changes by −0.19 to +0.21 pp.
- **Pre-registered bar.** The follow-up needed ≥ +0.3 pp in both windows at
  both capital levels, at ≤ 0.3 pp pre-tax cost. Neither band meets it, so
  **no band follow-up is recommended.**
- **No resize.** Without vol resizing the position stays at its entry weight
  through calm markets, which costs 3–8 pp pre-tax.
- **Lot method.** Selling the highest-cost lots first on trims (HIFO) would
  target the same tax directly. It is not modelled, because FIFO is the
  practice assumed here; ask an adviser whether specific-lot identification
  is accepted for foreign brokers.

## Caveats

- **The rules are uncertain in four places.**
  - Indexation for foreign ETFs: the no-indexation sensitivity removes about
    0.6–1.6 pp of E2's edge over SPY.
  - The indexation month convention.
  - Same-year loss netting, which rests on a Finance Ministry statement.
  - FIFO.
- **Today's rules are applied to every past year.** The real 2000s rules
  were different. The deflated brackets approximate, but do not equal, the
  historical tariffs. For example, 2026's 190k TL bracket is 1.20x the 2025
  bracket, while Yİ-ÜFE rose 1.27x.
- **Investor assumptions.** No other income. A wage earner's gains would
  stack on top of the salary and start in a higher bracket; the flat 27–40%
  rows bound this. Full reinvestment, no withdrawals, no idle-cash interest,
  and no QLD distributions.
- **Pre-tax returns are the harness's synthetic QLD\*.** The 2000–2026
  window includes the regime-dependent E2 gain discussed in
  `e2_integration_v1`. The bootstrap gates there failed, and tax does not
  change that.
- **Pre-2003 Yİ-ÜFE is a proxy.** CBRT rates before 2005 are old lira
  divided by 10⁶.
- **Consult a Turkish mali müşavir** before relying on any figure here. That
  covers filing, the 1042-S documentation for the foreign tax credit, and
  how a broker without a TRY statement is reported.

## Evidence

`research/after_tax_tr_v1/` and `data/historical/after_tax_tr_v1_*` were
removed from main on 2026-10-12 and are preserved in tag
`archive/research/after-tax-tr-v1-2026-10`.

- **Pre-registration and data.** `PREREGISTRATION.md`, `after_tax.py`,
  `download_data.py`, `se1_screen.py` (a byte copy of the archived
  `signal_ensemble_v1` screen) and the data with its manifest were committed
  in `c3102c8`. That was before any after-tax number was computed.
- **Amendment A1 (`57d0ac6`).** It anchors the Yİ-ÜFE chain at 2003-01, after
  the first run stopped on the missing month before producing any result.
- **Outputs.** `RESULTS.json` and `SUMMARY.txt`. The run is offline and
  deterministic and takes about 2 s; two runs gave byte-identical outputs.
