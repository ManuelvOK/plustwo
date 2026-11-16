# SPDX-License-Identifier: Zlib
"""The change screen's stack pane: the changes of the stack."""

import curses
from typing import TYPE_CHECKING

from plustwo.model.change import Change
from plustwo.tui import colours as tui_colours
from plustwo.tui.colours import CURSOR_LINE_GREY, SECONDARY, Colour, cursor_tint
from plustwo.tui.lists import keyed_cursor, position_after
from plustwo.tui.pane_frame import PaneBox, draw_frame

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface


class StackPane:
    """The changes of the stack, newest on top like Gerrit's relation chain,
    with a cursor that sticks to its change (see keyed_cursor()), so it
    stays on it when the list changes.

    Attributes:
        changes: the changes of the stack, oldest first.
        marks: the marks of the changes, see stack_marks().
        yours: the Change-Ids where it is the user's turn.
    """

    def __init__(self, interface: 'Interface') -> None:
        """Starts the pane empty, the cursor on the top row.

        Args:
            interface: the interface, which draws.
        """
        self._interface = interface
        self.changes: list[Change] = []
        self.marks: dict[str, str] = {}
        self.yours: set[str] = set()
        self._position = 0
        self._key: str | None = None

    def _shown(self) -> list[Change]:
        """Lists the changes in the pane's order.

        Returns:
            The changes, newest first.
        """
        return list(reversed(self.changes))

    @property
    def index(self) -> int:
        """The cursor's row, counted from the top, the newest change."""
        keys = [other.change_id for other in self._shown()]
        self._position, self._key = keyed_cursor(keys, self._key, self._position)
        return self._position

    @index.setter
    def index(self, index: int) -> None:
        """Puts the cursor on a row.

        Args:
            index: the row.
        """
        keys = [other.change_id for other in self._shown()]
        self._position = index
        self._key = keys[index] if 0 <= index < len(keys) else None

    def row_of(self, change_id: str) -> int:
        """Finds a change's row.

        Args:
            change_id: the change's full Change-Id.

        Returns:
            The row, 0 if the stack hasn't the change.
        """
        return next((index for index, other in enumerate(self._shown())
                     if other.change_id == change_id), 0)

    def selected(self) -> Change | None:
        """Finds the change at the cursor.

        Returns:
            The change, None without changes.
        """
        shown = self._shown()
        return shown[self.index] if self.index < len(shown) else None

    def first(self, height: int) -> int:
        """Finds the first row the pane shows: the cursor stays visible, in
        the middle if it can.

        Args:
            height: the pane's height.

        Returns:
            The row.
        """
        return max(0, min(self.index - height // 2, len(self.changes) - height))

    def move(self, action: str, count: int | None) -> None:
        """Moves the cursor, see position_after().

        Args:
            action: one of LIST_ACTIONS.
            count: the count typed before the key, None for none.
        """
        self.index = position_after(self.index, action, len(self.changes), count)

    def click(self, offset: int, height: int) -> None:
        """Puts the cursor on a clicked row.

        Args:
            offset: the row in the pane.
            height: the pane's height.
        """
        if self.first(height) + offset < len(self.changes):
            self.index = self.first(height) + offset

    def draw(self, box: PaneBox, is_focused: bool, shown_id: str, base: str) -> None:
        """Draws the changes: a green mark for submittable (✓) or finished
        (+2), grey where it isn't the user's turn, the shown one bold. The
        cursor row is highlighted while focused, else the shown change.

        Args:
            box: the pane's inside.
            is_focused: whether the pane has the focus.
            shown_id: the Change-Id of the change the screen shows.
            base: the stack's base branch, for the title.
        """
        out = self._interface.text
        top, left, height, width = box
        shown = self._shown()
        first = self.first(height)
        draw_frame(self._interface, box, f'Stack ({len(self.changes)}) on {base}', is_focused,
                   first, len(shown) - first - height)
        pairs = self._interface.colour_pairs
        # The one whole-line highlight: the cursor while the pane has the
        # focus, else the shown change, tinted like the diff's cursor line
        tint = (cursor_tint(-1) if tui_colours.TRUE_COLOUR else CURSOR_LINE_GREY) \
            if curses.COLORS >= 256 else None
        for row, other in enumerate(shown[first:first + height]):
            is_current = other.change_id == shown_id
            mine = other.change_id in self.yours
            background: Colour = -1
            if is_focused and first + row == self.index:
                attributes = curses.A_REVERSE
                mark_attributes = attributes | curses.color_pair(2)
            else:
                if is_current and not is_focused and tint is not None:
                    background = tint
                # Grey where it is not the user's turn
                attributes = pairs.attribute(-1 if mine else SECONDARY, background)
                mark_attributes = pairs.attribute(2, background)
            if is_current:
                attributes |= curses.A_BOLD
                mark_attributes |= curses.A_BOLD
            out(top + row, left, ' ' * width, attributes, left + width)
            # Like the file box's ✓: submittable, or +2 for a finished
            # review that can't be submitted yet
            mark = self.marks.get(other.change_id, '')
            out(top + row, left, f'{mark:<2}', mark_attributes, left + width)
            out(top + row, left + 3, other.subject[:max(width - 3, 0)], attributes, left + width)
