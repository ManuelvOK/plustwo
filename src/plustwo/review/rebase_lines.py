# SPDX-License-Identifier: Zlib
"""Lines that differ only because of changes below."""

from plustwo.diff.edits import Edit, edits_of
from plustwo.git.commits import tree_of
from plustwo.model.change import Change
from plustwo.model.place import COMMIT_MSG
from plustwo.model.version import BASE
from plustwo.review.compare import versions_to_compare


# Line numbers of both versions: (old lines, new lines)
LineSets = tuple[set[int], set[int]]


def ranges_overlap(first_begin: int, first_end: int, second_begin: int,
                   second_end: int) -> bool:
    """Tells whether two line ranges overlap. An empty range is a point
    between lines and overlaps a range it touches.

    Args:
        first_begin: the first range's first line.
        first_end: the line after the first range.
        second_begin: the second range's first line.
        second_end: the line after the second range.

    Returns:
        True if the ranges overlap.
    """
    if first_begin == first_end:
        return second_begin <= first_begin <= second_end
    if second_begin == second_end:
        return first_begin <= second_begin <= first_end
    return first_begin < second_end and second_begin < first_end


def _moved_range(begin: int, end: int, edits: list[Edit]) -> tuple[int, int, bool]:
    """Moves a line range of a parent into its child, through the child's
    own edits.

    Args:
        begin: the range's first line in the parent.
        end: the line after the range.
        edits: the child's own edits against the parent.

    Returns:
        (begin, end, is_exact). is_exact is False when the range overlaps
        one of the edits: then the result covers the range and those edits,
        since the lines can't be placed more exactly.
    """
    overlapping = [edit for edit in edits if ranges_overlap(begin, end, edit[0], edit[1])]
    lowest = min([begin] + [edit[0] for edit in overlapping])
    highest = max([end] + [edit[1] for edit in overlapping])
    shift = sum(edit[3] - edit[2] - (edit[1] - edit[0]) for edit in edits
                if edit[1] <= lowest and edit not in overlapping)
    growth = sum(edit[3] - edit[2] - (edit[1] - edit[0]) for edit in overlapping)
    return lowest + shift, highest + shift + growth, not overlapping


def rebase_lines(change: Change, first: str | None = None, second: str | None = None,
                 path: str | None = None) -> tuple[LineSets, LineSets]:
    """Finds the lines of a diff between two patch sets that differ because
    the changes below changed, i.e. the base moved, not only the change
    itself.

    It works like Gerrit: the edits between the two parents are moved
    into the patch sets' line numbers, through each patch set's own edits
    against its parent. An edit of the diff that equals one of them comes
    from below. One that only overlaps one, as where a change below and
    the change itself edited the same lines, is partly from below. Unlike
    Gerrit, which leaves those unmarked, they get marked, so no line from
    below goes unnoticed.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        path: the file's path, None for none.

    Returns:
        (lines from below, lines partly from below), see LineSets. Empty
        for the whole change against base, the commit message, and when
        the parent stayed the same.
    """
    nothing: tuple[LineSets, LineSets] = ((set(), set()), (set(), set()))
    (first, old), (_second, new) = versions_to_compare(change, first, second)
    if BASE == first or COMMIT_MSG == path or not path:
        return nothing
    if tree_of(change.repository, f'{old}^') == tree_of(change.repository, f'{new}^'):
        return nothing
    old_own = edits_of(change.repository, f'{old}^', old, path)
    new_own = edits_of(change.repository, f'{new}^', new, path)
    # The edits between the parents, in the patch sets' line numbers
    moved = []
    for old_begin, old_end, new_begin, new_end in edits_of(change.repository, f'{old}^', f'{new}^',
                                                           path):
        begin, end, is_old_exact = _moved_range(old_begin, old_end, old_own)
        other_begin, other_end, is_new_exact = _moved_range(new_begin, new_end, new_own)
        moved.append((begin, end, other_begin, other_end, is_old_exact and is_new_exact))
    # The edits of the diff that match or overlap them
    from_below: LineSets = (set(), set())
    partly: LineSets = (set(), set())
    for edit in edits_of(change.repository, old, new, path):
        if any(edit == other[:4] and other[4] for other in moved):
            lines = from_below
        elif any(ranges_overlap(edit[0], edit[1], other[0], other[1])
                 or ranges_overlap(edit[2], edit[3], other[2], other[3]) for other in moved):
            lines = partly
        else:
            continue
        lines[0].update(range(edit[0], edit[1]))
        lines[1].update(range(edit[2], edit[3]))
    return from_below, partly
