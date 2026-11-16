# SPDX-License-Identifier: Zlib
"""Lines that moved within a diff."""

from collections.abc import Sequence

from plustwo.diff.edits import Edit


# A block of lines counts as moved with at least this many letters and
# digits, like git diff --color-moved
_MOVED_MIN_ALNUM = 20


def moved_lines(old_lines: Sequence[str], new_lines: Sequence[str],
                edits: list[Edit]) -> tuple[set[int], set[int]]:
    """Finds the lines of a file's diff that moved, like git diff
    --color-moved. A block of removed lines that comes back the same as a
    block of added lines elsewhere is moved, if it has at least
    _MOVED_MIN_ALNUM letters and digits, so single braces or blank lines
    don't count. Unlike git, only within the file.

    Args:
        old_lines: the lines of the old version.
        new_lines: the lines of the new version.
        edits: the diff's edits, see edits_of().

    Returns:
        (old line numbers, new line numbers) of the moved lines.
    """
    removed = {line for edit in edits for line in range(edit[0], edit[1])}
    added = {line for edit in edits for line in range(edit[2], edit[3])}
    added_by_text: dict[str, list[int]] = {}
    for line in sorted(added):
        added_by_text.setdefault(new_lines[line - 1], []).append(line)
    old_moved: set[int] = set()
    new_moved: set[int] = set()
    for line in sorted(removed):
        if line in old_moved:
            continue
        longest = _longest_block(line, added_by_text.get(old_lines[line - 1], []), removed,
                                 added, old_lines, new_lines)
        n_alnum = sum(character.isalnum() for old, _ in longest
                      for character in old_lines[old - 1])
        if n_alnum >= _MOVED_MIN_ALNUM:
            old_moved.update(old for old, _ in longest)
            new_moved.update(new for _, new in longest)
    return old_moved, new_moved


def _longest_block(line: int, starts: list[int], removed: set[int], added: set[int],
                   old_lines: Sequence[str], new_lines: Sequence[str]) -> list[tuple[int, int]]:
    """Finds the longest block of removed lines from a line that comes back
    the same as added lines.

    Args:
        line: the removed line where the block starts.
        starts: the added lines with the same text, where a copy may start.
        removed: the numbers of all removed lines.
        added: the numbers of all added lines.
        old_lines: the lines of the old version.
        new_lines: the lines of the new version.

    Returns:
        (old line number, new line number) pairs of the block, empty if
        there is none.
    """
    longest: list[tuple[int, int]] = []
    for start in starts:
        length = 0
        while (line + length in removed and start + length in added
               and old_lines[line + length - 1] == new_lines[start + length - 1]):
            length += 1
        if length > len(longest):
            longest = [(line + offset, start + offset) for offset in range(length)]
    return longest
