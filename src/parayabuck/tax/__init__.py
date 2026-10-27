"""TL tax ledger for a Turkish tax resident holding US-listed securities.

Implements the decision record ``docs/tax/turkish_tax_rules.md``. Estimates only;
this is software arithmetic, not tax advice. Modules:

* ``rules``: indexation threshold, bracket table, declaration threshold.
* ``rates``: TCMB USD/TRY and Yİ-ÜFE CSVs, previous-business-day lookup, EVDS fetch.
* ``ledger``: read-only fill query and FIFO lot matching into TL disposals.
* ``dividends``: Alpaca DIV/DIVNRA/INT activities, idempotent ``tax_activities`` table.
* ``export``: calendar-year report, CSV and summary JSON, YTD status.
* ``cli``: the ``parayabuck tax`` subcommands.
"""

from parayabuck.tax.errors import LedgerError, MissingIndexError, MissingRateError, TaxDataError

__all__ = ["LedgerError", "MissingIndexError", "MissingRateError", "TaxDataError"]
