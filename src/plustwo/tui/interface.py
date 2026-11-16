# SPDX-License-Identifier: Zlib
"""The interface: screens, the message line, dialogs and the loop."""

import curses
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from typing import Any

from plustwo.config.registry import review_repositories
from plustwo.config.settings import setting
from plustwo.config.view_state import follows_latest
from plustwo.diff.edits import WHITESPACE_MODES
from plustwo.diff.search import SEARCH_MODES
from plustwo.git.errors import ChangeError
from plustwo.review.stacks import stack_version
from plustwo.tui import colours as tui_colours
from plustwo.tui.area import Area
from plustwo.tui.choice import Choice
from plustwo.tui.colour_pairs import ColourPairs
from plustwo.tui.colours import SECONDARY, curses_colour
from plustwo.tui.diff_gap import DiffGap
from plustwo.tui.keymap import keymap_from_settings
from plustwo.tui.keys import META
from plustwo.tui.messages import ERROR, OK
from plustwo.tui.mouse import CLICK, DOUBLE_CLICK, WHEEL_DOWN, WHEEL_UP
from plustwo.tui.sidebar import SIDEBAR_MODES
from plustwo.tui.stack_view import StackView
from plustwo.tui.terminal import (
    keys_as_keys, pager_environment, parse_colour, query_background, true_colour_terminal)
from plustwo.tui.text_box import TEXT_BOX_ROWS, TextBox, box_keys, text_box_height
from plustwo.tui.view import View


# How often the screen checks for new commits or notes, in milliseconds
_RELOAD_INTERVAL_MS = 700

# How long curses waits after Esc for more bytes of a key sequence. The
# default of one second makes Esc feel stuck.
_ESC_DELAY_MS = 25

# Where write_text() puts its box: a DiffGap, (screen row, left, width), or
# None for above the bottom line
BoxAnchor = DiffGap | tuple[int, int, int] | None


def run(list_name: str | None = None) -> None:
    """Starts the interface.

    Args:
        list_name: the list of repositories the dashboard shows, None for
            all.
    """
    tui_colours.TERMINAL_BACKGROUND = (parse_colour(setting('tui.background', '') or '')
                                       or query_background() or (0, 0, 0))
    wants_true_colour = true_colour_terminal(setting('tui.colours', 'auto') or 'auto')

    def start(screen: Any) -> None:
        """Runs the interface on the screen curses set up.

        Args:
            screen: the curses window of the whole screen.
        """
        tui_colours.TRUE_COLOUR = wants_true_colour and curses.COLORS >= 1 << 24
        # Enter then arrives as \r and Ctrl+J as \n, so the comment box
        # tells them apart.
        curses.nonl()
        keys_as_keys()
        Interface(screen, list_name).loop()

    curses.wrapper(start)


