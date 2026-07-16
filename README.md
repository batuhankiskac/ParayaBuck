# ParayaBuck

Python trading bot for **US futures** (CME). Paper trading first, live later — own capital, no prop firm.

## Quick Start

```bash
# 1. Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Generate sample data (5000 bars of synthetic MES 5m)
python scripts/generate_sample_data.py --symbol MES --timeframe 5m --bars 5000

# 3. Run backtest
parayabuck backtest

# Or with overrides
parayabuck backtest --symbol MES --timeframe 5m --start 2024-01-01 --end 2024-01-15
```

## Architecture

```
src/parayabuck/
├── models.py          # Bar, Order, Position, Trade, Account
├── config.py          # YAML config loader
├── logger.py          # Logging setup
├── cli.py             # CLI: backtest / paper / live
├── data/
│   ├── base.py        # DataSource ABC
│   ├── historical.py  # CSV historical data
│   └── tradovate.py   # Tradovate live data (stub)
├── strategy/
│   ├── base.py        # Strategy ABC
│   └── ema_crossover.py  # Example: EMA 9/21 crossover
├── risk/
│   └── manager.py     # Position size, daily loss, drawdown limits
├── backtest/
│   └── engine.py      # Event-driven backtest engine
└── execution/
    ├── base.py        # Broker ABC
    ├── paper.py       # Simulated broker (paper trading + backtest)
    └── tradovate.py   # Tradovate broker (stub)
```

## Configuration

Edit `config/settings.yaml`:

```yaml
account:
  starting_balance: 10000.0

risk:
  max_position_size: 3       # max contracts per trade
  max_daily_loss: 300.0      # stop if daily PnL <= -$300
  max_drawdown: 1500.0       # stop if drawdown >= $1500
  commission_per_contract: 2.25
  slippage_ticks: 1

symbols:
  MES:                       # Micro S&P 500
    tick_size: 0.25
    tick_value: 1.25
    point_value: 5.0
```

## Writing a Strategy

Subclass `Strategy` and implement `on_bar()`:

```python
from parayabuck.strategy.base import Strategy
from parayabuck.models import Bar, Order, Position, Side, OrderType

class MyStrategy(Strategy):
    name = "my_strategy"

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        # Your logic here — return an Order or None
        if position.is_flat and bar.close > some_level:
            return Order(symbol=bar.symbol, side=Side.BUY, qty=1)
        return None
```

Register it in `cli.py`'s `_load_strategy()`.

## Roadmap

- [x] Backtest engine with historical CSV data
- [x] Paper broker with slippage + commission
- [x] Risk manager (position size, daily loss, drawdown)
- [x] EMA crossover example strategy
- [ ] **Live data from Tradovate** (paper trading)
- [ ] **Tradovate order execution** (live trading)
- [ ] More strategies (mean reversion, breakout, VWAP)
- [ ] Performance charts (matplotlib equity curve)
- [ ] Real historical data download (Tradovate / Polygon)

## Disclaimer

This is for educational purposes. Trading futures involves substantial risk of loss. Always test with paper money before risking real capital.
