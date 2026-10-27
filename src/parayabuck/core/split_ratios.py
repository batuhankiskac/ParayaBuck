"""Standard US equity split ratios, shared by the books and the signal basis.

Both sides must recognise the same ratios: the books side (share-count
rebase in ``portfolio.ownership_rebase``) and the signal side (the
split-continuity check in ``runtime``) read this one tuple.
"""

from __future__ import annotations

# Standard split and reverse-split ratios a US equity issuer uses (n-for-1
# and its reciprocal). A non-standard ratio is deliberately absent: it must
# halt for an operator, never be guessed at.
KNOWN_SPLIT_RATIOS: tuple[float, ...] = (0.1, 0.125, 0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0)

__all__ = ["KNOWN_SPLIT_RATIOS"]
