"""TCMB USD/TRY buying rates and Yİ-ÜFE monthly indices for the TL ledger.

Two operator-supplied CSV files (format of the research copies):

* ``usdtry.csv``: ``date,usdtry_forex_buying[,usdtry_forex_selling]`` with ISO dates.
* ``yiufe.csv``: ``month,yiufe_index[,source]`` with ``YYYY-MM`` months.

Rule 4 (decision record): a trade's leg uses the rate published on the previous
business day, i.e. the latest published rate strictly before the trade date. The
search looks back at most :data:`FX_LOOKBACK_CALENDAR_DAYS` calendar days, so a
file that ends too early, or a gap longer than a holiday block, raises.
Missing data raises :class:`MissingRateError` or :class:`MissingIndexError`
naming the date or month; no value is ever guessed.

``fetch_rates_to_files`` downloads the same two series from TCMB EVDS when the
operator provides an API key through ``PARAYABUCK_EVDS_KEY``. The key is sent
in a request header, never in the URL.
"""

from __future__ import annotations

import bisect
import csv
import io
import json
import math
import os
import re
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from parayabuck.tax.errors import MissingIndexError, MissingRateError, TaxDataError
from parayabuck.utils.io import atomic_write


def _project_root() -> Path:
    """The checkout that holds ``pyproject.toml`` above this package, else the working directory.

    The rate files are operator state under ``data/tax``. Anchoring the defaults to the
    checkout makes a run from another working directory read the same files (Q2-5-7).
    An installed package without a checkout keeps the old relative default.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path()


DEFAULT_USDTRY_PATH = _project_root() / "data" / "tax" / "usdtry.csv"
DEFAULT_YIUFE_PATH = _project_root() / "data" / "tax" / "yiufe.csv"
EVDS_KEY_ENV = "PARAYABUCK_EVDS_KEY"
EVDS_BASE_URL = "https://evds2.tcmb.gov.tr/service/evds/"
# EVDS series codes: USD buying (döviz alış kuru, TL) and Yİ-ÜFE (2003=100).
# TP.FG.J0 is the consumer index (TÜFE), not Yİ-ÜFE. The Yİ-ÜFE code is not
# confirmed against the EVDS data-code list, so it can be overridden by env;
# the CSV files remain the primary, operator-checked source.
EVDS_USD_BUYING_SERIES = "TP.DK.USD.A.YTL"
EVDS_YIUFE_SERIES = "TP.TUFE1YI.T1"
EVDS_YIUFE_SERIES_ENV = "PARAYABUCK_EVDS_YIUFE_SERIES"
# Rule 4 lookback. TCMB publishes no rate on public holidays and a Turkish holiday
# block can run nine days, so a shorter window would refuse valid trade dates. The
# bound still refuses a file that ends well before the trade date.
FX_LOOKBACK_CALENDAR_DAYS = 10


def evds_yiufe_series(env: Mapping[str, str] | None = None) -> str:
    """Yİ-ÜFE series code: ``PARAYABUCK_EVDS_YIUFE_SERIES`` when set, else the default."""
    source = os.environ if env is None else env
    return str(source.get(EVDS_YIUFE_SERIES_ENV) or "").strip() or EVDS_YIUFE_SERIES


USDTRY_COLUMN = "usdtry_forex_buying"
YIUFE_COLUMN = "yiufe_index"

HttpGet = Callable[[str, Mapping[str, str]], Any]


@dataclass(frozen=True)
class RateQuote:
    """A rate and the date TCMB published it (the previous business day used)."""

    rate: float
    published_on: date


def previous_weekday(day: date) -> date:
    """The latest Monday-to-Friday strictly before ``day`` (public holidays unknown)."""
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def prior_month_key(day: date) -> str:
    """``YYYY-MM`` of the month before ``day``'s month (Yİ-ÜFE rule 2)."""
    if day.month == 1:
        return f"{day.year - 1:04d}-12"
    return f"{day.year:04d}-{day.month - 1:02d}"


