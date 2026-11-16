# SPDX-License-Identifier: Zlib
"""Threads that the compared versions touch."""

import time

from plustwo.diff.edits import edits_of
from plustwo.git.errors import ChangeError
from plustwo.model.place import OLD_SIDE, PATCHSET_LEVEL, split_place
from plustwo.model.roles import REVIEWER
from plustwo.model.version import BASE
from plustwo.review.compare import compared_files, diff_endpoints, old_path_of, versions_to_compare
from plustwo.review.labels import threads_in_list_order
from plustwo.review.porting import thread_place
from plustwo.review.rebase_lines import ranges_overlap


def left_side_time(change, first=None, second=None):
    """When the left version of a comparison was uploaded. For base, the
    upload of the patch set before the right one (0 for the first), so
    messages since then count as new, as against that patch set. The
    current commit on the right is newer than the latest patch set. A
    current commit on the left counts as uploaded now.
    """
    (left, _), (right, _) = versions_to_compare(change, first, second)
    if left.isdigit():
        return change.uploads.get(int(left), 0)
    if BASE != left:
        return int(time.time())
    before = int(right) - 1 if right.isdigit() else change.latest_patch_set()
    return change.uploads.get(before, 0)


def is_touched(change, comment, first=None, second=None, repository=None):
    """Whether a thread is touched in the comparison of two versions: it
    has a sent message newer than the left version's upload, or its line
    is in code the comparison changes. A thread on a whole file is touched
    when the comparison changes the file, one on the whole change only by
    messages. Untouched threads are what the comparison leaves as it was.
    """
    since = left_side_time(change, first, second)
    if any(message.sent and message.time > since for message in comment.messages):
        return True
    shown = thread_place(change, comment, first, second, repository)
    if PATCHSET_LEVEL == shown.path:
        return False
    if shown.line is None:
        return shown.path in compared_files(change, first, second, repository)
    old, new, paths = diff_endpoints(change, first, second, shown.path, repository)
    old_path = old_path_of(change, first, second, paths[0], repository)
    edits = edits_of(old, new, paths[0], repository, old_path)
    line, old_side = shown.line, OLD_SIDE == shown.side
    return any(ranges_overlap(line, line + 1, *(edit[:2] if old_side else edit[2:]))
               for edit in edits)


def touched_threads(change, first=None, second=None, repository=None):
    """The numbers of the threads touched in a comparison, see
    is_touched(). Empty when the versions can't be compared.
    """
    try:
        return {comment.number for comment in change.comments
                if is_touched(change, comment, first, second, repository)}
    except ChangeError:
        return set()


def waits_for(comment, viewer=REVIEWER):
    """Whether an unresolved thread waits for the viewer: the other side
    wrote its last sent message.
    """
    last = comment.last_sent()
    return comment.is_unresolved() and last is not None and viewer != last.author


def relevant_threads(change, first=None, second=None, viewer=REVIEWER, repository=None):
    """The numbers of the threads worth a look in a comparison: touched
    ones (see is_touched()) and unresolved ones waiting for the viewer.
    """
    return touched_threads(change, first, second, repository) | {
        comment.number for comment in change.comments if waits_for(comment, viewer)}


def threads_by_file(change, files, first=None, second=None, repository=None):
    """The threads grouped by the file the comparison shows them on, in
    the order of files (the file list, /COMMIT_MSG first): [(path,
    threads)]. Threads on the whole change come first, under
    PATCHSET_LEVEL, also those on a file the version doesn't have. Files
    without threads are left out. In a group, the threads are in list
    order, see threads_in_list_order().
    """
    groups = {}
    for comment in change.comments:
        try:
            path = thread_place(change, comment, first, second, repository).path
        except ChangeError:
            path = split_place(comment.place)[0]
        groups.setdefault(path, []).append(comment)
    order = [PATCHSET_LEVEL] + [path for path in files if PATCHSET_LEVEL != path]
    order += sorted(path for path in groups if path not in order)
    return [(path, threads_in_list_order(groups[path])) for path in order if path in groups]
