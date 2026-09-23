# Physical availability rule

`bar_label_time` identifies the represented interval or session. `data_available_at` is a lower bound on when its full OHLCV can be known. `decision_at` must satisfy `max(consumed.data_available_at) <= decision_at`. `execution_at` must be a later executable event after order creation. These fields must not overwrite `Bar.timestamp`.

For equity daily bars, `data_available_at = XNYS.session_close(session_date)`. For crypto hourly bars, the Alpaca start label plus one source hour is the interval-completion lower bound. For a complete UTC crypto day, availability is the maximum component completion time; 24 distinct contiguous source intervals are mandatory. The research helper `timing.py` implements only these lower bounds and leaves each Bar unchanged.

This branch stops before integrating a combined event scheduler because the frozen crypto isolated baseline already uses an economically material next-day open that coincides exactly with signal availability. The baseline needs a separately governed correction first.
