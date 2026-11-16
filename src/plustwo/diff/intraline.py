# SPDX-License-Identifier: Zlib
"""Edits inside a changed line."""

import difflib
from collections.abc import Iterable


# A range of characters in a line: (begin, end), the end not included
CharacterRange = tuple[int, int]

# Intraline edits are only shown for line pairs at least this similar,
# else the whole line counts as changed, like in Gerrit
INTRALINE_MIN_RATIO = 0.5

# Intraline edits this few characters apart become one, like in Gerrit
_INTRALINE_COALESCE = 5


def _is_word_character(character: str) -> bool:
    """Tells whether a character belongs to a word or name, like in \\w.

    Args:
        character: the character.

    Returns:
        True for letters, digits and the underscore.
    """
    return character.isalnum() or '_' == character


def _widened(begin: int, end: int, text: str) -> CharacterRange:
    """Grows a character range to the words it cuts into.

    Args:
        begin: the range's first character.
        end: the character after the range.
        text: the line the range is in.

    Returns:
        The grown range.
    """
    while 0 < begin < len(text) and _is_word_character(text[begin - 1]) \
            and _is_word_character(text[begin]):
        begin -= 1
    while 0 < end < len(text) and _is_word_character(text[end - 1]) \
            and _is_word_character(text[end]):
        end += 1
    return begin, end


def _merged_ranges(ranges: Iterable[CharacterRange]) -> list[CharacterRange]:
    """Joins overlapping and touching ranges.

    Args:
        ranges: the ranges, in any order.

    Returns:
        The joined ranges, in order.
    """
    merged: list[CharacterRange] = []
    for begin, end in sorted(ranges):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((begin, end))
    return merged


def _whitespace_spans(text: str, whitespace: str) -> tuple[str, list[CharacterRange]]:
    """Writes a line as a whitespace mode compares it. trailing drops the
    whitespace at the end, changed also makes each run of whitespace one
    space, all drops every whitespace.

    Args:
        text: the line.
        whitespace: one of WHITESPACE_MODES.

    Returns:
        (compared text, spans), the spans giving each compared character's
        range in the line.
    """
    spans = []
    compared = []
    end = len(text.rstrip()) if whitespace in ('trailing', 'changed', 'all') else len(text)
    index = 0
    while index < end:
        if text[index].isspace() and whitespace in ('changed', 'all'):
            run_end = index
            while run_end < end and text[run_end].isspace():
                run_end += 1
            if 'changed' == whitespace:
                compared.append(' ')
                spans.append((index, run_end))
            index = run_end
            continue
        compared.append(text[index])
        spans.append((index, index + 1))
        index += 1
    return ''.join(compared), spans


def intraline_edits(old: str, new: str, whitespace: str = 'none'
                    ) -> tuple[list[CharacterRange], list[CharacterRange]]:
    """Finds the character ranges that differ between two lines, see
    intraline_pair().

    Args:
        old: the old line.
        new: the new line.
        whitespace: one of WHITESPACE_MODES.

    Returns:
        (old line's ranges, new line's ranges). Both empty when the lines
        are too different to compare.
    """
    pair = intraline_pair(old, new, whitespace)
    return pair if pair is not None else ([], [])


def intraline_pair(old: str, new: str, whitespace: str = 'none'
                   ) -> tuple[list[CharacterRange], list[CharacterRange]] | None:
    """Finds the character ranges that differ between two lines.

    Like Gerrit's IntraLineLoader: a diff of the characters, whose edits
    are joined when at most _INTRALINE_COALESCE characters lie between them,
    so a few equal characters don't split a change into pieces. Gerrit
    stops there. Here an edit also grows to the whole words it cuts into,
    so a changed name is marked whole, not as scattered letters.

    Gerrit's intraline diff doesn't look at the whitespace mode. Here it
    follows it, so ignoring whitespace hides it everywhere.

    Args:
        old: the old line.
        new: the new line.
        whitespace: one of WHITESPACE_MODES. Whitespace the mode ignores
            isn't marked either.

    Returns:
        (old line's ranges, new line's ranges), None when the lines are too
        different to compare.
    """
    (old_compared, old_spans), (new_compared, new_spans) = (
        _whitespace_spans(old, whitespace), _whitespace_spans(new, whitespace))
    matcher = difflib.SequenceMatcher(None, old_compared, new_compared, autojunk=False)
    if matcher.ratio() < INTRALINE_MIN_RATIO:
        return None

    def in_line(begin: int, end: int, spans: list[CharacterRange]) -> CharacterRange:
        """Converts a range of compared characters into one of the line.

        Args:
            begin: the range's first compared character.
            end: the compared character after the range.
            spans: the compared characters' ranges in the line.

        Returns:
            The range in the line.
        """
        return spans[begin][0], spans[end - 1][1]

    # The edits of the characters, close ones joined
    edits: list[tuple[int, int, int, int]] = []
    for tag, old_begin, old_end, new_begin, new_end in matcher.get_opcodes():
        if 'equal' == tag:
            continue
        if edits and (old_begin - edits[-1][1] <= _INTRALINE_COALESCE
                      or new_begin - edits[-1][3] <= _INTRALINE_COALESCE):
            edits[-1] = (edits[-1][0], old_end, edits[-1][2], new_end)
        else:
            edits.append((old_begin, old_end, new_begin, new_end))
    old_edits = _merged_ranges(_widened(*in_line(begin, end, old_spans), old)
                               for begin, end, _, _ in edits if end > begin)
    new_edits = _merged_ranges(_widened(*in_line(begin, end, new_spans), new)
                               for _, _, begin, end in edits if end > begin)
    return old_edits, new_edits
