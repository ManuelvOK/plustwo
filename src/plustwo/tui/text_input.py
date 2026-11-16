# SPDX-License-Identifier: Zlib
"""Writing text: the comment box over the screen, or the editor."""

import curses
import os
import tempfile
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from plustwo.tui.choice import Choice
from plustwo.tui.diff_gap import DiffGap
from plustwo.tui.terminal import ESC_DELAY_MS, RELOAD_INTERVAL_MS
from plustwo.tui.text_box import TEXT_BOX_ROWS, TextBox, box_keys, text_box_height

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface

# Where write_text() puts its box: a DiffGap, (screen row, left, width), or
# None for above the bottom line
BoxAnchor = DiffGap | tuple[int, int, int] | None


class TextInput:
    """The comment box and the editor, over the interface's screen.

    Attributes:
        footer: gives the keys of the open box for the footer, None while
            no box is open.
    """

    def __init__(self, interface: 'Interface', screen: Any) -> None:
        """Sets up the text input.

        Args:
            interface: the interface, which draws the screen below the box.
            screen: the curses window of the whole screen.
        """
        self._interface = interface
        self._screen = screen
        self.footer: Callable[[], list[tuple[str, str]]] | None = None

    def edit_text(self, initial: str = '') -> str:
        """Lets the reviewer write a text in their editor.

        Args:
            initial: the text to start with.

        Returns:
            The text, '' if left empty.
        """
        editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vi'
        with tempfile.NamedTemporaryFile('w+', suffix='.txt', delete=False) as file:
            file.write(initial)
            path = file.name
        try:
            self._interface.run_outside([editor, path])
            with open(path, encoding='utf-8') as file:
                return file.read().strip()
        finally:
            os.unlink(path)

    def write_text(self, title: str, initial: str = '', anchor: BoxAnchor = None,
                   save_label: str = 'save', choice: tuple[str, Choice] | None = None,
                   lines: Sequence[tuple[str, bool]] = ()) -> str | None:
        """Lets the reviewer write a comment in a small box, so the diff
        stays visible.

        Enter saves, Alt-Enter starts a new line, Esc or C-g cancels (with
        text in the box only when pressed twice), ^E continues in the
        editor. With the emacs keys, C-e goes to the end of the line, and
        C-x C-e continues in the editor.

        Args:
            title: the box's title.
            initial: the text to start with.
            anchor: a DiffGap: the diff makes room for the box below a
                row, as high as the box grows, and moves the rows below
                down. Or (row, left, width): the box goes over the screen
                below that row, or above it when there is no room. None:
                the box sits above the bottom line, as wide as the screen.
            save_label: what Enter does, for the hints.
            choice: (label, Choice) for a row above the text to pick a
                value, like the vote of Gerrit's Reply dialog. Tab, or Up
                on the first line, goes there. None for none.
            lines: (text, is focused) rows of information above it.

        Returns:
            The text, '' if left empty, None if cancelled.
        """
        out = self._interface.text
        has_emacs_keys = 'emacs' == self._interface.keymap.preset
        box = TextBox(initial, has_emacs_keys)
        # Whether the keys go to the choice's row instead of the text
        state = {'is_on_choice': False}

        def footer() -> list[tuple[str, str]]:
            """Lists the box's keys.

            Returns:
                (keys, what they do) per action.
            """
            keys = box_keys(has_emacs_keys, save_label, box.is_discarding)
            if choice:
                keys.insert(1, ('Tab', 'message' if state['is_on_choice'] else 'vote'))
            return keys

        self.footer = footer
        curses.curs_set(1)
        self._screen.timeout(-1)
        try:
            while True:
                # The box over the screen
                extra = list(lines)
                if choice:
                    extra.append((f'{choice[0]}: {choice[1].text()}', state['is_on_choice']))
                place: tuple[int, int, int] | None
                if isinstance(anchor, DiffGap):
                    # The box's height depends on its width, which the gap
                    # gives: once to learn the width, once with the height.
                    place = anchor.place(TEXT_BOX_ROWS + 2 + len(extra))
                    place = anchor.place(text_box_height(box, place[2]) + len(extra))
                else:
                    place = anchor
                self._interface.draw(does_refresh=False)
                self._draw_text_box(box, title, place, footer(), extra)
                self._screen.refresh()
                key = self._screen.get_wch()
                # Cancelling, and Alt-Enter
                is_cancel = '\x07' == key
                if '\x1b' == key:
                    if not self._read_after_escape(box):
                        continue
                    is_cancel = True
                if is_cancel:
                    if box.cancel():
                        return None
                    continue
                box.keep()
                # The choice's row
                if choice and ('\t' == key or (curses.KEY_UP == key
                                               and not state['is_on_choice']
                                               and '\n' not in box.text[:box.position])):
                    state['is_on_choice'] = not state['is_on_choice']
                    curses.curs_set(0 if state['is_on_choice'] else 1)
                    continue
                if choice and state['is_on_choice'] and key not in ('\r', curses.KEY_ENTER):
                    if curses.KEY_DOWN == key:
                        state['is_on_choice'] = False
                        curses.curs_set(1)
                    elif not choice[1].key(key) and isinstance(key, str) and key.isprintable():
                        # Typing goes back to the message
                        state['is_on_choice'] = False
                        curses.curs_set(1)
                        box.key(key)
                    continue
                # New lines, saving, the editor, editing
                if '\n' == key:
                    # Ctrl+J: Enter is \r, see curses.nonl() in run()
                    box.insert('\n')
                    continue
                if key in ('\r', curses.KEY_ENTER):
                    saved = self._enter_in_box(box)
                    if saved is not None:
                        return saved
                    continue
                if has_emacs_keys and '\x18' == key:
                    # C-x C-e continues in the editor, like in a shell
                    if '\x05' == self._screen.get_wch():
                        return self.edit_text(box.text)
                    continue
                if '\x05' == key and not has_emacs_keys:
                    return self.edit_text(box.text)
                box.key(key)
        finally:
            self.footer = None
            if isinstance(anchor, DiffGap):
                anchor.close()
            curses.curs_set(0)
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def _read_after_escape(self, box: TextBox) -> bool:
        """Reads what follows Esc in the text box: Alt-Enter arrives as Esc
        and Enter, Shift+Enter as an escape sequence where the terminal
        tells it apart, a lone Esc cancels.

        Args:
            box: the text box, which gets the new line.

        Returns:
            True if Esc cancels, False if the key was handled.
        """
        out = self._interface.text
        self._screen.timeout(ESC_DELAY_MS)
        try:
            following = self._screen.get_wch()
        except curses.error:
            following = None
        finally:
            self._screen.timeout(-1)
        if following is None:
            return True
        if '\x1b' == following:
            # Two quick presses
            box.cancel()
            return True
        box.keep()
        if following in ('\n', '\r'):
            box.insert('\n')
        elif '[' == following:
            # Shift+Enter: "CSI 13;2u" or "CSI 27;2;13~"
            if self._read_sequence() in ('13;2u', '27;2;13~'):
                box.insert('\n')
        return False

    @staticmethod
    def _enter_in_box(box: TextBox) -> str | None:
        """Handles Enter in the text box: it saves, but a backslash before
        the cursor starts a new line like in a shell, two save with one.

        Args:
            box: the text box.

        Returns:
            The text to save, None if Enter started a new line.
        """
        before = box.text[:box.position]
        if before.endswith('\\\\'):
            # \\ at the end keeps one backslash and saves
            box.text = before[:-1] + box.text[box.position:]
            return box.text.strip()
        if before.endswith('\\'):
            # \ at the end, like in a shell: a new line
            box.text = before[:-1] + box.text[box.position:]
            box.position -= 1
            box.insert('\n')
            return None
        return box.text.strip()

    def _read_sequence(self) -> str:
        """Reads the rest of an escape sequence after Esc [.

        Returns:
            The sequence up to its final character.
        """
        out = self._interface.text
        sequence = ''
        self._screen.timeout(ESC_DELAY_MS)
        try:
            while len(sequence) < 16:
                character = self._screen.get_wch()
                if not isinstance(character, str):
                    break
                sequence += character
                if character.isalpha() or '~' == character:
                    break
        except curses.error:
            pass
        finally:
            self._screen.timeout(-1)
        return sequence

    def _draw_text_box(self, box: TextBox, title: str, anchor: tuple[int, int, int] | None,
                       keys: Sequence[tuple[str, str]] = (),
                       extra: Sequence[tuple[str, bool]] = ()) -> None:
        """Draws the box of write_text() and puts the terminal cursor in it.

        Args:
            box: the text box.
            title: the box's title.
            anchor: (row, left, width) the box goes below, None for above
                the bottom line.
            keys: (keys, what they do) for the frame's bottom.
            extra: (text, is focused) rows above the text.
        """
        out = self._interface.text
        height, width = self._screen.getmaxyx()
        row, left, box_width = anchor or (height - 2, 0, width - 1)
        box_width = max(box_width, 20)
        rows = box.rows(box_width - 2)
        n_inside_rows = min(len(rows), TEXT_BOX_ROWS)
        cursor_row, cursor_column = box.cursor_position(box_width - 2)
        first = max(0, min(cursor_row - n_inside_rows + 1, len(rows) - n_inside_rows))
        n_rows_above = len(extra)
        top = row + 1
        if not anchor or top + n_inside_rows + n_rows_above + 2 > height - 1:
            top = max(row - n_inside_rows - n_rows_above - 2, 0)
        hint = ' ' + '  '.join(f'{key} {label}' for key, label in keys) + ' '
        bottom = top + n_rows_above + n_inside_rows + 1
        out(top, left, '┌' + '─' * (box_width - 2) + '┐', curses.A_BOLD)
        out(top, left + 2, f' {title} ', curses.A_BOLD)
        out(bottom, left, '└' + '─' * (box_width - 2) + '┘', curses.A_BOLD)
        if len(hint) + 4 < box_width:
            out(bottom, left + box_width - len(hint) - 2, hint,
                curses.A_BOLD if box.is_discarding else self._interface.dim())
        for offset, (line, is_focused) in enumerate(extra):
            out(top + 1 + offset, left, '│', curses.A_BOLD)
            out(top + 1 + offset, left + 1, f'{line:<{box_width - 2}}'[:box_width - 2],
                curses.A_REVERSE if is_focused else self._interface.dim())
            out(top + 1 + offset, left + box_width - 1, '│', curses.A_BOLD)
        for offset in range(n_inside_rows):
            line = rows[first + offset] if first + offset < len(rows) else ''
            out(top + 1 + n_rows_above + offset, left, '│', curses.A_BOLD)
            out(top + 1 + n_rows_above + offset, left + 1, f'{line:<{box_width - 2}}')
            out(top + 1 + n_rows_above + offset, left + box_width - 1, '│', curses.A_BOLD)
        try:
            self._screen.move(top + 1 + n_rows_above + cursor_row - first,
                              left + 1 + cursor_column)
        except curses.error:
            pass
