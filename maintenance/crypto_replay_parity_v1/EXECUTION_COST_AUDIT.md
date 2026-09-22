# Execution and cost audit

Authoritative current path: `backtest/product.py:run_product_backtest` -> `PortfolioBacktestEngine._fill_side`.

Configured initial cash100000; per-fill slippage2bps, full spread2bps (half charged each side), commission0, crypto taker25bps. Adverse price move is3bps: buy=next bar open*1.0003, sell=open*0.9997. Fee=executed quantity*adverse fill price*0.0025 on both sides. Slippage/spread accounting use raw open, avoiding counting the fee twice. Product cost_multiplier scales slippage, spread and taker fee; commission remains unscaled.

Orders created on a completed decision bar cannot fill on the same timestamp. Fills require known preceding average volume; zero known volume delays fills. Participation limits may partially fill and defer residuals. Sells execute before buys; sell quantity is capped by held quantity before calculating costs. Buy cash sufficiency includes fee. Execution policy band0.005 and minimum notional10 suppress small orders, so target changes need not equal fills. Pending orders can be replaced by a newer target. Target/holding/closed-trade/fee totals are distinct lifecycle quantities.

Risk allocation and drawdown overlays operate separately from alpha selection. Protocols overriding sleeve allocation or risk produce different economics. Terminal liquidation costs are accumulated separately from ordinary fills and included in total cost; unliquidated terminal positions must not be described as realized closed trades. Existing suite tests cover engine costs/terminal lifecycle; new contract fixtures cover the time and normalized-input boundary. No broker execution or physical market realism is claimed.
