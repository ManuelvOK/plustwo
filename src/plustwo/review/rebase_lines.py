# SPDX-License-Identifier: Zlib
"""Lines that differ only because of changes below."""

from plustwo.diff.edits import edits_of
from plustwo.git.commits import tree_of
from plustwo.model.place import COMMIT_MSG
from plustwo.model.version import BASE
from plustwo.review.compare import versions_to_compare


def ranges_overlap(first_begin, first_end, second_begin, second_end):
    """Whether two line ranges overlap. An empty range is a point between
    lines and overlaps a range it touches.
    """
    if first_begin == first_end:
        return second_begin <= first_begin <= second_end
    if second_begin == second_end:
        return first_begin <= second_begin <= first_end
    return first_begin < second_end and second_begin < first_end


def moved_range(begin, end, edits):
    """Moves a line range of a parent into its child, through the child's
    own edits. Returns (begin, end, exact). exact is False when the range
    overlaps one of the edits: then the result covers the range and those
    edits, since the lines can't be placed more exactly.
    """
    overlapping = [edit for edit in edits if ranges_overlap(begin, end, edit[0], edit[1])]
    low = min([begin] + [edit[0] for edit in overlapping])
    high = max([end] + [edit[1] for edit in overlapping])
    shift = sum(edit[3] - edit[2] - (edit[1] - edit[0]) for edit in edits
                if edit[1] <= low and edit not in overlapping)
    growth = sum(edit[3] - edit[2] - (edit[1] - edit[0]) for edit in overlapping)
    return low + shift, high + shift + growth, not overlapping


def rebase_lines(change, first=None, second=None, path=None, repository=None):
    """Lines of a diff between two patch sets that differ because the
    changes below changed, i.e. the base moved, not only the change itself.
    Returns ((old lines, new lines) from below, (old lines, new lines)
    partly from below).

    It works like Gerrit: the edits between the two parents are moved
    into the patch sets' line numbers, through each patch set's own edits
    against its parent. An edit of the diff that equals one of them comes
    from below. One that only overlaps one, e.g. where a change below and
    the change itself edited the same lines, is partly from below. Unlike
    Gerrit, which leaves those unmarked, they get marked, so no line from
    below goes unnoticed.

    Empty for the whole change against base, the commit message, and when
    the parent stayed the same.
    """
    nothing = ((set(), set()), (set(), set()))
    (first, old), (_second, new) = versions_to_compare(change, first, second)
    if BASE == first or COMMIT_MSG == path or not path:
        return nothing
    if tree_of(f'{old}^', repository) == tree_of(f'{new}^', repository):
        return nothing
    old_own = edits_of(f'{old}^', old, path, repository)
    new_own = edits_of(f'{new}^', new, path, repository)
    moved = []
    for old_begin, old_end, new_begin, new_end in edits_of(f'{old}^', f'{new}^', path, repository):
        begin, end, old_exact = moved_range(old_begin, old_end, old_own)
        other_begin, other_end, new_exact = moved_range(new_begin, new_end, new_own)
        moved.append((begin, end, other_begin, other_end, old_exact and new_exact))

    from_below, partly = (set(), set()), (set(), set())
    for edit in edits_of(old, new, path, repository):
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