@dataclass(frozen=True)
class RateBook:
    """USD/TRY buying rates by publication date and Yİ-ÜFE indices by month."""

    usd_try: Mapping[date, float]
    yiufe: Mapping[str, float]
    _dates: list[date] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_dates", sorted(self.usd_try))

    def usd_try_for_trade_date(self, trade_date: date) -> RateQuote:
        """Rule 4: the latest rate published strictly before ``trade_date``.

        The rate must be published within :data:`FX_LOOKBACK_CALENDAR_DAYS` before
        the trade date; a bigger gap, or a file that ends earlier, raises.
        """
        earliest = trade_date - timedelta(days=FX_LOOKBACK_CALENDAR_DAYS)
        position = bisect.bisect_left(self._dates, trade_date) - 1
        if position < 0 or self._dates[position] < earliest:
            latest = self._dates[-1].isoformat() if self._dates else "none"
            raise MissingRateError(
                f"no TCMB USD/TRY rate published between {earliest.isoformat()} and the day before trade date "
                f"{trade_date.isoformat()}; latest rate in file is {latest}"
            )
        published = self._dates[position]
        return RateQuote(rate=self.usd_try[published], published_on=published)

    def yiufe_for_month(self, month: str) -> float:
        """Yİ-ÜFE index for a ``YYYY-MM`` month; raises when it is not in the file."""
        if month not in self.yiufe:
            raise MissingIndexError(f"Yİ-ÜFE index for month {month} is missing from the yiufe file")
        return self.yiufe[month]


