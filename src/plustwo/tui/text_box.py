# SPDX-License-Identifier: Zlib
"""The box comments are written in."""

import curses


# Most text rows the comment box shows, it scrolls beyond
TEXT_BOX_ROWS = 6


def box_keys(emacs, save='save', discarding=False):
    """The keys of the comment box for its frame and the footer: (keys,
    what they do).
    """
    cancel = 'C-g' if emacs else 'Esc'
    keys = [('⏎', save), ('Alt-⏎', 'new line'),
            (cancel, 'again discards' if discarding else 'cancel'),
            ('C-x C-e' if emacs else '^E', 'editor')]
    return keys


def text_box_height(box, width):
    """Rows the comment box takes at a width, with its frame."""
    return min(len(box.rows(max(width, 20) - 2)), TEXT_BOX_ROWS) + 2


class TextBox:
    """The text and cursor of the comment box, independent of the screen.

    Lines wrap at the box's width, by character, so every position of the
    text has one place on the screen.
    """

    def __init__(self, text='', emacs=False):
        self.text = text
        self.position = len(text)
        # Whether Emacs' editing keys work: C-a, C-e, C-f, C-b, C-k, C-d
        self.emacs = emacs
        # Whether the cancel key was pressed once with text in the box
        self.discarding = False

    def cancel(self):
        """Esc (C-g with the emacs keys): whether the box closes. An empty
        box closes at once. With text in it, the first press only warns,
        the next one discards. Any other key ends the warning, see keep().
        """
        if not self.text.strip() or self.discarding:
            return True
        self.discarding = True
        return False

    def keep(self):
        """A key other than the cancel key: the text stays."""
        self.discarding = False

    def insert(self, characters):
        """Inserts at the cursor."""
        self.text = self.text[:self.position] + characters + self.text[self.position:]
        self.position += len(characters)

    def key(self, key):
        """Handles an editing key from get_wch()."""
        if self.emacs:
            key = {'\x06': curses.KEY_RIGHT, '\x02': curses.KEY_LEFT, '\x05': curses.KEY_END,
                   '\x04': curses.KEY_DC}.get(key, key)
        before, after = self.text[:self.position], self.text[self.position:]
        if self.emacs and '\x0b' == key:
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

    def _layout(self, width):
        """[(start index, text)] of every screen row."""
        rows = []
        start = 0
        for line in self.text.split('\n'):
            # One column stays free for the cursor at the end of a row.
            step = max(width - 1, 1)
            for offset in range(0, max(len(line), 1), step):
                rows.append((start + offset, line[offset:offset + step]))
            start += len(line) + 1
        return rows

    def rows(self, width):
        """The text as screen rows."""
        return [text for _, text in self._layout(width)]

    def cursor_position(self, width):
        """(row, column) of the cursor inside the rows."""
        layout = self._layout(width)
        for row in range(len(layout) - 1, -1, -1):
            start, text = layout[row]
            if start <= self.position:
                return row, min(self.position - start, len(text))
        return 0, 0
