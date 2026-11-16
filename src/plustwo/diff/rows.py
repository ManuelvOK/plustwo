# SPDX-License-Identifier: Zlib
"""Side by side rows of a diff."""

from dataclasses import dataclass, field

from plustwo.diff.intraline import intraline_pair
from plustwo.diff.moved import moved_lines
from plustwo.diff.pairing import paired_lines


# Kinds of the halves of a side by side row
CONTEXT = 'context'


REMOVED = 'removed'


ADDED = 'added'


EMPTY = 'empty'


@dataclass
class Half:
    """One side of a side by side row: a line of one version of the file,
    or nothing (EMPTY, number None) where the other side has more lines.
    edits are the (begin, end) character ranges an intraline diff found.
    """
    kind: str
    number: int | None = None
    text: str = ''
    edits: list[tuple[int, int]] = field(default_factory=list)
    # A removed line that comes back elsewhere, or an added one that was
    # elsewhere, see moved_lines()
    moved: bool = False
    # A removed or added line that is new text all over: without a
    # partner, or too different from it for intraline edits. The
    # interfaces give it the strong shade, a paired line only on its edits.
    whole: bool = False


@dataclass
class Row:
    """A row of a side by side diff: two halves, or a gap of common lines
    left out (skipped > 0, the halves give the first line of the gap).
    """
    old: Half
    new: Half
    skipped: int = 0


def side_by_side_rows(old_lines, new_lines, edits, context=None, whitespace='none',
                      shown=((), ())):
    """Lays out two versions of a file side by side, like Gerrit's diff
    view. edits are git's, as edits_of() gives them. Each edit's removed
    and added lines are paired by similarity (see paired_lines()), a line
    without a partner gets an empty half. context is the number of common
    lines kept around each edit, None for the whole file. Longer runs of
    common lines become one gap row. whitespace is the mode of the edits,
    the intraline edits follow it. shown are (old lines, new lines) that
    stay visible with context around them, though no edit is near, like
    the lines with threads in Gerrit's diff view: a gap splits around them.
    """
    rows = []
    old_moved, new_moved = moved_lines(old_lines, new_lines, edits)
    old_shown, new_shown = set(shown[0]), set(shown[1])

    def common(old_begin, new_begin, count, keep_before, keep_after):
        # Common lines from old_begin and new_begin, counted from 0. Lines
        # beyond keep_before at the start and keep_after at the end form a
        # gap, except those near a line to show.
        visible = [context is None or offset < keep_before or offset >= count - keep_after
                   for offset in range(count)]
        for offset in range(count):
            if old_begin + offset + 1 in old_shown or new_begin + offset + 1 in new_shown:
                for near in range(max(offset - context, 0) if context is not None else 0,
                                  min(offset + (context or 0) + 1, count)):
                    visible[near] = True
        offset = 0
        while offset < count:
            if visible[offset]:
                rows.append(Row(Half(CONTEXT, old_begin + offset + 1,
                                     old_lines[old_begin + offset]),
                                Half(CONTEXT, new_begin + offset + 1,
                                     new_lines[new_begin + offset])))
                offset += 1
                continue
            end = offset
            while end < count and not visible[end]:
                end += 1
            rows.append(Row(Half(CONTEXT, old_begin + offset + 1),
                            Half(CONTEXT, new_begin + offset + 1), skipped=end - offset))
            offset = end

    old_at = new_at = 0
    for old_begin, old_end, new_begin, new_end in edits:
        keep = 0 if context is None else context
        common(old_at, new_at, old_begin - 1 - old_at, keep if rows else 0, keep)
        removed = range(old_begin - 1, old_end - 1)
        added = range(new_begin - 1, new_end - 1)
        for old_index, new_index in paired_lines([old_lines[line] for line in removed],
                                                 [new_lines[line] for line in added]):
            old = (Half(REMOVED, removed[old_index] + 1, old_lines[removed[old_index]])
                   if old_index is not None else Half(EMPTY))
            new = (Half(ADDED, added[new_index] + 1, new_lines[added[new_index]])
                   if new_index is not None else Half(EMPTY))
            old.moved, new.moved = old.number in old_moved, new.number in new_moved
            pair = (intraline_pair(old.text, new.text, whitespace)
                    if old.number and new.number else None)
            if pair is not None:
                old.edits, new.edits = pair
            old.whole, new.whole = old.number is not None and pair is None, \
                new.number is not None and pair is None
            rows.append(Row(old, new))
        old_at, new_at = old_end - 1, new_end - 1
    common(old_at, new_at, len(old_lines) - old_at, 0 if context is None else context, 0)
    return rows


def expand_gaps(rows, starts, old_lines, new_lines):
    """The rows with the gaps that start at these old lines opened into
    their common lines.
    """
    result = []
    for row in rows:
        if not row.skipped or row.old.number not in starts:
            result.append(row)
            continue
        for offset in range(row.skipped):
            old, new = row.old.number + offset, row.new.number + offset
            result.append(Row(Half(CONTEXT, old, old_lines[old - 1]),
                              Half(CONTEXT, new, new_lines[new - 1])))
    return result
