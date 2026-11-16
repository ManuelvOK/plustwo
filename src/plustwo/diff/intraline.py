# SPDX-License-Identifier: Zlib
"""Edits inside a changed line."""

import difflib


# Intraline edits are only shown for line pairs at least this similar,
# else the whole line counts as changed, like in Gerrit
INTRALINE_MIN_RATIO = 0.5


# Intraline edits this few characters apart become one, like in Gerrit
INTRALINE_COALESCE = 5


def is_word_character(character):
    """Whether a character belongs to a word or name, like in \\w."""
    return character.isalnum() or '_' == character


def widened(begin, end, text):
    """A character range grown to the words it cuts into."""
    while 0 < begin < len(text) and is_word_character(text[begin - 1]) \
            and is_word_character(text[begin]):
        begin -= 1
    while 0 < end < len(text) and is_word_character(text[end - 1]) \
            and is_word_character(text[end]):
        end += 1
    return begin, end


def merged_ranges(ranges):
    """Ranges in order with overlapping or touching ones joined."""
    result = []
    for begin, end in sorted(ranges):
        if result and begin <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((begin, end))
    return result


def whitespace_spans(text, whitespace):
    """A line as the whitespace mode compares it: (text, spans), spans
    giving each compared character's (begin, end) in the line. trailing
    drops the whitespace at the end, changed also makes each run of
    whitespace one space, all drops every whitespace.
    """
    spans = []
    compared = []
    end = len(text.rstrip()) if whitespace in ('trailing', 'changed', 'all') else len(text)
    index = 0
    while index < end:
        if text[index].isspace() and whitespace in ('changed', 'all'):
            run = index
            while run < end and text[run].isspace():
                run += 1
            if 'changed' == whitespace:
                compared.append(' ')
                spans.append((index, run))
            index = run
            continue
        compared.append(text[index])
        spans.append((index, index + 1))
        index += 1
    return ''.join(compared), spans


def intraline_edits(old, new, whitespace='none'):
    """The character ranges that differ between two lines, for each line,
    or two empty lists when the lines are too different to compare.
    See intraline_pair().
    """
    pair = intraline_pair(old, new, whitespace)
    return pair if pair is not None else ([], [])


def intraline_pair(old, new, whitespace='none'):
    """The character ranges that differ between two lines, for each line,
    or None when the lines are too different to compare.
    whitespace is one of WHITESPACE_MODES: whitespace that mode ignores
    isn't marked either. Gerrit's intraline diff doesn't look at the mode,
    here it follows it, so ignoring whitespace hides it everywhere.

    Like Gerrit's IntraLineLoader: a diff of the characters, whose edits
    are joined when at most INTRALINE_COALESCE characters lie between them,
    so a few equal characters don't split a change into pieces. Gerrit
    stops there. Here an edit also grows to the whole words it cuts into,
    so a changed name is marked whole, not as scattered letters.
    """
    (old_compared, old_spans), (new_compared, new_spans) = (
        whitespace_spans(old, whitespace), whitespace_spans(new, whitespace))
    matcher = difflib.SequenceMatcher(None, old_compared, new_compared, autojunk=False)
    if matcher.ratio() < INTRALINE_MIN_RATIO:
        return None

    def in_line(begin, end, spans):
        # A range of compared characters as one of the line
        return spans[begin][0], spans[end - 1][1]

    edits = []
    for tag, old_begin, old_end, new_begin, new_end in matcher.get_opcodes():
        if 'equal' == tag:
            continue
        if edits and (old_begin - edits[-1][1] <= INTRALINE_COALESCE
                      or new_begin - edits[-1][3] <= INTRALINE_COALESCE):
            edits[-1] = (edits[-1][0], old_end, edits[-1][2], new_end)
        else:
            edits.append((old_begin, old_end, new_begin, new_end))
    old_edits = merged_ranges(widened(*in_line(begin, end, old_spans), old)
                              for begin, end, _, _ in edits if end > begin)
    new_edits = merged_ranges(widened(*in_line(begin, end, new_spans), new)
                              for _, _, begin, end in edits if end > begin)
    return old_edits, new_edits