def _parse_positive(value: str, where: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise TaxDataError(f"{where}: not a number: {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise TaxDataError(f"{where}: rate or index must be a positive finite number, got {value!r}")
    return parsed


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise TaxDataError(f"{label} file not found: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_usdtry_csv(path: Path) -> dict[date, float]:
    """Read ``usdtry.csv``; duplicate dates or bad numbers raise with the line."""
    rows = _read_csv(path, "USD/TRY")
    rates: dict[date, float] = {}
    for line_number, row in enumerate(rows, start=2):
        where = f"{path}:{line_number}"
        raw_date = (row.get("date") or "").strip()
        raw_rate = (row.get(USDTRY_COLUMN) or "").strip()
        if not raw_date or not raw_rate:
            raise TaxDataError(f"{where}: date and {USDTRY_COLUMN} are required")
        try:
            day = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise TaxDataError(f"{where}: date must be YYYY-MM-DD, got {raw_date!r}") from exc
        if day in rates:
            raise TaxDataError(f"{where}: duplicate rate for {raw_date}")
        rates[day] = _parse_positive(raw_rate, where)
    return rates


def load_yiufe_csv(path: Path) -> dict[str, float]:
    """Read ``yiufe.csv``; months are ``YYYY-MM`` and must be unique."""
    rows = _read_csv(path, "Yİ-ÜFE")
    indices: dict[str, float] = {}
    for line_number, row in enumerate(rows, start=2):
        where = f"{path}:{line_number}"
        month = (row.get("month") or "").strip()
        raw_index = (row.get(YIUFE_COLUMN) or "").strip()
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) or not raw_index:
            raise TaxDataError(f"{where}: month (YYYY-MM) and {YIUFE_COLUMN} are required")
        if month in indices:
            raise TaxDataError(f"{where}: duplicate index for {month}")
        indices[month] = _parse_positive(raw_index, where)
    return indices


def load_rate_book(usdtry_path: Path = DEFAULT_USDTRY_PATH, yiufe_path: Path = DEFAULT_YIUFE_PATH) -> RateBook:
    """Load both CSV files into one :class:`RateBook`."""
    return RateBook(usd_try=load_usdtry_csv(Path(usdtry_path)), yiufe=load_yiufe_csv(Path(yiufe_path)))


# -- TCMB EVDS download -------------------------------------------------------


def default_http_get(url: str, headers: Mapping[str, str]) -> Any:
    """GET ``url`` and decode the JSON body (stdlib only, 20 s timeout).

    Transport and decoding failures raise :class:`TaxDataError` (URLError and
    HTTPError are OSError subclasses). The key is in the headers only, so the
    messages carry no credential.
    """
    request = urllib.request.Request(url, headers={**headers, "Accept": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed TCMB host
            body = response.read()
    except OSError as exc:
        raise TaxDataError(f"EVDS request failed: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TaxDataError(f"EVDS response is not JSON: {exc}") from exc


def _evds_date(text: str) -> date | str:
    """EVDS period label as a ``date`` (daily) or a ``YYYY-MM`` string (monthly)."""
    daily = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", text)
    if daily:
        return date(int(daily.group(3)), int(daily.group(2)), int(daily.group(1)))
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    year_first = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if year_first:
        return f"{int(year_first.group(1)):04d}-{int(year_first.group(2)):02d}"
    month_first = re.fullmatch(r"(\d{1,2})-(\d{4})", text)
    if month_first:
        return f"{int(month_first.group(2)):04d}-{int(month_first.group(1)):02d}"
    raise TaxDataError(f"unrecognised EVDS period label: {text!r}")


def _evds_items(series: str, start: date, end: date, api_key: str, http_get: HttpGet) -> list[tuple[str, float]]:
    if not api_key.strip():
        raise TaxDataError(f"{EVDS_KEY_ENV} is not set; an EVDS API key is required to fetch rates")
    url = (
        f"{EVDS_BASE_URL}series={series}"
        f"&startDate={start.strftime('%d-%m-%Y')}&endDate={end.strftime('%d-%m-%Y')}&type=json"
    )
    payload = http_get(url, {"key": api_key.strip()})
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TaxDataError(f"EVDS response for {series} has no items list")
    value_key = series.replace(".", "_")
    results: list[tuple[str, float]] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        label = item.get("Tarih")
        raw = item.get(value_key)
        if raw is None or raw == "":
            # EVDS returns empty values for non-published days; skipping them keeps
            # the previous-business-day rule intact (the latest real value is used).
            continue
        try:
            value = float(str(raw))
        except ValueError as exc:
            raise TaxDataError(f"EVDS {series} value for {label} is not a number: {raw!r}") from exc
        results.append((str(label), value))
    return results


def fetch_usd_try(start: date, end: date, api_key: str, http_get: HttpGet = default_http_get) -> dict[date, float]:
    """Daily USD buying rates from EVDS between ``start`` and ``end`` (inclusive)."""
    rates: dict[date, float] = {}
    for label, value in _evds_items(EVDS_USD_BUYING_SERIES, start, end, api_key, http_get):
        period = _evds_date(label)
        if not isinstance(period, date):
            raise TaxDataError(f"EVDS USD/TRY period is not a date: {label!r}")
        rates[period] = _parse_positive(str(value), f"EVDS USD/TRY {label}")
    return rates


def fetch_yiufe(start: date, end: date, api_key: str, http_get: HttpGet = default_http_get) -> dict[str, float]:
    """Monthly Yİ-ÜFE indices from EVDS between ``start`` and ``end`` (inclusive)."""
    indices: dict[str, float] = {}
    for label, value in _evds_items(evds_yiufe_series(), start, end, api_key, http_get):
        period = _evds_date(label)
        month = period.strftime("%Y-%m") if isinstance(period, date) else period
        indices[month] = _parse_positive(str(value), f"EVDS Yİ-ÜFE {label}")
    return indices


def _write_csv_atomic(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    atomic_write(path, buffer.getvalue())


def _yiufe_sources(path: Path) -> dict[str, str]:
    """The ``source`` label of each month already in the Yİ-ÜFE file (empty when none)."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {(row.get("month") or "").strip(): (row.get("source") or "").strip() for row in csv.DictReader(handle)}


def fetch_rates_to_files(
    start: date,
    end: date,
    *,
    usdtry_path: Path = DEFAULT_USDTRY_PATH,
    yiufe_path: Path = DEFAULT_YIUFE_PATH,
    api_key: str | None = None,
    http_get: HttpGet = default_http_get,
    confirmed_series: bool = False,
) -> dict[str, int]:
    """Download both series and merge them into the CSV files.

    USD/TRY: existing rows are kept, new days are added, and a fetched rate that
    differs from a stored rate for the same day refuses the whole run (F2-9-8),
    as the Yİ-ÜFE series does; nothing is written. Yİ-ÜFE: the series code is not
    confirmed, so a fetched month that already holds a different index refuses the
    whole run and nothing is written. When no fetched month overlaps the existing
    file there is nothing to cross-check against, so the run refuses unless
    ``confirmed_series`` is set (the operator has checked the series code). Rows
    keep their own ``source``; only new months get the series label. Returns the
    number of rows in each file afterwards.
    """
    key = os.getenv(EVDS_KEY_ENV, "") if api_key is None else api_key
    usd: dict[date, float] = load_usdtry_csv(Path(usdtry_path)) if Path(usdtry_path).exists() else {}
    fetched_usd = fetch_usd_try(start, end, key, http_get)
    usd_differing = sorted(day for day, rate in fetched_usd.items() if day in usd and usd[day] != rate)
    if usd_differing:
        raise TaxDataError(
            f"EVDS USD/TRY differs from the usdtry file for {len(usd_differing)} day(s), "
            f"e.g. {', '.join(day.isoformat() for day in usd_differing[:3])}; nothing was written. "
            "Check the rate, or remove those rows first."
        )
    usd.update(fetched_usd)
    indices: dict[str, float] = load_yiufe_csv(Path(yiufe_path)) if Path(yiufe_path).exists() else {}
    fetched = fetch_yiufe(start, end, key, http_get)
    differing = sorted(month for month, value in fetched.items() if month in indices and indices[month] != value)
    if differing:
        raise TaxDataError(
            f"EVDS Yİ-ÜFE series {evds_yiufe_series()} differs from the yiufe file for {len(differing)} month(s), "
            f"e.g. {', '.join(differing[:3])}; nothing was written. Check the series code, or remove those rows first."
        )
    if fetched and not confirmed_series and not any(month in indices for month in fetched):
        raise TaxDataError(
            f"no fetched Yİ-ÜFE month overlaps the yiufe file, so EVDS series {evds_yiufe_series()} is not "
            "cross-checked; nothing was written. Check the series code against TÜİK, then rerun with "
            "--confirm-yiufe-series."
        )
    sources = _yiufe_sources(Path(yiufe_path))
    label = f"TCMB EVDS {evds_yiufe_series()}"
    indices.update(fetched)
    _write_csv_atomic(
        Path(usdtry_path),
        ["date", USDTRY_COLUMN],
        [[day.isoformat(), repr(usd[day])] for day in sorted(usd)],
    )
    _write_csv_atomic(
        Path(yiufe_path),
        ["month", YIUFE_COLUMN, "source"],
        [[month, repr(indices[month]), sources.get(month) or label] for month in sorted(indices)],
    )
    return {"usdtry_rows": len(usd), "yiufe_rows": len(indices)}


__all__ = [
    "DEFAULT_USDTRY_PATH",
    "DEFAULT_YIUFE_PATH",
    "EVDS_KEY_ENV",
    "FX_LOOKBACK_CALENDAR_DAYS",
    "RateBook",
    "RateQuote",
    "fetch_rates_to_files",
    "fetch_usd_try",
    "fetch_yiufe",
    "load_rate_book",
    "load_usdtry_csv",
    "load_yiufe_csv",
    "prior_month_key",
    "previous_weekday",
]
