# after_tax_tr_v1 — pre-registration (2026-10-12)

Question: what is E2 (`equity_leveraged_trend_v1` 1.1.0, `majority_sma_tsm_don`) worth
**after Turkish personal income tax** for a Turkish-resident individual (non-US person, W-8BEN)
holding it at a US broker, against simply holding SPY/VOO or QQQ?

This is a decision-support model, not tax advice. Committed before any after-tax number was
computed; only the pre-tax harness reproduction (`after_tax.py --check`) has been run.

## 1. Tax rules modelled (2026 law, applied to every year)

| # | Rule | Model | Source | Confidence |
|---|---|---|---|---|
| R1 | Gain on selling foreign-listed securities (US ETFs included) is *değer artışı kazancı* (GVK mük. 80), declared on the annual return (March of Y+1), no Turkish withholding (geçici 67 covers only Turkish intermediaries). | All realised gains go to the annual base. | GİB *Diğer Kazanç ve İratlar Rehberi* 2025; Yapı Kredi *Yabancı Hisse Senedi Gelirlerinde 2026 Yılı Vergi Durumu* | high |
| R2 | No 2-year holding exemption (Turkish fully-liable companies' shares only); the annual *değer artışı* exemption (150,000 TL in 2026) excludes securities; no declaration minimum for these gains. | None applied. | same; GVK mük. 80 | high |
| R3 | Progressive tariff GVK 103, non-wage income, 2026: 15% to 190,000; 20% to 400,000; 27% to 1,000,000; 35% to 5,300,000; 40% above (TL). | 2026 thresholds deflated to year Y by Yi-ÜFE Oct(Y−1)/Oct(2025), i.e. the revaluation the law applies each year, run backwards. No other income. | GVK Genel Tebliği Seri No 332, RG 31.12.2025 / 33124 (5. mük.); KPMG Türkiye bulletin | high (2026); the deflation is a modelling choice |
| R4 | TRY gain = sale proceeds × CBRT USD *döviz alış* rate on the sale date − cost × rate on the purchase date; TRY depreciation is taxable even with a USD loss. | CBRT indicative ForexBuying of the trade date (latest earlier bulletin on Turkish holidays). Commissions added to cost / deducted from proceeds. | Yapı Kredi note; adviser practice; GİB guide (non-resident passage) | medium-high |
| R5 | Cost indexed by the Yi-ÜFE rise when it is ≥ 10%, sale month excluded (GVK mük. 81 last para.). | Ratio Yi-ÜFE(month before sale) / Yi-ÜFE(month before purchase). Indexation can cut a gain to zero but never creates or enlarges a loss. Sensitivities: no indexation; purchase-month base. | GVK mük. 81; Yapı Kredi applies it to foreign shares; muhasebetr (K. C. Güneş, 2026) on the month convention | medium (application to foreign ETFs not confirmed by an özelge; month convention disputed) |
| R6 | Gains and losses on securities net within the calendar year; no carry-forward of a net loss; no offset against other income. | Annual netting; net loss lost. | GİB guide example; Yapı Kredi (based on a Ministry statement); GVK 88 | medium |
| R7 | Lot matching: FIFO (statutory only under geçici 67; adviser practice for foreign brokers). | FIFO. | GVK Tebliğ 257 by analogy | low-medium |
| R8 | Dividends from US ETFs (RICs): US withholding 20% under treaty Art. 10 with W-8BEN. Turkish tax: full amount (no 50% exemption), declared only if the year's non-withheld MSİ+GMSİ exceeds 22,000 TL (2026); credit for US tax up to the Turkish tax on that income (GVK 123). | 20% withheld at the ex-date; threshold deflated like R3; if exceeded, gross TRY dividend joins the base and the credit is min(US tax, Turkish tax × dividend share of the base). | US–Turkey treaty Art. 10 (irs.gov); GVK 75, 86, 123; KPMG | high (US), medium-high (TR) |
| R9 | Tax is paid in two equal instalments (March and July of Y+1). | Paid from the account at the first US session on/after 31 Mar and 31 Jul of Y+1, at that day's rate; FIFO lots sold at the open if cash is short (those sales are taxable in Y+1). | GVK 117/118 | high |
| R10 | US tax on capital gains: none (treaty Art. 13). Idle broker cash: not modelled (harness: idle cash earns zero, as in e2_integration_v1). QLD distributions: not modelled (the synthetic QLD* embeds QQQ's dividends in price; QLD's real distributions are small). | — | treaty Art. 13 | high / modelling choice |

Not modelled: US estate tax exposure (> $60k US-situs assets, no US–Turkey estate treaty), stamp/other
fees, the 2026 *varlık barışı* (Law 7582), any future change of law. Historical years use today's rules
(2026 tariff deflated), not the law in force at the time.

## 2. Arms

All on the archived `signal_ensemble_v1` harness (byte copy `se1_screen.py`), m = 2 (4 bps × 2 per side),
base financing, decide at close / fill next open, cap 0.98:

- **E2** — production E2 (price TSM vs zero hurdle, price 126-session channel, SMA200; ≥ 2 of 3), weight
  min(1, 0.35/vol20(QLD*)), 0.5 pp band. Pre-tax must reproduce 14.67% / 49.6% (2000-03-09 → 2026-09-29).
- **VT35** — C0 (SMA200 only), reference; must reproduce 13.39% / 49.6%.
- **SPY**, **QQQ** buy-and-hold, bought at the close before the first session, dividends net of US tax
  reinvested at the ex-date close.
- Report-only E2 band variants: 5 pp, 10 pp, and no resize (trend flips only; enter at the target weight,
  hold until exit). Trend flips always trade.

The dollar/lot simulator must reproduce the harness NAV to 1e-9 relative for E2 and VT35 with no tax
(`--check`), else the run stops.

## 3. Windows, capital, regimes

- Windows: 2000-03-09 → 2026-09-29 (primary), 2010-01-04 → 2026-09-29, 2017-01-03 → 2026-07-30.
  Each starts in cash with the stated capital.
- Capital: $10,000 and $100,000 at the window start.
- Regimes: pre-tax; progressive (R1–R9, primary); flat 15/27/35/40% on the positive annual base
  (40% = top-marginal sensitivity); progressive without indexation and with the purchase-month base
  ($100k only).

## 4. Metrics

Per arm × window × capital × regime: pre-tax and after-tax CAGR in USD and in TRY (wealth × CBRT rate),
terminal wealth, total tax (TRY and USD at payment rates), tax drag (pre-tax − after-tax CAGR, pp/yr),
max DD of the account NAV, and E2's after-tax edge over SPY and QQQ. Two views: **liquidated** (all lots
sold at the window's last close and taxed — primary, fair to buy-and-hold's deferral) and
**unliquidated** (realised-but-unpaid tax deducted, unrealised gains untaxed).

