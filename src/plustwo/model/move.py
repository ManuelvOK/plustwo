# SPDX-License-Identifier: Zlib
"""A move of a thread by hand."""

from dataclasses import dataclass

from plustwo.model.place import NEW_SIDE
from plustwo.model.roles import REVIEWER


@dataclass
class Move:
    """A thread moved by hand to the place it belongs to on a patch set,
    when porting lost it. From that patch set on the thread is ported from
    here, older ones keep where it was. Not a message: it doesn't change
    whose turn it is.
    """
    patch_set: int
    place: str
    side: str = NEW_SIDE
    by: str = REVIEWER
    # When it was moved, seconds since 1970
    time: int = 0
