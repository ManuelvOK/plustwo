# SPDX-License-Identifier: Zlib
"""The change screen's message pane: the commit message."""

import curses
import textwrap
from typing import TYPE_CHECKING

from plustwo.tui.lists import position_after
from plustwo.tui.pane_frame import PaneBox, draw_frame

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface


class MessagePane:
    """The box with the commit message, which scrolls while focused.

    Attributes:
        message: the commit message: (subject, body, trailers).
    """

    def __init__(self, interface: 'Interface') -> None:
        """Starts the pane empty, at the top.

        Args:
            interface: the interface, which draws.
        """
        self._interface = interface
        self.message: tuple[str, str, list[str]] = ('', '', [])
        self._scroll = 0

    def lines(self, width: int) -> list[tuple[str, int]]:
        """Wraps the commit message into the rows of its box, the subject
        bold, the trailers dimmed.

        Args:
            width: the box's width.

        Returns:
            (text, curses attributes) per row.
        """
        subject, body, trailers = self.message
        width = max(width, 10)
        lines = [(line, curses.A_BOLD) for line in textwrap.wrap(subject, width) or ['']]
        if body:
            lines.append(('', 0))
            lines += [(line, 0) for paragraph in body.split('\n')
                      for line in textwrap.wrap(paragraph, width) or ['']]
        if trailers:
            lines.append(('', 0))
            lines += [(line[:width], self._interface.dim()) for line in trailers]
        return lines

    def move(self, action: str, box: PaneBox, count: int | None) -> None:
        """Scrolls the message, see position_after().

        Args:
            action: one of LIST_ACTIONS.
            box: the pane's inside.
            count: the count typed before the key, None for none.
        """
        _top, _left, height, width = box
        self._scroll = position_after(self._scroll, action,
                                      max(len(self.lines(width)) - height + 1, 1), count)

    def draw(self, box: PaneBox, is_focused: bool) -> None:
        """Draws the box.

        Args:
            box: the pane's inside.
            is_focused: whether the pane has the focus.
        """
        out = self._interface.text
        top, left, height, width = box
        lines = self.lines(width)
        self._scroll = max(0, min(self._scroll, len(lines) - height))
        draw_frame(self._interface, box, 'Commit message', is_focused, self._scroll,
                   len(lines) - self._scroll - height)
        for row, (text, attributes) in enumerate(lines[self._scroll:self._scroll + height]):
            out(top + row, left, text, attributes, left + width)
