# Equity Veri Seti v2 — equity_etf_1d_v2

Tarih: 2026-08-05

## Amaç

Faz −1 doğruluk düzeltmeleri sonrası yeni araştırma verisi: 2016 başlangıç
(≥60 gerçek OOS calendar ay), 2020 crash, 2022 bear market, farklı faiz/
volatilite rejimleri, dividend+split ayarlı (total-return) günlük veri.
Eski `equity_etf_1d_v1` audit için değiştirilmedi.

## Provider seçimi

Görevdeki öncelik sırası: 1) Alpaca SIP, 2) doğrulanmış harici adjusted
daily, 3) kullanıcı CSV/Parquet.

- **Stooq (harici adjusted daily):** denendi; CSV endpoint'i otomasyon
  erişimine kapalı (JavaScript proof-of-work + `Access denied`; otomasyon
  UA'larına 404). Bu nedenle kullanılamadı — doğruluğu varsayılmadı.
- **Alpaca SIP:** hesapta etkin. Ancak abonelik yakın dönem SIP verisine
  izin vermiyor ("subscription does not permit querying recent SIP data").
  SIP 2016–2025 tam veri sunuyor; 2026 bloğu engelli.
- **Alpaca IEX (adjustment=all):** 2026'yı tam sunuyor.

### Nihai politika: `sip+iex_hybrid` (aynı broker, aynı adjustment)

| Pencere | Feed | Adjustment |
|---|---|---|
| 2016-01-01 → 2025-12-31 | Alpaca SIP | all (dividend+split) |
| 2026-01-01 → 2026-08-05 | Alpaca IEX | all (dividend+split) |

Ekonomik varsayım (manifeste `provider_metadata.notes` içinde açıkça
kayıtlı): her iki feed aynı broker'a ve aynı dividend+split adjustment
politikasına aittir; tek total-return serisidir. Splice (Aralık 2025)
feed-overlap reconciliation'ı her sembol için geçti:
`reconcile_providers` (SIP vs IEX, Aralık 2025) — maks abs günlük getiri
farkı eşik (0,5%) altında; aksi halde veri seti reddedilirdi.

## Dataset kalitesi

| Ölçüt | Değer |
|---|---|
| dataset_id | equity_etf_1d_v2 |
| format | csv_gz |
| kapsam | 2016-01-04 → 2026-08-05 (~10,5 yıl) |
| adjustment | all |
| source_feed | sip+iex_hybrid |
| final duplicate_bars | 0 (tüm semboller) |
| input_duplicate_bars | {} |
| conflicting_duplicate_bars | {} |
| SPY (benchmark) | 2662 bar, tam |
| XLC (geç listelenme, 2018) | 2043 bar; listeleme başlangıcı manifestte |
| Kalite kapısı | PASSED (gap yok, OHLC geçerli, tz UTC) |
| Reconciliation (v2 vs v1) | `reports/portfolio_research/reconciliation/equity_v2_vs_v1.json` |
| Hash | deterministik (canonical symbol + timeframe) |

## Üretim komutu

```bash
python scripts/run_portfolio_research.py --download-v2 --equity-years 11
```

Koşu: SIP indirme (2016-2025) + IEX indirme (Aralık 2025 - bugün) → splice
reconciliation → `save_dataset` (duplicate/conflict politikası) → kalite
kapısı → reconciliation raporu.

## Kullanım

```bash
python scripts/run_portfolio_research.py --nested --v2   # frozen aday + 12 ablasyon
```

`--v2` equity_etf_1d_v2 verisini ve `config/experiments/equity_dual_momentum_v2.yaml`
manifestini kullanır; kripto (suspended) koşulmaz.
