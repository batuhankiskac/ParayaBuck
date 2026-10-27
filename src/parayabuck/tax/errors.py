"""Errors raised by the TL tax ledger. Missing data always raises; nothing is guessed."""

from __future__ import annotations


class TaxDataError(RuntimeError):
    """Required tax input (rate, index, tariff, data file) is missing or invalid."""


class MissingRateError(TaxDataError):
    """No TCMB USD/TRY buying rate is available for a required trade date."""


class MissingIndexError(TaxDataError):
    """No Yİ-ÜFE index value is available for a required month."""


class MissingTableError(TaxDataError):
    """No bracket table or declaration threshold is defined for a tax year."""


class LedgerError(TaxDataError):
    """The bot's own fill book cannot be matched FIFO for tax purposes."""


__all__ = ["LedgerError", "MissingIndexError", "MissingRateError", "MissingTableError", "TaxDataError"]
