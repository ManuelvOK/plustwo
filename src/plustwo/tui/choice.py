# SPDX-License-Identifier: Zlib
"""A row of choices, like the votes of a reply."""

import curses


class Choice:
    """A value picked from a few, like a vote from -2 to +2: Left and Right
    move, a digit picks, - before it a negative one.
    """

    def __init__(self, values, current=0):
        self.values = tuple(values)
        self.index = self.values.index(current) if current in self.values else 0
        self._minus = False

    @property
    def value(self):
        return self.values[self.index]

    def key(self, key):
        """Handles a key from get_wch(). Returns whether it was one of the
        choice's keys.
        """
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            step = 1 if curses.KEY_RIGHT == key else -1
            self.index = max(0, min(self.index + step, len(self.values) - 1))
        elif '-' == key:
            self._minus = True
            return True
        elif '+' == key:
            pass
        elif isinstance(key, str) and key.isdigit():
            value = -int(key) if self._minus else int(key)
            if value in self.values:
                self.index = self.values.index(value)
        else:
            self._minus = False
            return False
        self._minus = False
        return True

    def text(self):
        """The values in a row, the chosen one in brackets: -2 -1 [0] +1 +2."""
        return ' '.join(f'[{value:+d}]' if index == self.index else f' {value:+d} '
                        for index, value in enumerate(self.values)).replace('+0', ' 0')
