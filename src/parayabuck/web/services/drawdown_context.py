"""Pure display helper: how unusual is the current drawdown for this strategy?

The trend card shows the account's drawdown from its peak. On its own a number
like -14 % reads as a malfunction; next to the strategy's own history it is a
routine event. This module compares it with the drawdown episodes of the
production rule K (``equity_leveraged_trend_v1`` 1.2.0) in its research
backtest: synthetic daily-reset 2x QQQ, base financing, 4 bp x 2 cost, pre-tax,
2000-03-08 -> 2026-09-29 (``research/fast_engine``, the control of
``docs/research/short_term_restore_v1.md``). Backtest returns do not imply live
returns; the table is a reference for scale, not a forecast.

Display-only: no trading state, ledger or runtime is read or written here.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any

BACKTEST_START = "2000-03-08"
BACKTEST_END = "2026-09-29"
BACKTEST_YEARS = 26.56

# Completed episodes with a depth of at least 5 %:
# (peak, trough, recovery, depth %, sessions peak->trough, sessions peak->recovery).
# An episode still open at the backtest end (2026-06-02 peak, 18.25 % deep at
# 2026-07-29) is left out because its recovery is unknown.
K_EPISODES: tuple[tuple[str, str, str, float, int, int], ...] = (
    ("2000-03-09", "2000-03-15", "2000-03-24", 9.13, 4, 11),
    ("2000-03-27", "2003-03-31", "2005-11-22", 46.67, 754, 1423),
    ("2005-12-02", "2005-12-30", "2006-01-06", 7.74, 19, 23),
    ("2006-01-11", "2006-09-19", "2006-11-22", 18.59, 173, 219),
    ("2006-11-22", "2006-12-22", "2007-01-11", 8.12, 21, 32),
    ("2007-01-12", "2007-03-05", "2007-04-25", 14.27, 34, 70),
    ("2007-06-04", "2007-06-07", "2007-06-15", 5.15, 3, 9),
    ("2007-07-19", "2007-08-16", "2007-09-27", 18.43, 20, 49),
    ("2007-10-31", "2009-05-22", "2009-11-16", 34.82, 392, 515),
    ("2010-01-19", "2010-02-04", "2010-03-10", 15.91, 12, 35),
    ("2010-04-23", "2010-09-09", "2011-01-14", 31.93, 96, 185),
    ("2011-01-18", "2011-01-21", "2011-02-04", 5.07, 3, 13),
    ("2011-02-16", "2011-03-16", "2011-04-27", 15.55, 19, 48),
    ("2011-04-27", "2011-12-19", "2012-09-06", 34.78, 164, 344),
    ("2012-09-19", "2013-02-25", "2013-10-01", 30.17, 106, 258),
    ("2013-10-01", "2013-10-09", "2013-10-14", 6.65, 6, 9),
    ("2014-01-22", "2014-02-03", "2014-02-13", 9.91, 8, 16),
    ("2014-03-05", "2014-04-11", "2014-06-05", 14.38, 27, 64),
    ("2014-07-23", "2014-08-07", "2014-08-15", 6.09, 11, 17),
    ("2014-09-18", "2014-10-16", "2014-11-03", 15.89, 20, 32),
    ("2014-11-28", "2015-01-15", "2015-02-13", 11.18, 32, 52),
    ("2015-03-02", "2015-03-11", "2015-04-24", 7.6, 7, 38),
    ("2015-04-24", "2015-05-06", "2015-05-27", 6.8, 8, 22),
    ("2015-05-27", "2015-07-09", "2015-07-16", 8.4, 30, 35),
    ("2015-07-20", "2016-07-06", "2017-05-25", 38.46, 243, 467),
    ("2017-06-08", "2017-07-03", "2017-07-19", 9.72, 17, 28),
    ("2017-07-26", "2017-08-21", "2017-08-31", 5.39, 18, 26),
    ("2018-01-26", "2018-04-02", "2018-07-12", 20.33, 44, 115),
    ("2018-07-25", "2018-07-30", "2018-08-27", 8.2, 3, 23),
    ("2018-08-29", "2019-02-07", "2019-04-29", 21.25, 110, 165),
    ("2019-05-03", "2019-06-03", "2019-07-15", 19.91, 20, 49),
    ("2019-07-26", "2019-08-05", "2019-11-01", 14.3, 6, 69),
    ("2020-01-23", "2020-01-27", "2020-02-04", 5.68, 2, 8),
    ("2020-02-19", "2020-04-09", "2020-08-03", 28.26, 36, 115),
    ("2020-08-06", "2020-08-11", "2020-08-17", 5.73, 3, 7),
    ("2020-09-02", "2020-10-30", "2020-12-29", 19.64, 41, 81),
    ("2021-01-26", "2021-01-29", "2021-02-05", 7.86, 3, 8),
    ("2021-02-12", "2021-03-08", "2021-06-22", 17.92, 15, 89),
    ("2021-09-07", "2021-10-04", "2021-10-28", 14.67, 19, 37),
    ("2021-11-19", "2023-03-13", "2023-06-13", 31.12, 327, 391),
    ("2023-06-15", "2023-06-26", "2023-07-12", 6.52, 6, 17),
    ("2023-07-18", "2023-10-26", "2023-12-11", 22.13, 71, 102),
    ("2023-12-27", "2024-01-04", "2024-01-18", 7.28, 5, 14),
    ("2024-01-29", "2024-01-31", "2024-02-02", 5.16, 2, 4),
    ("2024-02-09", "2024-02-21", "2024-02-22", 5.3, 7, 8),
    ("2024-03-01", "2024-03-15", "2024-03-22", 5.44, 10, 15),
    ("2024-03-22", "2024-04-19", "2024-05-15", 14.07, 19, 37),
    ("2024-07-10", "2024-08-07", "2024-12-04", 22.5, 20, 103),
    ("2024-12-16", "2025-05-06", "2025-07-21", 20.57, 95, 146),
    ("2025-08-13", "2025-08-21", "2025-09-11", 5.83, 6, 20),
    ("2025-10-08", "2025-10-10", "2025-10-24", 7.09, 2, 12),
    ("2025-10-29", "2026-03-20", "2026-05-05", 19.01, 97, 128),
    ("2026-05-14", "2026-05-19", "2026-05-26", 5.0, 3, 7),
)


def drawdown_context(drawdown_pct: float | None) -> dict[str, Any]:
    """Backtest episodes at least as deep as ``drawdown_pct`` (a positive percent).

    ``available`` is false for a missing, non-finite or sub-1 % drawdown (nothing
    to put in context). ``deeper_count`` counts completed backtest episodes with a
    depth >= the current drawdown; the medians describe those episodes. When no
    episode was that deep, the counts are zero and the medians None.
    """
    if (
        drawdown_pct is None
        or isinstance(drawdown_pct, bool)
        or not isinstance(drawdown_pct, (int, float))
        or not math.isfinite(drawdown_pct)
        or drawdown_pct < 1.0
    ):
        return {"available": False}
    depth = float(drawdown_pct)
    deeper = [row for row in K_EPISODES if row[3] >= depth]
    return {
        "available": True,
        "drawdown_pct": depth,
        "backtest_start": BACKTEST_START,
        "backtest_end": BACKTEST_END,
        "episodes_total": len(K_EPISODES),
        "deeper_count": len(deeper),
        "deeper_per_year": len(deeper) / BACKTEST_YEARS,
        "median_sessions_to_recover": median(row[5] for row in deeper) if deeper else None,
        "median_sessions_to_trough": median(row[4] for row in deeper) if deeper else None,
        "deepest_pct": max(row[3] for row in K_EPISODES),
        "last_deeper_peak": max(row[0] for row in deeper) if deeper else None,
    }


__all__ = ["K_EPISODES", "drawdown_context"]
