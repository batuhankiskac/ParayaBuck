"""Probability of backtest overfitting by combinatorially symmetric cross-validation (CSCV).

Bailey, Borwein, Lopez de Prado & Zhu (2015). Input: a T x N matrix of arm DAILY returns (rows =
sessions in time order, columns = arms). The rows are cut into S contiguous blocks (S even). For
every way of choosing S/2 blocks as in-sample (IS) and the rest as out-of-sample (OOS):

  1. IS rows that lie within `purge` sessions of any OOS row are dropped (purge, both sides).
  2. n* = argmax over arms of the IS Sharpe ratio (per period, mean / std).
  3. omega = rank of arm n* among the N arms' OOS Sharpe ratios, rank/(N+1) in (0, 1).
  4. lambda = logit(omega). PBO = fraction of combinations with lambda <= 0, i.e. the IS
     winner ranks at or below the OOS median.
"""

from __future__ import annotations

import itertools
import math

import numpy as np


def _sharpe_cols(x: np.ndarray) -> np.ndarray:
    """Per-column Sharpe ratio (mean / sample std); NaN where the std is zero."""
    mu = x.mean(axis=0)
    sd = x.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0.0, mu / sd, np.nan)


def pbo_cscv(returns, n_blocks: int = 16, purge: int = 252) -> dict:
    """PBO by CSCV with a `purge`-session embargo between train and test blocks."""
    r = np.asarray(returns, dtype=float)
    if r.ndim != 2:
        raise ValueError("returns must be a 2-D (T, N) array")
    t, n = r.shape
    if n < 2:
        raise ValueError("need at least 2 arms")
    if n_blocks % 2 or n_blocks < 4:
        raise ValueError("n_blocks must be an even integer >= 4")
    if not np.all(np.isfinite(r)):
        raise ValueError("returns must be finite")
    blocks = np.array_split(np.arange(t), n_blocks)
    block_id = np.empty(t, dtype=int)
    for b, idx in enumerate(blocks):
        block_id[idx] = b
    half = n_blocks // 2
    logits = []
    winner_is = []
    oos_ranks = []
    purged_rows = []
    for combo in itertools.combinations(range(n_blocks), half):
        is_mask = np.isin(block_id, combo)
        oos_mask = ~is_mask
        # purge: IS rows within `purge` sessions of any OOS row are removed
        csum = np.concatenate(([0], np.cumsum(oos_mask.astype(np.int64))))
        lo_i = np.clip(np.arange(t) - purge, 0, t)
        hi_i = np.clip(np.arange(t) + purge + 1, 0, t)
        near = (csum[hi_i] - csum[lo_i]) > 0  # any OOS row within +/- purge sessions
        train_mask = is_mask & ~near
        if int(train_mask.sum()) < 30:
            raise ValueError(
                "purge leaves fewer than 30 in-sample rows: need a longer sample "
                f"(T={t}, blocks={n_blocks}, purge={purge})"
            )
        purged_rows.append(int(is_mask.sum() - train_mask.sum()))
        sr_is = _sharpe_cols(r[train_mask])
        sr_oos = _sharpe_cols(r[oos_mask])
        if np.any(np.isnan(sr_is)) or np.any(np.isnan(sr_oos)):
            raise ValueError("degenerate block: zero-variance arm in a combination")
        best = int(np.argmax(sr_is))
        rank = int((sr_oos < sr_oos[best]).sum()) + 1  # 1 = worst, n = best
        omega = rank / (n + 1.0)
        logits.append(math.log(omega / (1.0 - omega)))
        winner_is.append(best)
        oos_ranks.append(rank)
    logits_arr = np.asarray(logits)
    return {
        "pbo": float(np.mean(logits_arr <= 0.0)),
        "n_combinations": len(logits),
        "n_blocks": n_blocks,
        "purge_sessions": purge,
        "mean_logit": float(logits_arr.mean()),
        "median_oos_rank_of_is_winner": float(np.median(oos_ranks) / (n + 1.0)),
        "mean_purged_is_rows": float(np.mean(purged_rows)),
        "is_winner_frequency": np.bincount(winner_is, minlength=n).tolist(),
    }
