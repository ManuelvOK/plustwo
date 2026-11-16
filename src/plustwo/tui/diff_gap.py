# SPDX-License-Identifier: Zlib
"""Rows a box reserves in the diff."""


class DiffGap:
    """Room for the comment box in the diff, below row `index` (-1: above
    the first row). While the box is open, the rows below move down by its
    height, so it hides nothing.
    """

    def __init__(self, diff, layout, index):
        self._diff = diff
        self._top, self._left, self._height, self._width = layout
        self._index = index

    def place(self, box_height):
        """Opens the gap for a box this high, scrolls it into view and
        returns where the box goes: (row above it, left, width).
        """
        self._diff.gap = (self._index, box_height)
        if self._index < 0:
            self._diff.scroll = 0
        else:
            self._diff.make_room_below(self._index, self._height, box_height)
        return self._top + self._index - self._diff.scroll, self._left, self._width

    def close(self):
        """Closes the gap again."""
        self._diff.gap = None
