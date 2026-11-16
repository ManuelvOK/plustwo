# SPDX-License-Identifier: Zlib
"""A row of choices, like the votes of a reply."""

import curses
from collections.abc import Iterable


class Choice:
    """A value picked from a few, like a vote from -2 to +2: Left and Right
    move, a digit picks, - before it a negative one.

    Attributes:
        values: the values to pick from, in order.
        index: the index of the picked value.
    """

    def __init__(self, values: Iterable[int], current: int = 0) -> None:
        """Starts the choice on a value.

        Args:
            values: the values to pick from, in order.
            current: the value picked first, the first value if it isn't
                one of them.
        """
        self.values = tuple(values)
        self.index = self.values.index(current) if current in self.values else 0
        self._has_minus = False

    @property
    def value(self) -> int:
        """The picked value."""
        return self.values[self.index]

    def key(self, key: int | str) -> bool:
        """Handles a key.

        Args:
            key: the key, as get_wch() returns it.

        Returns:
            Whether it was one of the choice's keys.
        """
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            step = 1 if curses.KEY_RIGHT == key else -1
            self.index = max(0, min(self.index + step, len(self.values) - 1))
        elif '-' == key:
            self._has_minus = True
            return True
        elif '+' == key:
            pass
        elif isinstance(key, str) and key.isdigit():
            value = -int(key) if self._has_minus else int(key)
            if value in self.values:
                self.index = self.values.index(value)
        else:
            self._has_minus = False
            return False
        self._has_minus = False
        return True

    def text(self) -> str:
        """Writes the values in a row.

        Returns:
            The values, the picked one in brackets: -2 -1 [0] +1 +2.
        """
        return ' '.join(f'[{value:+d}]' if index == self.index else f' {value:+d} '
                        for index, value in enumerate(self.values)).replace('+0', ' 0')