class Interface:
    """The running interface: the stack of screens, drawing and keys.

    Attributes:
        list_name: the list of repositories shown, None for all.
        keymap: the keys in use.
        colour_pairs: the curses colour pairs of the diff.
        context_step: the index into CONTEXT_STEPS, kept for all changes.
        whitespace: Gerrit's whitespace mode of the diff, one of
            WHITESPACE_MODES.
        follow_latest: whether a right side on the latest patch set moves
            to a new upload, state that the follow-latest key toggles.
        search: the text searched last in the diff.
        marking: whether the search's matches are marked.
        search_history: the texts searched before, the latest last.
        search_mode: where the search looks, one of SEARCH_MODES.
        sidebar: the left column of the change screen, one of
            SIDEBAR_MODES. The sidebar key sets it for the rest of the
            session.
    """

    def __init__(self, screen: Any, list_name: str | None = None) -> None:
        """Sets up curses and the settings, with the dashboard as the first
        screen.

        Args:
            screen: the curses window of the whole screen.
            list_name: the list of repositories shown, None for all.
        """
        self._screen = screen
        self.list_name = list_name
        self._views: list[View] = [StackView(self)]
        self._message = ''
        self._message_type: str | None = None
        self._version: str | None = None
        # The keys of an open text box for the footer, see write_text()
        self._box_footer: Callable[[], list[tuple[str, str]]] | None = None
        self.keymap = keymap_from_settings()
        if self.keymap.errors:
            self._message = 'Settings: ' + '; '.join(self.keymap.errors)
        curses.curs_set(0)
        curses.set_escdelay(_ESC_DELAY_MS)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(300)
        curses.use_default_colors()
        for number in range(1, 9):
            curses.init_pair(number, curses_colour(number if number < 8 else 8), -1)
        screen.timeout(_RELOAD_INTERVAL_MS)
        self.colour_pairs = ColourPairs()
        dim_colour = setting('tui.dim-colour', str(tui_colours.DIM_COLOUR)) or ''
        if dim_colour.isdigit() and int(dim_colour) < 256:
            tui_colours.DIM_COLOUR = int(dim_colour)
        self.context_step = 0
        self.whitespace = setting('diff.whitespace', 'none') or 'none'
        self.follow_latest = follows_latest()
        if self.whitespace not in WHITESPACE_MODES:
            self.whitespace = 'none'
        self.search = ''
        self.marking = False
        self.search_history: list[str] = []
        self.search_mode = setting('tui.search', 'shown') or 'shown'
        if self.search_mode not in SEARCH_MODES:
            self.search_mode = 'shown'
        self.sidebar = setting('tui.sidebar', 'auto') or 'auto'
        if self.sidebar not in SIDEBAR_MODES:
            self.sidebar = 'auto'

    # ── state shared by the views ─────────────────────────────────────────────

    def say(self, message: str, message_type: str | None = None) -> None:
        """Shows a message in the bottom line until the next key.

        Args:
            message: the message.
            message_type: OK (green), ERROR (red) or None for neutral
                information.
        """
        self._message = message
        self._message_type = message_type

    def open(self, view: View) -> None:
        """Shows a screen on top of the current one.

        Args:
            view: the screen.
        """
        self._views.append(view)
        view.reload()

    def back(self) -> bool:
        """Goes back to the previous screen.

        Returns:
            False at the first screen, else True.
        """
        if len(self._views) > 1:
            note = getattr(self._views.pop(), 'leaving_note', lambda: '')()
            self._views[-1].reload()
            if note:
                self.say(note)
            return True
        return False

    # ── main loop ─────────────────────────────────────────────────────────────

    def loop(self) -> None:
        """Draws, waits for a key or the reload interval, repeats. Keys
        typed while one was handled, like a held j, are all handled before
        the next draw: over a slow connection the screen skips steps
        instead of lagging behind.
        """
        self._views[-1].reload()
        self._version = self.version()
        while True:
            self._draw()
            key = self._screen.getch()
            if -1 == key:
                self._reload_if_changed()
                continue
            while -1 != key:
                self._handle_key(key)
                if not self._views:
                    return
                key = self._typed_ahead()

    def _typed_ahead(self) -> int:
        """Reads the next key if one is waiting already.

        Returns:
            The key code, -1 if none is waiting.
        """
        self._screen.timeout(0)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(_RELOAD_INTERVAL_MS)

    def _handle_key(self, key: int) -> None:
        """Does what a key asks for.

        Args:
            key: the key code, as getch() returns it.
        """
        self._message = ''
        if curses.KEY_MOUSE == key:
            self.keymap.reset()
            self._handle_mouse()
            return
        result = self.keymap.feed(self._with_meta(key))
        if isinstance(result, str):
            # PENDING: a count or sequence goes on
            return
        action, count = result
        if action is None:
            return
        if not self._handle_global(action):
            try:
                self._views[-1].action(action, count)
            except ChangeError as error:
                self.say(str(error), ERROR)

    def _with_meta(self, key: int) -> int:
        """Reads Alt plus a key, which arrives as Esc and the key.

        Args:
            key: the key code just read.

        Returns:
            The following key plus META after Esc, a lone Esc as Esc, any
            other key as it is.
        """
        if 27 != key:
            return key
        self._screen.timeout(_ESC_DELAY_MS)
        try:
            following = self._screen.getch()
        finally:
            self._screen.timeout(_RELOAD_INTERVAL_MS)
        return key if following in (-1, 27) else META | following

    def _handle_global(self, action: str) -> bool:
        """Handles the actions that work on every screen.

        Args:
            action: the action's name.

        Returns:
            Whether the action was one of them.
        """
        if 'quit-now' == action:
            self._views.clear()
            return True
        if 'quit' == action:
            # Quitting is easy by mistake, e.g. q instead of a tab
            # q twice quits too, like qq
            if self.ask_key('Quit plustwo? (y or q quits)') in (ord('y'), ord('q')):
                self._views.clear()
            return True
        if 'help' == action:
            self._show_help()
            return True
        if 'reload' == action:
            self._views[-1].reload()
            return True
        if 'back' == action:
            if getattr(self._views[-1], 'go_left', lambda: False)():
                return True
            # On the first screen, back does nothing: a stray Esc, e.g. from
            # a key sequence curses didn't understand, must not quit.
            self.back()
            return True
        return False

    def _handle_mouse(self) -> None:
        """Passes a click, double click or wheel turn to the current screen."""
        try:
            _id, column, row, _z, buttons = curses.getmouse()
        except curses.error:
            return
        if buttons & curses.BUTTON1_DOUBLE_CLICKED:
            mouse_event = DOUBLE_CLICK
        elif buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            mouse_event = CLICK
        elif buttons & curses.BUTTON4_PRESSED:
            mouse_event = WHEEL_UP
        elif buttons & getattr(curses, 'BUTTON5_PRESSED', 0):
            mouse_event = WHEEL_DOWN
        else:
            return
        try:
            self._views[-1].mouse(row, column, mouse_event)
        except ChangeError as error:
            self.say(str(error), ERROR)

    def _reload_if_changed(self) -> None:
        """Reloads the current view when commits or notes changed."""
        version = self.version()
        if version != self._version:
            self._version = version
            self._views[-1].reload()

    def version(self) -> str:
        """Reads the refs of every shown repository, see stack_version().

        Returns:
            The refs, which change with every write.
        """
        return '\n'.join(stack_version(repository)
                         for repository in review_repositories(self.list_name))

    def after_write(self) -> None:
        """Reloads after the interface itself wrote, without waiting."""
        self._version = self.version()
        self._views[-1].reload()

    # ── drawing ───────────────────────────────────────────────────────────────

    def _draw(self, does_refresh: bool = True) -> None:
        """Draws the current screen, its keys and the message line.

        Args:
            does_refresh: whether to show it on the terminal at once, else
                something is drawn over it first.
        """
        self._screen.erase()
        height, width = self._screen.getmaxyx()
        view = self._views[-1]
        view.draw(Area(self._screen, 0, height - 2, width))
        # The keys in bold, what they do dimmed. The help key stays, the
        # other hints go from the end when the line is too narrow.
        entries: list[tuple[tuple[str, ...], str, str]] = []
        if self._box_footer is not None:
            # A text box is open: its keys
            entries = [((), keys, label) for keys, label in self._box_footer()]
        for names, label in () if entries else view.footer():
            keys = '/'.join(filter(None, (self.keymap.label(name) for name in names)))
            if keys:
                entries.append((names, keys, label))
        kept = [entry for entry in entries if ('help',) == entry[0]]
        others = [entry for entry in entries if ('help',) != entry[0]]
        while others and 1 + sum(len(keys) + len(label) + 4
                                 for _, keys, label in others + kept) - 3 >= width:
            others.pop()
        column = 1
        for _names, keys, label in others + kept:
            if column + len(keys) + len(label) + 1 >= width:
                continue
            self.text(height - 2, column, keys, curses.A_BOLD, width)
            self.text(height - 2, column + len(keys) + 1, label, self.dim(), width)
            column += len(keys) + len(label) + 4
        colour = {OK: curses.color_pair(2), ERROR: curses.color_pair(1)}.get(
            self._message_type or '', 0)
        self.text(height - 1, 0, self._message, curses.A_BOLD | colour, width)
        # The typed prefix in the bottom right corner, like vim's showcmd
        prefix = self.keymap.typed()
        if prefix:
            self.text(height - 1, width - len(prefix) - 2, prefix, curses.A_BOLD)
        if does_refresh:
            self._screen.refresh()

    def dim(self, attributes: int = 0, background: int = -1) -> int:
        """Finds the look of secondary text, see secondary().

        Args:
            attributes: the row's curses attributes.
            background: the background, -1 for the terminal's.

        Returns:
            The curses attribute. Nothing on a reversed row, whose text is
            plain there.
        """
        if attributes & curses.A_REVERSE:
            return 0
        return self.colour_pairs.attribute(SECONDARY, background)

    def text(self, row: int, column: int, text: str, attributes: int = 0,
             width: int | None = None) -> None:
        """Writes text, cut at the screen's edge. Never raises for space.

        Args:
            row: the screen row.
            column: the screen column.
            text: the text.
            attributes: the curses attributes.
            width: the column to cut at, None for the screen's edge.
        """
        height, screen_width = self._screen.getmaxyx()
        if not 0 <= row < height or column >= screen_width:
            return
        limit = (width or screen_width) - column
        try:
            self._screen.addnstr(row, column, text, max(0, min(limit, screen_width - column - 1)),
                                 attributes)
        except curses.error:
            pass

    def _show_help(self) -> None:
        """Shows every action with its keys. The moving keys scroll, any
        other key closes it.
        """
        height, width = self._screen.getmaxyx()
        lines = self.keymap.help_lines(width - 2)
        first = 0
        self._screen.timeout(-1)
        try:
            while True:
                self._screen.erase()
                shown = lines[first:first + height - 1]
                for row, line in enumerate(shown):
                    self.text(row, 1, line)
                more = len(lines) > height - 1
                self.text(height - 1, 1, (f'{self.keymap.label("down")}/'
                                          f'{self.keymap.label("up")} scroll, ' if more else '')
                          + 'any other key closes', self.dim())
                self._screen.refresh()
                result = self.keymap.feed(self._with_meta(self._screen.getch()))
                if isinstance(result, str):
                    # PENDING: a count or sequence goes on
                    continue
                step = {'down': 1, 'up': -1, 'page-down': height // 2,
                        'page-up': -(height // 2)}.get(result[0] or '')
                if step is None:
                    return
                first = max(0, min(first + step, max(len(lines) - height + 1, 0)))
        finally:
            self._screen.timeout(_RELOAD_INTERVAL_MS)

    # ── leaving the screen for other programs ─────────────────────────────────

    def run_outside(self, command: list[str]) -> None:
        """Runs a program on the real terminal, like git diff with delta.

        Args:
            command: the command line.
        """
        curses.endwin()
        subprocess.run(command, env=pager_environment())
        # A refresh after endwin switches back to the alternate screen and
        # curses' modes. reset_prog_mode alone would not switch the screen,
        # and the interface would be left on the terminal after quitting.
        self._screen.clearok(True)
        self._screen.refresh()

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
            self.run_outside([editor, path])
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
        has_emacs_keys = 'emacs' == self.keymap.preset
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

        self._box_footer = footer
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
                self._draw(does_refresh=False)
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
            self._box_footer = None
            if isinstance(anchor, DiffGap):
                anchor.close()
            curses.curs_set(0)
            self._screen.timeout(_RELOAD_INTERVAL_MS)

    def _read_after_escape(self, box: TextBox) -> bool:
        """Reads what follows Esc in the text box: Alt-Enter arrives as Esc
        and Enter, Shift+Enter as an escape sequence where the terminal
        tells it apart, a lone Esc cancels.

        Args:
            box: the text box, which gets the new line.

        Returns:
            True if Esc cancels, False if the key was handled.
        """
        self._screen.timeout(_ESC_DELAY_MS)
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
        sequence = ''
        self._screen.timeout(_ESC_DELAY_MS)
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
        self.text(top, left, '┌' + '─' * (box_width - 2) + '┐', curses.A_BOLD)
        self.text(top, left + 2, f' {title} ', curses.A_BOLD)
        self.text(bottom, left, '└' + '─' * (box_width - 2) + '┘', curses.A_BOLD)
        if len(hint) + 4 < box_width:
            self.text(bottom, left + box_width - len(hint) - 2, hint,
                      curses.A_BOLD if box.is_discarding else self.dim())
        for offset, (line, is_focused) in enumerate(extra):
            self.text(top + 1 + offset, left, '│', curses.A_BOLD)
            self.text(top + 1 + offset, left + 1, f'{line:<{box_width - 2}}'[:box_width - 2],
                      curses.A_REVERSE if is_focused else self.dim())
            self.text(top + 1 + offset, left + box_width - 1, '│', curses.A_BOLD)
        for offset in range(n_inside_rows):
            line = rows[first + offset] if first + offset < len(rows) else ''
            self.text(top + 1 + n_rows_above + offset, left, '│', curses.A_BOLD)
            self.text(top + 1 + n_rows_above + offset, left + 1, f'{line:<{box_width - 2}}')
            self.text(top + 1 + n_rows_above + offset, left + box_width - 1, '│', curses.A_BOLD)
        try:
            self._screen.move(top + 1 + n_rows_above + cursor_row - first,
                              left + 1 + cursor_column)
        except curses.error:
            pass

    def ask(self, question: str, history: Sequence[str] = (),
            on_tab: Callable[[], str] | None = None) -> str | None:
        """Asks for one line in the bottom line.

        Args:
            question: the question.
            history: the earlier answers, the latest last, which Up and
                Down go through.
            on_tab: what Tab calls, which returns the new question. None
                for nothing.

        Returns:
            The answer, stripped. None on Esc or C-g.
        """
        height, width = self._screen.getmaxyx()
        answer = ''
        # Where in the history Up and Down are, len(history) is the answer typed
        position = len(history)
        typed = ''
        curses.curs_set(1)
        self._screen.timeout(-1)
        try:
            while True:
                self._screen.move(height - 1, 0)
                self._screen.clrtoeol()
                self.text(height - 1, 0, f'{question} {answer}', curses.A_BOLD, width)
                key = self._screen.get_wch()
                if key in ('\n', '\r'):
                    return answer.strip()
                if key in ('\x1b', '\x07'):
                    return None
                if key in (curses.KEY_UP, curses.KEY_DOWN) and history:
                    if len(history) == position:
                        typed = answer
                    step = -1 if curses.KEY_UP == key else 1
                    position = max(0, min(position + step, len(history)))
                    answer = history[position] if position < len(history) else typed
                elif '\t' == key and on_tab:
                    question = on_tab()
                elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                    answer = answer[:-1]
                elif isinstance(key, str) and key.isprintable():
                    answer += key
        finally:
            curses.curs_set(0)
            self._screen.timeout(_RELOAD_INTERVAL_MS)

    def choose(self, question: str, choice: Choice) -> int | None:
        """Lets the reviewer pick a value in the bottom line: Left, Right or
        a digit, Enter takes it.

        Args:
            question: the question.
            choice: the values to pick from.

        Returns:
            The value, None on Esc, C-g or q.
        """
        height, width = self._screen.getmaxyx()
        self._screen.timeout(-1)
        try:
            while True:
                self._draw(does_refresh=False)
                self._screen.move(height - 1, 0)
                self._screen.clrtoeol()
                line = f'{question} {choice.text()}   ←/→ or a digit, Enter takes it'
                self.text(height - 1, 0, line, curses.A_BOLD, width)
                self._screen.refresh()
                key = self._screen.get_wch()
                if key in ('\r', '\n', curses.KEY_ENTER):
                    return choice.value
                if key in ('\x1b', '\x07', 'q'):
                    return None
                choice.key(key)
        finally:
            self._screen.timeout(_RELOAD_INTERVAL_MS)

    def ask_key(self, question: str) -> int:
        """Shows a question in the bottom line over the screen.

        Args:
            question: the question.

        Returns:
            The key code of the key pressed.
        """
        height, width = self._screen.getmaxyx()
        self._draw(does_refresh=False)
        self._screen.move(height - 1, 0)
        self._screen.clrtoeol()
        self.text(height - 1, 0, question, curses.A_BOLD, width)
        self._screen.refresh()
        self._screen.timeout(-1)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(_RELOAD_INTERVAL_MS)

    def dialog(self, lines: list[str], footer: str) -> int:
        """Shows lines in a box in the middle of the screen, over the
        current screen, and waits for a key.

        Args:
            lines: the lines.
            footer: the last line, in bold.

        Returns:
            The key code of the key pressed.
        """
        height, width = self._screen.getmaxyx()
        inner = min(max([len(line) for line in lines] + [len(footer), 30]) + 2, width - 4)
        shown = lines[:max(height - 6, 1)]
        top = max((height - len(shown) - 4) // 2, 0)
        left = max((width - inner - 2) // 2, 0)
        self._draw(does_refresh=False)
        rows = shown + ['', footer]
        self.text(top, left, '┌' + '─' * inner + '┐', curses.A_BOLD)
        for offset, line in enumerate(rows, start=1):
            attributes = curses.A_BOLD if offset == len(rows) else 0
            self.text(top + offset, left, '│', curses.A_BOLD)
            self.text(top + offset, left + 1, f' {line:<{inner - 1}.{inner - 1}}', attributes)
            self.text(top + offset, left + inner + 1, '│', curses.A_BOLD)
        self.text(top + len(rows) + 1, left, '└' + '─' * inner + '┘', curses.A_BOLD)
        self._screen.refresh()
        self._screen.timeout(-1)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(_RELOAD_INTERVAL_MS)

    def show_lines(self, lines: list[str]) -> None:
        """Shows lines in a box until a key is pressed.

        Args:
            lines: the lines.
        """
        self.dialog(lines, 'Press any key.')

    def confirm(self, lines: list[str], question: str) -> bool:
        """Shows lines in a box and asks a yes/no question.

        Args:
            lines: the lines.
            question: the question.

        Returns:
            True for y.
        """
        return ord('y') == self.dialog(lines, f'{question} (y/N)')
