# SPDX-License-Identifier: Zlib
"""Pairing removed and added lines by similarity."""

import difflib
import functools
from collections.abc import Sequence

from plustwo.diff.intraline import INTRALINE_MIN_RATIO
from plustwo.git.cache import CACHE_SIZE


# Edits with more removed times added lines than this pair them by their
# order, the search for similar lines would take too long.
_PAIRING_MAX_CELLS = 2500

# A pair of lines of one edit: (index into the removed lines, index into the
# added lines), None for a line without a partner
LinePair = tuple[int | None, int | None]


def _line_similarity(old: str, new: str) -> float:
    """Measures how alike two lines are, see difflib's ratio().

    Args:
        old: a removed line.
        new: an added line.

    Returns:
        0 to 1. 0 also for lines less alike than INTRALINE_MIN_RATIO.
    """
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    if matcher.real_quick_ratio() < INTRALINE_MIN_RATIO \
            or matcher.quick_ratio() < INTRALINE_MIN_RATIO:
        return 0
    return matcher.ratio()


def paired_lines(removed: Sequence[str], added: Sequence[str]) -> list[LinePair]:
    """Pairs the removed and added lines of one edit by similarity, in
    order. The pairs are the ones with the most similarity in all, only
    lines at least INTRALINE_MIN_RATIO alike pair. The others stand alone,
    the removed ones of a run before the added ones. Like delta, unlike
    Gerrit, which pairs by position: then a rewritten block shows unrelated
    lines side by side. Big edits still pair by position. The result is
    kept for the same lines.

    Args:
        removed: the edit's removed lines.
        added: the edit's added lines.

    Returns:
        The pairs, top to bottom, see LinePair.
    """
    return _paired_lines(tuple(removed), tuple(added))


@functools.lru_cache(maxsize=CACHE_SIZE)
def _paired_lines(removed: tuple[str, ...], added: tuple[str, ...]) -> list[LinePair]:
    """Pairs the lines of one edit, see paired_lines().

    Args:
        removed: the edit's removed lines.
        added: the edit's added lines.

    Returns:
        The pairs, top to bottom, see LinePair.
    """
    if len(removed) * len(added) > _PAIRING_MAX_CELLS or not removed or not added:
        return [(index if index < len(removed) else None, index if index < len(added) else None)
                for index in range(max(len(removed), len(added)))]
    pairs = _most_similar_pairs(removed, added)
    # The pairs in order, the lines between them standing alone
    result: list[LinePair] = []
    old_at = new_at = 0
    for old, new in pairs + [(len(removed), len(added))]:
        result += [(index, None) for index in range(old_at, old)]
        result += [(None, index) for index in range(new_at, new)]
        if old < len(removed):
            result.append((old, new))
        old_at, new_at = old + 1, new + 1
    return result


def _most_similar_pairs(removed: tuple[str, ...], added: tuple[str, ...]
                        ) -> list[tuple[int, int]]:
    """Finds the pairs of lines in order with the most similarity in all,
    by dynamic programming like a longest common subsequence.

    Args:
        removed: the edit's removed lines.
        added: the edit's added lines.

    Returns:
        (index into removed, index into added) per pair, in order.
    """
    similarity = [[_line_similarity(old, new) for new in added] for old in removed]
    # best[i][j]: the most similarity of the first i removed and j added lines
    best = [[0.0] * (len(added) + 1) for _ in range(len(removed) + 1)]
    for i in range(1, len(removed) + 1):
        for j in range(1, len(added) + 1):
            best[i][j] = max(best[i - 1][j], best[i][j - 1],
                             best[i - 1][j - 1] + similarity[i - 1][j - 1]
                             if similarity[i - 1][j - 1] else 0)
    # Back from the end, the pairs that made the best sums
    pairs = []
    i, j = len(removed), len(added)
    while i and j:
        if similarity[i - 1][j - 1] and best[i][j] == best[i - 1][j - 1] + similarity[i - 1][j - 1]:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs
