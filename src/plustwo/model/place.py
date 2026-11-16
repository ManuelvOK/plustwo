# SPDX-License-Identifier: Zlib
"""Places of threads: files, lines, sides and the whole change."""


# The sides of a diff a thread can be on: the version of its patch set, or
# that version's parent for lines the change removed
NEW_SIDE = 'new'
OLD_SIDE = 'old'

# The commit message as a file of the change, named like in Gerrit. In the
# trees built to diff it, it is a plain file called COMMIT_MSG.
COMMIT_MSG = '/COMMIT_MSG'

# Comments on the whole change, like the message of Gerrit's Reply dialog
PATCHSET_LEVEL = '/PATCHSET_LEVEL'

# How the interfaces name the whole change: short, and in a sentence
CHANGE_PLACE = 'Change'
WHOLE_CHANGE = 'on the whole change'


def place_label(place: str, side: str = NEW_SIDE) -> str:
    """Writes a thread's place for display.

    Args:
        place: the place, like src/main.cc:12 or PATCHSET_LEVEL.
        side: the side the place is on, NEW_SIDE or OLD_SIDE.

    Returns:
        The place, "Change" for the whole change, with " (old)" when it is
        in the old version.
    """
    return (CHANGE_PLACE if PATCHSET_LEVEL == place else place) \
        + (' (old)' if OLD_SIDE == side else '')


def split_place(place: str) -> tuple[str, int | None]:
    """Splits a place into its path and line.

    Args:
        place: the place, like src/main.cc:12 or src/main.cc.

    Returns:
        (path, line), the line None for a whole file or the whole change.
    """
    path, _, line = place.rpartition(':')
    return (path, int(line)) if path and line.isdigit() else (place, None)


def join_place(path: str, line: int | None) -> str:
    """Joins a path and a line into a place, the reverse of split_place().

    Args:
        path: the file's path.
        line: the line number, None for the whole file.

    Returns:
        The place, like src/main.cc:12.
    """
    return path if line is None else f'{path}:{line}'
