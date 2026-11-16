# SPDX-License-Identifier: Zlib
"""Curses colour pairs, allocated on demand."""

import curses

from plustwo.tui import colours as tui_colours
from plustwo.tui.colours import FIRST_DIFF_PAIR, SECONDARY, curses_colour, secondary


class ColourPairs:
    """Hands out curses colour pairs for foreground/background combinations.

    curses can only draw colours through numbered pairs, so every
    combination the diff uses gets one, the first time it shows up.
    """

    def __init__(self):
        self._pairs = {}
        self._next = FIRST_DIFF_PAIR
        # The attributes handed out, because a redraw asks for the same
        # few hundred times and secondary() computes contrasts
        self._attributes = {}

    def attribute(self, foreground, background):
        """The curses attribute for two palette colours (-1 is default).
        SECONDARY as foreground is the grey of secondary text.
        """
        key = (foreground, background, tui_colours.DIM_COLOUR, tui_colours.TERMINAL_BACKGROUND, tui_colours.TRUE_COLOUR)
        if key not in self._attributes:
            self._attributes[key] = self._attribute(foreground, background)
        return self._attributes[key]

    def _attribute(self, foreground, background):
        """See attribute()."""
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
