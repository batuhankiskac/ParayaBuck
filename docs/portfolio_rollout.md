# Portfolio Rollout

Tarih: 2026-08-08
Statü: **ALTYAPI UYGULANDI — rollout başlamadı**

`RolloutController` ve approval-gated `PortfolioRuntime` aynı execution yoluna
bağlıdır. Geçerli GREEN manifest bulunmadığı için sayaçlar henüz başlamaz.

## Aşamalar

1. Shadow: en az 30 gün; hedef ve emirler SQLite'a kaydolur, broker submit yoktur.
2. Paper: en az 90 gün ve aktive edilen her sleeve için en az 50 fill.
3. Canary: doğrulanmış hedeflerin %10'u; live hesapta ayrıca HMAC imzalı operator
   approval ve açık dolar cap gerekir.
4. Live: otomatik geçiş yoktur; operator approval/expiry/account/manifest/cap her
   başlangıçta yeniden doğrulanır.

Duplicate order, state divergence veya çözümsüz reconciliation sayılarından biri
sıfır değilse hiçbir stage ilerleyemez. Günlük kayıp/drawdown haltında yeni veya
artan exposure reddedilir; satış ve diğer risk azaltıcı emirler çalışmaya devam eder.

Paper sonuçları market impact, queue position, latency slippage, tüm regulatory
fee davranışı ve temettü zamanlamasını eksiksiz temsil etmez. Paper geçişi strateji
kârlılık garantisi veya live güvenliği kanıtı değildir.

## Operasyon kanıtı

Her karar manifest checksum, runtime session, dataset timestamp, target, risk
kararı, client/broker order ID ve fill activity ID ile izlenir. Persist edilen fill
kayıtlarında realized/unrealized PnL, fee ve slippage ayrı alanlardır. Rollout
evidence dosyası stage başlangıcı, fill sayıları ve operasyon olaylarını taşır.
