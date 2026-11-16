# SPDX-License-Identifier: Zlib
"""The rows of a file's diff between two versions of a change."""

from plustwo.diff.edits import edits_of
from plustwo.diff.rows import side_by_side_rows
from plustwo.git.files import file_lines
from plustwo.model.place import OLD_SIDE
from plustwo.review.compare import diff_endpoints, old_path_of
from plustwo.review.porting import thread_place


def file_diff_rows(change, first=None, second=None, path=None, context=None, repository=None,
                   whitespace='none', shown=None):
    """The side by side rows of one file's diff between two versions of a
    change, None if the file is binary. A renamed file is diffed against its
    old path. whitespace is one of WHITESPACE_MODES. The lines with threads
    stay visible with context, like in Gerrit, see thread_lines(). shown
    gives them if known.
    """
    if shown is None:
        shown = thread_lines(change, first, second, path, repository)
    old, new, paths = diff_endpoints(change, first, second, path, repository)
    old_path = old_path_of(change, first, second, paths[0], repository)
    old_lines = file_lines(old, old_path or paths[0], repository)
    new_lines = file_lines(new, paths[0], repository)
    if old_lines is None or new_lines is None:
        return None
    return side_by_side_rows(old_lines, new_lines,
                             edits_of(old, new, paths[0], repository, old_path, whitespace),
                             context, whitespace, shown)


def thread_lines(change, first=None, second=None, path=None, repository=None):
    """The lines of a file the diff of two versions has threads on, see
    thread_place(): (old lines, new lines), as frozen sets.
    """
    lines = (set(), set())
    for comment in change.comments:
        shown = thread_place(change, comment, first, second, repository)
        if shown.path == path and shown.line is not None:
            lines[OLD_SIDE == shown.side].add(shown.line)
    return frozenset(lines[0]), frozenset(lines[1])
