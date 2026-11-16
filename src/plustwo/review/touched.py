# SPDX-License-Identifier: Zlib
"""Threads that the compared versions touch."""

import time
from collections.abc import Sequence

from plustwo.diff.edits import edits_of
from plustwo.git.errors import ChangeError
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.place import OLD_SIDE, PATCHSET_LEVEL, split_place
from plustwo.model.roles import REVIEWER
from plustwo.model.version import BASE
from plustwo.review.compare import compared_files, diff_endpoints, old_path_of, versions_to_compare
from plustwo.review.labels import threads_in_list_order
from plustwo.review.porting import thread_place
from plustwo.review.rebase_lines import ranges_overlap


def _left_side_time(change: Change, first: str | None = None,
                    second: str | None = None) -> int:
    """Finds when the left version of a comparison was uploaded.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        The upload time, seconds since 1970. For base, the upload of the
        patch set before the right one (0 for the first), so messages since
        then count as new, as against that patch set. The current commit on
        the right is newer than the latest patch set. A current commit on
        the left counts as uploaded now.
    """
    (left, _), (right, _) = versions_to_compare(change, first, second)
    if left.isdigit():
        return change.uploads.get(int(left), 0)
    if BASE != left:
        return int(time.time())
    before = int(right) - 1 if right.isdigit() else change.latest_patch_set()
    return change.uploads.get(before, 0)


def _is_touched(change: Change, comment: Comment, first: str | None = None,
                second: str | None = None) -> bool:
    """Tells whether a thread is touched in the comparison of two versions:
    it has a sent message newer than the left version's upload, or its line
    is in code the comparison changes. A thread on a whole file is touched
    when the comparison changes the file, one on the whole change only by
    messages. Untouched threads are what the comparison leaves as it was.

    Args:
        change: the change.
        comment: the thread.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        True if the thread is touched.
    """
    since = _left_side_time(change, first, second)
    if any(message.sent and message.time > since for message in comment.messages):
        return True
    shown = thread_place(change, comment, first, second)
    if PATCHSET_LEVEL == shown.path:
        return False
    if shown.line is None:
        return shown.path in compared_files(change, first, second)
    old, new, paths = diff_endpoints(change, first, second, shown.path)
    old_path = old_path_of(change, first, second, paths[0])
    edits = edits_of(change.repository, old, new, paths[0], old_path)
    line, is_old_side = shown.line, OLD_SIDE == shown.side
    return any(ranges_overlap(line, line + 1, *(edit[:2] if is_old_side else edit[2:]))
               for edit in edits)


def touched_threads(change: Change, first: str | None = None,
                    second: str | None = None) -> set[int]:
    """Finds the threads touched in a comparison, see _is_touched().

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        The threads' numbers. Empty when the versions can't be compared.
    """
    try:
        return {comment.number for comment in change.comments
                if _is_touched(change, comment, first, second)}
    except ChangeError:
        return set()


def _waits_for(comment: Comment, viewer: str = REVIEWER) -> bool:
    """Tells whether an unresolved thread waits for the viewer: the other
    side wrote its last sent message.

    Args:
        comment: the thread.
        viewer: who looks, REVIEWER or AUTHOR.

    Returns:
        True if the thread waits for the viewer.
    """
    last_sent = comment.last_sent()
    return comment.is_unresolved() and last_sent is not None and viewer != last_sent.author


def relevant_threads(change: Change, first: str | None = None, second: str | None = None,
                     viewer: str = REVIEWER) -> set[int]:
    """Finds the threads worth a look in a comparison: touched ones (see
    _is_touched()) and unresolved ones waiting for the viewer.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        viewer: who looks, REVIEWER or AUTHOR.

    Returns:
        The threads' numbers.
    """
    return touched_threads(change, first, second) | {
        comment.number for comment in change.comments if _waits_for(comment, viewer)}


def threads_by_file(change: Change, files: Sequence[str], first: str | None = None,
                    second: str | None = None) -> list[tuple[str, list[Comment]]]:
    """Groups the threads by the file the comparison shows them on.

    Args:
        change: the change.
        files: the file list's paths, COMMIT_MSG first.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        (path, threads) per file in the order of files. Threads on the
        whole change come first, under PATCHSET_LEVEL, also those on a file
        the version doesn't have. Files without threads are left out. In a
        group, the threads are in list order, see threads_in_list_order().
    """
    groups: dict[str, list[Comment]] = {}
    for comment in change.comments:
        try:
            path = thread_place(change, comment, first, second).path
        except ChangeError:
            path = split_place(comment.place)[0]
        groups.setdefault(path, []).append(comment)
    order = [PATCHSET_LEVEL] + [path for path in files if PATCHSET_LEVEL != path]
    order += sorted(path for path in groups if path not in order)
    return [(path, threads_in_list_order(groups[path])) for path in order if path in groups]