Report-only: E2's realised TRY gain split by sale reason (trend exit, vol resize, tax-funding sale,
liquidation), indexation relief, and the band variants' pre- and after-tax CAGR.

## 5. What counts as a finding

Primary comparison: E2 vs SPY, liquidated, progressive regime, USD CAGR, 2000-03 → 2026-09.

- **E2 keeps a meaningful after-tax edge** if its liquidated after-tax USD CAGR beats SPY by ≥ 1.0 pp/yr in
  the 2000 and 2017 windows at both capital levels, and also under flat 40%.
- **Edge erased** if it is ≤ 0 in either primary window at either capital level.
- Anything between is **reduced but positive**; reported as such.
- Same rule reported (not gated) against QQQ.
- **Band follow-up is promising** if a wider band (5 or 10 pp) raises E2's after-tax liquidated USD CAGR
  by ≥ 0.3 pp/yr under progressive at both capitals in both the 2000 and 2017 windows while costing
  ≤ 0.3 pp pre-tax in each. This would justify a separate pre-registered campaign; nothing here changes
  production.

## 6. Data

- `data/historical/stress_robustness_v1_{daily,dtb3}.csv` (on main; harness inputs).
- `data/historical/after_tax_tr_v1_usdtry_cbrt.csv`: CBRT daily indicative USD buying/selling rates,
  2000-01-03 → 2026-10-09, from `tcmb.gov.tr/kurlar/YYYYMM/DDMMYYYY.xml`; pre-2005 TRL ÷ 1,000,000.
- `data/historical/after_tax_tr_v1_yiufe.csv`: Yi-ÜFE (2003 = 100) chained from the monthly % changes on the
  CBRT *Üretici Fiyatları* page (2003-01 → 2026-09); 1999-01 → 2002-11 ratio-linked from FRED
  `TURPPDMMINMEI` (OECD, domestic manufacturing PPI) as a proxy.
- `data/historical/after_tax_tr_v1_manifest.json`: sources, retrieval time, sha256.
- `download_data.py` made them (network, once); `after_tax.py` is offline and deterministic.
