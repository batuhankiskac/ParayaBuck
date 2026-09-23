# Event clock

## Timestamp source

All event times come from timezone-aware historical bars or corporate-action
records. The source bars are UTC. Equity daily timestamps are encoded at
midnight New York time as a session-date label (05:00 UTC in standard time,
04:00 UTC in daylight time); they are not the actual market-close instant.
The canonical offline engine treats each such daily OHLCV bar as completed at
its source timestamp and makes it available to the decision at that timestamp.
Missing equity dates remain missing on weekends and U.S. holidays. Crypto uses
complete epoch-aligned UTC daily bars built from the supplied hourly data.

The event queue is the sorted union of execution-bar timestamps. At an event
time, each market receives only bars at that timestamp; a closed market keeps
its most recent known mark. Since the equity labels use New York midnight and
crypto labels use UTC midnight, same-calendar-date bars usually arrive at
different replay timestamps, four or five hours apart. That ordering follows
the frozen data labels, not actual intraday bar availability. Signals are
ingested only when their timestamp is no later than the current event time.
The runner checks each strategy history tail before a decision and fails if it
is future-dated.

## Session and risk dates

The single-market control preserves the canonical engine's session rule:
equities use the New York calendar date and crypto uses the UTC date
(`session_date_for`). In combined mode, the shared account uses the New York
calendar date for its one daily-loss/high-water lifecycle. This is a declared
account-calendar choice; the production runtime risk epoch and asynchronous
account refresh are not reproduced by a historical bar stream.

Risk state advances once after the current timestamp's fills and close marks,
before any market decision at that timestamp. It advances on crypto-only
weekend events using the last causally known equity mark.

## Market calendar cases

- U.S. holidays and weekends produce no equity event unless the supplied data
  contains an actual equity bar.
- Crypto can emit events every complete UTC day, including weekends and U.S.
  holidays.
- DST is inherited from the UTC source timestamps: the New York session-date
  labels move between 04:00 and 05:00 UTC; no fixed UTC offset is applied.
- Midnight UTC belongs to the UTC timestamp's date for crypto. Equity risk
  dates are converted to `America/New_York` before taking the date.
- The next executable fill uses the next actual bar for that instrument. The
  engine does not synthesize an opening price on a missing session.

This daily-bar timestamp convention is retained for exact isolated parity. A
future migration study should treat cross-market same-day intraday ordering as
an approximation unless higher-resolution, availability-timestamped inputs
are supplied.

## Total order key

The merged timestamps are strictly increasing. Within one timestamp, market
order is crypto then equity, symbols are ascending, and the event phases are
the fixed order in `SAME_TIMESTAMP_ORDERING.md`. Stable lists from the input
reader are sorted before processing. Equal timestamp events therefore have a
repeatable total order without relying on hash-map iteration.
