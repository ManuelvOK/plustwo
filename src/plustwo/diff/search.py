# SPDX-License-Identifier: Zlib
"""Searching the lines of a diff."""


def search_matches(text, query):
    """The (begin, end) character ranges where a searched text appears in a
    line, without overlaps. Like vim's smartcase: a text in lower case
    matches any case, one with capitals only itself.
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


# Where a search in the diff looks: the lines shown, the whole file (gaps
# with matches open), the whole file and the comment threads, only the
# threads
SEARCH_MODES = ('shown', 'file', 'all', 'comments')


def gaps_with_matches(gaps, old_lines, new_lines, search):
    """The first old lines of the gaps of common lines with a match of a
    search. gaps are (first old line, first new line, number of lines).
    """
    return {old for old, new, count in gaps
            if any(search_matches(line, search)
                   for lines, first in ((old_lines, old), (new_lines, new))
                   for line in lines[first - 1:first - 1 + count])}


def comment_matches(comment, search):
    """Whether a message of a comment thread has a match of a search."""
    return any(search_matches(message.text, search) for message in comment.messages)
