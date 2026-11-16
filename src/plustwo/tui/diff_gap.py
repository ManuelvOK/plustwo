# SPDX-License-Identifier: Zlib
"""Rows a box reserves in the diff."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plustwo.tui.diff_pane import DiffPane


class DiffGap:
    """Room for the comment box in the diff, below a row. While the box is
    open, the rows below move down by its height, so it hides nothing.
    """

    def __init__(self, diff: 'DiffPane', layout: tuple[int, int, int, int], index: int) -> None:
        """Prepares the gap, still closed.

        Args:
            diff: the diff pane.
            layout: the pane's (top, left, height, width) on the screen.
            index: the row the gap opens below, -1 for above the first row.
        """
        self._diff = diff
        self._top, self._left, self._height, self._width = layout
        self._index = index

    def place(self, box_height: int) -> tuple[int, int, int]:
        """Opens the gap for a box and scrolls it into view.

        Args:
            box_height: the box's height.

        Returns:
            Where the box goes: (screen row above it, left, width).
        """
        self._diff.gap = (self._index, box_height)
        if self._index < 0:
            self._diff.scroll = 0
        else:
            self._diff.make_room_below(self._index, self._height, box_height)
        return self._top + self._index - self._diff.scroll, self._left, self._width

    def close(self) -> None:
        """Closes the gap again."""
        self._diff.gap = None
