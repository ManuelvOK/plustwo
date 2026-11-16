# SPDX-License-Identifier: Zlib
"""The box comments are written in."""

import curses


# Most text rows the comment box shows, it scrolls beyond
TEXT_BOX_ROWS = 6

# The narrowest the box gets, frame included
_MIN_WIDTH = 20


def box_keys(has_emacs_keys: bool, save_label: str = 'save',
             is_discarding: bool = False) -> list[tuple[str, str]]:
    """Lists the keys of the comment box for its frame and the footer.

    Args:
        has_emacs_keys: whether the emacs preset is in use.
        save_label: what Enter does, like save or send.
        is_discarding: whether the cancel key was pressed once on text, so
            the next press discards it.

    Returns:
        (keys, what they do) per action.
    """
    cancel = 'C-g' if has_emacs_keys else 'Esc'
    return [('⏎', save_label), ('Alt-⏎', 'new line'),
            (cancel, 'again discards' if is_discarding else 'cancel'),
            ('C-x C-e' if has_emacs_keys else '^E', 'editor')]


def text_box_height(box: 'TextBox', width: int) -> int:
    """Finds the rows the comment box takes.

    Args:
        box: the box.
        width: the box's width, frame included.

    Returns:
        The number of rows, frame included.
    """
    return min(len(box.rows(max(width, _MIN_WIDTH) - 2)), TEXT_BOX_ROWS) + 2


class TextBox:
    """The text and cursor of the comment box, independent of the screen.
    Lines wrap at the box's width, by character, so every position of the
    text has one place on the screen.

    Attributes:
        text: the text.
        position: the cursor's index in the text.
        has_emacs_keys: whether Emacs' editing keys work: C-a, C-e, C-f,
            C-b, C-k, C-d.
        is_discarding: whether the cancel key was pressed once with text in
            the box.
    """

    def __init__(self, text: str = '', has_emacs_keys: bool = False) -> None:
        """Starts the box with the cursor at the text's end.

        Args:
            text: the text to start with.
            has_emacs_keys: whether Emacs' editing keys work.
        """
        self.text = text
        self.position = len(text)
        self.has_emacs_keys = has_emacs_keys
        self.is_discarding = False

    def cancel(self) -> bool:
        """Handles the cancel key, Esc (C-g with the emacs keys). An empty
        box closes at once. With text in it, the first press only warns,
        the next one discards. Any other key ends the warning, see keep().

        Returns:
            Whether the box closes.
        """
        if not self.text.strip() or self.is_discarding:
            return True
        self.is_discarding = True
        return False

    def keep(self) -> None:
        """Ends the warning of cancel(): a key other than the cancel key."""
        self.is_discarding = False

    def insert(self, characters: str) -> None:
        """Inserts text at the cursor.

        Args:
            characters: the text.
        """
        self.text = self.text[:self.position] + characters + self.text[self.position:]
        self.position += len(characters)

    def key(self, key: int | str) -> None:
        """Handles an editing key.

        Args:
            key: the key, as get_wch() returns it.
        """
        if self.has_emacs_keys:
            key = {'\x06': curses.KEY_RIGHT, '\x02': curses.KEY_LEFT, '\x05': curses.KEY_END,
                   '\x04': curses.KEY_DC}.get(str(key), key)
        before, after = self.text[:self.position], self.text[self.position:]
        if self.has_emacs_keys and '\x0b' == key:
            # C-k deletes to the end of the line, or the line end itself
            end = self.text.find('\n', self.position)
            end = len(self.text) if -1 == end else end
            self.text = before + self.text[max(end, self.position + 1)
                                           if end == self.position else end:]
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            if before:
                self.text, self.position = before[:-1] + after, self.position - 1
        elif curses.KEY_DC == key:
            self.text = before + after[1:]
        elif curses.KEY_LEFT == key:
            self.position = max(self.position - 1, 0)
        elif curses.KEY_RIGHT == key:
            self.position = min(self.position + 1, len(self.text))
        elif key in (curses.KEY_HOME, '\x01'):
            self.position = before.rfind('\n') + 1
        elif curses.KEY_END == key:
            end = self.text.find('\n', self.position)
            self.position = len(self.text) if -1 == end else end
        elif '\x15' == key:
            # ^U deletes back to the start of the line
            start = before.rfind('\n') + 1
            self.text, self.position = before[:start] + after, start
        elif '\x17' == key:
            # ^W deletes the word before the cursor
            kept = before.rstrip(' ')
            kept = kept[:max(kept.rfind(' '), kept.rfind('\n')) + 1]
            self.text, self.position = kept + after, len(kept)
        elif isinstance(key, str) and key.isprintable():
            self.insert(key)

    def _layout(self, width: int) -> list[tuple[int, str]]:
        """Wraps the text into screen rows.

        Args:
            width: the rows' width.

        Returns:
            (index of the row's first character in the text, the row's
            text) per row.
        """
        rows = []
        start = 0
        for line in self.text.split('\n'):
            # One column stays free for the cursor at the end of a row.
            step = max(width - 1, 1)
            for offset in range(0, max(len(line), 1), step):
                rows.append((start + offset, line[offset:offset + step]))
            start += len(line) + 1
        return rows

    def rows(self, width: int) -> list[str]:
        """Wraps the text into screen rows.

        Args:
            width: the rows' width.

        Returns:
            The rows' texts.
        """
        return [text for _, text in self._layout(width)]

    def cursor_position(self, width: int) -> tuple[int, int]:
        """Finds the cursor inside the rows.

        Args:
            width: the rows' width.

        Returns:
            (row, column).
        """
        layout = self._layout(width)
        for row in range(len(layout) - 1, -1, -1):
            start, text = layout[row]
            if start <= self.position:
                return row, min(self.position - start, len(text))
        return 0, 0
