#!/usr/bin/env python3
"""ParayaBuck veri kalite / legacy fixture CLI (tek giriş noktası).

BU ARAÇ STRATEJİ DEĞERLENDİRMEZ. Eski sinyal tabanlı simülasyon emekli
edilmiştir ve onaylı portföy stratejisi olmadan trade simülasyonu
çalıştırılmaz:

  * CSV / sentetik örnek veri (--generate-sample) akışları veri kalitesi ve
    legacy fixture doğrulama içindir.
  * Canlı indirme akışı yalnızca --legacy-fixture-mode ile çalışır ve
    manifest "data_quality_fixture / strategy_evaluated=false" etiketi taşır;
    sıfır işlemli sonuç strateji başarısı olarak raporlanmaz.

Portföy stratejisi araştırması için:
    python scripts/run_portfolio_research.py --baseline --nested

Tüm mantık parayabuck/backtest/cli.py içindedir; bu dosya yalnızca bir
sarmalayıcıdır. Aynı komuta şu yollardan erişebilirsiniz:

    python scripts/run_backtest.py ...      # script
    python -m parayabuck.backtest ...       # modül
    parayabuck-backtest ...                 # kurulu konsol komutu
"""

from __future__ import annotations

from parayabuck.backtest.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
