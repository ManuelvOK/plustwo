# SPDX-License-Identifier: Zlib
"""Searching the lines of a diff."""

from collections.abc import Iterable, Sequence
from typing import Protocol


# Where a search in the diff looks: the lines shown, the whole file (gaps
# with matches open), the whole file and the comment threads, only the
# threads
SEARCH_MODES = ('shown', 'file', 'all', 'comments')


class _HasText(Protocol):
    """Anything with a text, like a message of a thread."""

    @property
    def text(self) -> str:
        """The text."""


class _HasMessages(Protocol):
    """Anything with messages, like a comment thread."""

    @property
    def messages(self) -> Iterable[_HasText]:
        """The messages."""


def search_matches(text: str, query: str) -> list[tuple[int, int]]:
    """Finds where a searched text appears in a line, like vim's
    smartcase: a query in lower case matches any case, one with capitals
    only itself.

    Args:
        text: the line.
        query: the searched text.

    Returns:
        (begin, end) character ranges, in order and without overlaps.
    """
    if not query:
        return []
    if query == query.lower():
        text = text.lower()
    matches = []
    begin = text.find(query)
    while begin >= 0:
        matches.append((begin, begin + len(query)))
        begin = text.find(query, begin + len(query))
    return matches


def gaps_with_matches(gaps: Iterable[tuple[int, int, int]], old_lines: Sequence[str],
                      new_lines: Sequence[str], query: str) -> set[int]:
    """Finds the gaps of common lines that have a match of a search.

    Args:
        gaps: (first old line, first new line, number of lines) per gap.
        old_lines: the lines of the old version.
        new_lines: the lines of the new version.
        query: the searched text.

    Returns:
        The first old lines of the gaps with a match.
    """
    return {old for old, new, n_lines in gaps
            if any(search_matches(line, query)
                   for lines, first in ((old_lines, old), (new_lines, new))
                   for line in lines[first - 1:first - 1 + n_lines])}


def comment_matches(comment: _HasMessages, query: str) -> bool:
    """Tells whether a message of a comment thread has a match of a search.

    Args:
        comment: the thread.
        query: the searched text.

    Returns:
        True if any message's text matches.
    """
    return any(search_matches(message.text, query) for message in comment.messages)
