# SPDX-License-Identifier: Zlib
"""The rows of a file's diff between two versions of a change."""

from plustwo.diff.edits import edits_of
from plustwo.diff.rows import side_by_side_rows
from plustwo.diff.rows import Row
from plustwo.git.files import file_lines
from plustwo.model.change import Change
from plustwo.model.place import OLD_SIDE
from plustwo.review.compare import diff_endpoints, old_path_of
from plustwo.review.porting import thread_place


def file_diff_rows(change: Change, first: str | None = None, second: str | None = None,
                   path: str | None = None, context: int | None = None,
                   repository: str | None = None, whitespace: str = 'none',
                   shown: tuple[frozenset[int], frozenset[int]] | None = None
                   ) -> list[Row] | None:
    """Lays out one file's diff between two versions of a change side by
    side. A renamed file is diffed against its old path. The lines with
    threads stay visible with context, like in Gerrit.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        path: the file's path in the right version.
        context: the number of common lines kept around each edit, None for
            the whole file.
        repository: the repository's directory, None for the current one.
        whitespace: one of WHITESPACE_MODES.
        shown: the lines with threads, see thread_lines(). None to find
            them.

    Returns:
        The rows, see side_by_side_rows(). None if the file is binary.
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


def thread_lines(change: Change, first: str | None = None, second: str | None = None,
                 path: str | None = None,
                 repository: str | None = None) -> tuple[frozenset[int], frozenset[int]]:
    """Finds the lines of a file the diff of two versions has threads on,
    see thread_place().

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        path: the file's path in the right version.
        repository: the repository's directory, None for the current one.

    Returns:
        (old line numbers, new line numbers).
    """
    lines: tuple[set[int], set[int]] = (set(), set())
    for comment in change.comments:
        shown = thread_place(change, comment, first, second, repository)
        if shown.path == path and shown.line is not None:
            lines[OLD_SIDE == shown.side].add(shown.line)
    return frozenset(lines[0]), frozenset(lines[1])
