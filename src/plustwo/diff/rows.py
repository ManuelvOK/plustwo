# SPDX-License-Identifier: Zlib
"""Side by side rows of a diff."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from plustwo.diff.edits import Edit
from plustwo.diff.intraline import intraline_pair
from plustwo.diff.moved import moved_lines
from plustwo.diff.pairing import LinePair, paired_lines
from plustwo.diff.reflow import reflowed


# Types of the halves of a side by side row
CONTEXT = 'context'
REMOVED = 'removed'
ADDED = 'added'
EMPTY = 'empty'


@dataclass
class Half:
    """One side of a side by side row: a line of one version of the file,
    or nothing (EMPTY) where the other side has more lines.

    Attributes:
        type: CONTEXT, REMOVED, ADDED or EMPTY.
        number: the line's number in its version, None for EMPTY.
        text: the line's text without its line end.
        edits: the (begin, end) character ranges an intraline diff found.
        is_moved: whether a removed line comes back elsewhere, or an added
            one was elsewhere, see moved_lines().
        is_whole: whether a removed or added line is new text all over:
            without a partner, or too different from it for intraline
            edits. The interfaces give it the strong shade, a paired line
            only on its edits.
        is_reflowed: whether the line is part of a reflowed block, see
            reflowed(): its words stay, only the line breaks moved. Its
            edits are the words that really changed. The interfaces show
            it like a moved line.
    """
    type: str
    number: int | None = None
    text: str = ''
    edits: list[tuple[int, int]] = field(default_factory=list)
    is_moved: bool = False
    is_whole: bool = False
    is_reflowed: bool = False


@dataclass
class Row:
    """A row of a side by side diff: two halves, or a gap of common lines
    left out.

    Attributes:
        old: the half of the old version.
        new: the half of the new version.
        n_skipped: the number of common lines left out, 0 for a row with
            lines. In a gap row, the halves give the first line of the gap.
    """
    old: Half
    new: Half
    n_skipped: int = 0


def side_by_side_rows(old_lines: Sequence[str], new_lines: Sequence[str], edits: list[Edit],
                      context: int | None = None, whitespace: str = 'none',
                      shown: tuple[Iterable[int], Iterable[int]] = ((), ())) -> list[Row]:
    """Lays out two versions of a file side by side, like Gerrit's diff
    view. Each edit's removed and added lines are paired by similarity
    (see paired_lines()), a line without a partner gets an empty half.
    Longer runs of common lines become one gap row.

    Args:
        old_lines: the lines of the old version.
        new_lines: the lines of the new version.
        edits: git's edits between the two, as edits_of() finds them.
        context: the number of common lines kept around each edit, None for
            the whole file.
        whitespace: the whitespace mode of the edits, one of
            WHITESPACE_MODES. The intraline edits follow it.
        shown: (old line numbers, new line numbers) that stay visible with
            context around them, though no edit is near, like the lines
            with threads in Gerrit's diff view. A gap splits around them.

    Returns:
        The rows, top to bottom.
    """
    rows: list[Row] = []
    old_moved, new_moved = moved_lines(old_lines, new_lines, edits)
    old_shown, new_shown = set(shown[0]), set(shown[1])

    def add_common(old_begin: int, new_begin: int, n_lines: int, n_kept_before: int,
                   n_kept_after: int) -> None:
        """Adds the rows of a run of common lines. Lines beyond the kept
        ones at the start and the end form a gap, except those near a line
        to show.

        Args:
            old_begin: the index of the run's first line in old_lines.
            new_begin: the index of the run's first line in new_lines.
            n_lines: the number of lines in the run.
            n_kept_before: how many lines at the start stay visible.
            n_kept_after: how many lines at the end stay visible.
        """
        is_visible = [context is None or offset < n_kept_before
                      or offset >= n_lines - n_kept_after for offset in range(n_lines)]
        # Context around the lines to show
        for offset in range(n_lines):
            if old_begin + offset + 1 in old_shown or new_begin + offset + 1 in new_shown:
                for near in range(max(offset - context, 0) if context is not None else 0,
                                  min(offset + (context or 0) + 1, n_lines)):
                    is_visible[near] = True
        # A row per visible line, a gap row per run of hidden ones
        offset = 0
        while offset < n_lines:
            if is_visible[offset]:
                rows.append(Row(Half(CONTEXT, old_begin + offset + 1,
                                     old_lines[old_begin + offset]),
                                Half(CONTEXT, new_begin + offset + 1,
                                     new_lines[new_begin + offset])))
                offset += 1
                continue
            end = offset
            while end < n_lines and not is_visible[end]:
                end += 1
            rows.append(Row(Half(CONTEXT, old_begin + offset + 1),
                            Half(CONTEXT, new_begin + offset + 1), n_skipped=end - offset))
            offset = end

    old_at = new_at = 0
    for old_begin, old_end, new_begin, new_end in edits:
        n_kept = 0 if context is None else context
        add_common(old_at, new_at, old_begin - 1 - old_at, n_kept if rows else 0, n_kept)
        rows += _edit_rows(old_lines, new_lines, (old_begin, old_end, new_begin, new_end),
                           (old_moved, new_moved), whitespace)
        old_at, new_at = old_end - 1, new_end - 1
    add_common(old_at, new_at, len(old_lines) - old_at, 0 if context is None else context, 0)
    return rows


def _edit_rows(old_lines: Sequence[str], new_lines: Sequence[str], edit: Edit,
               moved: tuple[set[int], set[int]], whitespace: str) -> list[Row]:
    """Lays out the lines of one edit side by side, paired by similarity,
    or a reflowed paragraph by where its words went, see reflowed().

    Args:
        old_lines: the lines of the old version.
        new_lines: the lines of the new version.
        edit: the edit, see edits_of().
        moved: (old line numbers, new line numbers) that moved, see
            moved_lines().
        whitespace: the whitespace mode, one of WHITESPACE_MODES.

    Returns:
        A row per pair of lines and per line without a partner.
    """
    old_begin, old_end, new_begin, new_end = edit
    removed = range(old_begin - 1, old_end - 1)
    added = range(new_begin - 1, new_end - 1)
    rows = []
    for old_part, new_part in _parts(old_lines, new_lines, removed, added):
        old_texts = [old_lines[line] for line in old_part]
        new_texts = [new_lines[line] for line in new_part]
        reflow = reflowed(old_texts, new_texts)
        pairs: list[LinePair] = reflow.pairs if reflow else paired_lines(old_texts, new_texts)
        for old_index, new_index in pairs:
            old = (Half(REMOVED, old_part[old_index] + 1, old_texts[old_index])
                   if old_index is not None else Half(EMPTY))
            new = (Half(ADDED, new_part[new_index] + 1, new_texts[new_index])
                   if new_index is not None else Half(EMPTY))
            old.is_moved, new.is_moved = old.number in moved[0], new.number in moved[1]
            if reflow:
                for half, index, edits in ((old, old_index, reflow.old_edits),
                                           (new, new_index, reflow.new_edits)):
                    if index is not None:
                        half.is_reflowed, half.edits = True, edits[index]
                rows.append(Row(old, new))
                continue
            pair = (intraline_pair(old.text, new.text, whitespace)
                    if old.number and new.number else None)
            if pair is not None:
                old.edits, new.edits = pair
            old.is_whole = old.number is not None and pair is None
            new.is_whole = new.number is not None and pair is None
            rows.append(Row(old, new))
    return rows


def _parts(old_lines: Sequence[str], new_lines: Sequence[str], removed: range, added: range
           ) -> list[tuple[range, range]]:
    """Splits an edit into its paragraphs, so a reflowed one is found next
    to a rewritten one. Only when a part is reflowed and both sides have
    the same runs of blank and other lines. Else the edit stays whole, and
    its lines pair as before.

    Args:
        old_lines: the lines of the old version.
        new_lines: the lines of the new version.
        removed: the indexes of the edit's removed lines.
        added: the indexes of the edit's added lines.

    Returns:
        (removed indexes, added indexes) per part, in order.
    """
    whole = [(removed, added)]
    if reflowed([old_lines[line] for line in removed], [new_lines[line] for line in added]):
        return whole
    old_runs, new_runs = _runs(old_lines, removed), _runs(new_lines, added)
    if len(old_runs) < 2 or [is_blank for is_blank, _ in old_runs] \
            != [is_blank for is_blank, _ in new_runs]:
        return whole
    parts = [(old_run, new_run) for (_, old_run), (_, new_run) in zip(old_runs, new_runs)]
    if not any(reflowed([old_lines[line] for line in old_part],
                        [new_lines[line] for line in new_part]) for old_part, new_part in parts):
        return whole
    return parts


def _runs(lines: Sequence[str], indexes: range) -> list[tuple[bool, range]]:
    """Splits lines into runs of blank and of other lines.

    Args:
        lines: the lines of a version.
        indexes: the indexes of the lines to split.

    Returns:
        (whether the run is blank, its indexes) per run, in order.
    """
    runs: list[tuple[bool, range]] = []
    for index in indexes:
        is_blank = not lines[index].strip()
        if runs and runs[-1][0] == is_blank:
            runs[-1] = (is_blank, range(runs[-1][1].start, index + 1))
        else:
            runs.append((is_blank, range(index, index + 1)))
    return runs


def expand_gaps(rows: list[Row], starts: set[int] | frozenset[int],
                old_lines: Sequence[str], new_lines: Sequence[str]) -> list[Row]:
    """Opens gaps into their common lines.

    Args:
        rows: the rows, see side_by_side_rows().
        starts: the old line numbers where the gaps to open start.
        old_lines: the lines of the old version.
        new_lines: the lines of the new version.

    Returns:
        The rows with those gaps replaced by a row per line.
    """
    expanded = []
    for row in rows:
        if not row.n_skipped or row.old.number not in starts:
            expanded.append(row)
            continue
        assert row.old.number is not None and row.new.number is not None
        for offset in range(row.n_skipped):
            old, new = row.old.number + offset, row.new.number + offset
            expanded.append(Row(Half(CONTEXT, old, old_lines[old - 1]),
                                Half(CONTEXT, new, new_lines[new - 1])))
    return expanded
