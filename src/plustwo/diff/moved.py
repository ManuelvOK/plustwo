# SPDX-License-Identifier: Zlib
"""Lines that moved within a diff."""


# A block of lines counts as moved with at least this many letters and
# digits, like git diff --color-moved
MOVED_MIN_ALNUM = 20


def moved_lines(old_lines, new_lines, edits):
    """The lines of a file's diff that moved: (old line numbers, new line
    numbers), like git diff --color-moved. A block of removed lines that
    comes back the same as a block of added lines elsewhere is moved, if
    it has at least MOVED_MIN_ALNUM letters and digits, so single braces
    or blank lines don't count. Unlike git, only within the file.
    """
    removed = {line for edit in edits for line in range(edit[0], edit[1])}
    added = {line for edit in edits for line in range(edit[2], edit[3])}
    by_text = {}
    for line in sorted(added):
        by_text.setdefault(new_lines[line - 1], []).append(line)
    old_moved, new_moved = set(), set()
    for line in sorted(removed):
        if line in old_moved:
            continue
        best = []
        for start in by_text.get(old_lines[line - 1], []):
            length = 0
            while (line + length in removed and start + length in added
                   and old_lines[line + length - 1] == new_lines[start + length - 1]):
                length += 1
            if length > len(best):
                best = [(line + offset, start + offset) for offset in range(length)]
        if sum(character.isalnum() for old, _ in best for character in old_lines[old - 1]) \
                >= MOVED_MIN_ALNUM:
            old_moved.update(old for old, _ in best)
            new_moved.update(new for _, new in best)
    return old_moved, new_moved
