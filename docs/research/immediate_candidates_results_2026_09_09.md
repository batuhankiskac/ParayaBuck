# Immediate candidate ekonomik testi — 2026-09-09

Durum: `COMPLETE_RESEARCH_ONLY`. A01-A06 frozen manifestten, tuning veya en iyi kol seçimi yapılmadan çalıştırıldı. Entegrasyon, promotion, PAPER ve LIVE işlemi yapılmadı.

Manifest physical SHA-256: `8cec049f1d61d929f001b53af43a7e46ede1619243a1605e30d1dc312dd9965b`; canonical protocol SHA-256: `c6230ec5c8c3ef37a761c30d7425275e523528155ab73232e6d5a351ca2c27c1`.
Doğrulanan bar satırı: `11686832`; cutoff sonrası reddedilen satır: `0`; V3 forward-fill günü: `0`.

Primary matris (lag 1, maliyet 1x)

| Aday | Full getiri | Recent getiri | True holdout getiri | Full max DD | Full Sharpe | Full Sortino | Full turnover | V3 corr | İsim örtüşmesi | V3-down gün | Cap uygun gün | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| A01 | 0.04250016873464113 | 0.0954906643602691 | 0.013026994045655105 | 0.1136289788555761 | 0.21940430648209297 | 0.21195289539296278 | 37.45313663895416 | 0.5477640644891063 | 0.004726890756302521 | 537 | 37 | RESEARCH_ONLY_NO_PROMOTION |
| A02 | -0.07892033134633991 | 0.3114623043629423 | -0.07198133774671933 | 0.4504790218150283 | 0.047719264136817874 | 0.051095762802090355 | 237.9209469221867 | 0.33534322649340037 | 0.003676470588235294 | 537 | 37 | RESEARCH_ONLY_NO_PROMOTION |
| A03 | 0.6945667265027089 | 0.7404045609308352 | -0.09682504647845602 | 0.1849996757165714 | 0.751558712150892 | 0.7937911042059195 | 57.78841124622328 | 0.42261558257665127 | 0.005252100840336135 | 537 | 37 | RESEARCH_ONLY_NO_PROMOTION |
| A04 | 0.6480574910230001 | 0.6609094612143007 | 0.17785070130777902 | 0.26832064299751524 | 0.6104207273716299 | 0.6145530971089255 | 28.968766038513387 | 0.4979416542104362 | 0.0 | 537 | 37 | RESEARCH_ONLY_NO_PROMOTION |
| A05 | 0.06706818523459512 | 0.1553534680114257 | -0.03597951422969303 | 0.1848810167626731 | 0.21543995834283383 | 0.19011295043886375 | 53.196850519399646 | 0.5234339606763899 | 0.01523109243697479 | 537 | 37 | RESEARCH_ONLY_NO_PROMOTION |
| A06 | 0.33380409779796727 | 0.302602516560907 | 0.017959302859527293 | 0.15708402909563668 | 0.6373230944092569 | 0.6439057994550096 | 58.496156007017944 | 0.4501954030069662 | 0.007878151260504201 | 537 | 37 | RESEARCH_ONLY_NO_PROMOTION |

Tüm 54 aday hücresi (lag 1/3/7 × maliyet 1x/2x/3x) `results.json` içinde tutuldu. Her hücrede full/recent/true-holdout metrikleri ve T1 terminal close+cost muhasebesi bulunur. V3 eşlemesi yalnızca aynı tarihleri kullanır; 2026-07-30 ve 2026-07-31 forward-fill edilmedi.

Primary istatistik tanıları frozen 10.000 örnekli 21-session moving-block bootstrap ve 10.000 paired-sign permutation için aday eksi V3 günlük getirilerini kullanır. +7-session schedule placebo gerçekten çalıştırılır, diagnostic olarak tutulur ve seçimde kullanılmaz. Bunlar araştırma gate’leridir; sonuçlardan otomatik admission veya promotion çıkarılmaz.
