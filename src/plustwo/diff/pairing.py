# SPDX-License-Identifier: Zlib
"""Pairing removed and added lines by similarity."""

import difflib
import functools

from plustwo.diff.intraline import INTRALINE_MIN_RATIO
from plustwo.git.cache import CACHE_SIZE


# Edits with more removed times added lines than this pair them by their
# order, the search for similar lines would take too long.
PAIRING_MAX_CELLS = 2500


def line_similarity(old, new):
    """How alike two lines are, 0 to 1, see difflib's ratio()."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    if matcher.real_quick_ratio() < INTRALINE_MIN_RATIO \
            or matcher.quick_ratio() < INTRALINE_MIN_RATIO:
        return 0
    return matcher.ratio()


def paired_lines(removed, added):
    """See _paired_lines(), kept for the same lines."""
    return _paired_lines(tuple(removed), tuple(added))


@functools.lru_cache(maxsize=CACHE_SIZE)
def _paired_lines(removed, added):
    """Pairs the removed and added lines of one edit by similarity, in
    order: [(index into removed or None, index into added or None)].

    The pairs are the ones with the most similarity in all, only lines at
    least INTRALINE_MIN_RATIO alike pair. The others stand alone, the
    removed ones of a run before the added ones. Like delta, unlike
    Gerrit, which pairs by position: then a rewritten block shows
    unrelated lines side by side. Big edits still pair by position.
    """
    if len(removed) * len(added) > PAIRING_MAX_CELLS or not removed or not added:
        return [(index if index < len(removed) else None, index if index < len(added) else None)
                for index in range(max(len(removed), len(added)))]
    similarity = [[line_similarity(old, new) for new in added] for old in removed]
    # best[i][j]: the most similarity of the first i removed and j added lines
    best = [[0.0] * (len(added) + 1) for _ in range(len(removed) + 1)]
    for i in range(1, len(removed) + 1):
        for j in range(1, len(added) + 1):
            best[i][j] = max(best[i - 1][j], best[i][j - 1],
                             best[i - 1][j - 1] + similarity[i - 1][j - 1]
                             if similarity[i - 1][j - 1] else 0)
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
    result = []
    old_at = new_at = 0
    for old, new in pairs + [(len(removed), len(added))]:
        result += [(index, None) for index in range(old_at, old)]
        result += [(None, index) for index in range(new_at, new)]
        if old < len(removed):
            result.append((old, new))
        old_at, new_at = old + 1, new + 1
    return result
