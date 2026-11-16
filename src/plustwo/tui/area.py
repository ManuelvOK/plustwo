# SPDX-License-Identifier: Zlib
"""A rectangle of the screen."""

from typing import Any


class Area:
    """The part of the screen a view may draw into: rows 0 to height-1.

    Attributes:
        screen: the curses window to draw into.
        top: the screen row of the area's first row.
        height: the number of rows.
        width: the number of columns.
    """

    def __init__(self, screen: Any, top: int, height: int, width: int) -> None:
        """Sets the area's place.

        Args:
            screen: the curses window to draw into.
            top: the screen row of the area's first row.
            height: the number of rows.
            width: the number of columns.
        """
        self.screen = screen
        self.top = top
        self.height = height
        self.width = width
