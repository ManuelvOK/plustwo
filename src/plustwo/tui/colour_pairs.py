# SPDX-License-Identifier: Zlib
"""Curses colour pairs, allocated on demand."""

import curses

from plustwo.tui import colours as tui_colours
from plustwo.tui.colours import FIRST_DIFF_PAIR, SECONDARY, Colour, curses_colour, secondary


class ColourPairs:
    """Hands out curses colour pairs for foreground/background combinations.

    curses can only draw colours through numbered pairs, so every
    combination the diff uses gets one, the first time it shows up.
    """

    def __init__(self) -> None:
        """Starts without pairs."""
        # Each (foreground, background) in curses' numbers, mapped to its pair
        self._pairs: dict[tuple[int, int], int] = {}
        self._next = FIRST_DIFF_PAIR
        # The attributes handed out, because a redraw asks for the same
        # few hundred times and secondary() computes contrasts
        self._attributes: dict[tuple[object, ...], int] = {}

    def attribute(self, foreground: Colour, background: Colour) -> int:
        """Finds the curses attribute for two colours.

        Args:
            foreground: the text's colour, -1 for the default, SECONDARY
                for the grey of secondary text.
            background: the background, -1 for the default.

        Returns:
            The attribute, 0 when curses has no pair left.
        """
        key = (foreground, background, tui_colours.DIM_COLOUR, tui_colours.TERMINAL_BACKGROUND,
               tui_colours.TRUE_COLOUR)
        if key not in self._attributes:
            self._attributes[key] = self._attribute(foreground, background)
        return self._attributes[key]

    def _attribute(self, foreground: Colour, background: Colour) -> int:
        """Finds the curses attribute for two colours, see attribute().

        Args:
            foreground: the text's colour.
            background: the background.

        Returns:
            The attribute, 0 when curses has no pair left.
        """
        if SECONDARY == foreground:
            foreground = secondary(background)
        key = (curses_colour(foreground), curses_colour(background))
        if key not in self._pairs:
            if self._next >= curses.COLOR_PAIRS or max(key) >= curses.COLORS:
                return 0
            curses.init_pair(self._next, *key)
            self._pairs[key] = self._next
            self._next += 1
        return curses.color_pair(self._pairs[key])
