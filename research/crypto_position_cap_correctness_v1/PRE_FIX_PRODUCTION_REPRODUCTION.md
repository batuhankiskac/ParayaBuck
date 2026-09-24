# Pre-fix offline production reproduction

Authority: local `main` `be440821e34d859f2e693c45532566eb0dafb1f8`; `origin/main` was the same SHA at inspection. Shipped `config/settings.yaml`: `max_positions: 8`, `max_crypto_positions: 2`, `live.enabled: false`. Isolated worktree was clean before test construction.

Using real `PortfolioRuntime`, `PortfolioRiskOverlay`, `TargetToOrderPlanner`, `PortfolioStateRepository`, and shipped risk limits, with only the broker replaced by a deterministic in-memory fake and repository under a temporary directory, the target retained LTC/USD, exited BTC/USD, and opened DOGE/USD. The initial real decision submitted BTC SELL and deferred DOGE BUY. A terminal rejected update left BTC and LTC nonzero in the fresh broker account. `advance_rebalance()` returned `buy_phase_submitted` and submitted DOGE BUY. Thus the intended third crypto opening survived the final production boundary with two existing crypto positions and global count below eight.

Command: `PYTHONPATH=/private/tmp/crypto-position-cap-correctness/src:/private/tmp/crypto-position-cap-correctness/tests /Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python /private/tmp/repro_crypto.py`.

Output: `initial: sell_phase_submitted [('sell', 'BTC/USD')] [('sell', 'BTC/USD'), ('buy', 'DOGE/USD')]`; `advanced: buy_phase_submitted [('sell', 'BTC/USD'), ('buy', 'DOGE/USD')] [('BTC/USD', 10), ('LTC/USD', 10)]`.

Classification: `GENERIC_PRODUCTION_CRYPTO_CAP_BUG_CONFIRMED`; research replay also has the same class of failure. No production broker, network, PAPER run, or LIVE run was invoked.
