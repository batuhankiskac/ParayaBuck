#!/usr/bin/env python3
"""ParayaBuck backtest CLI (tek giriş noktası).

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
