# SPDX-License-Identifier: Zlib
"""A move of a thread by hand."""

from dataclasses import dataclass

from plustwo.model.place import NEW_SIDE
from plustwo.model.roles import REVIEWER


@dataclass
class Move:
    """A thread moved by hand to the place it belongs to on a patch set,
    when porting lost it, stored in the review note with these fields as
    keys. From that patch set on the thread is ported from here, older ones
    keep where it was. A move is no message: it doesn't change whose turn
    it is.

    Attributes:
        patch_set: the patch set the place counts on.
        place: the new place, like src/main.cc:12.
        side: the side the place is on, NEW_SIDE or OLD_SIDE.
        by: who moved it, REVIEWER or AUTHOR.
        time: when it was moved, seconds since 1970.
    """
    patch_set: int
    place: str
    side: str = NEW_SIDE
    by: str = REVIEWER
    time: int = 0
