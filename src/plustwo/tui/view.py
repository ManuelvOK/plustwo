# SPDX-License-Identifier: Zlib
"""What the interface asks of a screen."""

from typing import Protocol

from plustwo.tui.area import Area
from plustwo.tui.sidebar import FooterEntry


class View(Protocol):
    """A screen of the interface: the dashboard, a change, its patch sets.
    The interface keeps a stack of them and shows the top one.
    """

    def reload(self) -> None:
        """Reads what the screen shows again."""

    def draw(self, area: Area) -> None:
        """Draws the screen.

        Args:
            area: the part of the screen to draw into.
        """

    def footer(self) -> list[FooterEntry]:
        """Lists the actions the footer shows.

        Returns:
            The footer's entries.
        """
        return []

    def action(self, action: str, count: int | None = None) -> None:
        """Handles an action.

        Args:
            action: the action's name.
            count: the count typed before it, None for none.
        """

    def mouse(self, row: int, column: int, mouse_event: str) -> None:
        """Handles the mouse.

        Args:
            row: the screen row.
            column: the screen column.
            mouse_event: the event, see the module mouse.
        """
