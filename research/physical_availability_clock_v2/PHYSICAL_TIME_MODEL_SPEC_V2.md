# Physical availability clock V2

The scheduler retains each source `Bar.timestamp` as its historical label and uses a separate physical event clock. `PhysicalReplayTime` records market, source bar, label, availability, decision creation, order creation, and optional execution price event. No production model or configuration is changed.

| Market | Complete signal available | Decision / new order | Earliest price event |
| --- | --- | --- | --- |
| Equity V3 | Official XNYS close for that session, from installed `exchange-calendars==4.13.2` | At or after that close; original session label remains the strategy cadence/PIT key | Next valid XNYS session open, strictly after the completed signal |
| Crypto T1 | Maximum end of all 24 pinned, start-labeled hourly components, normally next UTC midnight | At daily availability; original daily label remains the 96-hour block key | First actual same-symbol hourly source OPEN strictly after order creation and the day's midnight, at most one attempt per UTC day |

The crypto proxy is copied from the accepted canonical repair at `b135d73743ec2652f79b8c840df50bd3f67166e8`; the old 455/388 control is not a parity oracle. Missing crypto source hours advance to the next actual eligible hour that UTC day. The pinned daily-volume participation and residual behavior remain in force. The common-source crypto terminal exit is precommitted at `2026-07-31T00:00:00Z`. Equity's final liquidation uses its final completed session close.

The scheduler groups actual price events and complete-signal events by physical UTC instant. It carries one cash, inventory and slot ledger across markets. A market's source label is still passed to its existing strategy cadence and PIT universe logic; physical time governs when that decision may be evaluated and when its order can exist. Synthetic hourly/opens are used only as existing backtest fill input, with their physical event timestamp and actual pinned source OPEN. Equity's 11.6-million-row source is not copied into another full bar array: one session calendar mapping controls physical open and close phases.

No broker acknowledgement/queue latency is inferred from daily or hourly historical OHLCV. Official close is the earliest defensible equity data-completion lower bound; actual provider publication may be later. These are research-only lower-bound clocks, not claims about exact live fill latency.
