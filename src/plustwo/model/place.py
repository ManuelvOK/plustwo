# SPDX-License-Identifier: Zlib
"""Places of threads: files, lines, sides and the whole change."""


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


def place_label(place, side=NEW_SIDE):
    """A thread's place for display: "Change" for the whole change, marked
    when it is in the old version.
    """
    return (CHANGE_PLACE if PATCHSET_LEVEL == place else place) \
        + (' (old)' if OLD_SIDE == side else '')


def split_place(place):
    """A place as (path, line), line None for a whole file or change."""
    path, _, line = place.rpartition(':')
    return (path, int(line)) if path and line.isdigit() else (place, None)


def join_place(path, line):
    """The place of a path and a line, see split_place()."""
    return path if line is None else f'{path}:{line}'
